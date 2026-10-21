"""walle scan --progress json: one JSON event per scan step on stderr, and the same scan without it."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from real0817 import Tree as RealTree  # noqa: E402

from walle import cli  # noqa: E402
from walle.scan import STEPS, scan  # noqa: E402

TREE = None


def setUpModule():
    global TREE
    TREE = RealTree()


def tearDownModule():
    TREE.close()


def stable(data: dict) -> dict:
    """scan.json without the fields that change from one run to the next."""
    data = json.loads(json.dumps(data))
    data.pop("generated", None)
    for k in ("id", "parent_id"):
        data["run"].pop(k, None)
    return data


class ScanCallbackTest(unittest.TestCase):
    def test_steps_in_order_with_counts(self):
        calls = []
        data = scan([TREE.projects], TREE.home, progress=lambda step, state, **kw: calls.append((step, state, kw)))
        want = [(s, st) for s in STEPS for st in ("start", "end")]
        self.assertEqual([(s, st) for s, st, _kw in calls], want)
        ends = {s: kw for s, st, kw in calls if st == "end"}
        self.assertEqual(ends["collect"], {"files": data["artifact_count"]})
        self.assertEqual(ends["pairs"], {"pairs": len(data["pairs"])})
        self.assertEqual(ends["triage"], {"items": len(data["items"])})
        self.assertEqual((ends["stack"], ends["classify"]), ({}, {}))
        self.assertTrue(all(kw == {} for _s, st, kw in calls if st == "start"))
        self.assertGreater(data["artifact_count"], 0)

    def test_the_callback_does_not_change_the_scan(self):
        plain = scan([TREE.projects], TREE.home)
        followed = scan([TREE.projects], TREE.home, progress=lambda *a, **kw: None)
        self.assertEqual(stable(plain), stable(followed))


class CliProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "runs"
        self.patches = [mock.patch("walle.cli.scan", lambda roots, **kw: scan(roots, TREE.home, **kw)),
                        mock.patch("walle.cli.HOME", TREE.home)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--out", str(self.out), "scan", str(TREE.projects), *args])
        newest = sorted(p for p in self.out.iterdir() if p.is_dir() and not p.is_symlink())[-1]
        return code, out.getvalue(), err.getvalue(), newest

    def test_json_events_on_stderr(self):
        code, text, err, run = self.run_cli("--progress", "json")
        self.assertEqual(code, 0)
        events = [json.loads(line) for line in err.splitlines()]
        steps = [e for e in events if e["event"] == "step"]
        self.assertEqual([(e["step"], e["state"]) for e in steps],
                         [(s, st) for s in STEPS for st in ("start", "end")])
        times = [e["t"] for e in events]
        self.assertEqual(times, sorted(times))
        data = json.loads((run / "scan.json").read_text())
        ends = {e["step"]: e for e in steps if e["state"] == "end"}
        self.assertEqual(ends["collect"]["files"], data["artifact_count"])
        self.assertEqual(ends["pairs"]["pairs"], len(data["pairs"]))
        self.assertEqual(ends["triage"]["items"], len(data["items"]))
        last = events[-1]
        self.assertEqual(last["event"], "done")
        self.assertEqual((last["files"], last["pairs"], last["items"], last["scan"]),
                         (data["artifact_count"], len(data["pairs"]), len(data["items"]), str(run / "scan.json")))
        self.assertEqual(text, f"{run / 'scan.md'}\n")

    def test_a_released_keep_is_a_json_note(self):
        gone = [{"id": "k1", "kind": "source", "name": "some-skill"}]
        with mock.patch("walle.cli.keep.release", return_value=gone):
            _c, _t, err, _r = self.run_cli("--progress", "json")
            _c, _t, plain_err, _r = self.run_cli()
        events = [json.loads(line) for line in err.splitlines()]
        notes = [e for e in events if e["event"] == "note"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["text"] + "\n", plain_err)
        self.assertIn("some-skill", plain_err)

    def test_stdout_and_scan_json_are_the_same_without_the_option(self):
        for fmt in ([], ["--json"]):
            _c, plain_out, plain_err, plain_run = self.run_cli(*fmt)
            _c, json_out, _e, json_run = self.run_cli(*fmt, "--progress", "json")
            self.assertEqual(plain_err, "")
            plain = stable(json.loads((plain_run / "scan.json").read_text()))
            followed = stable(json.loads((json_run / "scan.json").read_text()))
            self.assertEqual(plain, followed)
            if fmt:
                self.assertEqual(stable(json.loads(plain_out)), stable(json.loads(json_out)))
            else:
                self.assertEqual((plain_out, json_out), (f"{plain_run / 'scan.md'}\n", f"{json_run / 'scan.md'}\n"))


if __name__ == "__main__":
    unittest.main()
