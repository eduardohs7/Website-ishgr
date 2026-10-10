from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_countries",
    "accounts.apps.AccountsConfig",
    "registrations.apps.RegistrationsConfig",
    "operations.apps.OperationsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.language.ParticipantLanguageMiddleware",
    "accounts.middleware.AccountRateLimitMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

AUTH_USER_MODEL = "accounts.User"
REGISTRATION_EVENT_CODE = "chags14"
REGISTRATION_EXPORT_MAX_ROWS = 10000
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
        "OPTIONS": {"user_attributes": ["full_name", "email"]},
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
LANGUAGES = [("pt-br", "Português"), ("en", "English"), ("es", "Español")]
TIME_ZONE = "America/Belem"
USE_I18N = True
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_HTTPONLY = True
LANGUAGE_COOKIE_SAMESITE = "Lax"
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / ".local" / "static"
# No public media URL is registered. Downloads will require authorization.
MEDIA_ROOT = BASE_DIR / ".local" / "private-media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "accounts.api_protocol.csrf_failure"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
PASSWORD_RESET_TIMEOUT = 3600
EMAIL_CONFIRMATION_TIMEOUT = 86400
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"
PUBLIC_URL = "http://127.0.0.1:8001"
DEFAULT_FROM_EMAIL = "CHAGS 14 <no-reply@example.org>"
AUTH_TRUSTED_PROXY_NETWORKS = []
ACCOUNT_RATE_LIMITS = {
    "login": {"ip": (30, 900), "identity": (10, 900)},
    "signup": {"ip": (10, 3600), "identity": (3, 3600)},
    "request_email": {"ip": (20, 3600), "identity": (3, 3600)},
    "confirm": {"ip": (30, 900)},
    "reset": {"ip": (30, 900)},
}
STATICFILES_DIRS = [
    BASE_DIR / "static",
    ("portal/images", BASE_DIR.parent / "images"),
]
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "config.staticfiles.PortalStyleFinder",
]
