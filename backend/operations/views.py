from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from registrations.forms import RegistrationActionForm, RegistrationFilterForm
from registrations.models import Registration
from registrations.services import cancel_registration, review_category
from registrations.views import current_event
from .exports import ExportLimitExceeded, csv_response, registration_csv
from .queries import registration_queryset
from .services import require_operator


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
    event = current_event()
    if not form.is_valid():
        return form, Registration.objects.none()
    return form, registration_queryset(event, form.cleaned_data)


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


@never_cache
@operator("registrations.view_registration", "registrations.export_registration")
@require_http_methods(["GET", "POST"])
def exports(request):
    data = request.POST if request.method == "POST" else request.GET
    form = RegistrationFilterForm(data)
    if request.method == "GET" or not form.is_valid():
        return render(request, "operations/export.html", {"form": form})
    try:
        content = registration_csv(actor=request.user, event=current_event(), filters=form.cleaned_data)
    except ExportLimitExceeded:
        form.add_error(None, "Há inscrições demais para este arquivo. Refine os filtros.")
        return render(request, "operations/export.html", {"form": form}, status=413)
    return csv_response(content)
