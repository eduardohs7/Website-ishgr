"""Read-only audit API; unknown detail fields are never published implicitly."""
from datetime import timezone as datetime_timezone
import uuid

from django import forms
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, Paginator
from django.db import DatabaseError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.api_protocol import api_methods, error_response, form_errors, success_response
from registrations.models import Registration
from .api import VIEW_AUDIT, operator_api, query_data
from .models import AuditEntry


class AwareDateTimeField(forms.CharField):
    def __init__(self, **kwargs):
        super().__init__(max_length=40, **kwargs)

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return None
        try:
            parsed = parse_datetime(value)
        except ValueError:
            parsed = None
        if parsed is None or timezone.is_naive(parsed):
            raise ValidationError("Informe data e hora ISO 8601 com fuso horário.", code="invalid")
        return parsed.astimezone(datetime_timezone.utc)


class AuditFilterForm(forms.Form):
    action = forms.CharField(required=False, max_length=64)
    actor_id = forms.UUIDField(required=False)
    target_type = forms.CharField(required=False, max_length=64)
    target_id = forms.CharField(required=False, max_length=64)
    created_from = AwareDateTimeField(required=False)
    created_until = AwareDateTimeField(required=False)
    page = forms.IntegerField(required=False, min_value=1)
    page_size = forms.IntegerField(required=False, min_value=1, max_value=100)

    def clean(self):
        values = super().clean()
        start, end = values.get("created_from"), values.get("created_until")
        if start and end and start >= end:
            raise ValidationError("O fim do período deve ser posterior ao início.", code="invalid_range")
        return values


# Deliberately explicit schemas: adding data to internal logs does not expose it.
DETAIL_FIELDS = {
    ("participant.profile_updated", "accounts.user"): {
        "fields": forms.MultipleChoiceField(choices=[(name, name) for name in (
            "full_name", "institution", "country", "preferred_language",
        )]),
    },
    ("certificate.draft_prepared", "certificates.certificatedraft"): {
        "registration_id": forms.UUIDField(),
        "kind": forms.ChoiceField(choices=[("participation", "")]),
    },
    ("registration.created", "registrations.registration"): {
        "category_id": forms.UUIDField(),
        "amount": forms.DecimalField(min_value=0, max_digits=10, decimal_places=2),
        "currency": forms.RegexField(r"\A[A-Z]{3}\Z"),
    },
    ("registration.category_reviewed", "registrations.registration"): {
        "previous": forms.ChoiceField(choices=Registration.Review.choices),
        "decision": forms.ChoiceField(choices=[("approved", ""), ("rejected", "")]),
    },
    ("registration.exported", "registrations.event"): {
        "rows": forms.IntegerField(min_value=0),
        "status": forms.ChoiceField(required=False, choices=Registration.Status.choices),
        "review": forms.ChoiceField(required=False, choices=Registration.Review.choices),
        "search_applied": forms.BooleanField(required=False),
    },
    ("security.mfa_verified", "accounts.user"): {
        "method": forms.ChoiceField(choices=[("totp", ""), ("recovery", "")]),
    },
}


def public_details(obj):
    if not isinstance(obj.details, dict):
        return {}
    result = {}
    for name, field in DETAIL_FIELDS.get((obj.action, obj.target_type), {}).items():
        if name not in obj.details:
            continue
        raw = obj.details[name]
        # Do not let form coercion turn nested JSON or booleans into text/numbers.
        if isinstance(field, forms.MultipleChoiceField):
            if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
                continue
        elif isinstance(field, forms.BooleanField):
            if type(raw) is not bool:
                continue
        elif isinstance(field, forms.DecimalField):
            if not isinstance(raw, str):
                continue
        elif isinstance(field, forms.IntegerField):
            if type(raw) is not int:
                continue
        elif not isinstance(raw, str):
            continue
        try:
            value = field.clean(raw)
        except ValidationError:
            continue
        if isinstance(field, forms.DecimalField):
            value = format(value, ".2f")
        elif isinstance(value, uuid.UUID):
            value = str(value)
        result[name] = value
    return result


def entry_data(obj, *, detail=False):
    data = {
        "id": str(obj.pk), "action": obj.action,
        "actor": {"id": str(obj.actor_id), "full_name": obj.actor.full_name} if obj.actor_id else None,
        "target": {"type": obj.target_type, "id": obj.target_id},
        "created_at": obj.created_at.astimezone(datetime_timezone.utc).isoformat(),
    }
    if detail:
        data.update(reason=obj.reason, details=public_details(obj))
    return data


@api_methods("GET")
@operator_api(VIEW_AUDIT)
def entries(request):
    form = AuditFilterForm(query_data(request, AuditFilterForm.base_fields))
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    values = form.cleaned_data
    try:
        queryset = AuditEntry.objects.select_related("actor")
        for name in ("action", "actor_id", "target_type", "target_id"):
            if values[name]:
                queryset = queryset.filter(**{name: values[name]})
        if values["created_from"]:
            queryset = queryset.filter(created_at__gte=values["created_from"])
        if values["created_until"]:
            queryset = queryset.filter(created_at__lt=values["created_until"])
        paginator = Paginator(queryset.order_by("-created_at", "-pk"), values["page_size"] or 50)
        page = paginator.page(values["page"] or 1)
        rows = [entry_data(obj) for obj in page]
        pagination = {
            "page": page.number, "page_size": paginator.per_page, "total": paginator.count,
            "pages": paginator.num_pages, "has_next": page.has_next(), "has_previous": page.has_previous(),
        }
    except EmptyPage:
        return error_response("page_not_found", status=404)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response({"entries": rows, "pagination": pagination})


@api_methods("GET")
@operator_api(VIEW_AUDIT)
def entry(request, entry_id):
    query_data(request)
    try:
        identifier = uuid.UUID(entry_id)
    except ValueError:
        return error_response("audit_entry_not_found", status=404)
    try:
        obj = AuditEntry.objects.select_related("actor").filter(pk=identifier).first()
        if obj is None:
            return error_response("audit_entry_not_found", status=404)
        data = entry_data(obj, detail=True)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response({"entry": data})
