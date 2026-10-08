"""Règles métier de la bibliothèque (prêts, sanctions, commandes en ligne, achats)."""
from datetime import timedelta
from uuid import uuid4
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.db import transaction, models
from django.db.models import F, Sum
from django.utils import timezone

from .models import Achat, Emprunt, Exemplaire, LigneAchat, Livre, Utilisateur

HORS_CIRCUIT = ("perdu", "retire")


def params():
    """Règles de la bibliothèque (réglées dans settings.BIBLIO)."""
    d = dict(settings.BIBLIO)
    for cle in ("amende_par_jour", "amende_plafond", "frais_perte"):
        d[cle] = Decimal(str(d[cle]))
    return SimpleNamespace(**d)


def fmt(d):
    return d.strftime("%d/%m/%Y")


def generer_numero_carte():
    n = 1001
    existants = set(Utilisateur.objects.filter(numero_carte__startswith="ADH-").values_list("numero_carte", flat=True))
    while f"ADH-{n}" in existants:
        n += 1
    return f"ADH-{n}"


# ---------- Sanctions ----------
def montant_amende(jours):
    p = params()
    montant = p.amende_par_jour * jours
    if p.amende_plafond and p.amende_plafond > 0:
        montant = min(montant, p.amende_plafond)
    return montant


def montant_impaye(adherent):
    return (Emprunt.objects.filter(adherent=adherent, statut_paiement="impayee")
            .aggregate(t=Sum("montant_sanction"))["t"] or Decimal(0))


def _appliquer_sanction(e, supplement=Decimal(0), motif_supp=""):
    """Calcule la sanction de l'emprunt : amende de retard + éventuel supplément (perte, dégâts).
    Une sanction déjà payée ou annulée n'est plus modifiée."""
    # Une sanction annulée ne doit pas empêcher une nouvelle sanction légitime
    # (ex. le livre est ensuite déclaré perdu ou rendu avec dégâts).
    ancien_statut_paiement = e.statut_paiement
    if ancien_statut_paiement == "payee":
        return None
    jours = e.jours_retard
    montant = (montant_amende(jours) if jours > 0 else Decimal(0)) + supplement
    if montant <= 0:
        return None
    motifs = ([f"Retard de {jours} jour(s)"] if jours > 0 else []) + ([motif_supp] if motif_supp else [])
    e.montant_sanction, e.motif_sanction, e.statut_paiement = montant, " + ".join(motifs), "impayee"
    if not e.date_sanction or ancien_statut_paiement == "annulee":
        e.date_sanction = timezone.now()
    e.save(update_fields=["montant_sanction", "motif_sanction", "statut_paiement", "date_sanction"])
    return e


def maj_quotidienne():
    """Retards et amendes, fins de suspension, demandes prêtes non retirées. Idempotent."""
    today = timezone.localdate()
    en_retard = Emprunt.objects.filter(statut__in=Emprunt.OUVERTS, date_retour_prevue__lt=today)
    for e in en_retard.select_related("adherent", "exemplaire__livre"):
        if e.statut == "en_cours":
            e.statut = "retard"
            e.save(update_fields=["statut"])
        _appliquer_sanction(e)
    Utilisateur.objects.filter(statut="suspendu", suspendu_jusqu__lt=today).update(statut="actif", suspendu_jusqu=None)
    limite = today - timedelta(days=params().delai_retrait_jours)
    for e in Emprunt.objects.filter(statut="prete").filter(
            models.Q(date_limite_retrait__lt=today) | models.Q(date_limite_retrait__isnull=True, date_emprunt__lt=limite)):
        annuler_emprunt(e)

    # Les achats en ligne au retrait immobilisent le stock seulement pendant le délai prévu.
    for a in Achat.objects.filter(canal="en_ligne", statut__in=Achat.ACTIFS, mode_reception="retrait",
                                  date_limite_retrait__lt=today):
        annuler_achat(a, motif="Commande non retirée dans le délai prévu")


