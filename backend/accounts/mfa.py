"""TOTP step-up for staff; secrets encrypted separately from the database."""
import hashlib
import re
import secrets
import uuid
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.contrib.auth import update_session_auth_hash
from django.conf import settings
from django.db import transaction
from django.middleware.csrf import get_token, rotate_token
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables
import pyotp

from operations.services import audit
from .models import StaffMFA, StaffMFARecoveryCode, User

PROOF_KEY = "staff_mfa_proof"
CHALLENGE_KEY = "staff_mfa_enrollment"


@sensitive_variables("*")
def key_fingerprint():
    return hashlib.sha256(settings.STAFF_MFA_ENCRYPTION_KEY.encode("ascii")).hexdigest()


class MFAError(Exception):
    def __init__(self, code, status=403):
        self.code, self.status = code, status


def actor_for(request):
    user = User.objects.select_for_update().get(pk=request.user.pk)
    if not user.is_active:
        raise MFAError("authentication_required", 401)
    if not user.is_staff:
        raise MFAError("permission_denied")
    return user


@sensitive_variables("*")
def cipher():
    return Fernet(settings.STAFF_MFA_ENCRYPTION_KEY.encode("ascii"))


@sensitive_variables("*")
def decrypt(value):
    try:
        return cipher().decrypt(value.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError, ValueError):
        raise MFAError("mfa_unavailable", 503) from None


@sensitive_variables("*")
def digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def pending_valid(request, user, factor):
    challenge = request.session.get(CHALLENGE_KEY, "")
    return bool(factor and factor.pending_ciphertext and factor.pending_expires_at
                and factor.pending_expires_at > timezone.now() and isinstance(challenge, str) and challenge
                and constant_time_compare(factor.pending_auth_hash, user.get_session_auth_hash())
                and constant_time_compare(factor.pending_challenge_hash, digest(challenge)))


def verified(request, user, factor, *, recent=False):
    proof = request.session.get(PROOF_KEY)
    if not factor or not factor.enabled or not isinstance(proof, dict):
        return False
    timestamp = proof.get("at")
    if type(timestamp) not in (int, float):
        return False
    age = timezone.now().timestamp() - timestamp
    limit = settings.STAFF_MFA_RECENT_AGE if recent else settings.STAFF_MFA_MAX_AGE
    return (0 <= age < limit and proof.get("user") == str(user.pk)
            and proof.get("version") == str(factor.version)
            and proof.get("key") == key_fingerprint()
            and isinstance(proof.get("auth"), str)
            and constant_time_compare(proof["auth"], user.get_session_auth_hash()))


def access_code(request, user=None):
    user = user or request.user
    factor = StaffMFA.objects.filter(user=user).first()
    if not factor or not factor.enabled:
        return "mfa_enrollment_required" if settings.STAFF_MFA_REQUIRED else None
    return None if verified(request, user, factor) else "mfa_required"


@sensitive_variables("*")
def grant(request, user, factor):
    request.session.cycle_key()
    request.session[PROOF_KEY] = {"user": str(user.pk), "version": str(factor.version),
                                "auth": user.get_session_auth_hash(), "key": key_fingerprint(), "at": timezone.now().timestamp()}
    request.session.pop(CHALLENGE_KEY, None)
    request.session.save()  # Same transaction as factor consumption and audit.
    rotate_token(request)
    return get_token(request)


def clear_pending(factor):
    factor.pending_ciphertext = ""
    factor.pending_expires_at = None
    factor.pending_auth_hash = ""
    factor.pending_challenge_hash = ""


@sensitive_variables("*")
def new_recovery_codes(factor):
    factor.recovery_codes.all().delete()
    raw = ["-".join([token[i:i + 8] for i in range(0, 32, 8)])
           for token in (secrets.token_hex(16) for _ in range(10))]
    StaffMFARecoveryCode.objects.bulk_create([
        StaffMFARecoveryCode(factor=factor, digest=digest(code.replace("-", ""))) for code in raw
    ])
    return raw


@sensitive_variables("*")
def matching_step(secret, code, last_step):
    if not re.fullmatch(r"[0-9]{6}", code):
        return None
    current = int(timezone.now().timestamp()) // 30
    totp = pyotp.TOTP(secret)
    matches = [step for step in range(max(0, current - 1), current + 2)
               if step > last_step and constant_time_compare(totp.at(step * 30), code)]
    return max(matches) if matches else None


