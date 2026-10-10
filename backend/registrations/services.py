from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from operations.services import audit, require_operator
from .models import Event, Registration, RegistrationCategory, RegistrationPrice


def available_prices(event, at=None):
    at = at or timezone.now()
    return RegistrationPrice.objects.filter(
        category__event=event, category__active=True, active=True,
        currency=event.registration_currency, valid_from__lte=at,
    ).filter(Q(valid_until__isnull=True) | Q(valid_until__gt=at)).select_related("category")


@transaction.atomic
def register(*, user, event_id, price_id):
    # Same user/event concurrent submissions serialize before checking uniqueness.
    user = User.objects.select_for_update().get(pk=user.pk)
    if not user.is_active or user.email_verified_at is None:
        raise ValidationError(_("Confirme seu e-mail antes de se inscrever."))
    event = Event.objects.select_for_update().get(pk=event_id)
    existing = Registration.objects.filter(user=user, event=event).first()
    if existing:
        return existing, False
    if not event.registrations_open():
        raise ValidationError(_("As inscrições não estão abertas neste momento."))
    # Administrative configuration changes also lock the event first.
    try:
        price = available_prices(event).select_for_update(of=("self",)).get(pk=price_id)
    except (RegistrationPrice.DoesNotExist, ValueError, ValidationError):
        raise ValidationError(_("O preço selecionado não está disponível. Atualize a página e escolha novamente."))
    category = RegistrationCategory.objects.select_for_update().get(pk=price.category_id)
    registration = Registration.objects.create(
        user=user, event=event, category=category, source_price=price,
        amount=price.amount, currency=price.currency, category_name=category.name_pt,
        category_name_en=category.name_en, category_name_es=category.name_es,
        review_required=category.requires_review,
        category_review=Registration.Review.PENDING if category.requires_review else Registration.Review.NOT_REQUIRED,
    )
    audit(actor=user, action="registration.created", target=registration,
          details={"category_id": str(category.pk), "amount": str(price.amount), "currency": price.currency})
    return registration, True


def _reason(reason):
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise ValidationError("Informe um motivo de até 1000 caracteres.")
    return reason


@transaction.atomic
def review_category(*, actor, registration_id, decision, reason):
    require_operator(actor, "registrations.view_registration", "registrations.review_registration")
    reason = _reason(reason)
    if decision not in (Registration.Review.APPROVED, Registration.Review.REJECTED):
        raise ValidationError("Decisão de revisão inválida.")
    registration = Registration.objects.select_for_update().get(pk=registration_id)
    if registration.status != Registration.Status.PENDING or not registration.review_required:
        raise ValidationError("Esta inscrição não permite revisão de categoria.")
    previous = registration.category_review
    if previous == decision:
        return registration
    registration.category_review = decision
    registration.save(update_fields=["category_review"])
    audit(actor=actor, action="registration.category_reviewed", target=registration, reason=reason,
          details={"previous": previous, "decision": decision})
    return registration


@transaction.atomic
def cancel_registration(*, actor, registration_id, reason):
    require_operator(actor, "registrations.view_registration", "registrations.cancel_registration")
    reason = _reason(reason)
    registration = Registration.objects.select_for_update().get(pk=registration_id)
    if registration.status == Registration.Status.CANCELLED:
        return registration
    registration.status = Registration.Status.CANCELLED
    registration.cancelled_at = timezone.now()
    registration.cancelled_by = actor
    registration.cancellation_reason = reason
    registration.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancellation_reason"])
    audit(actor=actor, action="registration.cancelled", target=registration, reason=reason)
    return registration
