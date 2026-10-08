"""Contrôle d'accès : rôles du personnel et espace adhérent."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

STAFF = ("administrateur", "bibliothecaire")


def get_profil(request):
    return request.user if request.user.is_authenticated else None


def est_personnel(user):
    return user.is_authenticated and user.est_personnel


def role_requis(*roles):
    def deco(vue):
        @login_required
        @wraps(vue)
        def wrapper(request, *a, **kw):
            if request.user.is_superuser or request.user.role in roles:
                return vue(request, *a, **kw)
            messages.error(request, "Accès non autorisé pour votre rôle.")
            return redirect("accueil")
        return wrapper
    return deco


def lecteur_requis(vue):
    """Réservé aux comptes disposant d'une carte d'adhérent."""
    @login_required
    @wraps(vue)
    def wrapper(request, *a, **kw):
        if request.user.numero_carte:
            return vue(request, *a, **kw)
        messages.info(request, "Ce compte n'est pas un compte adhérent.")
        return redirect("tableau_bord" if est_personnel(request.user) else "accueil")
    return wrapper


def redirection_sure(request, defaut):
    """Redirige vers ?next / champ 'next' seulement s'il pointe vers ce site."""
    cible = request.POST.get("next") or request.GET.get("next")
    if cible and url_has_allowed_host_and_scheme(cible, allowed_hosts={request.get_host()}):
        return redirect(cible)
    return redirect(defaut)
