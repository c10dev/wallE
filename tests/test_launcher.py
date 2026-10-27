"""bin/walle on a Python older than 3.11: the SessionStart hook starts it with the python3 on PATH.

These tests fake the version. The old-python CI job runs bin/walle on a real Python 3.9 and 3.10.
"""

import ast
import io
import runpy
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import walle.cli

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "bin" / "walle"


def run(argv: list[str], version=(3, 9, 6, "final", 0)) -> tuple[int, str, str, mock.Mock]:
    """Run bin/walle with argv and a faked sys.version_info: the exit code, stdout, stderr and walle.cli.main."""
    out, err = io.StringIO(), io.StringIO()
    env = {"CLAUDE_PLUGIN_OPTION_SESSION_ALARM": "true", "LANG": "en_US.UTF-8"}
    with mock.patch.object(sys, "argv", [str(LAUNCHER), *argv]), mock.patch.object(sys, "version_info", version), \
            mock.patch.object(sys, "path", list(sys.path)), mock.patch.dict("os.environ", env), \
            mock.patch.object(walle.cli, "main", return_value=0) as main, redirect_stdout(out), redirect_stderr(err):
        try:
            runpy.run_path(str(LAUNCHER), run_name="__main__")
        except SystemExit as e:
            return e.code, out.getvalue(), err.getvalue(), main
    raise AssertionError("bin/walle did not exit")


class OldPythonTest(unittest.TestCase):
    def test_alarm_exits_0_and_prints_nothing(self):
        for argv in (["alarm"], ["--out", "/tmp/w", "alarm"], ["--out=/tmp/w", "alarm"]):
            code, out, err, main = run(argv)
            self.assertEqual((code, out, err), (0, "", ""), argv)
            main.assert_not_called()

    def test_other_commands_refuse_in_one_line(self):
        for argv in (["scan"], [], ["--out", "alarm", "scan"], ["scan", "alarm"], ["--version"]):
            code, out, err, main = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(out, "")
            self.assertEqual(err, "walle: this is Python 3.9. walle needs Python 3.11 or later.\n")
            main.assert_not_called()

    def test_python_3_11_runs_walle(self):
        code, out, err, main = run(["alarm"], (3, 11, 0, "final", 0))
        self.assertEqual((code, err), (0, ""))
        main.assert_called_once_with()

    def test_the_files_before_the_guard_parse_as_python_3_9(self):
        for path in (LAUNCHER, ROOT / "walle" / "__init__.py", ROOT / "walle" / "strings.py"):
            ast.parse(path.read_text(), str(path), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
