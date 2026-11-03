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

class PortalImageFinder(BaseFinder):
    """Find portal images using normalized paths on Windows."""

    def __init__(self, *args, **kwargs):
        self.storage = FileSystemStorage(location=settings.BASE_DIR.parent / "images")
        self.storage.prefix = "portal/images"
        super().__init__(*args, **kwargs)

    def find(self, path, find_all=False, **kwargs):
        import os

        path = path.replace("\\", "/")

        prefix = "portal/images/"
        if not path.startswith(prefix):
            return []

        relative_path = path[len(prefix):]
        result = self.storage.path(relative_path)

        if os.path.isfile(result):
            return [result] if find_all else result

        return []

    def list(self, ignore_patterns):
        for path in self.storage.listdir("")[1]:
            yield path, self.storage