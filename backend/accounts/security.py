"""Authenticated password changes and revocation, independent of email delivery."""
import uuid

from django.conf import settings
from django.contrib.auth import HASH_SESSION_KEY, logout, update_session_auth_hash
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.middleware.csrf import get_token, rotate_token
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables

from operations.services import audit
from . import mfa
from .api_protocol import form_errors
from .models import AccountActionToken, StaffMFA, User
from .services import cancel_deliveries


class SecurityError(Exception):
    def __init__(self, code, status=403, errors=None):
        self.code, self.status, self.errors = code, status, errors


@sensitive_variables("*")
def locked_actor(request):
    user = User.objects.select_for_update().get(pk=request.user.pk)
    # Authentication may have happened before a concurrent revocation acquired
    # the account lock. Never allow that stale session to perform another change.
    session_hash = request.session.get(HASH_SESSION_KEY)
    if not user.is_active or not isinstance(session_hash, str) or not constant_time_compare(session_hash, user.get_session_auth_hash()):
        raise SecurityError("authentication_required", 401)
    return user


@sensitive_variables("*")
def confirm_password(user, password):
    # No implicit hash upgrade before the operation has fully validated input.
    if not check_password(password, user.password):
        raise SecurityError("invalid_credentials", 401)


def invalidate_actions(user, factor):
    tokens = AccountActionToken.objects.filter(user=user, used_at__isnull=True)
    cancel_deliveries(tokens)
    tokens.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    if factor:
        mfa.clear_pending(factor)
        factor.save(update_fields=["pending_ciphertext", "pending_expires_at", "pending_auth_hash", "pending_challenge_hash"])


def clear_session_proofs(request):
    request.session.pop(mfa.PROOF_KEY, None)
    request.session.pop(mfa.CHALLENGE_KEY, None)


@transaction.atomic
@sensitive_variables("*")
def change_password(request, *, current_password, new_password1, new_password2):
    user = locked_actor(request)
    factor = StaffMFA.objects.filter(user=user).first()
    if user.is_staff:
        if (not factor or not factor.enabled) and settings.STAFF_MFA_REQUIRED:
            raise SecurityError("mfa_enrollment_required")
        if factor and factor.enabled and not mfa.verified(request, user, factor, recent=True):
            raise SecurityError("mfa_required")
    confirm_password(user, current_password)
    form = SetPasswordForm(user, {"new_password1": new_password1, "new_password2": new_password2})
    if not form.is_valid():
        raise SecurityError("validation_error", 422, form_errors(form))
    if check_password(new_password1, user.password):
        raise SecurityError("password_unchanged", 409)
    user.set_password(form.cleaned_data["new_password1"])
    user.session_version = uuid.uuid4()
    user.save(update_fields=["password", "session_version"])
    invalidate_actions(user, factor)
    audit(actor=user, action="security.password_changed", target=user)
    clear_session_proofs(request)
    update_session_auth_hash(request, user)
    request.user = user
    rotate_token(request)
    # Persist the replacement session in the same transaction as the password.
    request.session.save()
    return {"code": "password_changed", "authenticated": True, "csrf_token": get_token(request),
            "mfa_verification_required": bool(user.is_staff and (settings.STAFF_MFA_REQUIRED or factor and factor.enabled))}


@transaction.atomic
@sensitive_variables("*")
def logout_all(request, *, current_password):
    user = locked_actor(request)
    confirm_password(user, current_password)
    factor = StaffMFA.objects.filter(user=user).first()
    user.session_version = uuid.uuid4()
    user.save(update_fields=["session_version"])
    invalidate_actions(user, factor)
    audit(actor=user, action="security.sessions_revoked", target=user)
    # Revocation can reduce access even without MFA; it cannot grant a new proof.
    logout(request)
    rotate_token(request)
    return {"code": "all_sessions_logged_out", "authenticated": False, "csrf_token": get_token(request)}
