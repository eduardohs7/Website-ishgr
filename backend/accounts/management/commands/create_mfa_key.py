import os
from pathlib import Path

from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Cria chave Fernet em arquivo privado novo; não imprime a chave nem sobrescreve arquivos."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("output", help="Caminho privado fora do Git/servidor de arquivos.")

    def handle(self, *args, **options):
        path = Path(options["output"]).resolve()
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
        except OSError:
            raise CommandError("Não foi possível criar a chave; confira permissões e se o arquivo já existe.") from None
        self.stdout.write("Chave criada em arquivo privado. Transfira o conteúdo somente ao gerenciador de segredos autorizado.")