# ---------- Règles d'éligibilité ----------
def verifier_adherent(adherent):
    """Lève ValueError si l'adhérent n'a pas le droit d'emprunter."""
    today = timezone.localdate()
    if not adherent.numero_carte:
        raise ValueError("Ce compte n'est pas un compte adhérent.")
    if adherent.statut == "en_attente":
        raise ValueError("Votre adhésion n'a pas encore été validée : présentez-vous à la bibliothèque.")
    if adherent.date_expiration and adherent.date_expiration < today:
        raise ValueError("Votre adhésion a expiré : renouvelez-la au comptoir de la bibliothèque.")
    if adherent.suspendu_jusqu and adherent.suspendu_jusqu >= today:
        raise ValueError(f"Vos emprunts sont suspendus jusqu'au {fmt(adherent.suspendu_jusqu)} (retard précédent).")
    dette = montant_impaye(adherent)
    if dette > 0:
        raise ValueError(f"Vous avez {dette:.0f} FCFA de sanctions impayées : réglez-les au comptoir pour emprunter.")
    if Emprunt.objects.filter(adherent=adherent, statut__in=Emprunt.OUVERTS, date_retour_prevue__lt=today).exists():
        raise ValueError("Vous avez un livre en retard : rapportez-le avant d'emprunter à nouveau.")


def nb_emprunts_actifs(adherent):
    return Emprunt.objects.filter(adherent=adherent, statut__in=Emprunt.ACTIFS).count()


def verifier_quota(adherent, nb=1):
    maxi = params().max_emprunts
    actifs = nb_emprunts_actifs(adherent)
    if actifs + nb > maxi:
        raise ValueError(f"Limite de {maxi} emprunts simultanés atteinte (vous en avez déjà {actifs}, "
                         f"demandes en cours comprises).")


def deja_emprunte(adherent, livre):
    return Emprunt.objects.filter(adherent=adherent, exemplaire__livre=livre, statut__in=Emprunt.ACTIFS).exists()


# ---------- Exemplaires ----------
def liberer_exemplaire(ex):
    ex.refresh_from_db()
    if ex.statut not in HORS_CIRCUIT:
        ex.statut = "disponible"
        ex.save(update_fields=["statut"])


def attribuer_exemplaire(livre):
    """Met de côté un exemplaire disponible."""
    ex = Exemplaire.objects.select_for_update().filter(livre=livre, statut="disponible").first()
    if not ex:
        raise ValueError(f"« {livre.titre} » n'a plus d'exemplaire disponible.")
    ex.statut = "reserve"
    ex.save(update_fields=["statut"])
    return ex


# ---------- Prêts ----------
@transaction.atomic
def creer_emprunt_comptoir(adherent, code_barres, staff, jours=None):
    verifier_adherent(adherent)
    ex = Exemplaire.objects.select_for_update().select_related("livre").get(code_barres=code_barres)
    if ex.statut in HORS_CIRCUIT:
        raise ValueError("Cet exemplaire est retiré du fonds ou perdu.")
    if ex.statut == "reserve":
        dem = Emprunt.objects.filter(exemplaire=ex, statut__in=("demande", "prete")).first()
        if dem and dem.adherent_id == adherent.id:
            return remettre_emprunt(dem, staff, jours)
        raise ValueError("Cet exemplaire est mis de côté pour un autre adhérent.")
    if ex.statut != "disponible":
        raise ValueError("Exemplaire déjà emprunté ou indisponible.")
    verifier_quota(adherent, 1)
    if deja_emprunte(adherent, ex.livre):
        raise ValueError("Cet adhérent a déjà un exemplaire de ce livre.")
    today = timezone.localdate()
    e = Emprunt.objects.create(adherent=adherent, personnel=staff, exemplaire=ex, date_emprunt=today, statut="en_cours",
                               date_retour_prevue=today + timedelta(days=jours or params().duree_pret_jours))
    ex.statut = "emprunte"
    ex.save(update_fields=["statut"])
    return e


@transaction.atomic
def preparer_emprunt(e, staff):
    """Le personnel accepte la demande en ligne : le livre est prêt (à retirer ou à livrer)."""
    if e.statut != "demande":
        raise ValueError("Cette demande est déjà préparée.")
    e.statut, e.personnel, e.date_emprunt = "prete", staff, timezone.localdate()
    e.date_limite_retrait = timezone.localdate() + timedelta(days=params().delai_retrait_jours)
    e.save(update_fields=["statut", "personnel", "date_emprunt", "date_limite_retrait"])


