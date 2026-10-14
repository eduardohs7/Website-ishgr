"""Same-origin, permission-scoped commission API for the configured event."""
from decimal import Decimal
from functools import wraps
import uuid

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import EmptyPage, Paginator
from django.db import DatabaseError, transaction
from django.db.models import Count, Sum
from django.middleware.csrf import get_token
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from accounts.api_protocol import (
    PayloadError, api_methods, error_response, form_errors, request_data, success_response,
)
from accounts.models import ParticipantProfile, User
from registrations.api import registration_data
from registrations.forms import RegistrationFilterForm
from registrations.models import Registration
from registrations.services import cancel_registration, current_event, review_category
from .exports import ExportLimitExceeded, csv_response, registration_csv
from .queries import registration_queryset
from .services import require_operator


VIEW = "registrations.view_registration"
REVIEW = "registrations.review_registration"
CANCEL = "registrations.cancel_registration"
INDICATORS = "registrations.view_registration_indicators"
EXPORT = "registrations.export_registration"
VIEW_AUDIT = "operations.view_auditentry"


def operator_access_error(user, permissions=()):
    if not user.is_authenticated or not user.is_active:
        return error_response("authentication_required", status=401)
    try:
        require_operator(user, *permissions)
    except PermissionDenied:
        return error_response("permission_denied", status=403)
    return None


def operator_api(*permissions):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            error = operator_access_error(request.user, permissions)
            if error is not None:
                return error
            try:
                return view(request, *args, **kwargs)
            except PermissionDenied:
                return error_response("permission_denied", status=403)
        return wrapped
    return decorate


def query_data(request, allowed=()):
    if any(len(values) != 1 for _, values in request.GET.lists()):
        raise PayloadError("invalid_payload")
    if request.GET.keys() - set(allowed):
        raise PayloadError("unknown_fields")
    return request.GET.dict()


def capabilities(user):
    return {
        "view_registrations": user.has_perm(VIEW),
        "review_category": user.has_perms((VIEW, REVIEW)),
        "cancel_registration": user.has_perms((VIEW, CANCEL)),
        "view_indicators": user.has_perm(INDICATORS),
        "export_registrations": user.has_perms((VIEW, EXPORT)),
        "view_audit": user.has_perm(VIEW_AUDIT),
    }


def managed_registration_data(obj, actor, *, detail=False):
    data = registration_data(obj)
    data["participant"] = {"id": str(obj.user_id), "full_name": obj.user.full_name, "email": obj.user.email}
    data["actions"] = {
        "can_review": actor.has_perms((VIEW, REVIEW)) and obj.review_required and obj.status == Registration.Status.PENDING,
        "can_cancel": actor.has_perms((VIEW, CANCEL)) and obj.status != Registration.Status.CANCELLED,
    }
    if detail:
        profile = ParticipantProfile.objects.filter(user_id=obj.user_id).first()
        data["participant"].update({
            "institution": profile.institution if profile else "",
            "country": str(profile.country) if profile else "",
        })
        data.update({
            "category_names": {"pt": obj.category_name, "en": obj.category_name_en, "es": obj.category_name_es},
            "cancellation_reason": obj.cancellation_reason,
            "cancelled_by_id": str(obj.cancelled_by_id) if obj.cancelled_by_id else None,
        })
    return data


class ListInputForm(RegistrationFilterForm):
    page = forms.IntegerField(required=False, min_value=1)
    page_size = forms.IntegerField(required=False, min_value=1, max_value=100)


class ReviewInputForm(forms.Form):
    decision = forms.ChoiceField(choices=[("approved", "Aprovar"), ("rejected", "Rejeitar")])
    reason = forms.CharField(max_length=1000)


class CancelInputForm(forms.Form):
    reason = forms.CharField(max_length=1000)


def target_registration(event, registration_id):
    try:
        registration_id = uuid.UUID(registration_id)
    except ValueError:
        return None
    return Registration.objects.filter(event=event, pk=registration_id).select_related("user").first()


@api_methods("GET")
@operator_api()
def session(request):
    query_data(request)
    return success_response({"operator": {"id": str(request.user.pk), "full_name": request.user.full_name},
                             "permissions": capabilities(request.user), "csrf_token": get_token(request)})


@api_methods("GET")
@operator_api(VIEW)
def registrations(request):
    data = query_data(request, {"q", "status", "review", "page", "page_size"})
    form = ListInputForm(data)
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    configured = current_event()
    if configured is None:
        return error_response("event_not_configured", status=404)
    values = form.cleaned_data
    queryset = registration_queryset(configured, values)
    paginator = Paginator(queryset.order_by("-created_at", "pk"), values["page_size"] or 50)
    try:
        page = paginator.page(values["page"] or 1)
    except EmptyPage:
        return error_response("page_not_found", status=404)
    return success_response({
        "event_id": str(configured.pk),
        "registrations": [managed_registration_data(obj, request.user) for obj in page],
        "pagination": {"page": page.number, "page_size": paginator.per_page, "total": paginator.count,
                       "pages": paginator.num_pages, "has_next": page.has_next(), "has_previous": page.has_previous()},
    })


