from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import psycopg

from operations.database_backups import verify_backup


class Command(BaseCommand):
    help = "Testa um backup confiável em banco novo temporário e remove esse banco ao final."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("bundle", help="Pasta que contém database.dump e manifest.json.")
        parser.add_argument("--database", default="default", choices=list(settings.DATABASES))
        parser.add_argument("--pg-bin", help="Diretório de pg_dump e pg_restore.")

    def handle(self, *args, **options):
        try:
            count = verify_backup(bundle=options["bundle"], alias=options["database"], pg_bin=options["pg_bin"])
        except (OSError, psycopg.Error):
            raise CommandError("Verificação não concluída; confira acesso, CREATEDB e extensão btree_gist.") from None
        self.stdout.write(self.style.SUCCESS(f"Restauração verificada: {count} tabelas públicas; banco temporário removido."))
