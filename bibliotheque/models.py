"""Modèle métier BiblioFlow : 6 entités (Utilisateur, Livre, Exemplaire, Emprunt, Achat, LigneAchat)."""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import F, Sum
from django.utils import timezone
from uuid import uuid4


class Utilisateur(AbstractUser):
    """Adhérents ET personnel de la bibliothèque : le rôle les distingue."""
    ROLES = [("adherent", "Adhérent"), ("bibliothecaire", "Bibliothécaire"), ("administrateur", "Administrateur")]
    STATUTS = [("en_attente", "En attente de validation"), ("actif", "Actif"), ("suspendu", "Suspendu")]

    first_name = None  # remplacés par nom / prénom
    last_name = None
    nom = models.CharField(max_length=80)
    prenom = models.CharField(max_length=80)
    telephone = models.CharField(max_length=20, blank=True)
    adresse = models.CharField(max_length=200, blank=True)
    role = models.CharField(max_length=20, choices=ROLES, default="adherent")
    statut = models.CharField(max_length=12, choices=STATUTS, default="actif")
    numero_carte = models.CharField(max_length=20, unique=True, null=True, blank=True)
    date_expiration = models.DateField(null=True, blank=True)  # fin de validité de l'adhésion
    suspendu_jusqu = models.DateField(null=True, blank=True)   # emprunts suspendus après un retard

    REQUIRED_FIELDS = ["nom", "prenom", "email"]

    def get_full_name(self):
        return f"{self.prenom} {self.nom}".strip()

    def get_short_name(self):
        return self.prenom

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def est_personnel(self):
        return self.is_superuser or self.role in ("bibliothecaire", "administrateur")

    @property
    def est_admin(self):
        return self.is_superuser or self.role == "administrateur"

    @property
    def est_adherent(self):
        return bool(self.numero_carte)


class Livre(models.Model):
    titre = models.CharField(max_length=200)
    auteur = models.CharField(max_length=120)
    isbn = models.CharField(max_length=20, unique=True, null=True, blank=True)
    description = models.TextField(blank=True)
    image = models.CharField(max_length=255, blank=True)  # chemin relatif dans static/media
    categorie = models.CharField(max_length=80, blank=True)
    annee_publication = models.PositiveSmallIntegerField(null=True, blank=True)
    langue = models.CharField(max_length=30, default="Français")
    prix = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # prix de vente
    stock_vente = models.PositiveIntegerField(default=0)                    # exemplaires à vendre
    actif = models.BooleanField(default=True)

    class Meta:
        ordering = ["titre"]

    def __str__(self):
        return self.titre


class Exemplaire(models.Model):
    """Exemplaire physique d'un livre, avec son emplacement en rayon."""
    ETATS = [("bon", "Bon état"), ("abime", "Abîmé")]
    STATUTS = [("disponible", "En rayon"), ("emprunte", "Emprunté"), ("reserve", "Mis de côté"),
               ("perdu", "Perdu"), ("retire", "Retiré du fonds")]
    livre = models.ForeignKey(Livre, on_delete=models.CASCADE, related_name="exemplaires")
    code_barres = models.CharField(max_length=40, unique=True)
    etat_physique = models.CharField(max_length=10, choices=ETATS, default="bon")
    statut = models.CharField(max_length=12, choices=STATUTS, default="disponible")
    etagere = models.CharField(max_length=40, blank=True)
    code_rayon = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.code_barres

    @property
    def emplacement(self):
        return " / ".join(x for x in (self.etagere, self.code_rayon) if x)


class Emprunt(models.Model):
    """Un prêt (ou une demande de prêt en ligne) et, le cas échéant, la sanction qui en découle."""
    STATUTS = [("demande", "Demandé"), ("prete", "Prêt à retirer"), ("en_cours", "En cours"), ("retard", "En retard"),
               ("rendu", "Rendu"), ("perdu", "Perdu"), ("annule", "Annulé")]
    ACTIFS = ("demande", "prete", "en_cours", "retard")   # compte dans la limite d'emprunts
    OUVERTS = ("en_cours", "retard")                      # livre actuellement chez l'adhérent
    PAIEMENTS = [("impayee", "Impayée"), ("payee", "Payée"), ("annulee", "Annulée")]

    adherent = models.ForeignKey(Utilisateur, on_delete=models.PROTECT, related_name="emprunts")
    personnel = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="emprunts_acceptes")
    exemplaire = models.ForeignKey(Exemplaire, on_delete=models.PROTECT, related_name="emprunts")
    date_demande = models.DateTimeField(auto_now_add=True)
    date_emprunt = models.DateField(default=timezone.localdate)
    date_retour_prevue = models.DateField(null=True, blank=True)
    date_limite_retrait = models.DateField(null=True, blank=True)
    date_retour_effective = models.DateField(null=True, blank=True)
    statut = models.CharField(max_length=10, choices=STATUTS, default="en_cours")
    adresse_livraison = models.CharField(max_length=250, blank=True)  # vide = retrait à la bibliothèque
    nb_prolongations = models.PositiveSmallIntegerField(default=0)
    # --- sanction (une par emprunt) ---
    motif_sanction = models.CharField(max_length=200, blank=True)
    montant_sanction = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    statut_paiement = models.CharField(max_length=10, choices=PAIEMENTS, blank=True)
    date_sanction = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_emprunt", "-id"]

    @property
    def jours_retard(self):
        if not self.date_retour_prevue:
            return 0
        ref = self.date_retour_effective or timezone.localdate()
        return max(0, (ref - self.date_retour_prevue).days)

    @property
    def en_retard(self):
        return (self.statut in self.OUVERTS and self.date_retour_prevue is not None
                and self.date_retour_prevue < timezone.localdate())

    @property
    def jours_restants(self):
        return (self.date_retour_prevue - timezone.localdate()).days if self.date_retour_prevue else None

    @property
    def livraison(self):
        return bool(self.adresse_livraison)

    def __str__(self):
        return f"Emprunt #{self.pk}"


