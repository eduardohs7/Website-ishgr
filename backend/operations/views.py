import csv
import io
import uuid
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from registrations.forms import RegistrationActionForm, RegistrationFilterForm
from registrations.models import Registration
from registrations.services import cancel_registration, review_category
from registrations.views import current_event
from .services import audit, require_operator


def operator(*permissions):
    def decorate(view):
        @login_required(login_url="admin:login")
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            require_operator(request.user, *permissions)
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def filtered_registrations(data):
    form = RegistrationFilterForm(data)
    queryset = Registration.objects.filter(event=current_event()).select_related("user", "event")
    if not form.is_valid():
        return form, queryset.none()
    for field, lookup in [("status", "status"), ("review", "category_review")]:
        if form.cleaned_data[field]:
            queryset = queryset.filter(**{lookup: form.cleaned_data[field]})
    query = form.cleaned_data["q"].strip()
    if query:
        condition = Q(user__full_name__icontains=query) | Q(user__email__icontains=query)
        try:
            condition |= Q(pk=uuid.UUID(query))
        except ValueError:
            pass
        queryset = queryset.filter(condition)
    return form, queryset


@never_cache
@operator("registrations.view_registration")
@require_http_methods(["GET"])
def registrations(request):
    form, queryset = filtered_registrations(request.GET)
    page = Paginator(queryset.order_by("-created_at", "pk"), 50).get_page(request.GET.get("page"))
    filters = request.GET.copy()
    filters.pop("page", None)
    return render(request, "operations/list.html", {"form": form, "page": page, "filters": filters.urlencode()})


@never_cache
@operator("registrations.view_registration")
@require_http_methods(["GET", "POST"])
def registration(request, registration_id):
    obj = get_object_or_404(Registration.objects.select_related("user", "event"), pk=registration_id)
    can_review = request.user.has_perm("registrations.review_registration") and obj.review_required and obj.status == "pending"
    can_cancel = request.user.has_perm("registrations.cancel_registration") and obj.status != "cancelled"
    form = RegistrationActionForm(request.POST if request.method == "POST" else None, can_review=can_review, can_cancel=can_cancel)
    if request.method == "POST" and form.is_valid():
        try:
            if form.cleaned_data["action"] == "cancel":
                cancel_registration(actor=request.user, registration_id=obj.pk, reason=form.cleaned_data["reason"])
            else:
                review_category(actor=request.user, registration_id=obj.pk,
                                decision=form.cleaned_data["action"], reason=form.cleaned_data["reason"])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Operação registrada.")
            return redirect("operations:registration", registration_id=obj.pk)
    return render(request, "operations/detail.html", {"registration": obj, "form": form, "can_act": can_review or can_cancel})


@never_cache
@operator("registrations.view_registration_indicators")
@require_http_methods(["GET"])
def indicators(request):
    event = current_event()
    queryset = Registration.objects.filter(event=event)
    by_status = queryset.values("status").annotate(total=Count("id")).order_by("status")
    by_review = queryset.values("category_review").annotate(total=Count("id")).order_by("category_review")
    amounts = queryset.exclude(status="cancelled").values("currency").annotate(total=Sum("amount"), count=Count("id")).order_by("currency")
    return render(request, "operations/indicators.html", {
        "event": event, "total": queryset.count(), "by_status": by_status, "by_review": by_review, "amounts": amounts,
        "status_labels": dict(Registration.Status.choices), "review_labels": dict(Registration.Review.choices),
    })


def csv_cell(value):
    text = str(value)
    # Excel can interpret formulas after leading whitespace, BOM or controls.
    probe = text
    while probe and (probe[0].isspace() or ord(probe[0]) < 32 or probe[0] == "\ufeff"):
        probe = probe[1:]
    text = "".join(" " if ord(char) < 32 else char for char in text)
    if probe.startswith(("=", "+", "-", "@")):
        text = "'" + text
    return text


@never_cache
@operator("registrations.view_registration", "registrations.export_registration")
@require_http_methods(["GET", "POST"])
def exports(request):
    data = request.POST if request.method == "POST" else request.GET
    form, queryset = filtered_registrations(data)
    if request.method == "GET" or not form.is_valid():
        return render(request, "operations/export.html", {"form": form})
    if queryset.count() > settings.REGISTRATION_EXPORT_MAX_ROWS:
        form.add_error(None, "Há inscrições demais para este arquivo. Refine os filtros.")
        return render(request, "operations/export.html", {"form": form}, status=413)
    # One in-memory response and matching count, audited before delivering PII.
    output = io.StringIO(newline="")
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["Inscrição", "Nome", "E-mail", "Categoria", "Situação", "Revisão", "Valor da inscrição", "Moeda", "Criada em (UTC)"])
    with transaction.atomic():
        count = 0
        for obj in queryset.order_by("created_at", "pk").iterator(chunk_size=500):
            writer.writerow([csv_cell(value) for value in [
                obj.id, obj.user.full_name, obj.user.email, obj.category_name,
                obj.get_status_display(), obj.get_category_review_display(),
                f"{obj.amount:.2f}", obj.currency, obj.created_at.isoformat(),
            ]])
            count += 1
            if count > settings.REGISTRATION_EXPORT_MAX_ROWS:
                return HttpResponse("Refine os filtros para reduzir o arquivo.", status=413)
        event = current_event()
        if event:
            # Do not retain the free-text search: it can contain personal data.
            audit(actor=request.user, action="registration.exported", target=event,
                  details={"rows": count, "status": form.cleaned_data["status"], "review": form.cleaned_data["review"],
                           "search_applied": bool(form.cleaned_data["q"].strip())})
    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="chags14-inscricoes.csv"'
    return response
