from .base import *  # noqa: F403
from .environment import database_settings, development_secret_key

DEBUG = True
SECRET_KEY = development_secret_key(BASE_DIR)  # noqa: F405
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
DATABASES = {
    "default": database_settings(
        defaults={
            "NAME": "chags_dev",
            "USER": "chags_dev",
            "HOST": str(BASE_DIR / ".local" / "postgres" / "socket"),  # noqa: F405
            "PORT": "55432",
        }
    )
}
EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
EMAIL_FILE_PATH = BASE_DIR / ".local" / "emails"  # noqa: F405
