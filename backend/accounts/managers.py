from django.contrib.auth.base_user import BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


class UserManager(BaseUserManager):
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        # The application treats the entire address as case-insensitive.
        # Preserve dots and plus tags; they are part of the actual address.
        return super().normalize_email(email).strip().lower()

    def get_by_natural_key(self, email):
        return self.get(email__iexact=self.normalize_email(email))

    def _create_user(self, email, password, **extra_fields):
        if not email or not email.strip():
            raise ValueError("O e-mail é obrigatório.")
        email = self.normalize_email(email)
        validate_email(email)
        full_name = extra_fields.get("full_name", "").strip()
        if not full_name:
            raise ValidationError({"full_name": "O nome completo é obrigatório."})
        extra_fields["full_name"] = full_name
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superusuário deve ter is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superusuário deve ter is_superuser=True.")
        if not password:
            raise ValueError("Superusuário deve ter uma senha.")
        return self._create_user(email, password, **extra_fields)