@transaction.atomic
def remettre_emprunt(e, staff, jours=None):
    """Remise du livre à l'adhérent (au comptoir ou à domicile) : le prêt commence."""
    if e.statut not in ("demande", "prete"):
        raise ValueError("Cette demande n'est plus en attente.")
    verifier_adherent(e.adherent)
    today = timezone.localdate()
    e.statut, e.personnel, e.date_emprunt = "en_cours", staff, today
    e.date_retour_prevue = today + timedelta(days=jours or params().duree_pret_jours)
    e.date_limite_retrait = None
    e.save(update_fields=["statut", "personnel", "date_emprunt", "date_retour_prevue", "date_limite_retrait"])
    Exemplaire.objects.filter(pk=e.exemplaire_id).update(statut="emprunte")
    return e


@transaction.atomic
def annuler_emprunt(e):
    if e.statut not in ("demande", "prete"):
        raise ValueError("Cette demande ne peut plus être annulée.")
    e.statut = "annule"
    e.save(update_fields=["statut"])
    liberer_exemplaire(e.exemplaire)


@transaction.atomic
def enregistrer_retour(e, etat="bon", degats=0):
    """Clôt un prêt : calcule la sanction (retard, dégâts), suspend l'adhérent si besoin, remet le livre en rayon."""
    if e.statut not in Emprunt.OUVERTS:
        raise ValueError("Cet emprunt n'est pas en cours.")
    today, p = timezone.localdate(), params()
    e.date_retour_effective, e.statut = today, "rendu"
    e.save(update_fields=["date_retour_effective", "statut"])
    ex = e.exemplaire
    degats = Decimal(str(degats or 0))
    if etat == "abime":
        ex.etat_physique = "abime"
        ex.save(update_fields=["etat_physique"])
    motif = ("Livre rendu abîmé" if etat == "abime" else "Dégradation du livre") if degats > 0 else ""
    sanction = _appliquer_sanction(e, degats, motif)
    jours = e.jours_retard
    a = e.adherent
    if jours > 0 and p.coef_suspension > 0 and a.statut == "actif":
        fin = today + timedelta(days=jours * p.coef_suspension)
        if not a.suspendu_jusqu or a.suspendu_jusqu < fin:
            a.suspendu_jusqu, a.statut = fin, "suspendu"
            a.save(update_fields=["suspendu_jusqu", "statut"])
    liberer_exemplaire(ex)
    return sanction


@transaction.atomic
def prolonger(e):
    p, today = params(), timezone.localdate()
    if e.statut not in Emprunt.OUVERTS:
        raise ValueError("Cet emprunt n'est pas en cours.")
    if e.date_retour_prevue < today:
        raise ValueError("Ce livre est en retard : il doit être rapporté, il ne peut plus être prolongé.")
    if e.nb_prolongations >= p.max_prolongations:
        raise ValueError(f"Limite de {p.max_prolongations} prolongation(s) atteinte pour ce prêt.")
    e.date_retour_prevue += timedelta(days=p.duree_prolongation_jours)
    e.nb_prolongations += 1
    e.save(update_fields=["date_retour_prevue", "nb_prolongations"])
    return e


@transaction.atomic
def declarer_perdu(e):
    """Livre perdu : sanction = prix du livre + frais de dossier (+ retard accumulé)."""
    if e.statut not in Emprunt.OUVERTS:
        raise ValueError("Cet emprunt n'est pas en cours.")
    e.date_retour_effective, e.statut = timezone.localdate(), "perdu"
    e.save(update_fields=["date_retour_effective", "statut"])
    ex = e.exemplaire
    ex.statut = "perdu"
    ex.save(update_fields=["statut"])
    return _appliquer_sanction(e, ex.livre.prix + params().frais_perte, "Livre perdu (prix + frais de dossier)")


@transaction.atomic
def encaisser_sanction(e):
    if e.statut_paiement != "impayee":
        raise ValueError("Cette sanction n'est plus à payer.")
    if e.statut in Emprunt.OUVERTS:
        raise ValueError("Le montant n'est définitif qu'au retour du livre : enregistrez d'abord le retour.")
    e.statut_paiement = "payee"
    e.save(update_fields=["statut_paiement"])


@transaction.atomic
def annuler_sanction(e):
    if e.statut_paiement != "impayee":
        raise ValueError("Seules les sanctions impayées peuvent être annulées.")
    e.statut_paiement = "annulee"
    e.save(update_fields=["statut_paiement"])


