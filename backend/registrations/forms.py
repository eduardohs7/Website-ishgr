from django.utils.translation import gettext_lazy as _
from django import forms
from django.utils.translation import get_language

from .models import Registration
from .services import available_prices


class PriceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, price):
        review = _(" — exige revisão da comissão") if price.category.requires_review else ""
        language = (get_language() or "pt-br").split("-")[0]
        name = getattr(price.category, f"name_{language}", "") or price.category.name_pt
        return f"{name} — {price.amount:.2f} {price.currency}{review}"


class RegistrationForm(forms.Form):
    price = PriceChoiceField(queryset=None, label=_("Categoria e preço"), empty_label=_("Selecione uma categoria"))

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["price"].queryset = available_prices(event)


class RegistrationActionForm(forms.Form):
    action = forms.ChoiceField(label="Operação", choices=[])
    reason = forms.CharField(label="Motivo", max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, can_review, can_cancel, **kwargs):
        super().__init__(*args, **kwargs)
        choices = []
        if can_review:
            choices.extend([("approved", "Aprovar categoria"), ("rejected", "Rejeitar categoria")])
        if can_cancel:
            choices.append(("cancel", "Cancelar inscrição"))
        self.fields["action"].choices = choices


class RegistrationFilterForm(forms.Form):
    q = forms.CharField(label="Nome, e-mail ou número da inscrição", required=False, max_length=255)
    status = forms.ChoiceField(label=_("Situação"), required=False, choices=[("", "Todas")] + list(Registration.Status.choices))
    review = forms.ChoiceField(label="Revisão", required=False, choices=[("", "Todas")] + list(Registration.Review.choices))
