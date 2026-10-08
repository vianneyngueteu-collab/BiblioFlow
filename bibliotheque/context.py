from .acces import est_personnel
from .services import params


def globales(request):
    ctx = {"p": params(), "panier_count": 0, "est_personnel": False}
    try:
        ctx["panier_count"] = sum(request.session.get("panier", {}).values())
    except Exception:
        pass
    if request.user.is_authenticated:
        ctx["est_personnel"] = est_personnel(request.user)
    return ctx
