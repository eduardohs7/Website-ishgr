from django.core.exceptions import PermissionDenied

from .models import AuditEntry


def require_operator(user, *permissions):
    if not user.is_active or not user.is_staff or not user.has_perms(permissions):
        raise PermissionDenied


def audit(*, actor, action, target, reason="", details=None):
    return AuditEntry.objects.create(
        actor=actor, action=action, target_type=target._meta.label_lower,
        target_id=str(target.pk), reason=reason, details=details or {},
    )
