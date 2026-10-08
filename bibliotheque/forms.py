from datetime import date

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import Achat, Emprunt, Livre, Utilisateur


class BootstrapMixin:
    def _style(self):
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, (forms.CheckboxInput, forms.RadioSelect)):
                w.attrs["class"] = "form-check-input"
            elif isinstance(w, forms.Select):
                w.attrs["class"] = "form-select"
            else:
                w.attrs["class"] = "form-control"


class BForm(BootstrapMixin, forms.Form):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._style()


class AdherentForm(BForm):
    """Création / modification d'un adhérent par le personnel."""
    prenom = forms.CharField(max_length=80)
    nom = forms.CharField(max_length=80)
    email = forms.EmailField(required=False)
    telephone = forms.CharField(max_length=20, required=False)
    adresse = forms.CharField(max_length=200, required=False)
    date_expiration = forms.DateField(required=False, label="Adhésion valable jusqu'au",
                                      widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    username = forms.CharField(max_length=150, label="Nom d'utilisateur (connexion)")
    mot_de_passe = forms.CharField(widget=forms.PasswordInput, label="Mot de passe provisoire")

    def __init__(self, *a, instance=None, **kw):
        self.instance = instance
        if instance is not None:
            kw.setdefault("initial", {
                "prenom": instance.prenom, "nom": instance.nom, "email": instance.email,
                "telephone": instance.telephone, "adresse": instance.adresse, "date_expiration": instance.date_expiration})
        super().__init__(*a, **kw)
        if instance is not None:
            del self.fields["username"], self.fields["mot_de_passe"]

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        if Utilisateur.objects.filter(username__iexact=u).exists():
            raise ValidationError("Ce nom d'utilisateur existe déjà.")
        return u

    def clean_mot_de_passe(self):
        validate_password(self.cleaned_data["mot_de_passe"])
        return self.cleaned_data["mot_de_passe"]


class InscriptionForm(BForm):
    """Inscription en ligne d'un nouvel adhérent."""
    prenom = forms.CharField(max_length=80)
    nom = forms.CharField(max_length=80)
    email = forms.EmailField(required=False)
    telephone = forms.CharField(max_length=20)
    adresse = forms.CharField(max_length=200, required=False)
    username = forms.CharField(max_length=150, label="Nom d'utilisateur")
    password1 = forms.CharField(widget=forms.PasswordInput, label="Mot de passe")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirmer le mot de passe")

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        if Utilisateur.objects.filter(username__iexact=u).exists():
            raise ValidationError("Ce nom d'utilisateur est déjà pris.")
        return u

    def clean(self):
        d = super().clean()
        if d.get("password1") and d.get("password1") != d.get("password2"):
            self.add_error("password2", "Les deux mots de passe ne correspondent pas.")
        elif d.get("password1"):
            try:
                validate_password(d["password1"])
            except ValidationError as e:
                self.add_error("password1", e)
        return d


class LivreForm(BForm):
    titre = forms.CharField(max_length=200)
    auteur = forms.CharField(max_length=120)
    isbn = forms.CharField(max_length=20, required=False, label="ISBN")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}), label="Description")
    image = forms.CharField(max_length=255, required=False, label="Image / couverture (chemin local)")
    categorie = forms.CharField(max_length=80, required=False, label="Catégorie")
    annee_publication = forms.IntegerField(required=False, min_value=1000, max_value=date.today().year + 1,
                                           label="Année de publication")
    langue = forms.CharField(max_length=30, initial="Français")
    prix = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10, initial=0, label="Prix de vente (FCFA)")
    stock_vente = forms.IntegerField(min_value=0, initial=0, label="Stock à vendre")
    actif = forms.BooleanField(required=False, initial=True, label="Livre visible au catalogue")
    nb_exemplaires = forms.IntegerField(min_value=0, initial=1, label="Exemplaires à prêter (à créer)")
    etagere = forms.CharField(max_length=40, required=False, label="Étagère des nouveaux exemplaires")
    code_rayon = forms.CharField(max_length=20, required=False, label="Code du rayon")

    def clean_isbn(self):
        value = self.cleaned_data.get("isbn", "").strip()
        if not value:
            return None
        qs = Livre.objects.filter(isbn__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Cet ISBN est déjà utilisé par un autre livre.")
        return value

    def __init__(self, *a, instance=None, **kw):
        self.instance = instance
        if instance is not None:
            kw.setdefault("initial", {f: getattr(instance, f) for f in (
                "titre", "auteur", "isbn", "description", "image", "categorie", "annee_publication", "langue", "prix", "stock_vente", "actif")})
        super().__init__(*a, **kw)
        self.fields["categorie"].widget.attrs["list"] = "categories"
        self.fields["auteur"].widget.attrs["list"] = "auteurs"
        if instance is not None:
            for champ in ("nb_exemplaires", "etagere", "code_rayon"):
                del self.fields[champ]


class CommandeForm(BForm):
    mode = forms.ChoiceField(choices=[("retrait", "Retrait à la bibliothèque"), ("livraison", "Livraison à domicile")],
                             widget=forms.RadioSelect, initial="retrait",
                             label="Comment souhaitez-vous recevoir vos livres ?")
    adresse = forms.CharField(max_length=250, required=False, label="Adresse de livraison (quartier, repère…)")
    telephone = forms.CharField(max_length=20, label="Téléphone de contact")

    def clean(self):
        d = super().clean()
        if d.get("mode") == "livraison" and not d.get("adresse", "").strip():
            self.add_error("adresse", "L'adresse est obligatoire pour une livraison.")
        return d


class AchatInviteForm(BForm):
    """Commande d'achat sans création de compte adhérent."""
    prenom = forms.CharField(max_length=80, label="Prénom")
    nom = forms.CharField(max_length=80, label="Nom")
    email = forms.EmailField(required=False, label="E-mail")
    telephone = forms.CharField(max_length=20, label="Téléphone")
    mode = forms.ChoiceField(choices=[("retrait", "Retrait à la bibliothèque"), ("livraison", "Livraison à domicile")],
                             widget=forms.RadioSelect, initial="retrait",
                             label="Comment souhaitez-vous recevoir votre achat ?")
    adresse = forms.CharField(max_length=250, required=False, label="Adresse de livraison (quartier, repère…)")

    def clean(self):
        d = super().clean()
        if d.get("mode") == "livraison" and not d.get("adresse", "").strip():
            self.add_error("adresse", "L'adresse est obligatoire pour une livraison.")
        return d
