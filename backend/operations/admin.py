from django.contrib import admin

from .models import AuditEntry


@admin.register(AuditEntry)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "target_type", "target_id")
    list_filter = ("action", "target_type")
    readonly_fields = tuple(field.name for field in AuditEntry._meta.fields)
    list_select_related = ("actor",)
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
