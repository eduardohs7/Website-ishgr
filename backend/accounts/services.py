import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables

from .models import AccountActionToken, AccountEmail, ParticipantProfile, User


@sensitive_variables("raw_token")
def token_digest(raw_token):
    return hashlib.sha256(raw_token.encode()).hexdigest()


@sensitive_variables("raw_token")
def active_token(raw_token, purpose):
    if not raw_token or len(raw_token) > 64:
        return None
    token = AccountActionToken.objects.select_related("user").filter(
        digest=token_digest(raw_token), purpose=purpose, expires_at__gt=timezone.now(),
        used_at__isnull=True, revoked_at__isnull=True, user__is_active=True,
    ).first()
    if token and token.email_snapshot == token.user.email and constant_time_compare(token.auth_snapshot, token.user.get_session_auth_hash()):
        return token
    return None


def cancel_deliveries(tokens):
    AccountEmail.objects.filter(token__in=tokens).exclude(status=AccountEmail.Status.SENT).update(
        status=AccountEmail.Status.CANCELLED, raw_token="", lease_until=None,
    )


@sensitive_variables("raw_token")
def issue_token(user, purpose):
    """Caller holds the user row lock and transaction (or just created it)."""
    now = timezone.now()
    old = AccountActionToken.objects.filter(user=user, purpose=purpose, used_at__isnull=True)
    cancel_deliveries(old)
    old.filter(revoked_at__isnull=True).update(revoked_at=now)
    raw_token = secrets.token_urlsafe(32)
    lifetime = settings.EMAIL_CONFIRMATION_TIMEOUT if purpose == "confirm" else settings.PASSWORD_RESET_TIMEOUT
    token = AccountActionToken.objects.create(
        user=user, purpose=purpose, digest=token_digest(raw_token),
        email_snapshot=user.email, auth_snapshot=user.get_session_auth_hash(),
        expires_at=now + timedelta(seconds=lifetime),
    )
    language = ParticipantProfile.objects.filter(user=user).values_list("preferred_language", flat=True).first()
    AccountEmail.objects.create(token=token, raw_token=raw_token, language=language or settings.LANGUAGE_CODE)


@sensitive_variables("password", "password_hash")
def register_participant(*, email, full_name, password, language="pt-br", institution="", country=""):
    if language not in dict(settings.LANGUAGES):
        raise ValueError("Unsupported participant language")
    email = User.objects.normalize_email(email)
    # Hash even when the address is already registered; never change its account.
    password_hash = make_password(password)
    try:
        with transaction.atomic():
            if User.objects.filter(email__iexact=email).exists():
                return
            user = User.objects.create(email=email, full_name=full_name, password=password_hash)
            profile = ParticipantProfile(user=user, preferred_language=language, institution=institution, country=country)
            profile.full_clean()
            profile.save()
            issue_token(user, AccountActionToken.Purpose.CONFIRM)
    except IntegrityError:
        # A concurrent registration can win. Only hide the expected duplicate.
        if not User.objects.filter(email__iexact=email).exists():
            raise


def request_account_email(email, purpose):
    with transaction.atomic():
        user = User.objects.select_for_update().filter(
            email__iexact=User.objects.normalize_email(email), is_active=True,
        ).first()
        if not user or not user.has_usable_password():
            return
        if purpose == "confirm" and user.email_verified_at is not None:
            return
        if purpose == "reset" and user.email_verified_at is None:
            return
        issue_token(user, purpose)


@sensitive_variables("raw_token", "password")
def consume_token(raw_token, purpose, *, password=None):
    candidate = active_token(raw_token, purpose)
    if not candidate:
        return False
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=candidate.user_id)
        token = AccountActionToken.objects.select_for_update().filter(
            pk=candidate.pk, expires_at__gt=timezone.now(), used_at__isnull=True,
            revoked_at__isnull=True, email_snapshot=user.email,
        ).first()
        if not token or not user.is_active or not constant_time_compare(token.auth_snapshot, user.get_session_auth_hash()):
            return False
        now = timezone.now()
        if purpose == "confirm":
            if user.email_verified_at is not None:
                return False
            user.email_verified_at = now
            user.save(update_fields=["email_verified_at"])
        else:
            if not password or user.email_verified_at is None or not user.has_usable_password():
                return False
            user.set_password(password)
            user.save(update_fields=["password"])
        token.used_at = now
        token.save(update_fields=["used_at"])
        cancel_deliveries([token])
        return True


def update_profile(user, cleaned_data):
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        locked_user.full_name = cleaned_data["full_name"]
        locked_user.save(update_fields=["full_name"])
        profile, _ = ParticipantProfile.objects.get_or_create(user=locked_user)
        for field in ("institution", "country", "preferred_language"):
            setattr(profile, field, cleaned_data[field])
        profile.full_clean()
        profile.save()
