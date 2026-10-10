from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from accounts.views import verified_participant
from .forms import RegistrationForm
from .models import Event, Registration
from .services import register


def current_event():
    return Event.objects.filter(code=settings.REGISTRATION_EVENT_CODE).first()


@never_cache
@verified_participant
@require_http_methods(["GET", "POST"])
def mine(request):
    event = current_event()
    registration = Registration.objects.filter(user=request.user, event=event).first() if event else None
    form = None
    if event and not registration and event.registrations_open():
        form = RegistrationForm(request.POST if request.method == "POST" else None, event=event)
        if not form.fields["price"].queryset.exists():
            form = None
        if request.method == "POST" and form is not None and form.is_valid():
            try:
                register(user=request.user, event_id=event.pk, price_id=form.cleaned_data["price"].pk)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                return redirect("registrations:mine")
    elif request.method == "POST" and registration:
        # Retried requests never modify category, price or cancellation.
        return redirect("registrations:mine")
    return render(request, "registrations/mine.html", {"event": event, "registration": registration, "form": form})


@never_cache
@verified_participant
@require_http_methods(["GET"])
def jems(request):
    event = current_event()
    if event and event.jems_url:
        try:
            event.clean()
            URLValidator(schemes=["https"])(event.jems_url)
        except ValidationError:
            pass
        else:
            return HttpResponseRedirect(event.jems_url)
    return render(request, "accounts/notice.html", {"title": _("Submissão de trabalhos"), "text": _("O link de acesso ao JEMS será disponibilizado pela organização. A conta no JEMS é independente da conta neste portal.")})
