from datetime import timedelta
from smtplib import SMTPException

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils import translation
from django.utils.translation import gettext as _
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables

from .models import AccountEmail


def claim_email():
    now = timezone.now()
    with transaction.atomic():
        job = AccountEmail.objects.select_for_update(skip_locked=True).filter(
            models.Q(status="pending", available_at__lte=now)
            | models.Q(status="processing", lease_until__lte=now)
        ).order_by("pk").first()
        if not job:
            return None
        job.status = "processing"
        job.attempts += 1
        job.lease_until = now + timedelta(seconds=120)
        job.save(update_fields=["status", "attempts", "lease_until"])
        return job


@sensitive_variables("job", "url", "body")
def deliver_email(job):
    token = job.token
    user = token.user
    valid = (
        user.is_active and user.email == token.email_snapshot and token.used_at is None
        and token.revoked_at is None and token.expires_at > timezone.now()
        and job.raw_token and job.attempts <= 5
        and constant_time_compare(token.auth_snapshot, user.get_session_auth_hash())
    )
    status, error_code, sent_at = "cancelled", "", None
    if valid:
        confirmation = token.purpose == "confirm"
        route = "accounts:confirm" if confirmation else "accounts:reset"
        # URL fragments never reach HTTP access logs or the Referer header.
        url = f"{settings.PUBLIC_URL}{reverse(route)}#token={job.raw_token}"
        with translation.override(job.language):
            subject = _("Confirme seu e-mail — CHAGS 14") if confirmation else _("Recupere sua senha — CHAGS 14")
            instruction = _("Confirme seu e-mail") if confirmation else _("Redefina sua senha")
            body = _(
                "Olá,\n\n%(instruction)s pelo link:\n%(url)s\n\n"
                "Se precisar informar o código manualmente: %(code)s\n"
                "Válido até %(expires)s. O código só pode ser utilizado uma vez.\n\n"
                "Se você não solicitou esta mensagem, ignore-a.\nEquipe CHAGS 14\n"
            ) % {"instruction": instruction, "url": url, "code": job.raw_token, "expires": token.expires_at.isoformat()}
        try:
            count = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [token.email_snapshot]).send()
            if count != 1:
                raise SMTPException("Delivery not acknowledged")
            status, sent_at = "sent", timezone.now()
        except (SMTPException, OSError):
            status = "failed" if job.attempts >= 5 else "pending"
            error_code = "delivery_failed"
    updates = {
        "status": status, "sent_at": sent_at, "lease_until": None, "error_code": error_code,
        "available_at": timezone.now() + timedelta(seconds=min(60 * 2 ** min(job.attempts, 6), 3600)),
    }
    if status != "pending":
        updates["raw_token"] = ""
    # A resend/consumption may have cancelled this job while SMTP was running.
    AccountEmail.objects.filter(pk=job.pk, status="processing", attempts=job.attempts).update(**updates)
    return status
