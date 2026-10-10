from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from config.staticfiles import PortalStyleFinder


class PortalWindowsPathsTests(SimpleTestCase):
    def test_public_assets_resolve_with_both_path_separators(self):
        finder = PortalStyleFinder()
        for filename in ("style.css", "script.js"):
            for separator in ("/", "\\"):
                with self.subTest(filename=filename, separator=separator):
                    result = finder.find(f"portal{separator}{filename}")
                    self.assertEqual(Path(result), settings.BASE_DIR.parent / filename)
                    self.assertTrue(Path(result).is_file())

    def test_windows_paths_do_not_expose_private_files(self):
        finder = PortalStyleFinder()
        for path in (
            "portal\\..\\backend\\manage.py",
            "portal\\backend\\.env",
            "portal\\README.md",
            "portal\\..\\style.css",
        ):
            with self.subTest(path=path):
                self.assertEqual(finder.find(path), [])
