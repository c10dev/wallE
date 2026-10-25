"""The package holds every file walle reads at run time: the modules, report.html, the fonts and the app.

The globs test runs everywhere. The wheel test needs a built wheel: the release workflow builds one and
sets WALLE_WHEEL to its path. Without WALLE_WHEEL it skips.
"""

import os
import tomllib
import unittest
import zipfile
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "walle"


def package_files() -> list[str]:
    """Every file in walle/ that the package must hold, relative to walle/. Hidden files and caches are not."""
    return sorted(p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith("."))


def matches(rel: str, glob: str) -> bool:
    """setuptools package-data globs: "*" does not cross a "/"."""
    parts, pattern = rel.split("/"), glob.split("/")
    return len(parts) == len(pattern) and all(fnmatchcase(a, b) for a, b in zip(parts, pattern))


class PackageTest(unittest.TestCase):
    def test_package_data_names_every_data_file(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(project["tool"]["setuptools"]["packages"], ["walle"])
        globs = project["tool"]["setuptools"]["package-data"]["walle"]
        data = [f for f in package_files() if not f.endswith(".py")]
        for must in ("report.html", "fonts/", "appui/index.html", "appui/app.js", "appui/vendor/"):
            self.assertTrue(any(f.startswith(must) for f in data), must)
        self.assertEqual([f for f in data if not any(matches(f, g) for g in globs)], [])
        # A subfolder with modules would need its own entry in packages.
        self.assertEqual([f for f in package_files() if f.endswith(".py") and "/" in f], [])

    @unittest.skipUnless(os.environ.get("WALLE_WHEEL"), "WALLE_WHEEL is not set: no built wheel to read")
    def test_the_wheel_holds_every_package_file(self):
        with zipfile.ZipFile(os.environ["WALLE_WHEEL"]) as wheel:
            names = set(wheel.namelist())
        self.assertEqual([f for f in package_files() if f"walle/{f}" not in names], [])


if __name__ == "__main__":
    unittest.main()
