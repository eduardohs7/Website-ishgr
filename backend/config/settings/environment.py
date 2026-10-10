"""Small configuration helpers; never include environment values in errors."""
import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def required(name):
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ImproperlyConfigured(f"Configure a variável {name}.")
    return value


def csv_setting(name):
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def development_secret_key(base_dir: Path):
    """Persist a random local key; never fall back to it in production."""
    if os.environ.get("CHAGS_SECRET_KEY"):
        return os.environ["CHAGS_SECRET_KEY"]
    directory = base_dir / ".local"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / "django-secret-key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(64))
    key = path.read_text().strip()
    if len(key) < 50:
        raise ImproperlyConfigured("A chave local de desenvolvimento é inválida.")
    return key


def database_settings(*, defaults=None, production=False):
    defaults = defaults or {}
    config = {"ENGINE": "django.db.backends.postgresql"}
    for field in ("NAME", "USER", "PASSWORD", "HOST", "PORT"):
        name = f"CHAGS_DB_{field}"
        if production and field != "PORT":
            config[field] = required(name)
        else:
            config[field] = os.environ.get(name, defaults.get(field, ""))
    config["CONN_MAX_AGE"] = 60 if production else 0
    config["CONN_HEALTH_CHECKS"] = True
    config["OPTIONS"] = {"connect_timeout": 5}
    if production:
        sslmode = os.environ.get("CHAGS_DB_SSLMODE", "verify-full")
        if sslmode not in ("verify-ca", "verify-full"):
            raise ImproperlyConfigured("CHAGS_DB_SSLMODE deve verificar TLS em produção.")
        config["OPTIONS"]["sslmode"] = sslmode
        if os.environ.get("CHAGS_DB_SSLROOTCERT"):
            config["OPTIONS"]["sslrootcert"] = os.environ["CHAGS_DB_SSLROOTCERT"]
    return config
