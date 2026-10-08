# BiblioFlow : bibliothèque physique + site de commande

## Démarrer
```
pip install -r requirements.txt
python manage.py runserver
```
(La base `db.sqlite3` fournie est déjà prête. Pour repartir de zéro : supprimer `db.sqlite3`, puis
`python manage.py migrate` et `python manage.py seed_demo`.)

- Site public et adhérents : http://127.0.0.1:8000/
- Gestion (personnel)      : http://127.0.0.1:8000/gestion/
- Comptes de démo (mot de passe `Bibliotheque2026!`) : `admin`, `bibliothecaire`, `etudiant`, `lecteur2`
  (`lecteur2` a un livre en retard donc une sanction automatique ; `etudiant` a un prêt en cours)
- Tests : `python manage.py test bibliotheque`

## Modèle de données
6 entités principales : Utilisateur, Livre, Exemplaire, Emprunt, Achat et LigneAchat.
Détails, cardinalités et modèle logique dans **MODELE_DONNEES.md**.

## Règles de la bibliothèque
Elles se changent dans `biblio_site/settings.py`, variable `BIBLIO` : prêt de 14 jours, 3 livres maximum,
1 prolongation de 7 jours, sanction de 100 FCFA par jour de retard (plafond 5 000), suspension d'1 jour par jour
de retard, livre perdu = prix + 500 FCFA, commande prête gardée 3 jours, adhésion de 365 jours.

## Parcours
**Adhérent** : catalogue, fiche livre, panier (emprunt et/ou achat), commande (retrait ou livraison), suivi dans
« Mon compte » et « Mes commandes » (prolonger, annuler, voir ses sanctions).

**Personnel** : Commandes en ligne (préparer, remettre ou livrer) · Prêts et retours · Sanctions · Adhérents ·
Livres et exemplaires (avec rayon) · Achats (dont vente au comptoir).

## Mise à jour quotidienne
Retards, sanctions, fins de suspension et commandes non retirées se mettent à jour automatiquement quand
quelqu'un ouvre une page de gestion ou son compte. Pour le faire aussi sans visite, planifier chaque nuit :
`python -c "import os,django;os.environ['DJANGO_SETTINGS_MODULE']='biblio_site.settings';django.setup();from bibliotheque import services;services.maj_quotidienne()"`


## Mise à jour métier

- Un achat en ligne peut être effectué sans compte ni adhésion. Les coordonnées du client sont enregistrées directement dans `Achat`.
- Un achat au comptoir peut également être enregistré pour un client de passage.
- `Livre` possède désormais ISBN, description, image locale et statut actif.
- `Emprunt` distingue la date de demande et la limite de retrait.
- Les achats possèdent une référence, un mode de réception et un suivi du paiement.
- Le projet reste à 6 entités principales (Utilisateur, Livre, Exemplaire, Emprunt, Achat, LigneAchat), donc sous la limite de 7 entités.

## Corrections métier – 08/10/2026

- Les achats en ligne au retrait expirent après le délai configuré et restituent automatiquement le stock.
- Les commandes invitées sont limitées en nombre actif et en quantité par livre.
- Une référence `BF-YYYYMMDD-XXXXXXXX` est la référence unique affichée partout.
- Une page « Suivre une commande » permet de retrouver une commande depuis un autre navigateur avec la référence + e-mail/téléphone.
- Une annulation met le paiement à l’état « Annulé ».
- Les sanctions annulées peuvent être recréées si une nouvelle perte ou dégradation survient.
- Les achats du personnel connecté sont autorisés ; seuls les emprunts exigent une carte adhérent.
- `mode_reception` est la source de vérité pour les achats ; l’identité d’un adhérent est lue depuis son compte.
- Les livres masqués ne sont plus proposés dans le catalogue, les catégories ni les ventes en ligne.
- Les commandes expirées peuvent être nettoyées par `python manage.py maj_bibliotheque` et le nettoyage est aussi déclenché lors des principaux accès publics.
