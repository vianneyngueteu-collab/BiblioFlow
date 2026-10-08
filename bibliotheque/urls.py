from django.urls import path

from . import views, views_public as pub

urlpatterns = [
    # ----- Site public & espace adhérent -----
    path("", pub.accueil, name="accueil"),
    path("catalogue/", pub.catalogue, name="catalogue"),
    path("livres/<int:pk>/", pub.livre_detail, name="livre_detail"),
    path("inscription/", pub.inscription, name="inscription"),
    path("panier/", pub.panier, name="panier"),
    path("panier/<str:action>/", pub.panier_action, name="panier_action"),
    path("commander/", pub.commander, name="commander"),
    path("commande/confirmation/<int:pk>/", pub.confirmation_achat, name="confirmation_achat"),
    path("suivi-commande/", pub.suivi_commande, name="suivi_commande"),
    path("mon-compte/", pub.mon_compte, name="mon_compte"),
    path("mes-commandes/", pub.mes_commandes, name="mes_commandes"),
    path("mes-commandes/demande/<int:pk>/annuler/", pub.demande_annuler, name="demande_annuler"),
    path("mes-commandes/achat/<int:pk>/annuler/", pub.achat_annuler, name="achat_annuler"),
    path("mes-emprunts/<int:pk>/prolonger/", pub.emprunt_prolonger, name="emprunt_prolonger"),
    path("apres-connexion/", views.apres_connexion, name="apres_connexion"),

    # ----- Gestion (personnel) -----
    path("gestion/", views.tableau_bord, name="tableau_bord"),
    path("gestion/commandes/", views.commandes, name="commandes"),
    path("gestion/emprunts/", views.emprunts, name="emprunts"),
    path("gestion/emprunts/<int:pk>/<str:action>/", views.emprunt_action, name="emprunt_action"),
    path("gestion/achats/", views.achats, name="achats"),
    path("gestion/achats/nouveau/", views.achat_nouveau, name="achat_nouveau"),
    path("gestion/achats/<int:pk>/", views.achat_detail, name="achat_detail"),
    path("gestion/sanctions/", views.sanctions, name="sanctions"),
    path("gestion/sanctions/<int:pk>/<str:action>/", views.sanction_action, name="sanction_action"),
    path("gestion/adherents/", views.adherents, name="adherents"),
    path("gestion/adherents/nouveau/", views.adherent_nouveau, name="adherent_nouveau"),
    path("gestion/adherents/<int:pk>/", views.adherent_detail, name="adherent_detail"),
    path("gestion/adherents/<int:pk>/modifier/", views.adherent_modifier, name="adherent_modifier"),
    path("gestion/livres/", views.livres_gestion, name="livres_gestion"),
    path("gestion/livres/nouveau/", views.livre_nouveau, name="livre_nouveau"),
    path("gestion/livres/<int:pk>/", views.livre_exemplaires, name="livre_exemplaires"),
    path("gestion/livres/<int:pk>/modifier/", views.livre_modifier, name="livre_modifier"),
]
