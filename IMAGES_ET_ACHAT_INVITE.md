# BiblioFlow — images et achat sans adhésion

## Images
- Couvertures SVG locales dans `bibliotheque/static/bibliotheque/images/covers/`.
- Illustration principale dans `bibliotheque/static/bibliotheque/images/library-hero.svg`.
- Aucun champ `image` n’a été ajouté au modèle `Livre`.

## Achat sans inscription
Un visiteur peut acheter des livres sans créer de compte. Il renseigne prénom, nom, téléphone, e-mail facultatif et choisit retrait ou livraison.

La base actuelle impose `Achat.adherent` comme clé étrangère obligatoire vers `Utilisateur`. Pour respecter cette contrainte sans modifier les entités, l’application crée un profil technique sans numéro de carte et avec mot de passe inutilisable. Ce profil n’est pas affiché dans la liste des adhérents et il ne permet pas d’emprunter.

Les emprunts restent réservés aux véritables adhérents disposant d’une carte.

## Modèles
`bibliotheque/models.py` et les migrations existantes sont conservés sans modification.
