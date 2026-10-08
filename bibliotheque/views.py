"""Espace de gestion (personnel) : prêts, commandes, achats, sanctions, adhérents, livres."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import services
from .acces import STAFF, est_personnel, redirection_sure, role_requis
from .forms import AdherentForm, LivreForm
from .models import Achat, Emprunt, Exemplaire, Livre, Utilisateur


def _post_only(request, nom):
    return None if request.method == "POST" else redirect(nom)


@login_required
def apres_connexion(request):
    """Après la connexion : personnel vers la gestion, adhérent vers son espace."""
    return redirect("tableau_bord" if est_personnel(request.user) else "mon_compte")


# ---------- Tableau de bord ----------
@role_requis(*STAFF)
def tableau_bord(request):
    services.maj_quotidienne()
    today = timezone.localdate()
    ouverts = Emprunt.objects.filter(statut__in=Emprunt.OUVERTS)
    demandes = Emprunt.objects.filter(statut__in=("demande", "prete"))
    achats_actifs = Achat.objects.filter(statut__in=Achat.ACTIFS)
    stats = {
        "livres": Livre.objects.filter(actif=True).count(),
        "exemplaires_dispo": Exemplaire.objects.filter(statut="disponible").count(),
        "emprunts_en_cours": ouverts.count(),
        "retards": ouverts.filter(date_retour_prevue__lt=today).count(),
        "retours_du_jour": ouverts.filter(date_retour_prevue=today).count(),
        "commandes_a_traiter": demandes.count() + achats_actifs.count(),
        "sanctions_impayees": Emprunt.objects.filter(statut_paiement="impayee").aggregate(t=Sum("montant_sanction"))["t"] or 0,
        "adhesions_a_valider": Utilisateur.objects.filter(numero_carte__isnull=False, statut="en_attente").count(),
        "achats_jour": Achat.objects.filter(date_achat__date=today).exclude(statut="annulee").count(),
    }
    retards = list(ouverts.filter(date_retour_prevue__lt=today).select_related("adherent", "exemplaire__livre")
                   .order_by("date_retour_prevue")[:8])
    for e in retards:
        e.amende = services.montant_amende(e.jours_retard)
    a_traiter = [{"date": e.date_emprunt, "type": "Emprunt", "adherent": e.adherent, "libelle": e.exemplaire.livre.titre,
                  "statut": e.get_statut_display(), "livraison": e.livraison, "url": reverse("commandes")}
                 for e in demandes.select_related("adherent", "exemplaire__livre")]
    a_traiter += [{"date": a.date_achat.date(), "type": "Achat", "adherent": a.client_nom_complet, "libelle": f"{a.lignes.count()} livre(s)",
                   "statut": a.get_statut_display(), "livraison": a.livraison, "url": reverse("achat_detail", args=[a.pk])}
                  for a in achats_actifs.select_related("adherent")]
    a_traiter.sort(key=lambda x: x["date"])
    return render(request, "bibliotheque/tableau_bord.html", {"stats": stats, "retards": retards, "a_traiter": a_traiter[:8]})


# ---------- Prêts & retours ----------
@role_requis(*STAFF)
def emprunts(request):
    services.maj_quotidienne()
    if request.method == "POST":
        try:
            adherent = Utilisateur.objects.get(numero_carte=request.POST.get("carte", "").strip())
            jours = int(request.POST.get("jours") or 0) or None
            e = services.creer_emprunt_comptoir(adherent, request.POST.get("code_barres", "").strip(), request.user, jours)
            messages.success(request, f"Emprunt enregistré pour {adherent}, retour prévu le "
                                      f"{services.fmt(e.date_retour_prevue)}.")
        except Utilisateur.DoesNotExist:
            messages.error(request, "Carte d'adhérent introuvable.")
        except Exemplaire.DoesNotExist:
            messages.error(request, "Code-barres d'exemplaire introuvable.")
        except ValueError as err:
            messages.error(request, str(err))
        return redirect("emprunts")

    vue, q = request.GET.get("vue", "en_cours"), request.GET.get("q", "").strip()
    today = timezone.localdate()
    liste = Emprunt.objects.select_related("adherent", "exemplaire__livre")
    if vue == "en_cours":
        liste = liste.filter(statut__in=Emprunt.OUVERTS)
    elif vue == "retards":
        liste = liste.filter(statut__in=Emprunt.OUVERTS, date_retour_prevue__lt=today)
    else:
        liste = liste.exclude(statut__in=("demande", "prete", "annule"))
    if q:
        liste = liste.filter(Q(adherent__nom__icontains=q) | Q(adherent__prenom__icontains=q)
                             | Q(adherent__numero_carte__icontains=q) | Q(exemplaire__livre__titre__icontains=q)
                             | Q(exemplaire__code_barres__icontains=q))
    liste = list(liste.order_by("date_retour_prevue" if vue != "tous" else "-date_emprunt", "-id")[:150])
    for e in liste:
        e.amende = services.montant_amende(e.jours_retard) if (e.statut in Emprunt.OUVERTS and e.jours_retard) else 0
    return render(request, "bibliotheque/emprunts.html", {
        "emprunts": liste, "vue": vue, "q": q, "params": services.params(), "etats": Exemplaire.ETATS})


@role_requis(*STAFF)
def emprunt_action(request, pk, action):
    r = _post_only(request, "emprunts")
    if r:
        return r
    e = get_object_or_404(Emprunt.objects.select_related("adherent", "exemplaire__livre"), pk=pk)
    try:
        if action == "retour":
            sanction = services.enregistrer_retour(e, request.POST.get("etat", "bon"), request.POST.get("degats") or 0)
            if sanction and sanction.statut_paiement == "impayee":
                messages.warning(request, f"Retour enregistré. Sanction de {sanction.montant_sanction:.0f} FCFA : "
                                          f"{sanction.motif_sanction}.")
            else:
                messages.success(request, "Retour enregistré.")
        elif action == "prolonger":
            services.prolonger(e)
            messages.success(request, f"Prêt prolongé jusqu'au {services.fmt(e.date_retour_prevue)}.")
        elif action == "perdu":
            s = services.declarer_perdu(e)
            messages.warning(request, f"Livre déclaré perdu : sanction de {s.montant_sanction:.0f} FCFA enregistrée.")
        elif action == "preparer":
            services.preparer_emprunt(e, request.user)
            messages.success(request, "Demande acceptée : le livre est prêt.")
        elif action == "remettre":
            services.remettre_emprunt(e, request.user)
            messages.success(request, f"Livre remis. Retour prévu le {services.fmt(e.date_retour_prevue)}.")
        elif action == "refuser":
            services.annuler_emprunt(e)
            messages.info(request, "Demande annulée, l'exemplaire est remis en rayon.")
    except (ValueError, InvalidOperation) as err:
        messages.error(request, str(err) if isinstance(err, ValueError) else "Montant invalide.")
    return redirection_sure(request, "emprunts")


# ---------- Commandes en ligne & achats ----------
@role_requis(*STAFF)
def commandes(request):
    services.maj_quotidienne()
    demandes = (Emprunt.objects.filter(statut__in=("demande", "prete")).select_related("adherent", "exemplaire__livre")
                .order_by("date_emprunt", "id"))
    achats = (Achat.objects.filter(canal="en_ligne", statut__in=Achat.ACTIFS).select_related("adherent")
              .prefetch_related("lignes__livre").order_by("date_achat"))
    return render(request, "bibliotheque/commandes.html", {"demandes": demandes, "achats": achats})


@role_requis(*STAFF)
def achats(request):
    statut = request.GET.get("statut", "actives")
    liste = Achat.objects.select_related("adherent").prefetch_related("lignes")
    if statut == "actives":
        liste = liste.filter(statut__in=Achat.ACTIFS)
    elif statut != "toutes":
        liste = liste.filter(statut=statut)
    return render(request, "bibliotheque/achats.html", {"achats": liste[:100], "statut": statut})


@role_requis(*STAFF)
def achat_nouveau(request):
    """Vente au comptoir : adhérent identifié ou client de passage."""
    livres = Livre.objects.filter(stock_vente__gt=0, actif=True).order_by("titre")
    if request.method == "POST":
        try:
            carte = request.POST.get("carte", "").strip()
            adherent = Utilisateur.objects.filter(numero_carte=carte).first() if carte else None
            if carte and not adherent:
                raise ValueError("Carte d'adhérent introuvable.")
            lignes = {}
            for livre_id, qte in zip(request.POST.getlist("livre"), request.POST.getlist("quantite")):
                if livre_id:
                    lignes[int(livre_id)] = lignes.get(int(livre_id), 0) + max(1, int(qte or 1))
            client = {
                "prenom": request.POST.get("prenom", "").strip(), "nom": request.POST.get("nom", "").strip(),
                "email": request.POST.get("email", "").strip(), "telephone": request.POST.get("telephone", "").strip()
            }
            if not adherent and not (client["prenom"] and client["nom"] and client["telephone"]):
                raise ValueError("Pour un client sans carte, indiquez au minimum prénom, nom et téléphone.")
            a = services.achat_comptoir(adherent, list(lignes.items()), request.POST.get("mode_paiement"), client, request.POST.get("reference_paiement", ""))
            messages.success(request, f"Achat {a.reference} enregistré : {a.total:.0f} FCFA.")
            return redirect("achat_detail", pk=a.pk)
        except ValueError as err:
            messages.error(request, str(err))
    return render(request, "bibliotheque/achat_nouveau.html", {
        "livres": livres, "modes": Achat.MODES_PAIEMENT, "lignes_vides": range(4)})


@role_requis(*STAFF)
def achat_detail(request, pk):
    a = get_object_or_404(Achat.objects.select_related("adherent"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "annuler":
                services.annuler_achat(a)
                messages.info(request, "Achat annulé, le stock est remis en vente.")
            else:
                services.avancer_achat(a, action, request.POST.get("mode_paiement"), request.POST.get("reference_paiement", ""))
                messages.success(request, "Achat mis à jour.")
        except ValueError as err:
            messages.error(request, str(err))
        return redirect("achat_detail", pk=pk)
    return render(request, "bibliotheque/achat_detail.html", {
        "a": a, "lignes": a.lignes.select_related("livre"), "modes": Achat.MODES_PAIEMENT,
        "dette": services.montant_impaye(a.adherent) if a.adherent_id else 0})


# ---------- Sanctions ----------
@role_requis(*STAFF)
def sanctions(request):
    services.maj_quotidienne()
    statut = request.GET.get("statut", "impayee")
    liste = Emprunt.objects.exclude(statut_paiement="").select_related("adherent", "exemplaire__livre")
    if statut != "toutes":
        liste = liste.filter(statut_paiement=statut)
    total = liste.filter(statut_paiement="impayee").aggregate(t=Sum("montant_sanction"))["t"] or 0
    return render(request, "bibliotheque/sanctions.html", {
        "sanctions": liste.order_by("-date_sanction")[:200], "statut": statut, "total": total})


@role_requis(*STAFF)
def sanction_action(request, pk, action):
    r = _post_only(request, "sanctions")
    if r:
        return r
    e = get_object_or_404(Emprunt, pk=pk)
    try:
        if action == "encaisser":
            services.encaisser_sanction(e)
            messages.success(request, "Paiement enregistré.")
        elif action == "annuler":
            if not request.user.est_admin:
                raise ValueError("Seul un administrateur peut annuler une sanction.")
            services.annuler_sanction(e)
            messages.info(request, "Sanction annulée.")
    except ValueError as err:
        messages.error(request, str(err))
    return redirection_sure(request, "sanctions")


# ---------- Adhérents ----------
@role_requis(*STAFF)
def adherents(request):
    q, filtre = request.GET.get("q", "").strip(), request.GET.get("filtre", "")
    today = timezone.localdate()
    liste = Utilisateur.objects.filter(numero_carte__isnull=False)
    if q:
        liste = liste.filter(Q(nom__icontains=q) | Q(prenom__icontains=q) | Q(numero_carte__icontains=q)
                             | Q(telephone__icontains=q))
    if filtre == "a_valider":
        liste = liste.filter(statut="en_attente")
    elif filtre == "expires":
        liste = liste.filter(date_expiration__lt=today)
    elif filtre == "suspendus":
        liste = liste.filter(suspendu_jusqu__gte=today)
    elif filtre == "dettes":
        liste = liste.filter(emprunts__statut_paiement="impayee").distinct()
    liste = liste.annotate(nb_emprunts=Count("emprunts", filter=Q(emprunts__statut__in=Emprunt.OUVERTS), distinct=True)
                           ).order_by("nom", "prenom")
    return render(request, "bibliotheque/adherents.html", {"adherents": liste[:200], "q": q, "filtre": filtre, "today": today})


@role_requis(*STAFF)
def adherent_nouveau(request):
    form = AdherentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d, p, today = form.cleaned_data, services.params(), timezone.localdate()
        a = Utilisateur.objects.create_user(
            username=d["username"], password=d["mot_de_passe"], email=d["email"], nom=d["nom"], prenom=d["prenom"],
            telephone=d["telephone"], adresse=d["adresse"], role="adherent", statut="actif",
            numero_carte=services.generer_numero_carte(),
            date_expiration=d["date_expiration"] or today + timedelta(days=p.duree_adhesion_jours))
        messages.success(request, f"Adhérent créé. Carte n° {a.numero_carte}.")
        return redirect("adherent_detail", pk=a.pk)
    return render(request, "bibliotheque/form_page.html", {
        "form": form, "titre": "Nouvel adhérent", "sous_titre": "Inscription au comptoir : la carte est créée automatiquement.",
        "retour": "adherents", "bouton": "Créer l'adhérent"})


@role_requis(*STAFF)
def adherent_modifier(request, pk):
    a = get_object_or_404(Utilisateur, pk=pk, numero_carte__isnull=False)
    form = AdherentForm(request.POST or None, instance=a)
    if request.method == "POST" and form.is_valid():
        for champ in ("prenom", "nom", "email", "telephone", "adresse", "date_expiration"):
            setattr(a, champ, form.cleaned_data[champ])
        a.save()
        messages.success(request, "Adhérent modifié.")
        return redirect("adherent_detail", pk=a.pk)
    return render(request, "bibliotheque/form_page.html", {
        "form": form, "titre": f"Modifier {a}", "retour": "adherents", "bouton": "Enregistrer"})


@role_requis(*STAFF)
def adherent_detail(request, pk):
    a = get_object_or_404(Utilisateur, pk=pk, numero_carte__isnull=False)
    today, p = timezone.localdate(), services.params()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "valider":
            a.statut = "actif"
            if not a.date_expiration or a.date_expiration < today:
                a.date_expiration = today + timedelta(days=p.duree_adhesion_jours)
            messages.success(request, "Adhésion validée.")
        elif action == "renouveler":
            a.date_expiration = max(today, a.date_expiration or today) + timedelta(days=p.duree_adhesion_jours)
            messages.success(request, f"Adhésion renouvelée jusqu'au {services.fmt(a.date_expiration)}.")
        elif action == "lever_suspension":
            a.suspendu_jusqu, a.statut = None, "actif"
            messages.success(request, "Suspension levée.")
        elif action == "desactiver":
            a.is_active = not a.is_active
            messages.info(request, "Compte activé." if a.is_active else "Compte désactivé.")
        a.save()
        return redirect("adherent_detail", pk=pk)
    emprunts_ = list(a.emprunts.exclude(statut="annule").select_related("exemplaire__livre")[:30])
    for e in emprunts_:
        e.amende = services.montant_amende(e.jours_retard) if (e.statut in Emprunt.OUVERTS and e.jours_retard) else 0
    return render(request, "bibliotheque/adherent_detail.html", {
        "a": a, "emprunts": emprunts_, "sanctions": a.emprunts.exclude(statut_paiement="").select_related("exemplaire__livre"),
        "achats": a.achats.all()[:10], "dette": services.montant_impaye(a), "today": today})


# ---------- Livres & exemplaires ----------
def _ctx_livre_form(form, titre):
    return {"form": form, "titre": titre, "retour": "livres_gestion", "bouton": "Enregistrer",
            "auteurs": Livre.objects.order_by("auteur").values_list("auteur", flat=True).distinct(),
            "categories": Livre.objects.exclude(categorie="").order_by("categorie").values_list("categorie", flat=True).distinct()}


def _ajouter_exemplaires(livre, n, etagere="", code_rayon=""):
    i = livre.exemplaires.count()
    crees = 0
    while crees < n:
        i += 1
        code = f"EX-{livre.pk:04d}-{i:02d}"
        if not Exemplaire.objects.filter(code_barres=code).exists():
            Exemplaire.objects.create(livre=livre, code_barres=code, etagere=etagere, code_rayon=code_rayon)
            crees += 1


@role_requis(*STAFF)
def livres_gestion(request):
    q = request.GET.get("q", "").strip()
    liste = Livre.objects.annotate(nb_ex=Count("exemplaires", distinct=True),
                                   nb_dispo=Count("exemplaires", filter=Q(exemplaires__statut="disponible"), distinct=True))
    if q:
        liste = liste.filter(Q(titre__icontains=q) | Q(auteur__icontains=q))
    return render(request, "bibliotheque/livres_gestion.html", {"livres": liste.order_by("titre")[:200], "q": q})


@role_requis(*STAFF)
def livre_nouveau(request):
    form = LivreForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            livre = Livre.objects.create(titre=d["titre"].strip(), auteur=d["auteur"].strip(), isbn=d["isbn"],
                                         description=d["description"].strip(), image=d["image"].strip(), categorie=d["categorie"].strip(),
                                         annee_publication=d["annee_publication"], langue=d["langue"], prix=d["prix"],
                                         stock_vente=d["stock_vente"], actif=d["actif"])
            _ajouter_exemplaires(livre, d["nb_exemplaires"], d["etagere"], d["code_rayon"])
        messages.success(request, f"« {livre.titre} » ajouté au catalogue.")
        return redirect("livre_exemplaires", pk=livre.pk)
    return render(request, "bibliotheque/form_page.html", _ctx_livre_form(form, "Nouveau livre"))


@role_requis(*STAFF)
def livre_modifier(request, pk):
    livre = get_object_or_404(Livre, pk=pk)
    form = LivreForm(request.POST or None, instance=livre)
    if request.method == "POST" and form.is_valid():
        for champ, val in form.cleaned_data.items():
            setattr(livre, champ, val.strip() if isinstance(val, str) else val)
        livre.save()
        messages.success(request, "Livre modifié.")
        return redirect("livre_exemplaires", pk=pk)
    return render(request, "bibliotheque/form_page.html", _ctx_livre_form(form, f"Modifier « {livre.titre} »"))


@role_requis(*STAFF)
def livre_exemplaires(request, pk):
    livre = get_object_or_404(Livre, pk=pk)
    if request.method == "POST":
        if request.POST.get("action") == "ajouter":
            n = max(1, min(int(request.POST.get("nombre") or 1), 50))
            _ajouter_exemplaires(livre, n, request.POST.get("etagere", ""), request.POST.get("code_rayon", ""))
            messages.success(request, f"{n} exemplaire(s) ajouté(s).")
        elif request.POST.get("action") == "modifier":
            ex = get_object_or_404(Exemplaire, pk=request.POST.get("ex"), livre=livre)
            if request.POST.get("etat_physique") in dict(Exemplaire.ETATS):
                ex.etat_physique = request.POST["etat_physique"]
            ex.etagere, ex.code_rayon = request.POST.get("etagere", "").strip(), request.POST.get("code_rayon", "").strip()
            nouveau = request.POST.get("statut")
            libres = ("disponible", "perdu", "retire")
            if nouveau in libres and ex.statut in libres:
                ex.statut = nouveau
            ex.save()
            messages.success(request, f"Exemplaire {ex.code_barres} mis à jour.")
        return redirect("livre_exemplaires", pk=pk)
    exs = list(livre.exemplaires.order_by("code_barres"))
    en_cours = {e.exemplaire_id: e for e in Emprunt.objects.filter(exemplaire__livre=livre, statut__in=Emprunt.ACTIFS)
                .select_related("adherent")}
    for ex in exs:
        ex.emprunt_actif = en_cours.get(ex.pk)
        ex.statut_modifiable = ex.statut in ("disponible", "perdu", "retire")
    return render(request, "bibliotheque/livre_exemplaires.html", {
        "livre": livre, "exemplaires": exs, "etats": Exemplaire.ETATS,
        "statuts_libres": [s for s in Exemplaire.STATUTS if s[0] in ("disponible", "perdu", "retire")]})
