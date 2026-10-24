"""The version: `walle --version`, and one version string in the four files that carry it."""

import io
import json
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from walle import __version__, cli

REPO = Path(__file__).resolve().parent.parent


class VersionTest(unittest.TestCase):
    def test_version_flag_prints_the_package_version(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as e:
            cli.main(["--version"])
        self.assertEqual(e.exception.code, 0)
        self.assertEqual(out.getvalue(), f"walle {__version__}\n")

    def test_the_four_files_give_one_version(self):
        package = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
        plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())["version"]
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
        listed = [p["version"] for p in market["plugins"] if p["name"] == "walle"]
        self.assertEqual(len(listed), 1)
        found = {"walle/__init__.py": __version__, ".claude-plugin/plugin.json": plugin,
                 ".claude-plugin/marketplace.json": listed[0]}
        self.assertEqual(found, dict.fromkeys(found, package), "pyproject.toml has " + package)


if __name__ == "__main__":
    unittest.main()
