"""Permission-scoped participant corrections; credentials and roles stay technical."""
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.debug import sensitive_variables

from accounts.mfa import MFAError, access_code, actor_for
from accounts.models import ParticipantProfile, User
from .services import audit, require_operator

VIEW = "accounts.view_participant"
CORRECT = "accounts.correct_participant"
FIELDS = ("full_name", "institution", "country", "preferred_language")
ACTION = "participant.profile_updated"


class ParticipantNotFound(Exception):
    pass


class ParticipantChanged(Exception):
    pass


def participants():
    return User.objects.filter(is_staff=False, is_superuser=False)


def profile_values(user, profile):
    return {
        "full_name": user.full_name,
        "institution": profile.institution if profile else "",
        "country": str(profile.country) if profile else "",
        "preferred_language": profile.preferred_language if profile else "pt-br",
    }


@sensitive_variables("*")
def revision(user, profile):
    # A state fingerprint, not an authorization token or a monotonic version.
    state = {**profile_values(user, profile), "id": str(user.pk), "email": user.email,
             "is_active": user.is_active, "profile_exists": profile is not None,
             "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None}
    return salted_hmac("operations.participant_revision", json.dumps(state, sort_keys=True), algorithm="sha256").hexdigest()


@transaction.atomic
@sensitive_variables("*")
def correct_participant(*, request, participant_id, expected_revision, values, reason):
    actor = actor_for(request)
    require_operator(actor, VIEW, CORRECT)
    code = access_code(request, actor)
    if code:
        raise MFAError(code)
    # Lock the parent first, then read the profile in a fresh statement. The
    # participant's own profile service uses this same user lock.
    target = participants().select_for_update().filter(pk=participant_id).first()
    if target is None:
        raise ParticipantNotFound
    profile = ParticipantProfile.objects.filter(user=target).first()
    before = profile_values(target, profile)
    if set(values) != set(FIELDS):
        raise ValidationError("Campos de correção inválidos.")
    normalized = {name: str(value).strip() for name, value in values.items()}
    User._meta.get_field("full_name").clean(normalized["full_name"], target)
    candidate = ParticipantProfile(user=target, **{name: normalized[name] for name in FIELDS if name != "full_name"})
    if profile:
        candidate.pk = profile.pk
    candidate.full_clean(validate_unique=False)
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise ValidationError("Informe um motivo de até 1000 caracteres.")
    same_revision = constant_time_compare(expected_revision, revision(target, profile))
    changes = sorted(name for name in FIELDS if before[name] != normalized[name])
    if not same_revision and changes:
        raise ParticipantChanged
    # An identical retry is successful and never creates another audit record.
    if not changes:
        return False
    if "full_name" in changes:
        target.full_name = normalized["full_name"]
        target.save(update_fields=["full_name"])
    if any(name != "full_name" for name in changes):
        if profile is None:
            profile = candidate
        else:
            for name in FIELDS[1:]:
                setattr(profile, name, normalized[name])
        profile.save()
    audit(actor=actor, action=ACTION, target=target, reason=reason, details={"fields": changes})
    return True
