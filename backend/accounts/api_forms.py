"""Backend input validation; no HTML widget or layout configuration."""
from django import forms
from django.conf import settings

from .forms import SignupForm
from .models import ParticipantProfile


class ApiSignupForm(SignupForm):
    # The account keeps one full name; split inputs are combined before the
    # existing password validators run so they can reject personal names.
    full_name = forms.CharField(required=False, max_length=255)
    first_name = forms.CharField(max_length=127)
    last_name = forms.CharField(max_length=127)
    institution = forms.CharField(max_length=255, required=False)
    country = ParticipantProfile._meta.get_field("country").formfield(required=False)
    language = forms.ChoiceField(choices=settings.LANGUAGES)

    def clean(self):
        if self.cleaned_data.get("first_name") and self.cleaned_data.get("last_name"):
            self.cleaned_data["full_name"] = (
                f'{self.cleaned_data["first_name"]} {self.cleaned_data["last_name"]}'
            )
        return super().clean()


class ApiLoginInputForm(forms.Form):
    email = forms.EmailField(max_length=254)
    password = forms.CharField(strip=False)
    remember_me = forms.ChoiceField(choices=[("false", "false"), ("true", "true")], required=False)
    next = forms.CharField(max_length=2048, required=False)


class ApiResetForm(forms.Form):
    # Only bound token shape is checked here; passwords use SetPasswordForm.
    token = forms.CharField(max_length=64)
    password1 = forms.CharField(strip=False)
    password2 = forms.CharField(strip=False)
