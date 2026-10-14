"""Synthetic fixtures, accessible only in a runner-created private environment."""
from datetime import date, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets


def guarded_directory():
    from django.conf import settings
    from django.db import connection

    directory = Path(os.environ["CHAGS_RESILIENCE_DIR"]).resolve()
    root = Path(__file__).resolve().parents[1] / ".local" / "resilience"
    if directory.parent != root.resolve() or os.environ.get("DJANGO_SETTINGS_MODULE") != "resilience_settings":
        raise RuntimeError("Ambiente descartável obrigatório.")
    state = json.loads((directory / "postgres.json").read_text())
    expected = state["environment"]
    for field in ("HOST", "PORT", "NAME", "USER", "PASSWORD"):
        if str(settings.DATABASES["default"][field]) != expected[f"CHAGS_DB_{field}"]:
            raise RuntimeError("Conexão diferente da criada pelo ensaio.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user")
        if cursor.fetchone() != ("chags_ci", "chags_ci"):
            raise RuntimeError("Banco descartável obrigatório.")
    return directory


def seed():
    from django.db import connection, transaction
    from django.utils import timezone
    from accounts.models import ParticipantProfile, User
    from registrations.models import Event, RegistrationCategory, RegistrationPrice

    directory = guarded_directory()
    if User.objects.exists() or Event.objects.exists():
        raise RuntimeError("O ensaio exige um banco novo e vazio.")
    password = secrets.token_urlsafe(32)
    participants = []
    now = timezone.now()
    with transaction.atomic():
        event = Event.objects.create(
            code="chags14", title="Evento sintético — ensaio descartável",
            starts_on=date(2027, 7, 12), ends_on=date(2027, 7, 16), registration_enabled=True,
            registration_opens_at=now-timedelta(days=1), registration_closes_at=now+timedelta(days=1),
        )
        category = RegistrationCategory.objects.create(event=event, code="synthetic", name_pt="Categoria sintética")
        price = RegistrationPrice.objects.create(
            category=category, amount=Decimal("9.99"), currency="BRL", valid_from=now-timedelta(hours=1),
        )
        for index in range(5):
            user = User.objects.create_user(
                f"load{index}@example.invalid", password=password, full_name="Participante sintético",
                email_verified_at=now,
            )
            ParticipantProfile.objects.create(user=user)
            participants.append(user.email)
    # Contains only synthetic credentials, kept private and removed by the runner.
    descriptor = os.open(directory / "fixtures.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"password": password, "participants": participants, "price_id": str(price.pk)}, stream)
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('server_version'), rolsuper, rolcreaterole FROM pg_roles WHERE rolname = current_user")
        server_version, superuser, create_role = cursor.fetchone()
    assert not superuser and not create_role
    return {"postgresql": server_version, "database_superuser": superuser, "database_create_role": create_role}


def verify(registrations_expected):
    from accounts.models import AccountActionToken, AccountEmail, ParticipantProfile, User
    from operations.models import AuditEntry
    from registrations.models import Registration

    guarded_directory()
    assert registrations_expected in (4, 5)
    assert User.objects.count() == ParticipantProfile.objects.count() == 6
    assert User.objects.filter(email="duplicate@example.invalid", email_verified_at__isnull=True).count() == 1
    assert AccountActionToken.objects.count() == AccountEmail.objects.count() == 1
    assert AccountEmail.objects.filter(status="pending").count() == 1
    assert Registration.objects.count() == registrations_expected
    assert AuditEntry.objects.filter(action="registration.created").count() == registrations_expected
    assert Registration.objects.filter(
        amount=Decimal("9.99"), currency="BRL", status="pending", category_review="not_required",
    ).count() == registrations_expected
    assert Registration.objects.values("user_id", "event_id").distinct().count() == registrations_expected
    return {"users": 6, "profiles": 6, "queued_emails": 1, "registrations": registrations_expected,
            "registration_audit_entries": registrations_expected, "snapshots_intact": True}
