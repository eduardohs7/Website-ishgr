from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from accounts.models import User
from registrations.models import Event, RegistrationCategory, RegistrationPrice


def user(email="participant@example.org", **kwargs):
    return User.objects.create_user(email=email, password="Synthetic-registration-password-47!", full_name="Participante de teste",
                                    email_verified_at=timezone.now(), **kwargs)


def event(code="chags14", **kwargs):
    return Event.objects.create(code=code, title="Evento fictício", starts_on=date(2027, 7, 12), ends_on=date(2027, 7, 16),
                                registration_enabled=True, registration_opens_at=timezone.now() - timedelta(days=1),
                                registration_closes_at=timezone.now() + timedelta(days=1), **kwargs)


def price(event, **kwargs):
    category = RegistrationCategory.objects.create(event=event, code="test", name_pt="Categoria fictícia",
                                                   requires_review=kwargs.pop("requires_review", False))
    return RegistrationPrice.objects.create(category=category, amount=Decimal("123.45"), currency="BRL",
                                             valid_from=timezone.now() - timedelta(hours=1), **kwargs)
