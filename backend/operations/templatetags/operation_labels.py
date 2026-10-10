from django import template

register = template.Library()


@register.filter
def label(labels, key):
    return labels.get(key, key)
