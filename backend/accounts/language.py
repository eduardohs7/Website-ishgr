from django.conf import settings
from django.utils import translation

from .models import ParticipantProfile


class ParticipantLanguageMiddleware:
    """Use the saved participant preference after authentication is available."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.path.startswith(("/conta/", "/participante/", "/api/v1/auth/")):
            language = ParticipantProfile.objects.filter(user=request.user).values_list("preferred_language", flat=True).first()
            if language in dict(settings.LANGUAGES):
                with translation.override(language):
                    request.LANGUAGE_CODE = language
                    response = self.get_response(request)
                    response.setdefault("Content-Language", language)
                    return response
        return self.get_response(request)
