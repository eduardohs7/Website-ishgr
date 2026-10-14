import csv
import io

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.debug import sensitive_variables

from accounts.models import User
from .queries import registration_queryset
from .services import audit, require_operator


class ExportLimitExceeded(Exception):
    pass


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


@transaction.atomic
@sensitive_variables("*")
def registration_csv(*, actor, event, filters):
    # Recheck account and permission changes without the request's permission cache.
    actor = User.objects.select_for_update().get(pk=actor.pk)
    require_operator(actor, "registrations.view_registration", "registrations.export_registration")
    queryset = registration_queryset(event, filters)
    limit = settings.REGISTRATION_EXPORT_MAX_ROWS
    if queryset.count() > limit:
        raise ExportLimitExceeded
    output = io.StringIO(newline="")
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["Inscrição", "Nome", "E-mail", "Categoria", "Situação", "Revisão", "Valor da inscrição", "Moeda", "Criada em (UTC)"])
    count = 0
    for obj in queryset.order_by("created_at", "pk").iterator(chunk_size=500):
        count += 1
        # Catch growth between the preliminary count and the row query too.
        if count > limit:
            raise ExportLimitExceeded
        writer.writerow([csv_cell(value) for value in [
            obj.id, obj.user.full_name, obj.user.email, obj.category_name,
            obj.get_status_display(), obj.get_category_review_display(),
            f"{obj.amount:.2f}", obj.currency, obj.created_at.isoformat(),
        ]])
    if event is not None:
        audit(actor=actor, action="registration.exported", target=event, details={
            "rows": count, "status": filters.get("status", ""), "review": filters.get("review", ""),
            "search_applied": bool(filters.get("q", "").strip()),
        })
    return output.getvalue()


def csv_response(content):
    response = HttpResponse(content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="chags14-inscricoes.csv"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
