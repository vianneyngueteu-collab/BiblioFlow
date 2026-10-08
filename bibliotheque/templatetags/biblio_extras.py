from django import template

register = template.Library()


@register.filter
def teinte(livre):
    """Couleur de couverture (0 à 5), identique pour tous les livres d'une même catégorie."""
    return sum(ord(c) for c in (livre.categorie or "")) % 6


@register.filter
def dos(livre):
    """Hauteur et largeur du dos d'un livre sur l'étagère, stables d'un chargement à l'autre."""
    return f"height:{168 + (livre.pk * 37) % 56}px;width:{46 + (livre.pk * 11) % 18}px"


@register.filter
def couverture(livre):
    """Retourne l'image déclarée sur le livre, avec une couverture locale de secours."""
    image = getattr(livre, "image", "") or f"cover-{((livre.pk - 1) % 8) + 1}.svg"
    if image.startswith("/"):
        return image
    return f"/static/bibliotheque/images/covers/{image}"
