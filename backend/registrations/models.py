from django.utils.translation import gettext_lazy as _
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, URLValidator
from django.db import models
from django.db.models import F, Q, Value
from django.utils import timezone
from django.utils.translation import get_language


class Currency(models.TextChoices):
    BRL = "BRL", "Real (BRL)"
    USD = "USD", "Dólar (USD)"
    EUR = "EUR", "Euro (EUR)"


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(unique=True)
    title = models.CharField("título", max_length=255)
    starts_on = models.DateField("data inicial")
    ends_on = models.DateField("data final")
    timezone_name = models.CharField("fuso horário", max_length=64, default="America/Belem")
    registration_enabled = models.BooleanField("inscrições habilitadas", default=False)
    registration_opens_at = models.DateTimeField("abertura das inscrições", null=True, blank=True)
    registration_closes_at = models.DateTimeField("encerramento das inscrições", null=True, blank=True)
    registration_currency = models.CharField("moeda das inscrições", max_length=3, choices=Currency.choices, default=Currency.BRL)
    jems_url = models.URLField("URL do JEMS", blank=True, validators=[URLValidator(schemes=["https"])])

    class Meta:
        ordering = ("starts_on", "code")
        verbose_name = "evento"
        constraints = [
            models.CheckConstraint(condition=Q(ends_on__gte=F("starts_on")), name="event_dates_valid"),
            models.CheckConstraint(condition=(
                Q(registration_opens_at__isnull=True, registration_closes_at__isnull=True, registration_enabled=False)
                | Q(registration_opens_at__isnull=False, registration_closes_at__isnull=False,
                    registration_closes_at__gt=F("registration_opens_at"))
            ), name="event_registration_window_valid"),
            models.CheckConstraint(condition=Q(registration_currency__in=Currency.values), name="event_currency_valid"),
        ]

    def clean(self):
        super().clean()
        try:
            ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError({"timezone_name": "Informe um fuso IANA válido."})
        if self.jems_url:
            from urllib.parse import urlsplit
            url = urlsplit(self.jems_url)
            if url.scheme != "https" or url.username or url.password:
                raise ValidationError({"jems_url": "Use uma URL HTTPS sem credenciais."})

    def registrations_open(self, at=None):
        at = at or timezone.now()
        return bool(self.registration_enabled and self.registration_opens_at and self.registration_closes_at
                    and self.registration_opens_at <= at < self.registration_closes_at)

    def __str__(self):
        return self.title


class RegistrationCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="categories")
    code = models.SlugField("código")
    name_pt = models.CharField("nome em português", max_length=255)
    name_en = models.CharField("nome em inglês", max_length=255, blank=True)
    name_es = models.CharField("nome em espanhol", max_length=255, blank=True)
    active = models.BooleanField("ativa", default=True)
    requires_review = models.BooleanField("exige revisão da comissão", default=False)

    class Meta:
        ordering = ("name_pt",)
        verbose_name = "categoria de inscrição"
        verbose_name_plural = "categorias de inscrição"
        constraints = [models.UniqueConstraint(fields=("event", "code"), name="category_event_code_unique")]

    def __str__(self):
        return f"{self.event.code} — {self.name_pt}"


