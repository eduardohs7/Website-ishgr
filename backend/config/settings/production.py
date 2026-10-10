import os
from ipaddress import ip_network
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .environment import csv_setting, database_settings, required

DEBUG = False
SECRET_KEY = required("CHAGS_SECRET_KEY")
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("CHAGS_SECRET_KEY deve ter pelo menos 50 caracteres.")
ALLOWED_HOSTS = csv_setting("CHAGS_ALLOWED_HOSTS")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Configure CHAGS_ALLOWED_HOSTS com os hosts da aplicação.")
DATABASES = {"default": database_settings(production=True, defaults={"PORT": "5432"})}
CSRF_TRUSTED_ORIGINS = csv_setting("CHAGS_CSRF_TRUSTED_ORIGINS")

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
LANGUAGE_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_REFERRER_POLICY = "same-origin"
# Only enable after the CTIC confirms that its proxy overwrites this header.
if os.environ.get("CHAGS_TRUST_HTTPS_PROXY") == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

PUBLIC_URL = required("CHAGS_PUBLIC_URL").rstrip("/")
origin = urlsplit(PUBLIC_URL)
if (
    origin.scheme != "https" or origin.hostname not in ALLOWED_HOSTS
    or origin.username or origin.password or origin.query or origin.fragment
    or origin.path
):
    raise ImproperlyConfigured("CHAGS_PUBLIC_URL deve ser uma origem HTTPS em CHAGS_ALLOWED_HOSTS.")

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = required("CHAGS_EMAIL_HOST")
DEFAULT_FROM_EMAIL = required("CHAGS_DEFAULT_FROM_EMAIL")
EMAIL_USE_SSL = os.environ.get("CHAGS_EMAIL_USE_SSL", "false") == "true"
EMAIL_USE_TLS = not EMAIL_USE_SSL
try:
    EMAIL_PORT = int(os.environ.get("CHAGS_EMAIL_PORT", "465" if EMAIL_USE_SSL else "587"))
except ValueError:
    raise ImproperlyConfigured("CHAGS_EMAIL_PORT deve ser uma porta válida.") from None
EMAIL_HOST_USER = os.environ.get("CHAGS_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("CHAGS_EMAIL_PASSWORD", "")
if bool(EMAIL_HOST_USER) != bool(EMAIL_HOST_PASSWORD):
    raise ImproperlyConfigured("Configure CHAGS_EMAIL_USER e CHAGS_EMAIL_PASSWORD juntos.")
EMAIL_TIMEOUT = 10
try:
    AUTH_TRUSTED_PROXY_NETWORKS = [
        ip_network(value) for value in csv_setting("CHAGS_TRUSTED_PROXY_NETWORKS")
    ]
except ValueError:
    raise ImproperlyConfigured("CHAGS_TRUSTED_PROXY_NETWORKS deve conter redes IP válidas.") from None
