"""Private certificate preparation contracts; issuance is deliberately unavailable."""
import uuid

from django import forms
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, Paginator
from django.db import DatabaseError
from django.views.decorators.debug import sensitive_post_parameters

from accounts.api_protocol import (
    api_methods, error_response, form_errors, request_data, success_response, verified_api_participant,
)
from accounts.models import User
from operations.api import operator_api, query_data
from registrations.models import Event, Registration
from registrations.services import current_event
from .models import CertificateDraft, CertificateEligibility
from .services import BLOCK_REASON, PREPARE_PERMISSIONS, VIEW_PERMISSIONS, prepare_draft


class ListInput(forms.Form):
    registration_id = forms.UUIDField(required=False)
    page = forms.IntegerField(required=False, min_value=1)
    page_size = forms.IntegerField(required=False, min_value=1, max_value=100)


class PrepareInput(forms.Form):
    registration_id = forms.UUIDField()
    reason = forms.CharField(max_length=1000)


def draft_data(obj, *, detail=False):
    registration = obj.eligibility.registration
    data = {
        "id": str(obj.pk), "registration_id": str(registration.pk), "kind": obj.kind,
        "state": "draft", "participant_name": obj.participant_name, "event_title": obj.event_title,
        "created_at": obj.created_at.isoformat(), "prepared_by_id": str(obj.prepared_by_id),
        "eligibility_status": obj.eligibility.decision, "registration_status": registration.status,
        "issuance_enabled": False, "issuance_block_reason": BLOCK_REASON,
    }
    if detail:
        data.update(event_starts_on=obj.event_starts_on.isoformat(), event_ends_on=obj.event_ends_on.isoformat(),
                    preparation_reason=obj.preparation_reason)
    return data


def draft_queryset(event):
    return CertificateDraft.objects.filter(eligibility__registration__event=event).select_related("eligibility__registration")


@api_methods("GET")
@operator_api(*VIEW_PERMISSIONS)
def drafts(request):
    form = ListInput(query_data(request, ListInput.base_fields))
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    try:
        event = current_event()
        if event is None:
            return error_response("event_not_configured", status=404)
        values = form.cleaned_data
        queryset = draft_queryset(event)
        if values["registration_id"]:
            queryset = queryset.filter(eligibility__registration_id=values["registration_id"])
        paginator = Paginator(queryset, values["page_size"] or 50)
        page = paginator.page(values["page"] or 1)
        data = {"drafts": [draft_data(obj) for obj in page], "pagination": {
            "page": page.number, "page_size": paginator.per_page, "total": paginator.count,
            "pages": paginator.num_pages, "has_next": page.has_next(), "has_previous": page.has_previous(),
        }, "issuance_enabled": False, "issuance_block_reason": BLOCK_REASON}
    except EmptyPage:
        return error_response("page_not_found", status=404)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response(data)


@api_methods("GET")
@operator_api(*VIEW_PERMISSIONS)
def draft(request, draft_id):
    query_data(request)
    try:
        identifier = uuid.UUID(draft_id)
    except ValueError:
        return error_response("certificate_draft_not_found", status=404)
    try:
        event = current_event()
        if event is None:
            return error_response("event_not_configured", status=404)
        obj = draft_queryset(event).filter(pk=identifier).first()
        if obj is None:
            return error_response("certificate_draft_not_found", status=404)
        data = draft_data(obj, detail=True)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response({"draft": data})


@api_methods("POST")
@operator_api(*PREPARE_PERMISSIONS)
@sensitive_post_parameters("*")
def prepare(request):
    query_data(request)
    data = request_data(request)
    if data.keys() - set(PrepareInput.base_fields):
        return error_response("unknown_fields", status=400)
    form = PrepareInput(data)
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    try:
        event = current_event()
        if event is None:
            return error_response("event_not_configured", status=404)
        obj, created = prepare_draft(actor=request.user, event_id=event.pk, **form.cleaned_data)
        data = draft_data(obj, detail=True)
    except User.DoesNotExist:
        return error_response("authentication_required", status=401)
    except Event.DoesNotExist:
        return error_response("event_not_configured", status=404)
    except Registration.DoesNotExist:
        return error_response("registration_not_found", status=404)
    except ValidationError as error:
        return error_response(error.code, status=409)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response({"draft": data, "created": created}, status=201 if created else 200)


@api_methods("GET")
@verified_api_participant
def own_certificates(request):
    query_data(request)
    try:
        event = current_event()
        if event is None:
            return error_response("event_not_configured", status=404)
        registration = Registration.objects.filter(event=event, user=request.user).first()
        eligibility = CertificateEligibility.objects.filter(registration=registration).first() if registration else None
        block = ("registration_required" if registration is None else "registration_cancelled"
                 if registration.status == Registration.Status.CANCELLED else BLOCK_REASON)
        data = {
            "registration_id": str(registration.pk) if registration else None,
            "eligibility_status": eligibility.decision if eligibility else "pending",
            "issuance_enabled": False, "issuance_block_reason": block, "certificates": [],
        }
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response(data)