@sensitive_variables("*")
def confirm_password(request, user, password, *, preserve_proof=False):
    previous_hash = user.get_session_auth_hash()
    if not user.check_password(password):
        raise MFAError("invalid_credentials", 401)
    # Django may upgrade a valid older password hash during reauthentication.
    if previous_hash != user.get_session_auth_hash():
        update_session_auth_hash(request, user)
        if preserve_proof:
            request.session[PROOF_KEY]["auth"] = user.get_session_auth_hash()


@transaction.atomic
@sensitive_variables("*")
def begin(request, password):
    user = actor_for(request)
    factor, _ = StaffMFA.objects.get_or_create(user=user)
    if factor.enabled and not verified(request, user, factor, recent=True):
        raise MFAError("mfa_required")
    confirm_password(request, user, password, preserve_proof=factor.enabled)
    secret, challenge = pyotp.random_base32(), secrets.token_urlsafe(32)
    factor.pending_ciphertext = cipher().encrypt(secret.encode("ascii")).decode("ascii")
    factor.pending_expires_at = timezone.now() + timedelta(seconds=settings.STAFF_MFA_ENROLLMENT_AGE)
    factor.pending_auth_hash = user.get_session_auth_hash()
    factor.pending_challenge_hash = digest(challenge)
    factor.save()
    audit(actor=user, action="security.mfa_enrollment_started", target=user)
    request.session[CHALLENGE_KEY] = challenge
    request.session.save()
    return {"secret": secret, "provisioning_uri": pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=settings.STAFF_MFA_ISSUER),
            "expires_at": factor.pending_expires_at.isoformat()}


@transaction.atomic
@sensitive_variables("*")
def confirm(request, code):
    user = actor_for(request)
    factor = StaffMFA.objects.filter(user=user).first()
    if not pending_valid(request, user, factor):
        raise MFAError("mfa_enrollment_expired", 409)
    if factor.enabled and not verified(request, user, factor, recent=True):
        raise MFAError("mfa_required")
    step = matching_step(decrypt(factor.pending_ciphertext), code, -1)
    if step is None:
        raise MFAError("invalid_mfa_code", 401)
    factor.secret_ciphertext = factor.pending_ciphertext
    factor.enabled, factor.last_step, factor.version = True, step, uuid.uuid4()
    clear_pending(factor)
    factor.save()
    recovery_codes = new_recovery_codes(factor)
    audit(actor=user, action="security.mfa_enabled", target=user)
    return {"recovery_codes": recovery_codes, "csrf_token": grant(request, user, factor)}


@transaction.atomic
@sensitive_variables("*")
def verify(request, *, code, method):
    user = actor_for(request)
    factor = StaffMFA.objects.filter(user=user, enabled=True).first()
    if factor is None:
        raise MFAError("mfa_enrollment_required")
    if method == "totp":
        step = matching_step(decrypt(factor.secret_ciphertext), code, factor.last_step)
        if step is None:
            raise MFAError("invalid_mfa_code", 401)
        factor.last_step = step
        factor.save(update_fields=["last_step"])
    else:
        normalized = code.replace("-", "").lower()
        if not re.fullmatch(r"[a-f0-9]{32}", normalized):
            raise MFAError("invalid_mfa_code", 401)
        recovery = factor.recovery_codes.filter(digest=digest(normalized), used_at__isnull=True).first()
        if recovery is None:
            raise MFAError("invalid_mfa_code", 401)
        recovery.used_at = timezone.now()
        recovery.save(update_fields=["used_at"])
    audit(actor=user, action="security.mfa_verified", target=user, details={"method": method})
    return {"verified": True, "csrf_token": grant(request, user, factor)}


@transaction.atomic
@sensitive_variables("*")
def manage(request, password, *, disable=False):
    user = actor_for(request)
    factor = StaffMFA.objects.filter(user=user, enabled=True).first()
    if not verified(request, user, factor, recent=True):
        raise MFAError("mfa_required")
    confirm_password(request, user, password, preserve_proof=True)
    if disable and settings.STAFF_MFA_REQUIRED:
        raise MFAError("mfa_mandatory", 409)
    factor.version = uuid.uuid4()
    clear_pending(factor)
    if disable:
        factor.enabled, factor.secret_ciphertext, factor.last_step = False, "", -1
        factor.recovery_codes.all().delete()
        factor.save()
        audit(actor=user, action="security.mfa_disabled", target=user)
        request.session.pop(PROOF_KEY, None)
        request.session.pop(CHALLENGE_KEY, None)
        request.session.cycle_key()
        request.session.save()
        rotate_token(request)
        return {"enabled": False, "csrf_token": get_token(request)}
    factor.save()
    codes = new_recovery_codes(factor)
    audit(actor=user, action="security.mfa_recovery_rotated", target=user)
    return {"recovery_codes": codes, "csrf_token": grant(request, user, factor)}