# ---------- Commande en ligne ----------
@transaction.atomic
def passer_commande(adherent, lignes, mode, adresse=""):
    """lignes : liste de (livre_id, type, quantité). Les emprunts deviennent des demandes (exemplaire mis de côté),
    les achats forment un Achat en ligne (stock mis de côté). Retourne (emprunts, achat)."""
    if not lignes:
        raise ValueError("Votre panier est vide.")
    # Un compte connecté peut acheter. Seul l’emprunt exige une carte d’adhérent.
    if not adherent.is_authenticated:
        raise ValueError("Connectez-vous pour utiliser cette commande.")
    if mode == "livraison" and not adresse.strip():
        raise ValueError("Indiquez l'adresse de livraison.")
    adresse = adresse.strip() if mode == "livraison" else ""
    nb_emprunts = sum(1 for _, t, _ in lignes if t == "emprunt")
    if nb_emprunts:
        if not adherent.numero_carte:
            raise ValueError("Une carte d’adhérent est nécessaire pour emprunter un livre.")
        verifier_adherent(adherent)
        verifier_quota(adherent, nb_emprunts)
    emprunts, achat = [], None
    for livre_id, type_, qte in lignes:
        livre = Livre.objects.select_for_update().get(pk=livre_id)
        if not livre.actif:
            raise ValueError(f"« {livre.titre} » n’est plus disponible dans le catalogue.")
        if type_ == "emprunt":
            if deja_emprunte(adherent, livre):
                raise ValueError(f"Vous avez déjà « {livre.titre} » (emprunt ou demande en cours).")
            ex = attribuer_exemplaire(livre)
            emprunts.append(Emprunt.objects.create(adherent=adherent, exemplaire=ex, statut="demande",
                                                   adresse_livraison=adresse))
        else:
            qte = max(1, int(qte))
            if qte > params().max_quantite_achat_en_ligne:
                raise ValueError(f"Maximum {params().max_quantite_achat_en_ligne} exemplaires de « {livre.titre} » par commande en ligne.")
            if qte > livre.stock_vente:
                raise ValueError(f"Stock insuffisant pour « {livre.titre} » ({livre.stock_vente} en vente).")
            if achat is None:
                achat = Achat.objects.create(adherent=adherent, canal="en_ligne", mode_reception=mode,
                                            adresse_livraison=adresse, statut_paiement="en_attente",
                                            date_limite_retrait=(timezone.localdate() + timedelta(days=params().delai_retrait_jours))
                                            if mode == "retrait" else None)
            livre.stock_vente -= qte
            livre.save(update_fields=["stock_vente"])
            LigneAchat.objects.create(achat=achat, livre=livre, quantite=qte, prix_applique=livre.prix)
    return emprunts, achat


@transaction.atomic
def passer_achat_invite(prenom, nom, email, telephone, lignes, mode="retrait", adresse=""):
    """Crée un achat réellement invité : aucun compte adhérent n'est créé."""
    if not lignes:
        raise ValueError("Votre panier est vide.")
    if mode not in ("retrait", "livraison"):
        raise ValueError("Mode de réception invalide.")
    if mode == "livraison" and not adresse.strip():
        raise ValueError("Indiquez l'adresse de livraison.")
    identifiants = []
    if email.strip():
        identifiants.append(models.Q(client_email__iexact=email.strip()))
    if telephone.strip():
        identifiants.append(models.Q(client_telephone=telephone.strip()))
    if identifiants:
        q = identifiants[0]
        for part in identifiants[1:]:
            q |= part
        actifs = Achat.objects.filter(adherent__isnull=True, canal="en_ligne", statut__in=Achat.ACTIFS).filter(q).count()
        if actifs >= params().max_commandes_invite_actives:
            raise ValueError(f"Limite de {params().max_commandes_invite_actives} commandes invitées actives atteinte. Suivez ou finalisez vos commandes en cours avant d’en créer une autre.")
    achat = Achat.objects.create(
        adherent=None, canal="en_ligne", mode_reception=mode,
        adresse_livraison=adresse.strip() if mode == "livraison" else "",
        client_prenom=prenom.strip(), client_nom=nom.strip(), client_email=email.strip(),
        client_telephone=telephone.strip(), statut_paiement="en_attente",
        date_limite_retrait=(timezone.localdate() + timedelta(days=params().delai_retrait_jours)) if mode == "retrait" else None
    )
    for livre_id, qte in lignes:
        livre = Livre.objects.select_for_update().get(pk=livre_id)
        if not livre.actif:
            raise ValueError(f"« {livre.titre} » n'est plus disponible à la vente.")
        qte = max(1, int(qte))
        if qte > params().max_quantite_achat_en_ligne:
            raise ValueError(f"Maximum {params().max_quantite_achat_en_ligne} exemplaires de « {livre.titre} » par commande en ligne.")
        if qte > livre.stock_vente:
            raise ValueError(f"Stock insuffisant pour « {livre.titre} » ({livre.stock_vente} en vente).")
        livre.stock_vente -= qte
        livre.save(update_fields=["stock_vente"])
        LigneAchat.objects.create(achat=achat, livre=livre, quantite=qte, prix_applique=livre.prix)
    return achat


