# Modèle de données de BiblioFlow (6 entités)

## Entités et attributs

| Entité | Attributs |
|---|---|
| **UTILISATEUR** | id, nom, prenom, email, telephone, mot de passe (`password`, chiffré), role, statut, date d'inscription (`date_joined`) <br>+ techniques : username (connexion), adresse, numero_carte, date_expiration, suspendu_jusqu |
| **LIVRE** | id, titre, auteur, annee_publication, langue, prix, stock_vente <br>+ categorie (filtres du catalogue) |
| **EXEMPLAIRE** | id, code_barres, etat_physique, statut, etagere, code_rayon, #livre |
| **EMPRUNT** | id, date_emprunt, date_retour_prevue, date_retour_effective, motif_sanction, montant_sanction, statut_paiement, date_sanction, #exemplaire, #adherent, #personnel <br>+ statut, adresse_livraison, nb_prolongations |
| **ACHAT** | id, date_achat, mode_paiement, canal, adresse_livraison, statut, #adherent |
| *LIGNE_ACHAT* (association) | #achat, #livre, quantite, prix_applique |

Valeurs : `role` = adherent, bibliothecaire, administrateur. `statut` d'un utilisateur = en_attente, actif, suspendu.
`statut` d'un exemplaire = disponible, emprunte, reserve (mis de côté), perdu, retire. `etat_physique` = bon, abime.
`statut` d'un emprunt = demande, prete, en_cours, retard, rendu, perdu, annule.
`canal` d'un achat = comptoir, en_ligne. `statut_paiement` de la sanction = impayee, payee, annulee (vide = aucune sanction).

## Relations (cardinalités)

- LIVRE (0,n) ── POSSEDE ── (1,1) EXEMPLAIRE
- EXEMPLAIRE (0,n) ── CONCERNE ── (1,1) EMPRUNT
- UTILISATEUR adhérent (0,n) ── EFFECTUE ── (1,1) EMPRUNT
- UTILISATEUR personnel (0,n) ── ACCEPTE ── (0,1) EMPRUNT (vide tant qu'une demande en ligne n'est pas acceptée)
- UTILISATEUR adhérent (0,n) ── PASSE ── (0,1) ACHAT ; un ACHAT peut aussi être invité
- ACHAT (1,n) ── CONTIENT ── (0,n) LIVRE, avec quantite et prix_applique

## Modèle logique

```
UTILISATEUR (id, username, password, nom, prenom, email, telephone, adresse, role, statut,
             numero_carte, date_joined, date_expiration, suspendu_jusqu)
LIVRE (id, titre, auteur, categorie, annee_publication, langue, prix, stock_vente)
EXEMPLAIRE (id, code_barres, etat_physique, statut, etagere, code_rayon, #id_livre)
EMPRUNT (id, date_emprunt, date_retour_prevue, date_retour_effective, statut, adresse_livraison, nb_prolongations,
         motif_sanction, montant_sanction, statut_paiement, date_sanction,
         #id_exemplaire, #id_adherent, #id_personnel)
ACHAT (id, date_achat, canal, mode_paiement, adresse_livraison, statut, #id_adherent)
LIGNE_ACHAT (#id_achat, #id_livre, quantite, prix_applique)      clé primaire : (id_achat, id_livre)
```

## Comment les anciennes fonctions sont rangées

| Besoin | Où c'est dans le modèle |
|---|---|
| Rôles (adhérent / bibliothécaire / admin) | `Utilisateur.role` |
| Sanction (retard, perte, dégâts) | attributs de `Emprunt` (une sanction par emprunt) |
| Rayon | `Exemplaire.etagere` et `Exemplaire.code_rayon` |
| Commande de livres à emprunter | un `Emprunt` au statut `demande`, accepté par le personnel (`personnel`) |
| Commande de livres à acheter | un `Achat` avec `canal = en_ligne` |
| Livraison à domicile | `adresse_livraison` renseignée (vide = retrait à la bibliothèque) |
| Livre mis de côté pour une commande | `Exemplaire.statut = reserve` |

## Ce qui a disparu par rapport à la version précédente du site

Caisse (sessions, mouvements), factures, paiements multiples, réservations avec file d'attente, notifications,
types d'adhérents, tables Auteur et Catégorie, ISBN et éditeur, frais de livraison, sanctions saisies hors
d'un emprunt, page « Paramètres » (les règles sont maintenant dans `biblio_site/settings.py`, variable `BIBLIO`).


## Évolution du modèle
Le modèle reste volontairement limité à 6 entités : Utilisateur, Livre, Exemplaire, Emprunt, Achat et LigneAchat. Aucun modèle supplémentaire n'est nécessaire pour gérer les achats invités : Achat accepte désormais un adhérent nul et conserve les coordonnées du client.
