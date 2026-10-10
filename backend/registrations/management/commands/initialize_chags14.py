from datetime import date

from django.core.management.base import BaseCommand

from registrations.models import Event


class Command(BaseCommand):
    help = "Cria o evento CHAGS 14 fechado, sem categorias, preços ou URL JEMS; preserva configuração existente."

    def handle(self, *args, **options):
        _, created = Event.objects.get_or_create(code="chags14", defaults={
            "title": "CHAGS 14", "starts_on": date(2027, 7, 12), "ends_on": date(2027, 7, 16),
        })
        self.stdout.write("Evento inicial criado com inscrições fechadas." if created else "Evento existente preservado.")
