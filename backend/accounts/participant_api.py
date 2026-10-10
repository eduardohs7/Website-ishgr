"""Own-profile endpoints for the verified participant session."""
from django.conf import settings
from django.db import transaction
from django_countries import countries

from .api_protocol import (
    api_methods, error_response, form_errors, participant_access_error,
    request_data, success_response, verified_api_participant,
)
from .forms import ProfileForm
from .models import ParticipantProfile, User
from .services import update_profile


def profile_data(user):
    profile = ParticipantProfile.objects.filter(user=user).first()
    return {
        "id": str(user.pk), "full_name": user.full_name, "email": user.email,
        "institution": profile.institution if profile else "",
        "country": str(profile.country) if profile else "",
        "preferred_language": profile.preferred_language if profile else settings.LANGUAGE_CODE,
    }


@api_methods("GET", "POST")
@verified_api_participant
def profile(request):
    if request.method == "GET":
        return success_response({"profile": profile_data(request.user)})
    data = request_data(request)
    if data.keys() - {"full_name", "institution", "country", "preferred_language"}:
        return error_response("unknown_fields", status=400)
    with transaction.atomic():
        try:
            # Recheck eligibility under the same user lock as update_profile.
            user = User.objects.select_for_update().get(pk=request.user.pk)
        except User.DoesNotExist:
            return error_response("authentication_required", status=401)
        access_error = participant_access_error(user)
        if access_error is not None:
            return access_error
        instance = ParticipantProfile.objects.filter(user=user).first() or ParticipantProfile(user=user)
        form = ProfileForm(data, instance=instance)
        if not form.is_valid():
            return error_response("validation_error", status=422, errors=form_errors(form))
        update_profile(user, form.cleaned_data)
        user.refresh_from_db()
        response = success_response({"profile": profile_data(user)})
        response["Content-Language"] = form.cleaned_data["preferred_language"]
        return response


@api_methods("GET")
@verified_api_participant
def profile_options(request):
    return success_response({
        "languages": [{"code": code, "name": name} for code, name in settings.LANGUAGES],
        "countries": [{"code": code, "name": str(name)} for code, name in countries],
    })
