from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Cria grupos operacionais de inscrições, sem usuários; adiciona permissões sem remover vínculos existentes."

    @transaction.atomic
    def handle(self, *args, **options):
        roles = {
            "CHAGS — Atendimento": ["view_registration", "review_registration", "cancel_registration"],
            "CHAGS — Indicadores": ["view_registration_indicators"],
            "CHAGS — Exportação de inscrições": ["view_registration", "export_registration"],
        }
        for name, codes in roles.items():
            group, _ = Group.objects.get_or_create(name=name)
            permissions = list(Permission.objects.filter(content_type__app_label="registrations", codename__in=codes))
            if len(permissions) != len(codes):
                raise RuntimeError("Aplique as migrações antes de configurar os grupos.")
            group.permissions.add(*permissions)
        self.stdout.write("Grupos operacionais preparados. Nenhuma conta ou credencial criada.")
