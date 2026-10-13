import uuid

from django.db.models import Q

from registrations.models import Registration


def registration_queryset(event, filters):
    """Use only validated registration filters, always scoped to one event."""
    queryset = Registration.objects.filter(event=event).select_related("user", "event")
    for field, lookup in (("status", "status"), ("review", "category_review")):
        if filters.get(field):
            queryset = queryset.filter(**{lookup: filters[field]})
    query = filters.get("q", "").strip()
    if query:
        condition = Q(user__full_name__icontains=query) | Q(user__email__icontains=query)
        try:
            condition |= Q(pk=uuid.UUID(query))
        except ValueError:
            pass
        queryset = queryset.filter(condition)
    return queryset
