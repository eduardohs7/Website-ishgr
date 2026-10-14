import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.mfa import clear_pending
from accounts.models import StaffMFA, User
from operations.services import audit


class Command(BaseCommand):
    help = "Reset técnico de MFA após confirmação externa de identidade; exige UUID, motivo e confirmação."

    def add_arguments(self, parser):
        parser.add_argument("user_id", type=uuid.UUID)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--confirm", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        reason = options["reason"].strip()
        if not options["confirm"] or not reason or len(reason) > 1000:
            raise CommandError("Exige --confirm e motivo de 1 a 1000 caracteres, sem códigos ou segredos.")
        try:
            user = User.objects.select_for_update().get(pk=options["user_id"], is_staff=True)
        except User.DoesNotExist:
            raise CommandError("Conta staff não encontrada.") from None
        factor = StaffMFA.objects.filter(user=user).first()
        if factor is None:
            raise CommandError("A conta não possui cadastro de MFA para resetar.")
        factor.enabled, factor.secret_ciphertext, factor.last_step = False, "", -1
        factor.version = uuid.uuid4()
        clear_pending(factor)
        factor.recovery_codes.all().delete()
        factor.save()
        audit(actor=None, action="security.mfa_reset", target=user, reason=reason)
        self.stdout.write("MFA resetado e auditado. Com exigência ativa, a conta precisa cadastrar novo fator antes da gestão.")
