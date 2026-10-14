"""Certificate preparation only; issued documents are not implemented yet."""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class CertificateEligibility(models.Model):
    # Only PENDING is written by this delivery. Review needs approved rules first.
    class Decision(models.TextChoices):
        PENDING = "pending", "Aguardando critérios e avaliação"
        ELIGIBLE = "eligible", "Elegível"
        INELIGIBLE = "ineligible", "Não elegível"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.OneToOneField("registrations.Registration", on_delete=models.PROTECT,
                                        related_name="certificate_eligibility")
    decision = models.CharField(max_length=16, choices=Decision.choices, default=Decision.PENDING)
    criteria_version = models.CharField(max_length=64, blank=True)
    decision_reason = models.CharField(max_length=1000, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="certificate_reviews")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ("view",)
        constraints = [
            models.CheckConstraint(condition=Q(decision__in=["pending", "eligible", "ineligible"]),
                                   name="certificate_eligibility_decision_valid"),
            models.CheckConstraint(condition=(
                Q(decision="pending", criteria_version="", decision_reason="", reviewed_by__isnull=True,
                  reviewed_at__isnull=True)
                | (Q(decision__in=["eligible", "ineligible"], reviewed_by__isnull=False, reviewed_at__isnull=False)
                   & ~Q(criteria_version="") & ~Q(decision_reason=""))
            ), name="certificate_eligibility_review_valid"),
        ]


class CertificateDraft(models.Model):
    """Internal snapshot, with no PDF, issuance timestamp, signature or workload."""
    class Kind(models.TextChoices):
        PARTICIPATION = "participation", "Participação"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eligibility = models.OneToOneField(CertificateEligibility, on_delete=models.PROTECT, related_name="draft")
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.PARTICIPATION)
    participant_name = models.CharField(max_length=255)
    event_title = models.CharField(max_length=255)
    event_starts_on = models.DateField()
    event_ends_on = models.DateField()
    preparation_reason = models.CharField(max_length=1000)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name="certificate_drafts")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        default_permissions = ("view",)
        permissions = [("prepare_certificatedraft", "Pode preparar rascunhos internos de certificados")]
        constraints = [
            models.CheckConstraint(condition=Q(kind="participation"), name="certificate_draft_kind_valid"),
            models.CheckConstraint(condition=Q(event_ends_on__gte=F("event_starts_on")), name="certificate_draft_dates_valid"),
            models.CheckConstraint(condition=~Q(participant_name="") & ~Q(event_title="") & ~Q(preparation_reason=""),
                                   name="certificate_draft_snapshot_nonempty"),
        ]