# ---------- Achats ----------
@transaction.atomic
def achat_comptoir(adherent, lignes, mode_paiement, client=None, reference_paiement=""):
    """Vente immédiate au comptoir, à un adhérent ou à un client de passage."""
    if not lignes:
        raise ValueError("Ajoutez au moins un livre.")
    if mode_paiement not in dict(Achat.MODES_PAIEMENT):
        raise ValueError("Choisissez le mode de paiement.")
    client = client or {}
    if mode_paiement == "mobile" and not reference_paiement.strip():
        raise ValueError("Indiquez la référence de la transaction Mobile Money.")
    achat = Achat.objects.create(adherent=adherent, canal="comptoir", mode_reception="retrait",
                                 mode_paiement=mode_paiement, reference_paiement=reference_paiement.strip(),
                                 statut_paiement="paye", date_paiement=timezone.now(),
                                 statut="terminee", client_prenom=client.get("prenom", ""), client_nom=client.get("nom", ""),
                                 client_email=client.get("email", ""), client_telephone=client.get("telephone", ""))
    for livre_id, qte in lignes:
        livre = Livre.objects.select_for_update().get(pk=livre_id)
        if not livre.actif:
            raise ValueError(f"« {livre.titre} » est masqué du catalogue et ne peut plus être vendu en ligne.")
        if qte > livre.stock_vente:
            raise ValueError(f"Stock insuffisant pour « {livre.titre} » ({livre.stock_vente} en vente).")
        livre.stock_vente -= qte
        livre.save(update_fields=["stock_vente"])
        LigneAchat.objects.create(achat=achat, livre=livre, quantite=qte, prix_applique=livre.prix)
    return achat


@transaction.atomic
def annuler_achat(a, motif="Commande annulée"):
    if a.statut not in Achat.ACTIFS:
        raise ValueError("Cet achat ne peut plus être annulé.")
    for l in a.lignes.all():
        Livre.objects.filter(pk=l.livre_id).update(stock_vente=F("stock_vente") + l.quantite)
    a.statut = "annulee"
    a.statut_paiement = "annule"
    a.save(update_fields=["statut", "statut_paiement", "date_limite_retrait"])


@transaction.atomic
def avancer_achat(a, action, mode_paiement=None, reference_paiement=""):
    """Étapes d'un achat en ligne : preparer, expedier (livraison), remettre (paiement à la remise)."""
    if action == "preparer":
        if a.statut != "en_attente":
            raise ValueError("Cet achat est déjà préparé.")
        a.statut = "prete"
    elif action == "expedier":
        if a.statut != "prete" or not a.livraison:
            raise ValueError("Seul un achat prêt, à livrer, peut partir en livraison.")
        a.statut = "en_livraison"
    elif action == "remettre":
        if a.statut not in ("prete", "en_livraison"):
            raise ValueError("L'achat doit d'abord être préparé.")
        if mode_paiement not in dict(Achat.MODES_PAIEMENT):
            raise ValueError("Choisissez le mode de paiement.")
        if mode_paiement == "mobile" and not reference_paiement.strip():
            raise ValueError("Indiquez la référence de la transaction Mobile Money.")
        a.statut, a.mode_paiement = "terminee", mode_paiement
        a.reference_paiement = reference_paiement.strip()
        a.statut_paiement, a.date_paiement = "paye", timezone.now()
    else:
        raise ValueError("Action inconnue.")
    a.save()
