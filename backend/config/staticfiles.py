from django.conf import settings
from django.contrib.staticfiles.finders import BaseFinder
from django.core.files.storage import FileSystemStorage


class PortalStyleFinder(BaseFinder):
    """Expose only the two public root assets, never the whole repository."""
    assets = ("style.css", "script.js")

    def __init__(self, *args, **kwargs):
        self.storage = FileSystemStorage(location=settings.BASE_DIR.parent)
        self.storage.prefix = "portal"
        super().__init__(*args, **kwargs)

    def find(self, path, find_all=False, **kwargs):
        # Django's development server normalizes paths with Windows separators.
        path = path.replace("\\", "/")
        if path in {f"portal/{name}" for name in self.assets}:
            result = self.storage.path(path.removeprefix("portal/"))
            return [result] if find_all else result
        return []

    def list(self, ignore_patterns):
        for name in self.assets:
            yield name, self.storage
