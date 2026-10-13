from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import psycopg

from operations.database_backups import create_backup


class Command(BaseCommand):
    help = "Gera backup privado do PostgreSQL com manifesto e checksum, sem sobrescrever backups."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", default=str(settings.BASE_DIR / ".local/backups"))
        parser.add_argument("--database", default="default", choices=list(settings.DATABASES))
        parser.add_argument("--pg-bin", help="Diretório de pg_dump e pg_restore.")

    def handle(self, *args, **options):
        try:
            bundle = create_backup(output_dir=options["output_dir"], alias=options["database"], pg_bin=options["pg_bin"])
        except (OSError, psycopg.Error):
            raise CommandError("Backup não concluído; confira conexão, permissões e espaço disponível.") from None
        self.stdout.write(self.style.SUCCESS(f"Backup criado: {bundle}"))
        self.stdout.write("Guarde a pasta completa em armazenamento privado; execute verify_database_backup antes de confiar na recuperação.")