class RegistrationPrice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(RegistrationCategory, on_delete=models.PROTECT, related_name="prices")
    amount = models.DecimalField("valor", max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField("moeda", max_length=3, choices=Currency.choices, default=Currency.BRL)
    valid_from = models.DateTimeField("válido desde")
    valid_until = models.DateTimeField("válido até (exclusivo)", null=True, blank=True)
    active = models.BooleanField("ativo", default=True)

    class Meta:
        ordering = ("category__name_pt", "valid_from")
        verbose_name = "preço de inscrição"
        verbose_name_plural = "preços de inscrição"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name="price_amount_nonnegative"),
            models.CheckConstraint(condition=Q(currency__in=Currency.values), name="price_currency_valid"),
            models.CheckConstraint(condition=Q(valid_until__isnull=True) | Q(valid_until__gt=F("valid_from")), name="price_window_valid"),
            ExclusionConstraint(name="price_no_active_overlap", expressions=[
                ("category", RangeOperators.EQUAL), ("currency", RangeOperators.EQUAL),
                (models.Func(F("valid_from"), F("valid_until"), Value("[)"),
                             function="TSTZRANGE", output_field=DateTimeRangeField()), RangeOperators.OVERLAPS),
            ], condition=Q(active=True)),
        ]

    def clean(self):
        super().clean()
        if not self._state.adding:
            original = type(self).objects.get(pk=self.pk)
            fields = ("category_id", "amount", "currency", "valid_from")
            if any(getattr(self, field) != getattr(original, field) for field in fields):
                raise ValidationError("Crie um novo preço para alterar categoria, valor, moeda ou início de validade.")

    def __str__(self):
        return f"{self.category.name_pt}: {self.amount} {self.currency}"


class Registration(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Aguardando requisitos")
        CONFIRMED = "confirmed", _("Confirmada")
        CANCELLED = "cancelled", _("Cancelada")

    class Review(models.TextChoices):
        NOT_REQUIRED = "not_required", _("Não necessária")
        PENDING = "pending", _("Aguardando revisão")
        APPROVED = "approved", _("Aprovada")
        REJECTED = "rejected", _("Rejeitada")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="registrations")
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="registrations")
    category = models.ForeignKey(RegistrationCategory, on_delete=models.PROTECT, related_name="registrations")
    source_price = models.ForeignKey(RegistrationPrice, on_delete=models.PROTECT, related_name="registrations")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, choices=Currency.choices)
    category_name = models.CharField(max_length=255)
    category_name_en = models.CharField(max_length=255, blank=True, default="")
    category_name_es = models.CharField(max_length=255, blank=True, default="")
    review_required = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    category_review = models.CharField(max_length=16, choices=Review.choices, default=Review.NOT_REQUIRED)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="cancelled_registrations")
    cancellation_reason = models.CharField(max_length=1000, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "inscrição"
        verbose_name_plural = "inscrições"
        default_permissions = ("view",)
        permissions = [
            ("review_registration", "Pode revisar categorias de inscrições"),
            ("cancel_registration", "Pode cancelar inscrições com motivo"),
            ("export_registration", "Pode exportar dados pessoais de inscrições"),
            ("view_registration_indicators", "Pode consultar indicadores de inscrições"),
        ]
        constraints = [
            models.UniqueConstraint(fields=("user", "event"), name="registration_user_event_unique"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="registration_amount_nonnegative"),
            models.CheckConstraint(condition=Q(currency__in=Currency.values), name="registration_currency_valid"),
            models.CheckConstraint(condition=Q(status__in=["pending", "confirmed", "cancelled"]), name="registration_status_valid"),
            models.CheckConstraint(condition=Q(category_review__in=["not_required", "pending", "approved", "rejected"]), name="registration_review_valid"),
            models.CheckConstraint(condition=(
                Q(review_required=False, category_review="not_required")
                | (Q(review_required=True) & ~Q(category_review="not_required"))
            ), name="registration_review_requirement_valid"),
            models.CheckConstraint(condition=(
                Q(status="cancelled", cancelled_at__isnull=False, cancelled_by__isnull=False) & ~Q(cancellation_reason="")
                | (~Q(status="cancelled") & Q(cancelled_at__isnull=True, cancelled_by__isnull=True, cancellation_reason=""))
            ), name="registration_cancellation_valid"),
        ]

    def __str__(self):
        return f"{self.id} — {self.category_name}"

    @property
    def localized_category_name(self):
        language = (get_language() or "pt-br").split("-")[0]
        return getattr(self, f"category_name_{language}", "") or self.category_name