@api_methods("GET")
@operator_api(VIEW)
def registration(request, registration_id):
    query_data(request)
    configured = current_event()
    if configured is None:
        return error_response("event_not_configured", status=404)
    obj = target_registration(configured, registration_id)
    if obj is None:
        return error_response("registration_not_found", status=404)
    return success_response({"registration": managed_registration_data(obj, request.user, detail=True)})


@sensitive_variables("*")
def mutate(request, registration_id, *, permission, form_class, service):
    query_data(request)
    data = request_data(request)
    if data.keys() - set(form_class.base_fields):
        return error_response("unknown_fields", status=400)
    form = form_class(data)
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    configured = current_event()
    if configured is None:
        return error_response("event_not_configured", status=404)
    try:
        with transaction.atomic():
            # A fresh actor avoids permission caches and serializes account changes.
            actor = User.objects.select_for_update().get(pk=request.user.pk)
            error = operator_access_error(actor, (VIEW, permission))
            if error is not None:
                return error
            obj = target_registration(configured, registration_id)
            if obj is None:
                return error_response("registration_not_found", status=404)
            obj = service(actor=actor, registration_id=obj.pk, **form.cleaned_data)
            response_data = managed_registration_data(obj, actor, detail=True)
    except User.DoesNotExist:
        return error_response("authentication_required", status=401)
    except Registration.DoesNotExist:
        return error_response("registration_not_found", status=404)
    except ValidationError:
        return error_response("registration_action_unavailable", status=409)
    except DatabaseError:
        # Outside the atomic block: state and audit have both been rolled back.
        return error_response("operation_unavailable", status=503)
    return success_response({"registration": response_data})


@api_methods("POST")
@operator_api(VIEW, REVIEW)
@sensitive_post_parameters("*")
def review(request, registration_id):
    return mutate(request, registration_id, permission=REVIEW, form_class=ReviewInputForm, service=review_category)


@api_methods("POST")
@operator_api(VIEW, CANCEL)
@sensitive_post_parameters("*")
def cancel(request, registration_id):
    return mutate(request, registration_id, permission=CANCEL, form_class=CancelInputForm, service=cancel_registration)


@api_methods("POST")
@operator_api(VIEW, EXPORT)
@sensitive_post_parameters("*")
@sensitive_variables("*")
def export(request):
    query_data(request)
    data = request_data(request)
    if data.keys() - {"q", "status", "review"}:
        return error_response("unknown_fields", status=400)
    form = RegistrationFilterForm(data)
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    configured = current_event()
    if configured is None:
        return error_response("event_not_configured", status=404)
    try:
        content = registration_csv(actor=request.user, event=configured, filters=form.cleaned_data)
    except User.DoesNotExist:
        return error_response("authentication_required", status=401)
    except ExportLimitExceeded:
        return error_response("export_limit_exceeded", status=413)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return csv_response(content)


@api_methods("GET")
@operator_api(INDICATORS)
def indicators(request):
    query_data(request)
    configured = current_event()
    if configured is None:
        return error_response("event_not_configured", status=404)
    # One aggregate query keeps totals consistent without exposing participant rows.
    groups = list(Registration.objects.filter(event=configured).order_by().values(
        "status", "category_review", "currency",
    ).annotate(count=Count("id"), amount=Sum("amount")))
    status_totals = {code: 0 for code in Registration.Status.values}
    review_totals = {code: 0 for code in Registration.Review.values}
    amounts = {}
    for group in groups:
        status_totals[group["status"]] += group["count"]
        review_totals[group["category_review"]] += group["count"]
        if group["status"] != Registration.Status.CANCELLED:
            amount = amounts.setdefault(group["currency"], {"count": 0, "total": Decimal("0.00")})
            amount["count"] += group["count"]
            amount["total"] += group["amount"]
    return success_response({
        "event": {"id": str(configured.pk), "code": configured.code, "title": configured.title},
        "total": sum(status_totals.values()),
        "by_status": [{"code": code, "label": str(label), "total": status_totals[code]} for code, label in Registration.Status.choices],
        "by_review": [{"code": code, "label": str(label), "total": review_totals[code]} for code, label in Registration.Review.choices],
        "amount_basis": "registration_value_excluding_cancelled",
        "amounts": [{"currency": currency, "count": value["count"], "total": f'{value["total"]:.2f}'}
                    for currency, value in sorted(amounts.items())],
    })
