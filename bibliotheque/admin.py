from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Achat, Emprunt, Exemplaire, LigneAchat, Livre, Utilisateur

admin.site.site_header = "BiblioFlow – Administration"
admin.site.site_title = "BiblioFlow"
admin.site.index_title = "Gestion de la bibliothèque"


@admin.register(Utilisateur)
class UtilisateurAdmin(UserAdmin):
    list_display = ("username", "prenom", "nom", "role", "numero_carte", "statut")
    list_filter = ("role", "statut")
    search_fields = ("username", "nom", "prenom", "numero_carte", "telephone")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Identité", {"fields": ("nom", "prenom", "email", "telephone", "adresse")}),
        ("Bibliothèque", {"fields": ("role", "statut", "numero_carte", "date_expiration", "suspendu_jusqu")}),
        ("Droits", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("username", "nom", "prenom", "email", "role", "password1", "password2")}),)


class LigneAchatInline(admin.TabularInline):
    model = LigneAchat
    extra = 0


@admin.register(Achat)
class AchatAdmin(admin.ModelAdmin):
    list_display = ("id", "adherent", "canal", "statut", "date_achat")
    list_filter = ("canal", "statut")
    inlines = [LigneAchatInline]


@admin.register(Livre)
class LivreAdmin(admin.ModelAdmin):
    list_display = ("titre", "auteur", "categorie", "prix", "stock_vente")
    search_fields = ("titre", "auteur")
    list_filter = ("categorie",)


admin.site.register(Exemplaire)
admin.site.register(Emprunt)
