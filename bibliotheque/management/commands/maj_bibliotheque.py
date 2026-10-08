from django.core.management.base import BaseCommand
from bibliotheque.services import maj_quotidienne


class Command(BaseCommand):
    help = "Met à jour les retards, sanctions, suspensions et demandes non retirées."

    def handle(self, *args, **options):
        maj_quotidienne()
        self.stdout.write(self.style.SUCCESS("Mise à jour quotidienne de BiblioFlow effectuée."))
