"""Participant-owned event catalogue and registration endpoints."""
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import get_language

from accounts.api_protocol import (
    api_methods, error_response, form_errors, request_data,
    success_response, verified_api_participant,
)
from accounts.models import User
from .models import Event, Registration
from .services import available_prices, current_event, register


class RegistrationInputForm(forms.Form):
    price_id = forms.UUIDField()


def date_time(value):
    return value.isoformat() if value is not None else None


def event_data(event, at):
    return {
        "id": str(event.pk), "code": event.code, "title": event.title,
        "starts_on": event.starts_on.isoformat(), "ends_on": event.ends_on.isoformat(),
        "timezone": event.timezone_name, "registration_currency": event.registration_currency,
        "registration_opens_at": date_time(event.registration_opens_at),
        "registration_closes_at": date_time(event.registration_closes_at),
        "registrations_open": event.registrations_open(at),
    }


def registration_data(registration):
    return {
        "id": str(registration.pk), "event_id": str(registration.event_id),
        "category_id": str(registration.category_id), "category_name": registration.localized_category_name,
        "source_price_id": str(registration.source_price_id),
        "amount": f"{registration.amount:.2f}", "currency": registration.currency,
        "status": registration.status, "status_label": str(registration.get_status_display()),
        "review_required": registration.review_required, "category_review": registration.category_review,
        "category_review_label": str(registration.get_category_review_display()),
        "created_at": date_time(registration.created_at), "cancelled_at": date_time(registration.cancelled_at),
    }


@api_methods("GET")
@verified_api_participant
def event(request):
    configured = current_event()
    if configured is None:
        return error_response("event_not_configured", status=404)
    return success_response({"event": event_data(configured, timezone.now())})


@api_methods("GET")
@verified_api_participant
def prices(request):
    configured = current_event()
    if configured is None:
        return error_response("event_not_configured", status=404)
    at = timezone.now()
    is_open = configured.registrations_open(at)
    language = (get_language() or "pt-br").split("-")[0]
    catalogue = []
    if is_open:
        for price in available_prices(configured, at).order_by("category__name_pt", "pk"):
            category = price.category
            catalogue.append({
                "price_id": str(price.pk), "category_id": str(category.pk), "category_code": category.code,
                "category_name": getattr(category, f"name_{language}", "") or category.name_pt,
                "requires_review": category.requires_review,
                "amount": f"{price.amount:.2f}", "currency": price.currency,
                "valid_from": date_time(price.valid_from), "valid_until": date_time(price.valid_until),
            })
    return success_response({"event_id": str(configured.pk), "registrations_open": is_open, "prices": catalogue})


@api_methods("GET", "POST")
@verified_api_participant
def registration(request):
    configured = current_event()
    if configured is None:
        return error_response("event_not_configured", status=404)
    if request.method == "GET":
        own = Registration.objects.filter(user=request.user, event=configured).first()
        return success_response({"registration": registration_data(own) if own else None})
    data = request_data(request)
    if data.keys() - {"price_id"}:
        return error_response("unknown_fields", status=400)
    form = RegistrationInputForm(data)
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    try:
        own, created = register(user=request.user, event_id=configured.pk, price_id=form.cleaned_data["price_id"])
    except Event.DoesNotExist:
        return error_response("event_not_configured", status=404)
    except User.DoesNotExist:
        return error_response("authentication_required", status=401)
    except ValidationError as error:
        code = getattr(error, "code", None)
        if code in {"authentication_required", "email_not_verified"}:
            return error_response(code, status=401 if code == "authentication_required" else 403)
        return error_response(code or "registration_unavailable", status=409)
    return success_response({"registration": registration_data(own), "created": created}, status=201 if created else 200)
