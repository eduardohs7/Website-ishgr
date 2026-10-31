from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Prepara grupos de consulta/correção de participantes, sem criar contas ou atribuí-las."

    @transaction.atomic
    def handle(self, *args, **options):
        roles = {
            "CHAGS — Consulta de participantes": ["view_participant"],
            "CHAGS — Correção de participantes": ["view_participant", "correct_participant"],
        }
        for name, codes in roles.items():
            permissions = list(Permission.objects.filter(content_type__app_label="accounts", content_type__model="user", codename__in=codes))
            if len(permissions) != len(codes):
                raise RuntimeError("Aplique as migrações antes de configurar os grupos.")
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.add(*permissions)
        self.stdout.write("Grupos de participantes preparados. Nenhuma conta criada ou atribuída.")
