from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import AccountActionToken, AuthThrottleBucket


class Command(BaseCommand):
    help = "Remove limites de tentativas antigos e tokens expirados há mais de sete dias."

    def handle(self, *args, **options):
        now = timezone.now()
        max_window = max(rule[1] for policy in settings.ACCOUNT_RATE_LIMITS.values() for rule in policy.values())
        buckets, _ = AuthThrottleBucket.objects.filter(window_start__lt=now - timedelta(seconds=2 * max_window)).delete()
        tokens, _ = AccountActionToken.objects.filter(expires_at__lt=now - timedelta(days=7)).delete()
        self.stdout.write(f"Registros técnicos removidos: limites={buckets}, tokens/entregas={tokens}.")
