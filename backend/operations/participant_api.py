"""Commission-only participant list, detail, corrections and scoped history."""
import uuid

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, Paginator
from django.db import DatabaseError, transaction
from django.db.models import Exists, OuterRef, Q
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from accounts.api_protocol import api_methods, error_response, form_errors, request_data, success_response
from accounts.mfa import MFAError
from accounts.models import ParticipantProfile, User
from registrations.models import Registration
from registrations.services import current_event
from .api import operator_api, query_data
from .audit_api import entry_data
from .models import AuditEntry
from .participants import ACTION, CORRECT, FIELDS, VIEW, ParticipantChanged, ParticipantNotFound, correct_participant, profile_values, revision, participants


class PageForm(forms.Form):
    page = forms.IntegerField(required=False, min_value=1)
    page_size = forms.IntegerField(required=False, min_value=1, max_value=100)


class ListForm(PageForm):
    q = forms.CharField(required=False, max_length=255)
    email_status = forms.ChoiceField(required=False, choices=[("verified", ""), ("unverified", "")])
    account_status = forms.ChoiceField(required=False, choices=[("active", ""), ("inactive", "")])
    registration = forms.ChoiceField(required=False, choices=[("registered", ""), ("not_registered", "")])


class CorrectionForm(forms.Form):
    full_name = forms.CharField(max_length=255)
    institution = forms.CharField(required=False, max_length=255)
    country = ParticipantProfile._meta.get_field("country").formfield(required=False)
    preferred_language = forms.ChoiceField(choices=settings.LANGUAGES)
    revision = forms.RegexField(r"\A[0-9a-f]{64}\Z", max_length=64)
    reason = forms.CharField(max_length=1000)


def identifier(value):
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def participant_data(user, *, event, actor, detail=False):
    data = {
        "id": str(user.pk), "full_name": user.full_name, "email": user.email,
        "is_active": user.is_active, "email_verified": user.email_verified_at is not None,
        "joined_at": user.date_joined.isoformat(),
    }
    if detail:
        profile = ParticipantProfile.objects.filter(user=user).first()
        registration = Registration.objects.filter(event=event, user=user).first()
        data.update(profile_values(user, profile), revision=revision(user, profile),
                    registration={"id": str(registration.pk), "status": registration.status} if registration else None,
                    has_registration=registration is not None,
                    actions={"can_correct": actor.has_perms((VIEW, CORRECT)),
                             "can_view_registration": registration is not None and actor.has_perm("registrations.view_registration")})
    else:
        data["has_registration"] = user.has_registration
    return data


def page_data(page):
    return {"page": page.number, "page_size": page.paginator.per_page, "total": page.paginator.count,
            "pages": page.paginator.num_pages, "has_next": page.has_next(), "has_previous": page.has_previous()}


@api_methods("GET")
@operator_api(VIEW)
def listing(request):
    form = ListForm(query_data(request, ListForm.base_fields))
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    event = current_event()
    if event is None:
        return error_response("event_not_configured", status=404)
    values = form.cleaned_data
    try:
        own_registration = Registration.objects.filter(event=event, user_id=OuterRef("pk"))
        queryset = participants().annotate(has_registration=Exists(own_registration))
        if values["q"]:
            condition = Q(full_name__icontains=values["q"]) | Q(email__icontains=values["q"])
            parsed = identifier(values["q"])
            if parsed:
                condition |= Q(pk=parsed)
            queryset = queryset.filter(condition)
        if values["email_status"]:
            queryset = queryset.filter(email_verified_at__isnull=values["email_status"] == "unverified")
        if values["account_status"]:
            queryset = queryset.filter(is_active=values["account_status"] == "active")
        if values["registration"]:
            queryset = queryset.filter(has_registration=values["registration"] == "registered")
        paginator = Paginator(queryset.order_by("-date_joined", "pk"), values["page_size"] or 50)
        page = paginator.page(values["page"] or 1)
        data = {"event_id": str(event.pk), "participants": [participant_data(user, event=event, actor=request.user) for user in page],
                "pagination": page_data(page)}
    except EmptyPage:
        return error_response("page_not_found", status=404)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response(data)


@api_methods("GET", "POST")
@operator_api(VIEW)
@sensitive_post_parameters("*")
@sensitive_variables("*")
def detail(request, participant_id):
    query_data(request)
    parsed = identifier(participant_id)
    if parsed is None:
        return error_response("participant_not_found", status=404)
    if request.method == "POST" and not request.user.has_perm(CORRECT):
        return error_response("permission_denied", status=403)
    form = None
    if request.method == "POST":
        data = request_data(request)
        if data.keys() - set(CorrectionForm.base_fields):
            return error_response("unknown_fields", status=400)
        form = CorrectionForm(data)
        if not form.is_valid():
            return error_response("validation_error", status=422, errors=form_errors(form))
    event = current_event()
    if event is None:
        return error_response("event_not_configured", status=404)
    try:
        with transaction.atomic():
            if form is not None:
                changed = correct_participant(
                    request=request, participant_id=parsed, expected_revision=form.cleaned_data["revision"],
                    reason=form.cleaned_data["reason"], values={name: form.cleaned_data[name] for name in FIELDS},
                )
            target = participants().filter(pk=parsed).first()
            if target is None:
                return error_response("participant_not_found", status=404)
            data = {"event_id": str(event.pk), "participant": participant_data(target, event=event, actor=request.user, detail=True)}
            if form is not None:
                data["changed"] = changed
    except ParticipantNotFound:
        return error_response("participant_not_found", status=404)
    except ParticipantChanged:
        return error_response("participant_changed", status=409)
    except User.DoesNotExist:
        return error_response("authentication_required", status=401)
    except MFAError as error:
        return error_response(error.code, status=error.status)
    except ValidationError:
        return error_response("participant_update_unavailable", status=409)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response(data)


@api_methods("GET")
@operator_api(VIEW)
def history(request, participant_id):
    form = PageForm(query_data(request, PageForm.base_fields))
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    parsed = identifier(participant_id)
    try:
        if parsed is None or not participants().filter(pk=parsed).exists():
            return error_response("participant_not_found", status=404)
        entries = AuditEntry.objects.filter(action=ACTION, target_type="accounts.user", target_id=str(parsed)).select_related("actor")
        paginator = Paginator(entries.order_by("-created_at", "-pk"), form.cleaned_data["page_size"] or 50)
        page = paginator.page(form.cleaned_data["page"] or 1)
        data = {"participant_id": str(parsed), "history": [entry_data(obj, detail=True) for obj in page],
                "pagination": page_data(page)}
    except EmptyPage:
        return error_response("page_not_found", status=404)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response(data)
