"""Site public et espace adhérent : catalogue, panier, commande, inscription, compte."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import services
from .acces import lecteur_requis, redirection_sure
from .forms import AchatInviteForm, CommandeForm, InscriptionForm
from .models import Achat, Emprunt, Exemplaire, Livre, Utilisateur


def _livres_avec_dispo():
    return Livre.objects.filter(actif=True).annotate(
        dispo=Count("exemplaires", filter=Q(exemplaires__statut="disponible"), distinct=True))


def _categories():
    return (Livre.objects.filter(actif=True).exclude(categorie="").values("categorie").annotate(n=Count("id")).order_by("categorie"))


# ---------- Vitrine ----------
def accueil(request):
    services.maj_quotidienne()
    return render(request, "bibliotheque/public/accueil.html", {
        "nouveautes": _livres_avec_dispo().order_by("-id")[:8],
        "categories": _categories(),
        "nb_livres": Livre.objects.filter(actif=True).count(),
        "nb_dispo": Exemplaire.objects.filter(statut="disponible").count(),
    })


def catalogue(request):
    services.maj_quotidienne()
    q, cat, dispo = request.GET.get("q", "").strip(), request.GET.get("cat", ""), request.GET.get("dispo", "")
    livres = _livres_avec_dispo()
    if q:
        livres = livres.filter(Q(titre__icontains=q) | Q(auteur__icontains=q) | Q(categorie__icontains=q) | Q(isbn__icontains=q))
    if cat:
        livres = livres.filter(categorie=cat)
    if dispo == "pret":
        livres = livres.filter(dispo__gt=0)
    elif dispo == "achat":
        livres = livres.filter(stock_vente__gt=0)
    page = Paginator(livres.order_by("titre"), 12).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "bibliotheque/public/catalogue.html", {
        "page": page, "q": q, "cat": cat, "dispo": dispo, "categories": _categories(), "qs": params.urlencode()})


def livre_detail(request, pk):
    livre = get_object_or_404(_livres_avec_dispo(), pk=pk)
    emplacements = sorted({ex.emplacement for ex in livre.exemplaires.filter(statut="disponible") if ex.emplacement})
    ctx = {"livre": livre, "emplacements": emplacements,
           "similaires": _livres_avec_dispo().filter(categorie=livre.categorie).exclude(pk=pk)[:4] if livre.categorie else []}
    if request.user.is_authenticated and request.user.numero_carte:
        ctx["deja"] = services.deja_emprunte(request.user, livre)
    return render(request, "bibliotheque/public/livre.html", ctx)


# ---------- Inscription ----------
def inscription(request):
    if request.user.is_authenticated:
        return redirect("apres_connexion")
    form = InscriptionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d, p, today = form.cleaned_data, services.params(), timezone.localdate()
        valide = not p.validation_adhesion_requise
        u = Utilisateur.objects.create_user(
            username=d["username"], password=d["password1"], email=d["email"], nom=d["nom"], prenom=d["prenom"],
            telephone=d["telephone"], adresse=d["adresse"], role="adherent", numero_carte=services.generer_numero_carte(),
            statut="actif" if valide else "en_attente",
            date_expiration=today + timedelta(days=p.duree_adhesion_jours) if valide else None)
        login(request, u)
        if valide:
            messages.success(request, f"Bienvenue {u.prenom} ! Votre carte n° {u.numero_carte} est active.")
        else:
            messages.success(request, f"Compte créé, carte n° {u.numero_carte}. Passez à la bibliothèque pour valider "
                                      f"votre adhésion : vous pourrez alors emprunter.")
        return redirect("mon_compte")
    return render(request, "bibliotheque/public/inscription.html", {"form": form})


# ---------- Panier ----------
def _panier(request):
    return request.session.setdefault("panier", {})


def _lignes_panier(request):
    """Transforme le panier de session en lignes affichables."""
    lignes, total = [], 0
    for cle, qte in list(_panier(request).items()):
        livre_id, type_ = cle.split(":")
        livre = _livres_avec_dispo().filter(pk=livre_id).first()
        if not livre:
            _panier(request).pop(cle, None)
            request.session.modified = True
            continue
        sous = livre.prix * qte if type_ == "achat" else 0
        total += sous
        lignes.append({"cle": cle, "livre": livre, "type": type_, "quantite": qte, "sous_total": sous})
    return lignes, total


def panier(request):
    services.maj_quotidienne()
    lignes, total = _lignes_panier(request)
    user = request.user if request.user.is_authenticated else None
    return render(request, "bibliotheque/public/panier.html", {
        "lignes": lignes, "total": total, "nb_emprunts": sum(1 for l in lignes if l["type"] == "emprunt"),
        "reste_quota": (services.params().max_emprunts - services.nb_emprunts_actifs(user))
        if user and user.numero_carte else None})


def panier_action(request, action):
    if request.method != "POST":
        return redirect("panier")
    pan = _panier(request)
    if action == "ajouter":
        livre = get_object_or_404(Livre, pk=request.POST.get("livre"), actif=True)
        type_ = request.POST.get("type")
        if type_ not in ("emprunt", "achat"):
            return redirect("catalogue")
        cle = f"{livre.pk}:{type_}"
        if type_ == "emprunt":
            if not Exemplaire.objects.filter(livre=livre, statut="disponible").exists():
                messages.error(request, f"« {livre.titre} » n'est pas disponible à l'emprunt pour le moment.")
                return redirect("livre_detail", pk=livre.pk)
            pan[cle] = 1
        else:
            qte = max(1, int(request.POST.get("quantite") or 1))
            if pan.get(cle, 0) + qte > livre.stock_vente:
                messages.error(request, f"Stock insuffisant : {livre.stock_vente} exemplaire(s) en vente.")
                return redirect("livre_detail", pk=livre.pk)
            pan[cle] = pan.get(cle, 0) + qte
        request.session.modified = True
        messages.success(request, f"« {livre.titre} » ajouté au panier ({'emprunt' if type_ == 'emprunt' else 'achat'}).")
        return redirection_sure(request, "panier")
    if action == "retirer":
        pan.pop(request.POST.get("cle", ""), None)
    elif action == "vider":
        pan.clear()
    request.session.modified = True
    return redirect("panier")


def commander(request):
    services.maj_quotidienne()
    """Finalise un panier : emprunts réservés aux adhérents, achats ouverts aux visiteurs."""
    lignes, total = _lignes_panier(request)
    if not lignes:
        messages.info(request, "Votre panier est vide.")
        return redirect("catalogue")
    contient_emprunt = any(l["type"] == "emprunt" for l in lignes)
    if contient_emprunt and (not request.user.is_authenticated or not request.user.numero_carte):
        messages.info(request, "Connectez-vous avec un compte adhérent pour emprunter. Vous pouvez retirer l’emprunt du panier pour acheter les autres livres.")
        return redirect(f"{reverse('login')}?next={reverse('commander')}")

    if request.user.is_authenticated:
        user = request.user
        form = CommandeForm(request.POST or None, initial={"telephone": user.telephone, "adresse": user.adresse})
        avertissement = None
        try:
            if contient_emprunt:
                services.verifier_adherent(user)
        except ValueError as e:
            avertissement = str(e)
        if request.method == "POST" and form.is_valid():
            d = form.cleaned_data
            try:
                items = [(l["livre"].pk, l["type"], l["quantite"]) for l in lignes]
                emprunts, achat = services.passer_commande(user, items, d["mode"], d["adresse"])
            except ValueError as e:
                messages.error(request, str(e))
            else:
                if d["telephone"] != user.telephone:
                    user.telephone = d["telephone"]
                    user.save(update_fields=["telephone"])
                request.session["panier"] = {}
                messages.success(request, "Commande enregistrée ! Nous la préparons.")
                return redirect("mes_commandes")
        return render(request, "bibliotheque/public/commander.html", {"form": form, "lignes": lignes, "total": total, "avertissement": avertissement, "invite": False})

    # Visiteur : uniquement des achats, sans inscription.
    if contient_emprunt:
        messages.info(request, "Retirez l'emprunt du panier ou connectez-vous avec un compte adhérent.")
        return redirect("panier")
    form = AchatInviteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            achat = services.passer_achat_invite(
                d["prenom"], d["nom"], d["email"], d["telephone"],
                [(l["livre"].pk, l["quantite"]) for l in lignes], d["mode"], d["adresse"]
            )
        except ValueError as e:
            messages.error(request, str(e))
        else:
            request.session["panier"] = {}
            request.session["dernier_achat_invite"] = achat.pk
            return redirect("confirmation_achat", pk=achat.pk)
    return render(request, "bibliotheque/public/commander.html", {"form": form, "lignes": lignes, "total": total, "avertissement": None, "invite": True})


def confirmation_achat(request, pk):
    """Confirmation consultable après création ou via la référence + e-mail/téléphone."""
    achat = get_object_or_404(Achat.objects.prefetch_related("lignes__livre"), pk=pk)
    autorise = request.session.get("dernier_achat_invite") == pk
    if request.user.is_authenticated and achat.adherent_id == request.user.id:
        autorise = True
    if not autorise:
        reference = request.GET.get("reference", "").strip().upper()
        contact = request.GET.get("contact", "").strip()
        if reference == achat.reference and contact and contact.lower() in {achat.client_email_effectif.lower(), achat.client_telephone_effectif}:
            autorise = True
    if not autorise:
        messages.error(request, "Pour consulter cette commande, utilisez sa référence et l’e-mail ou le téléphone fourni lors de l’achat.")
        return redirect("suivi_commande")
    return render(request, "bibliotheque/public/confirmation_achat.html", {"achat": achat})

def suivi_commande(request):
    services.maj_quotidienne()
    achat = None
    if request.method == "POST":
        reference = request.POST.get("reference", "").strip().upper()
        contact = request.POST.get("contact", "").strip()
        achat = Achat.objects.prefetch_related("lignes__livre").filter(reference=reference).first()
        if not achat or not contact or contact.lower() not in {achat.client_email_effectif.lower(), achat.client_telephone_effectif}:
            messages.error(request, "Commande introuvable. Vérifiez la référence et le contact saisi lors de l’achat.")
            achat = None
    return render(request, "bibliotheque/public/suivi_commande.html", {"achat": achat})


# ---------- Espace adhérent ----------
@lecteur_requis
def mon_compte(request):
    services.maj_quotidienne()
    a = request.user
    emprunts = list(a.emprunts.filter(statut__in=Emprunt.ACTIFS).select_related("exemplaire__livre")
                    .order_by("date_retour_prevue", "id"))
    for e in emprunts:
        e.amende = services.montant_amende(e.jours_retard) if e.jours_retard else 0
    try:
        services.verifier_adherent(a)
        blocage = None
    except ValueError as e:
        blocage = str(e)
    return render(request, "bibliotheque/public/mon_compte.html", {
        "a": a, "emprunts": emprunts, "blocage": blocage,
        "sanctions": a.emprunts.filter(statut_paiement="impayee").select_related("exemplaire__livre"),
        "dette": services.montant_impaye(a),
        "historique": a.emprunts.filter(statut__in=("rendu", "perdu")).select_related("exemplaire__livre")
                       .order_by("-date_retour_effective")[:10],
        "commandes_actives": a.emprunts.filter(statut__in=("demande", "prete")).count()
                              + a.achats.filter(statut__in=Achat.ACTIFS).count()})


@login_required
def mes_commandes(request):
    a = request.user
    return render(request, "bibliotheque/public/mes_commandes.html", {
        "demandes": a.emprunts.filter(statut__in=("demande", "prete", "annule")).select_related("exemplaire__livre")
                     .order_by("-id")[:20],
        "achats": a.achats.filter(canal="en_ligne").prefetch_related("lignes__livre")[:30]})


@lecteur_requis
def demande_annuler(request, pk):
    if request.method == "POST":
        e = get_object_or_404(Emprunt, pk=pk, adherent=request.user)
        try:
            services.annuler_emprunt(e)
            messages.info(request, "Demande d'emprunt annulée.")
        except ValueError as err:
            messages.error(request, str(err))
    return redirect("mes_commandes")


@login_required
def achat_annuler(request, pk):
    if request.method == "POST":
        a = get_object_or_404(Achat, pk=pk, adherent=request.user)
        if a.statut in ("en_attente", "prete"):
            try:
                services.annuler_achat(a)
                messages.info(request, f"Commande #{a.pk} annulée.")
            except ValueError as err:
                messages.error(request, str(err))
        else:
            messages.error(request, "Cette commande est déjà en livraison ou terminée : contactez la bibliothèque.")
    return redirect("mes_commandes")


@lecteur_requis
def emprunt_prolonger(request, pk):
    if request.method == "POST":
        e = get_object_or_404(Emprunt, pk=pk, adherent=request.user)
        try:
            services.prolonger(e)
            messages.success(request, f"Prêt prolongé : à rendre avant le {services.fmt(e.date_retour_prevue)}.")
        except ValueError as err:
            messages.error(request, str(err))
    return redirect("mon_compte")
