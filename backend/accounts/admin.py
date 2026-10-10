from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.admin import GroupAdmin
from django.contrib.auth.models import Group

from .forms import AccountChangeForm, AccountCreationForm
from .models import User


class SuperuserOnlyAdmin:
    # Delegated account/group management needs its own privilege policy.
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(User)
class AccountAdmin(SuperuserOnlyAdmin, UserAdmin):
    form = AccountChangeForm
    add_form = AccountCreationForm
    list_display = ("email", "full_name", "is_active", "is_staff", "email_verified_at")
    search_fields = ("email", "full_name")
    ordering = ("email",)
    readonly_fields = ("id", "email_verified_at", "date_joined", "last_login")
    fieldsets = (
        (None, {"fields": ("id", "email", "full_name", "password")}),
        ("Confirmação de e-mail", {"fields": ("email_verified_at",)}),
        (
            "Permissões",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Datas", {"fields": ("date_joined", "last_login")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "usable_password", "password1", "password2"),
            },
        ),
    )


admin.site.unregister(Group)


@admin.register(Group)
class TechnicalGroupAdmin(SuperuserOnlyAdmin, GroupAdmin):
    pass
