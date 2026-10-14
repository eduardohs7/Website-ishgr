from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import User
from operations.services import audit, require_operator
from registrations.models import Event, Registration
from .models import CertificateDraft, CertificateEligibility

VIEW_REGISTRATION = "registrations.view_registration"
VIEW_DRAFT = "certificates.view_certificatedraft"
PREPARE_DRAFT = "certificates.prepare_certificatedraft"
VIEW_PERMISSIONS = (VIEW_REGISTRATION, VIEW_DRAFT)
PREPARE_PERMISSIONS = (*VIEW_PERMISSIONS, PREPARE_DRAFT)
BLOCK_REASON = "certificate_rules_not_configured"


@transaction.atomic
def prepare_draft(*, actor, event_id, registration_id, reason):
    actor = User.objects.select_for_update().get(pk=actor.pk)
    require_operator(actor, *PREPARE_PERMISSIONS)
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise ValidationError("Informe um motivo de até 1000 caracteres.", code="invalid_reason")
    # Same event-before-registration order used by registration configuration.
    event = Event.objects.select_for_update().get(pk=event_id)
    registration = Registration.objects.select_for_update(of=("self",)).select_related("user").get(
        pk=registration_id, event=event,
    )
    if registration.status == Registration.Status.CANCELLED:
        raise ValidationError("Inscrição cancelada não permite preparar rascunho.", code="registration_cancelled")
    existing = CertificateDraft.objects.filter(eligibility__registration=registration).select_related("eligibility").first()
    if existing:
        return existing, False
    if not registration.user.full_name.strip() or not event.title.strip():
        raise ValidationError("Nome e título do evento precisam estar preenchidos.", code="certificate_data_incomplete")
    eligibility, _ = CertificateEligibility.objects.get_or_create(registration=registration)
    # No decision is inferred from payment, registration confirmation or review.
    draft = CertificateDraft.objects.create(
        eligibility=eligibility, participant_name=registration.user.full_name,
        event_title=event.title, event_starts_on=event.starts_on, event_ends_on=event.ends_on,
        preparation_reason=reason, prepared_by=actor,
    )
    audit(actor=actor, action="certificate.draft_prepared", target=draft, reason=reason,
          details={"registration_id": str(registration.pk), "kind": draft.kind})
    return draft, True
