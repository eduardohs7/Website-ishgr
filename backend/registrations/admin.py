from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from accounts.admin import SuperuserOnlyAdmin
from operations.services import audit
from .models import Event, Registration, RegistrationCategory, RegistrationPrice


class ConfigurationAdmin(SuperuserOnlyAdmin, admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method != "POST":
            return super().changeform_view(request, object_id, form_url, extra_context)
        try:
            with transaction.atomic():
                return super().changeform_view(request, object_id, form_url, extra_context)
        except (IntegrityError, ValidationError):
            messages.error(request, "A configuração conflita com dados existentes ou mudou durante a operação. Confira os períodos e tente novamente.")
            return redirect(request.path)

    def save_model(self, request, obj, form, change):
        if isinstance(obj, Event):
            if change:
                Event.objects.select_for_update().get(pk=obj.pk)
        else:
            event_id = obj.event_id if isinstance(obj, RegistrationCategory) else obj.category.event_id
            Event.objects.select_for_update().get(pk=event_id)
        obj.full_clean()
        super().save_model(request, obj, form, change)
        audit(actor=request.user, action="configuration.updated" if change else "configuration.created", target=obj,
              details={"fields": sorted(form.changed_data)})


@admin.register(Event)
class EventAdmin(ConfigurationAdmin):
    list_display = ("code", "title", "starts_on", "registration_enabled", "registration_currency")

    def get_readonly_fields(self, request, obj=None):
        return ("code",) if obj else ()


@admin.register(RegistrationCategory)
class CategoryAdmin(ConfigurationAdmin):
    list_display = ("name_pt", "event", "active", "requires_review")
    list_filter = ("event", "active", "requires_review")
    list_select_related = ("event",)

    def get_readonly_fields(self, request, obj=None):
        return ("event", "code") if obj else ()


@admin.register(RegistrationPrice)
class PriceAdmin(ConfigurationAdmin):
    list_display = ("category", "amount", "currency", "valid_from", "valid_until", "active")
    list_filter = ("currency", "active", "category__event")
    list_select_related = ("category", "category__event")

    def get_readonly_fields(self, request, obj=None):
        return ("category", "amount", "currency", "valid_from") if obj else ()


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("id", "participant", "category_name", "amount", "currency", "status", "category_review", "created_at", "manage_link")
    list_filter = ("event", "status", "category_review", "category", "currency")
    search_fields = ("id", "user__full_name", "user__email")
    list_select_related = ("user", "event", "category", "source_price", "cancelled_by")
    actions = None

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Participante")
    def participant(self, obj):
        return f"{obj.user.full_name} ({obj.user.email})"

    @admin.display(description="Operações")
    def manage_link(self, obj):
        return format_html('<a href="{}">Consultar / gerir</a>', reverse("operations:registration", args=[obj.pk]))
