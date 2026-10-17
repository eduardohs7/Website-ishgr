import uuid

from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django_countries.fields import CountryField

from .managers import UserManager


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    first_name = None
    last_name = None
    email = models.EmailField("e-mail", unique=True)
    full_name = models.CharField("nome completo", max_length=255)
    email_verified_at = models.DateTimeField("e-mail confirmado em", null=True, blank=True)
    session_version = models.UUIDField(null=True, editable=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]
    objects = UserManager()

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"
        permissions = [
            ("view_participant", "Pode consultar participantes na gestão"),
            ("correct_participant", "Pode corrigir cadastros de participantes na gestão"),
        ]
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
            models.CheckConstraint(
                condition=~models.Q(email=""), name="accounts_user_email_not_empty"
            ),
        ]

    def clean(self):
        super().clean()
        self.email = UserManager.normalize_email(self.email)
        self.full_name = self.full_name.strip()

    def save(self, *args, **kwargs):
        self.email = UserManager.normalize_email(self.email)
        update_fields = kwargs.get("update_fields")
        if not self._state.adding and (update_fields is None or "email" in update_fields):
            previous = type(self).objects.filter(pk=self.pk).values_list("email", flat=True).first()
            if previous and UserManager.normalize_email(previous) != self.email:
                self.email_verified_at = None
                if update_fields is not None:
                    kwargs["update_fields"] = set(update_fields) | {"email_verified_at"}
        if not self._state.adding and self.pk is not None and kwargs.get("update_fields") is None and not kwargs.get("force_insert"):
            # Ordinary saves (including a stale admin form) must not restore an
            # old revocation version. Security services save it explicitly.
            deferred = self.get_deferred_fields()
            protected = {"session_version"}
            if self._password is None:
                # set_password() or explicit update_fields is required to change
                # an existing hash; a stale read-only form must not restore it.
                protected.add("password")
            kwargs["update_fields"] = {
                field.attname for field in self._meta.concrete_fields
                if not field.primary_key and field.attname not in protected and field.attname not in deferred
            }
        return super().save(*args, **kwargs)

    def get_full_name(self):
        return self.full_name

    def _get_session_auth_hash(self, secret=None):
        # Preserve existing sessions/tokens until this account first revokes them.
        if self.session_version is None:
            return super()._get_session_auth_hash(secret=secret)
        return salted_hmac("accounts.User.session", f"{self.password}:{self.session_version}",
                           secret=secret, algorithm="sha256").hexdigest()

    def get_short_name(self):
        return self.full_name.split(" ", 1)[0]

    def __str__(self):
        return self.email


class ParticipantProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    institution = models.CharField("instituição", max_length=255, blank=True)
    country = CountryField("país", blank=True)
    preferred_language = models.CharField(
        "idioma preferido", max_length=5, default="pt-br",
        choices=[("pt-br", "Português"), ("en", "English"), ("es", "Español")],
    )


class StaffMFA(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="staff_mfa")
    secret_ciphertext = models.TextField(blank=True)
    enabled = models.BooleanField(default=False)
    version = models.UUIDField(default=uuid.uuid4)
    last_step = models.BigIntegerField(default=-1)
    pending_ciphertext = models.TextField(blank=True)
    pending_expires_at = models.DateTimeField(null=True, blank=True)
    pending_auth_hash = models.CharField(max_length=64, blank=True)
    pending_challenge_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        default_permissions = ()
        constraints = [models.CheckConstraint(condition=models.Q(enabled=False) | ~models.Q(secret_ciphertext=""),
                                             name="staff_mfa_enabled_has_secret")]


class StaffMFARecoveryCode(models.Model):
    factor = models.ForeignKey(StaffMFA, on_delete=models.CASCADE, related_name="recovery_codes")
    digest = models.CharField(max_length=64, unique=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()


class AccountActionToken(models.Model):
    class Purpose(models.TextChoices):
        CONFIRM = "confirm", "Confirmar e-mail"
        RESET = "reset", "Redefinir senha"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="action_tokens")
    purpose = models.CharField(max_length=10, choices=Purpose.choices)
    digest = models.CharField(max_length=64, unique=True)
    email_snapshot = models.EmailField()
    auth_snapshot = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "purpose"],
                condition=models.Q(used_at__isnull=True, revoked_at__isnull=True),
                name="accounts_one_active_action_token",
            ),
        ]
        indexes = [models.Index(fields=["expires_at"])]


class AccountEmail(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        PROCESSING = "processing", "Em envio"
        SENT = "sent", "Enviado"
        FAILED = "failed", "Falha"
        CANCELLED = "cancelled", "Cancelado"

    token = models.OneToOneField(AccountActionToken, on_delete=models.CASCADE, related_name="delivery")
    # Only the pending delivery holds the raw token; erase it after delivery.
    raw_token = models.CharField(max_length=64)
    language = models.CharField(max_length=5, choices=settings.LANGUAGES, default="pt-br")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=32, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "available_at"])]


class AuthThrottleBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    window_start = models.DateTimeField(default=timezone.now, db_index=True)
    count = models.PositiveIntegerField(default=0)
