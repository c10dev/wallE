"""walle runs, walle pin and walle diff (brief step 6) on a fake home."""

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_keep import tree_hash  # noqa: E402
from test_triage import Home  # noqa: E402

from walle import brief, cli, rundiff, runs, triage  # noqa: E402
from walle.scan import scan  # noqa: E402

V1 = """# App

- Always run the tests before you commit any change to this repository.
- Always run the tests before you commit any change to this repository.
- As of 2025-01-01 the deploy uses vercel for every preview branch.
- Write clean code.
"""
# One file edited: the repeated line goes (closed), the dated line changes a little (edited), the lines
# below the new intro move (moved), and a new dated line comes (new).
V2 = """# App

Intro line about this app.

- Always run the tests before you commit any change to this repository.
- As of 2025-01-02 the deploy uses vercel for every preview branch now.
- Write clean code.
- Since 2024-05-05 the staging box lives on fly for review apps.
"""


class RunsFixture:
    def __init__(self):
        self.h = Home()
        self.home = self.h.home
        self.out = self.h.root / "walle" / "runs"
        self.app = self.h.repo("app")
        self.md = self.h.write("projects/app/CLAUDE.md", V1)
        self.patches = [mock.patch("walle.cli.HOME", self.home),
                        mock.patch("walle.cli.scan", lambda roots, **kw: scan(roots, self.home, **kw))]
        for p in self.patches:
            p.start()

    def cli(self, *args, code=0) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            got = cli.main(["--out", str(self.out), *args])
        assert got == code, (args, got, out.getvalue(), err.getvalue())
        return out.getvalue(), err.getvalue()

    def scan(self, *extra) -> dict:
        self.cli("scan", str(self.app), "--format", "path", *extra)
        return runs.scans(self.out)[-1]

    def diff(self, *args, code=0) -> tuple[str, dict | None]:
        out, _err = self.cli("diff", *args, "--root", str(self.app), "--color", "never", code=code)
        last = Path(out.strip().splitlines()[-1]) if out.strip() else None
        data = json.loads((last.parent / "diff.json").read_text()) if last and last.name == "diff.md" else None
        return out, data

    def close(self):
        for p in self.patches:
            p.stop()
        self.h.close()


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.f = RunsFixture()

    def tearDown(self):
        self.f.close()

    def test_first_run_is_the_baseline(self):
        self.f.scan()
        out, data = self.f.diff()
        self.assertEqual(out.strip(), "First run for this folder. It is the baseline for walle diff.")
        self.assertIsNone(data)
        out, _ = self.f.diff("last")
        self.assertIn("First run for this folder.", out)

    def test_an_edited_file_counts_closed_new_moved_and_edited(self):
        a = self.f.scan("--label", "before")
        self.f.md.write_text(V2)
        b = self.f.scan()
        out, data = self.f.diff()
        lines = out.splitlines()
        self.assertEqual(lines[0], f"walle diff  {a['id']} -> {b['id']}  ~/projects/app")
        self.assertEqual(lines[1], f"rules {a['rules'][:4]} = {b['rules'][:4]}  estimator bytes-v2 = bytes-v2")
        self.assertRegex(lines[3], r"^Stack   claude  ~\d+ -> ~\d+ est\. tokens  \(\+\d+, \+\d+%\)$")
        self.assertIn("Items   closed 1 | new 1 | moved 2 | edited 1 (review) | kept 0", lines)
        self.assertTrue(all(brief.width(x) <= 80 for x in lines[:-1]))
        items = data["items"]
        old = {it["id"]: it for it in json.loads(Path(a["path"]).read_text())["items"]}
        new = {it["id"]: it for it in json.loads(Path(b["path"]).read_text())["items"]}
        self.assertEqual([old[i]["rule"] for i in items["closed"]], ["repeat"])
        self.assertEqual([new[i]["rule"] for i in items["new"]], ["dated"])
        self.assertEqual(sorted(new[i]["rule"] for i in items["moved"]), ["always_step", "vague_quality"])
        self.assertEqual([(old[x]["rule"], new[y]["rule"]) for x, y in items["edited"]], [("dated", "dated")])
        # The diff is a run: kind diff in the index, diff.md next to diff.json, the path on the last line.
        row = runs.read_index(self.f.out)[-1]
        self.assertEqual((row["kind"], row["parent_id"], row["baseline_id"]), ("diff", b["id"], a["id"]))
        self.assertEqual(data["run"]["kind"], "diff")
        md = (Path(lines[-1])).read_text()
        self.assertIn("### edited (review) (1)", md)
        # A diff run is not a scan: the next scan's parent is still the previous scan.
        c = self.f.scan()
        self.assertEqual(c["parent_id"], b["id"])

    def test_rules_changed_means_no_finding_counts(self):
        a = self.f.scan()
        self.f.md.write_text(V2)
        with mock.patch.object(triage, "SAFE_DELETE", 0.9):
            b = self.f.scan()
        self.assertNotEqual(a["rules"], b["rules"])
        out, data = self.f.diff()
        self.assertIn(f"rules changed {a['rules'][:4]} -> {b['rules'][:4]}: finding counts not comparable", out)
        self.assertNotIn("Items", out)
        self.assertIn("Stack   claude", out)  # stack tokens still compare
        self.assertIsNone(data["items"])
        self.assertEqual(data["comparable"], {"findings": False, "tokens": True})

    def test_estimator_or_counting_changed_means_no_token_change(self):
        self.f.scan("--estimator", "v1")
        self.f.scan()
        out, data = self.f.diff()
        self.assertIn("estimator changed: token totals not comparable", out)
        self.assertNotIn("Stack", out)
        self.assertIn("Items   closed 0", out)
        self.assertEqual(data["stacks"], [])
        # Another counting revision: the same, with its own line.
        with mock.patch.object(runs, "COUNTING_REVISION", 99):
            self.f.scan()
        out, _ = self.f.diff("last")
        self.assertIn("counting revision changed: token totals not comparable", out)
        self.assertNotIn("Stack", out)

    def test_pin_fixes_the_baseline_and_last_compares_the_newest_two(self):
        a = self.f.scan()
        self.f.md.write_text(V2)
        b = self.f.scan()
        c = self.f.scan()
        out, _ = self.f.cli("pin", a["id"])
        self.assertIn(f"pinned {a['id']} as the baseline of ~/projects/app.", out)
        out, data = self.f.diff()
        self.assertIn(f"walle diff  {a['id']} -> {c['id']}  ~/projects/app  (baseline pinned)", out)
        self.assertTrue(data["run"]["baseline_pinned"])
        out, _ = self.f.diff("last")
        self.assertIn(f"walle diff  {b['id']} -> {c['id']}", out)
        runs_out, _ = self.f.cli("runs")
        self.assertRegex(runs_out, rf"{a['id']}\s+scan .* pinned")
        out, _ = self.f.cli("pin", "--clear", "--root", str(self.f.app))
        self.assertIn("cleared the pin of ~/projects/app.", out)
        out, _ = self.f.diff()
        self.assertIn(f"walle diff  {b['id']} -> {c['id']}", out)
        # Pinning the newest scan leaves nothing to compare; a run that is not a scan cannot be pinned.
        self.f.cli("pin", c["id"])
        out, _ = self.f.diff()
        self.assertIn(f"The baseline is the newest scan ({c['id']}).", out)
        _out, err = self.f.cli("pin", "nope", code=2)
        self.assertIn("walle pin: nope is not a scan", err)
        self.f.cli("pin", code=2)

    def test_two_runs_by_id_or_by_path_and_errors(self):
        a = self.f.scan()
        self.f.md.write_text(V2)
        b = self.f.scan()
        by_id, _ = self.f.diff(a["id"], b["id"])
        by_path, _ = self.f.diff(a["path"], str(Path(b["path"]).parent))
        self.assertEqual(by_id.splitlines()[:-1], by_path.splitlines()[:-1])
        _out, err = self.f.cli("diff", "nope", b["id"], code=2)
        self.assertIn("walle diff: nope is not a run id or a scan.json path.", err)
        self.f.cli("diff", "a", "b", "c", code=2)
        _out, err = self.f.cli("diff", "--root", str(self.f.h.root), code=2)
        self.assertIn("walle diff: no scan of", err)
        # Korean: the same lines from the string table.
        out, _ = self.f.cli("diff", "--root", str(self.f.app), "--lang", "ko", "--color", "never")
        self.assertIn("항목    닫힘 1 | 새 항목 1 | 옮김 2 | 고침 1 (검토) | 남김 0", out)

    def test_diff_lists_the_applies_between_the_two_scans_and_stale_files(self):
        a = self.f.scan()
        runs.append_row(self.f.out, {"id": "zz-apply", "kind": "apply", "parent_id": "zz-plan", "roots": [],
                                     "rows": ["a1b2c3", "d4e5f6"], "status": "applied"})
        runs.append_row(self.f.out, {"id": "zz-undo", "kind": "undo", "parent_id": "zz-apply", "roots": [],
                                     "status": "restored"})
        b = self.f.scan()
        out, data = self.f.diff()
        self.assertIn("Applied zz-apply (plan zz-plan, 2 changed)", out)
        self.assertIn("        zz-undo undo of zz-apply", out)
        self.assertEqual([r["id"] for r in data["applied"]], ["zz-apply", "zz-undo"])
        self.f.md.write_text(V2)
        out, _ = self.f.diff()
        self.assertIn(f"1 files changed since {b['id']}. Run walle scan.", out)
        self.assertEqual(out.splitlines()[0], f"walle diff  {a['id']} -> {b['id']}  ~/projects/app")

    def test_negative_changes_only_are_green(self):
        self.f.scan()
        self.f.md.write_text("# App\n\n- Write clean code.\n")
        self.f.scan()
        out, _ = self.f.cli("diff", "--root", str(self.f.app), "--color", "always")
        self.assertIn("\x1b[32m-", out)
        self.f.md.write_text(V2)
        self.f.scan()
        out, _ = self.f.cli("diff", "last", "--root", str(self.f.app), "--color", "always")
        self.assertNotIn("\x1b[32m", out)

    def test_runs_and_diff_write_nothing_into_the_scanned_folder(self):
        self.f.scan()
        self.f.md.write_text(V2)
        self.f.scan()
        before = tree_hash(self.f.home / "projects")
        self.f.diff()
        self.f.cli("runs")
        self.f.cli("runs", "--root", str(self.f.app))
        self.assertEqual(tree_hash(self.f.home / "projects"), before)


