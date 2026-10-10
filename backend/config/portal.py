from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.templatetags.static import static

PUBLIC_PAGES = {
    "index", "artigos-aceitos", "atracoes", "belem", "chamadas", "contato",
    "evento", "hospedagem", "inscricoes", "programacao",
}


def public_page(request, page="index", language=""):
    if page not in PUBLIC_PAGES:
        raise Http404
    root = settings.BASE_DIR.parent
    path = root / language / f"{page}.html"
    return FileResponse(path.open("rb"), content_type="text/html; charset=utf-8")


def public_asset(request, asset):
    return redirect(static(f"portal/{asset}"))


def public_image(request, path):
    if ".." in path.split("/"):
        raise Http404
    return redirect(static(f"portal/images/{path}"))
