import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection

from accounts.mail_delivery import claim_email, deliver_email


class Command(BaseCommand):
    help = "Envia e-mails pendentes sem imprimir endereços, códigos ou conteúdo."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--interval", type=int, default=2)

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 1000 or not 1 <= options["interval"] <= 30:
            raise CommandError("Use limit de 1–1000 e interval de 1–30 segundos.")
        try:
            while True:
                counts = {}
                if not connection.in_atomic_block:
                    close_old_connections()
                for _ in range(options["limit"]):
                    job = claim_email()
                    if job is None:
                        break
                    status = deliver_email(job)
                    counts[status] = counts.get(status, 0) + 1
                if counts:
                    self.stdout.write("Entregas processadas: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
                if not options["watch"]:
                    break
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("Processamento encerrado.")
