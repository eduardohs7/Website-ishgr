from django.utils.translation import gettext_lazy as _
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AdminUserCreationForm, AuthenticationForm, SetPasswordForm, UserChangeForm
from django.core.exceptions import ValidationError

from .models import ParticipantProfile, User


class AccountCreationForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("email", "full_name")


class AccountChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


def style_fields(form):
    for name, field in form.fields.items():
        field.widget.attrs["class"] = "form-control"
        if name in ("password1", "password2", "new_password1", "new_password2"):
            field.widget.attrs["autocomplete"] = "new-password"


class SignupForm(forms.Form):
    full_name = forms.CharField(label=_("Nome completo"), max_length=255)
    email = forms.EmailField(label=_("E-mail"), max_length=254)
    password1 = forms.CharField(label=_("Senha"), widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(label=_("Confirme a senha"), widget=forms.PasswordInput, strip=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)
        self.fields["email"].widget.attrs["autocomplete"] = "email"
        self.fields["password1"].help_text = _("Use pelo menos 12 caracteres e evite dados pessoais.")

    def clean_email(self):
        return User.objects.normalize_email(self.cleaned_data["email"])

    def clean(self):
        data = super().clean()
        first, second = data.get("password1"), data.get("password2")
        if first and second and first != second:
            self.add_error("password2", _("As senhas não coincidem."))
        if first:
            user = User(email=data.get("email", ""), full_name=data.get("full_name", ""))
            try:
                password_validation.validate_password(first, user)
            except ValidationError as error:
                self.add_error("password1", error)
        return data


class ParticipantLoginForm(AuthenticationForm):
    username = forms.EmailField(label=_("E-mail"), max_length=254)
    error_messages = {
        "invalid_login": _("E-mail ou senha inválidos, ou conta ainda não confirmada."),
        "inactive": _("E-mail ou senha inválidos, ou conta ainda não confirmada."),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)
        self.fields["username"].widget.attrs["autocomplete"] = "email"
        self.fields["password"].widget.attrs["autocomplete"] = "current-password"

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.email_verified_at is None:
            raise self.get_invalid_login_error()


class EmailRequestForm(forms.Form):
    email = forms.EmailField(label=_("E-mail"), max_length=254)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)


class ConfirmationForm(forms.Form):
    token = forms.CharField(label=_("Código de confirmação"), max_length=64)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)
        self.fields["token"].widget.attrs["autocomplete"] = "off"


class ParticipantPasswordResetForm(SetPasswordForm):
    token = forms.CharField(label=_("Código de recuperação"), max_length=64)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)
        self.fields["token"].widget.attrs["autocomplete"] = "off"
        self.fields["new_password1"].help_text = _("Use pelo menos 12 caracteres e evite dados pessoais.")


class ProfileForm(forms.ModelForm):
    full_name = forms.CharField(label=_("Nome completo"), max_length=255)

    class Meta:
        model = ParticipantProfile
        fields = ("full_name", "institution", "country", "preferred_language")
        labels = {"institution": _("Instituição"), "country": _("País"), "preferred_language": _("Idioma preferido")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)
