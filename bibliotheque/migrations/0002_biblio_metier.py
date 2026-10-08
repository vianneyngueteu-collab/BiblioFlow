from django.db import migrations, models
import django.db.models.deletion


def remplir_donnees(apps, schema_editor):
    Livre = apps.get_model('bibliotheque', 'Livre')
    Achat = apps.get_model('bibliotheque', 'Achat')
    for livre in Livre.objects.all():
        if not livre.image:
            livre.image = f"cover-{((livre.pk - 1) % 8) + 1}.svg"
            livre.save(update_fields=['image'])
    for achat in Achat.objects.select_related('adherent').all():
        if not achat.reference:
            achat.reference = f"BF-LEGACY-{achat.pk:06d}"
        if achat.adherent_id:
            achat.client_prenom = achat.adherent.prenom
            achat.client_nom = achat.adherent.nom
            achat.client_email = achat.adherent.email or ''
            achat.client_telephone = achat.adherent.telephone or ''
        achat.mode_reception = 'livraison' if achat.adresse_livraison else 'retrait'
        if achat.mode_paiement:
            achat.statut_paiement = 'paye'
        achat.save(update_fields=['reference', 'client_prenom', 'client_nom', 'client_email', 'client_telephone', 'mode_reception', 'statut_paiement'])


class Migration(migrations.Migration):
    dependencies = [('bibliotheque', '0001_initial')]
    operations = [
        migrations.AddField(model_name='livre', name='isbn', field=models.CharField(blank=True, max_length=20, null=True, unique=True)),
        migrations.AddField(model_name='livre', name='description', field=models.TextField(blank=True)),
        migrations.AddField(model_name='livre', name='image', field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='livre', name='actif', field=models.BooleanField(default=True)),
        migrations.AddField(model_name='emprunt', name='date_demande', field=models.DateTimeField(auto_now_add=True, null=True)),
        migrations.AddField(model_name='emprunt', name='date_limite_retrait', field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='achat', name='reference', field=models.CharField(blank=True, max_length=24, null=True, unique=True)),
        migrations.AddField(model_name='achat', name='mode_reception', field=models.CharField(choices=[('retrait', 'Retrait'), ('livraison', 'Livraison')], default='retrait', max_length=12)),
        migrations.AddField(model_name='achat', name='statut_paiement', field=models.CharField(choices=[('en_attente', 'À payer'), ('paye', 'Payé'), ('annule', 'Annulé')], default='en_attente', max_length=12)),
        migrations.AddField(model_name='achat', name='reference_paiement', field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name='achat', name='date_paiement', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='achat', name='date_limite_retrait', field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name='achat', name='client_prenom', field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name='achat', name='client_nom', field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name='achat', name='client_email', field=models.EmailField(blank=True, max_length=254)),
        migrations.AddField(model_name='achat', name='client_telephone', field=models.CharField(blank=True, max_length=20)),
        migrations.AlterField(model_name='achat', name='adherent', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='achats', to='bibliotheque.utilisateur')),
        migrations.RunPython(remplir_donnees, migrations.RunPython.noop),
        migrations.AlterField(model_name='emprunt', name='date_demande', field=models.DateTimeField(auto_now_add=True)),
    ]