class Achat(models.Model):
    """Achat de livres : au comptoir ou commandé en ligne (retrait ou livraison)."""
    STATUTS = [("en_attente", "En attente de préparation"), ("prete", "Prête"), ("en_livraison", "En livraison"),
               ("terminee", "Terminé"), ("annulee", "Annulé")]
    ACTIFS = ("en_attente", "prete", "en_livraison")
    CANAUX = [("comptoir", "Au comptoir"), ("en_ligne", "En ligne")]
    MODES_PAIEMENT = [("especes", "Espèces"), ("mobile", "Mobile money"), ("carte", "Carte")]

    adherent = models.ForeignKey(Utilisateur, on_delete=models.PROTECT, null=True, blank=True, related_name="achats")
    reference = models.CharField(max_length=24, unique=True, null=True, blank=True)
    date_achat = models.DateTimeField(auto_now_add=True)
    canal = models.CharField(max_length=10, choices=CANAUX, default="comptoir")
    mode_reception = models.CharField(max_length=12, choices=[("retrait", "Retrait"), ("livraison", "Livraison")], default="retrait")
    mode_paiement = models.CharField(max_length=10, choices=MODES_PAIEMENT, blank=True)
    statut_paiement = models.CharField(max_length=12, choices=[("en_attente", "À payer"), ("paye", "Payé"), ("annule", "Annulé")], default="en_attente")
    reference_paiement = models.CharField(max_length=80, blank=True)
    date_paiement = models.DateTimeField(null=True, blank=True)
    adresse_livraison = models.CharField(max_length=250, blank=True)  # vide = retrait à la bibliothèque
    date_limite_retrait = models.DateField(null=True, blank=True)
    client_prenom = models.CharField(max_length=80, blank=True)
    client_nom = models.CharField(max_length=80, blank=True)
    client_email = models.EmailField(blank=True)
    client_telephone = models.CharField(max_length=20, blank=True)
    statut = models.CharField(max_length=14, choices=STATUTS, default="en_attente")

    class Meta:
        ordering = ["-date_achat"]

    @property
    def active(self):
        return self.statut in self.ACTIFS

    @property
    def livraison(self):
        return self.mode_reception == "livraison"

    @property
    def total(self):
        return self.lignes.aggregate(t=Sum(F("quantite") * F("prix_applique")))["t"] or 0

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"BF-{timezone.now():%Y%m%d}-{uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Achat {self.reference or f'#{self.pk}'}"

    @property
    def client_nom_complet(self):
        if self.adherent_id:
            return self.adherent.get_full_name()
        return f"{self.client_prenom} {self.client_nom}".strip() or "Client invité"

    @property
    def client_telephone_effectif(self):
        return self.adherent.telephone if self.adherent_id else self.client_telephone

    @property
    def client_email_effectif(self):
        return self.adherent.email if self.adherent_id else self.client_email

    @property
    def client_prenom_effectif(self):
        return self.adherent.prenom if self.adherent_id else self.client_prenom

    @property
    def client_nom_effectif(self):
        return self.adherent.nom if self.adherent_id else self.client_nom

    @property
    def est_invite(self):
        return self.adherent_id is None


class LigneAchat(models.Model):
    """Association Achat / Livre (CONTIENT) : quantité et prix appliqué."""
    achat = models.ForeignKey(Achat, on_delete=models.CASCADE, related_name="lignes")
    livre = models.ForeignKey(Livre, on_delete=models.PROTECT)
    quantite = models.PositiveIntegerField(default=1)
    prix_applique = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["achat", "livre"], name="une_ligne_par_livre_et_achat")]

    @property
    def sous_total(self):
        return self.quantite * self.prix_applique