class RunsListTest(unittest.TestCase):
    def setUp(self):
        self.f = RunsFixture()

    def tearDown(self):
        self.f.close()

    def test_every_kind_is_listed_within_80_columns(self):
        out, _ = self.f.cli("runs")
        self.assertIn("No runs in ", out)
        a = self.f.scan("--label", "a very long label that is cut")
        runs.append_row(self.f.out, {"id": "20990101-000000-2", "kind": "trim", "roots": [], "rows": 61})
        runs.append_row(self.f.out, {"id": "20990101-000001-2", "kind": "apply", "parent_id": "20990101-000000-2",
                                     "roots": [], "rows": ["x"], "status": "applied"})
        runs.append_row(self.f.out, {"id": "20990101-000002-2", "kind": "undo", "parent_id": "20990101-000001-2",
                                     "roots": [], "status": "restored"})
        out, _ = self.f.cli("runs")
        lines = out.splitlines()
        self.assertTrue(all(brief.width(x) <= 80 for x in lines[:-1]), out)
        self.assertEqual(lines[-1], str(self.f.out / "index.jsonl"))
        self.assertRegex(lines[0], r"^run\s+kind\s+label\s+rules\s+stack sum\s+open\s+kept$")
        self.assertRegex(out, rf"{a['id']}\s+scan\s+a very lon \.\.\.\s+{a['rules'][:4]}\s+~")
        self.assertIn("plan 20990101-000000-2: 1 changed, applied", out)
        self.assertIn("undo of 20990101-000001-2: restored", out)
        self.assertIn("rows: 61", out)
        # --root keeps the runs of that folder and the runs that belong to no folder (plans, applies).
        out, _ = self.f.cli("runs", "--root", str(self.f.h.root))
        self.assertNotIn(a["id"], out)
        self.assertIn("20990101-000001-2", out)
        self.assertIn("20990101-000002-2", self.f.cli("runs", "--limit", "1")[0])
        self.assertNotIn(a["id"], self.f.cli("runs", "--limit", "1")[0])


class PartsTest(unittest.TestCase):
    def test_signed_and_items_of_schema_1(self):
        self.assertEqual([rundiff._signed(n) for n in (0, -8100, 30, 1234)], ["0", "-8,100", "+30", "+1,200"])
        self.assertEqual(rundiff.item_states({}, {})["closed"], [])


if __name__ == "__main__":
    unittest.main()
