"""walle runs, walle pin and walle diff (brief step 6) on a fake home."""

import io
import json
import os
import shlex
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sessions import claude_session  # noqa: E402
from test_keep import tree_hash  # noqa: E402
from test_triage import Home  # noqa: E402

from walle import brief, cli, measure, rundiff, runs, triage  # noqa: E402
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
        self.assertIsNone(data["sessions"])  # no transcripts: no Sessions lines
        self.assertNotIn("Sessions", out)
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


APPLY = "20261021-090000"
LABEL = rundiff.LABEL_COLS
# The apply is two days old, so every session is in the past and the since window is still open.
AT = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0)


class DiffSessionsTest(unittest.TestCase):
    """The Sessions lines of walle diff on synthetic transcripts: the n rule of measure.py, the split, Next."""

    def setUp(self):
        self.f = RunsFixture()
        self.f.out = self.f.home / ".walle" / "runs"  # the default --out of this home: Next needs no --out
        self.transcripts = self.f.home / ".claude" / "projects"
        self.web = self.f.h.repo("web")
        self.f.h.write("projects/web/CLAUDE.md", "- rule\n")
        self.patches = [mock.patch("walle.eve.HOME", self.f.home), mock.patch("walle.eve.SCRATCH_ROOTS", ()),
                        mock.patch("walle.cli.DEFAULT_OUT", self.f.out)]
        for p in self.patches:
            p.start()
        self.cwd = os.getcwd()
        os.chdir(self.f.app)  # walle diff in the scanned folder: the Next command needs no folder

    def tearDown(self):
        os.chdir(self.cwd)
        for p in self.patches:
            p.stop()
        self.f.close()

    def sessions(self, before: int, after: int, model: str = "claude-opus-5-5", after_total: int = 80000,
                 folder: Path | None = None, tag: str = "o", at: datetime = AT):
        """before: first calls 90,000 + 500 i in the 10 days before at; after: after_total + 400 i, 1-9 h after."""
        for i in range(before):
            claude_session(self.transcripts, f"{tag}b{i:02d}", folder or self.f.app,
                           measure.utc(at - timedelta(days=1 + i % 10, hours=i % 9)), model=model, total=90000 + 500 * i)
        for i in range(after):
            claude_session(self.transcripts, f"{tag}a{i:02d}", folder or self.f.app,
                           measure.utc(at + timedelta(hours=1 + i % 9)), model=model, total=after_total + 400 * i)

    def change_row(self, rid: str = APPLY, when: datetime | None = AT, kind: str = "apply"):
        row = {"id": rid, "kind": kind, "parent_id": APPLY if kind == "undo" else "20261021-041703", "roots": [],
               "rows": ["e94298"], "status": "applied"}
        runs.append_row(self.f.out, {**row, "generated": when.isoformat()} if when else row)

    def around_apply(self, *args, at: datetime = AT, undo: datetime | None = None) -> tuple[list[str], dict]:
        self.f.scan()
        self.change_row(when=at)
        if undo:
            self.change_row("20261021-100000", undo, "undo")
        self.f.scan()
        out, data = self.f.diff(*args)
        return out.splitlines(), data

    def run_next(self, lines: list[str]) -> tuple[list[str], dict]:
        """Run the Next command as a shell would: join the lines that end with a backslash, expand ~."""
        i = next(n for n, x in enumerate(lines) if x.startswith("Next"))
        text = lines[i][LABEL:]
        while text.endswith(" \\"):
            i += 1
            text = text[:-2] + " " + lines[i].strip()
        words = [str(self.f.home) + w[1:] if w.startswith("~/") else w
                 for w in shlex.split(text.removesuffix(" (after 10 sessions)"))]
        self.assertEqual(words[0], "walle")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(words[1:])
        self.assertEqual(code, 0, err.getvalue())
        last = Path(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(last.name, "eve.md", out.getvalue())
        return words, json.loads((last.parent / "eve.json").read_text())["measure"]

    def test_too_few_since_the_apply_gives_the_next_line(self):
        self.sessions(12, 3)
        self.sessions(0, 2, model="claude-sonnet-5-5", tag="s")
        self.sessions(12, 12, folder=self.web, tag="w")  # another folder: not counted
        before = tree_hash(self.f.home / "projects")
        lines, data = self.around_apply()
        i = lines.index("Sessions (~/projects/app, opus 5.5 only)")
        self.assertEqual(lines[i + 1:i + 4], [
            "        first-call input median 92,750 [91,375-94,125] n=12",
            "        -> too few sessions (3 of 10)",
            "Too few sessions in ~/projects/app for a value: sonnet 5.5 (0 before, 2 since)"])
        self.assertEqual(lines[-2], f"Next    walle measure --around {APPLY} (after 10 sessions)")
        self.assertNotIn("A change in median is not proof of cause: task mix and connectors move it.", lines)
        self.assertTrue(all(brief.width(x) <= 80 for x in lines[:-1]), lines)
        s = data["sessions"]
        self.assertEqual((s["split_at"], s["ref"]), ("apply", APPLY))
        self.assertEqual(s["next"], {"around": APPLY, "folder": None, "out": None, "transcripts": None})
        self.assertEqual(s["folder"], str(self.f.app))
        block = s["measure"]
        self.assertEqual((block["agent"], block["folder"]["key"]), ("claude", "app"))
        self.assertEqual([(r["family"], r["before"]["sessions"], r["after"]["sessions"]) for r in block["rows"]],
                         [("opus 5.5", 12, 3), ("sonnet 5.5", 0, 2)])
        self.assertEqual(block["rows"][0]["change"]["first_call"]["state"], "too_few")
        # The Next line splits the sessions the same way: walle measure --around takes its ref.
        self.assertEqual(measure.split_point(self.f.out, s["next"]["around"])["windows"], block["split"]["windows"])
        words, again = self.run_next(lines)
        self.assertEqual(words, ["walle", "measure", "--around", APPLY])
        self.assertEqual(again["rows"], block["rows"])
        # walle diff reads the transcripts and writes its cache next to the runs folder, not into the scan.
        self.assertEqual(tree_hash(self.f.home / "projects"), before)
        self.assertTrue((self.f.out.parent / "cache" / "eve.json").is_file())

    def test_from_another_folder_the_next_command_names_the_folder(self):
        self.sessions(12, 3)
        os.chdir(self.f.h.root)
        lines, data = self.around_apply()
        self.assertEqual(data["sessions"]["next"]["folder"], str(self.f.app))
        # The command and its condition do not fit in 80 columns together: the condition goes under it.
        self.assertEqual(lines[-3:-1], [f"Next    walle measure ~/projects/app --around {APPLY}",
                                        "        (after 10 sessions)"])
        self.assertEqual(self.run_next(lines)[1]["rows"], data["sessions"]["measure"]["rows"])

    def test_a_scan_of_two_roots_measures_the_folder_that_diff_compares(self):
        # Defect: the Sessions lines measured the first root (app) when walle diff --root picked web.
        self.sessions(12, 12, folder=self.web, tag="w")
        self.f.cli("scan", str(self.f.app), str(self.web), "--format", "path")
        self.change_row()
        self.f.cli("scan", str(self.f.app), str(self.web), "--format", "path")
        os.chdir(self.web)
        lines = self.f.cli("diff", "--root", str(self.web), "--color", "never")[0].splitlines()
        data = json.loads((Path(lines[-1]).parent / "diff.json").read_text())
        self.assertEqual(data["sessions"]["measure"]["folder"]["path"], str(self.web))
        self.assertNotIn("No sessions for ~/projects/app in these windows.", lines)
        i = lines.index("Sessions (~/projects/web, opus 5.5 only)")
        self.assertEqual(lines[i + 1:i + 3], [
            "        first-call input median 92,750 [91,375-94,125] n=12",
            "        -> median 82,200 [81,100-83,300] n=12  -10,550 lower"])
        self.assertEqual((data["sessions"]["folder"], data["sessions"]["next"]), (str(self.web), None))
        # walle diff A B from a folder outside both roots measures the first root of the newest scan.
        os.chdir(self.f.h.root)
        ids = [r["id"] for r in runs.scans(self.f.out)]
        out = self.f.cli("diff", ids[0], ids[1], "--color", "never")[0]
        self.assertIn("No sessions for ~/projects/app in these windows.", out)
        self.assertIn(f"Next    walle measure ~/projects/app --around {APPLY}", out)

    def test_the_next_command_keeps_out(self):
        # Defect: the Next command dropped --out, so walle measure read the default runs folder and exited 2.
        self.f.out = self.f.home / "other" / "runs"
        self.sessions(12, 3)
        lines, data = self.around_apply()
        words, again = self.run_next(lines)
        self.assertEqual(words, ["walle", "--out", str(self.f.out), "measure", "--around", APPLY])
        self.assertEqual(again["rows"], data["sessions"]["measure"]["rows"])
        self.assertEqual(again["split"]["windows"], data["sessions"]["measure"]["split"]["windows"])
        self.assertEqual(lines[-3:-1], [f"Next    walle --out ~/other/runs measure --around {APPLY}",
                                        "        (after 10 sessions)"])  # 83 columns on one line

    def test_the_next_command_keeps_transcripts(self):
        # Defect: the Next command dropped --transcripts, so walle measure read ~/.claude/projects instead.
        moved = self.f.home / "sessions-copy"
        self.transcripts = moved
        self.sessions(12, 3)
        lines, data = self.around_apply("--transcripts", str(moved))
        words, again = self.run_next(lines)
        self.assertEqual(words[-2:], ["--transcripts", str(moved)])
        self.assertEqual(again["rows"], data["sessions"]["measure"]["rows"])
        self.assertEqual(data["sessions"]["next"], {"around": APPLY, "folder": None, "out": None,
                                                    "transcripts": str(moved)})
        # Both flags: the command goes on to a second line, and the condition takes its own line.
        self.f.out = self.f.home / "other" / "runs"
        lines, _ = self.around_apply("--transcripts", str(moved))
        self.assertEqual(lines[-4:-1], [
            f"Next    walle --out ~/other/runs measure --around {APPLY} \\",
            "          --transcripts ~/sessions-copy",
            "        (after 10 sessions)"])
        self.assertEqual(self.run_next(lines)[1]["rows"], data["sessions"]["measure"]["rows"])

    def test_a_long_next_command_goes_on_with_a_backslash(self):
        deep = self.f.home / "runs-folder-with-a-long-name" / "runs"
        self.f.out = deep
        self.sessions(12, 3)
        os.chdir(self.f.h.root)
        lines, data = self.around_apply("--lang", "ko")
        i = next(n for n, x in enumerate(lines) if x.startswith("다음"))
        self.assertTrue(lines[i].endswith(" \\"), lines[i:])
        self.assertTrue(all(brief.width(x) <= 80 for x in lines[:-1]), lines)
        self.assertEqual(lines[-2], "        (이후 세션 10개 뒤)")
        text = " ".join(x.strip().removesuffix(" \\") for x in lines[i:-2])
        self.assertEqual(text, f"다음    walle --out ~/runs-folder-with-a-long-name/runs measure ~/projects/app "
                               f"--around {APPLY}")
        self.assertEqual(data["sessions"]["next"]["out"], str(deep))

    def test_a_closed_since_window_gives_no_next_line(self):
        self.sessions(12, 3)
        lines, data = self.around_apply(undo=AT + timedelta(days=1))  # the undo ends the since window
        self.assertIn("        -> too few sessions (3 of 10)", lines)
        self.assertFalse(any(x.startswith("Next") for x in lines))
        self.assertIsNone(data["sessions"]["next"])
        self.assertEqual(data["sessions"]["measure"]["split"]["bounded_by"]["after"]["kind"], "undo")

    def test_an_old_apply_gives_no_next_line(self):
        old = AT - timedelta(days=40)  # its 30 days are over
        self.sessions(12, 3, at=old)
        lines, data = self.around_apply(at=old)
        self.assertIn("        -> too few sessions (3 of 10)", lines)
        self.assertIsNone(data["sessions"]["next"])

    def test_ten_on_each_side_give_a_change_and_no_next_line(self):
        self.sessions(12, 11)
        lines, data = self.around_apply()
        i = lines.index("Sessions (~/projects/app, opus 5.5 only)")
        self.assertEqual(lines[i + 1:i + 4], [
            "        first-call input median 92,750 [91,375-94,125] n=12",
            "        -> median 82,000 [81,000-83,000] n=11  -10,750 lower",
            "A change in median is not proof of cause: task mix and connectors move it."])
        self.assertFalse(any(x.startswith("Next") for x in lines))
        self.assertIsNone(data["sessions"]["next"])
        self.assertEqual(data["sessions"]["measure"]["rows"][0]["change"]["first_call"]["state"], "lower")
        # A lower measured value is not green: only the estimated stack change can be.
        out, _ = self.f.cli("diff", "--root", str(self.f.app), "--color", "always")
        self.assertNotIn("\x1b", "\n".join(out.splitlines()[i:]))

    def test_few_sessions_since_are_marked_in_both_languages(self):
        self.sessions(12, 6)
        lines, _data = self.around_apply("--lang", "ko")
        i = lines.index("세션 (~/projects/app, opus 5.5만)")
        self.assertEqual(lines[i + 1:i + 3], [
            "        첫 호출 입력 중앙값 92,750 [91,375-94,125] n=12",
            "        -> 중앙값 81,000 [80,500-81,500] n=6  (세션 적음)"])
        self.assertEqual(lines[-2], f"다음    walle measure --around {APPLY} (이후 세션 10개 뒤)")
        self.assertTrue(all(brief.width(x) <= 80 for x in lines[:-1]), lines)
        out, _ = self.f.diff()
        self.assertIn("        -> median 81,000 [80,500-81,500] n=6  (few sessions)", out.splitlines())

    def test_the_newest_apply_with_a_time_is_the_split(self):
        self.sessions(12, 3)
        self.f.scan()
        self.change_row("20261015-000000", AT - timedelta(days=6))
        self.change_row()
        self.change_row("zz-apply", None)  # an apply line without a time cannot split
        self.f.scan()
        out, data = self.f.diff()
        s = data["sessions"]
        self.assertEqual((s["split_at"], s["ref"]), ("apply", APPLY))
        self.assertEqual(s["measure"]["split"]["bounded_by"]["before"]["id"], "20261015-000000")
        self.assertIn("        zz-apply (plan 20261021-041703, 1 changed)", out)

    def test_no_apply_splits_at_the_time_of_the_newest_scan(self):
        now = datetime.now().astimezone()
        for i in range(7):
            claude_session(self.transcripts, f"n{i:02d}", self.f.app, measure.utc(now - timedelta(hours=1 + i)),
                           total=70000 + 1000 * i)
        self.f.scan()
        b = self.f.scan()
        out, data = self.f.diff()
        lines = out.splitlines()
        at = json.loads(Path(b["path"]).read_text())["generated"]
        s = data["sessions"]
        self.assertEqual((s["split_at"], s["ref"], s["next"]["around"]), ("scan", at, at))
        self.assertEqual(s["measure"]["split"]["kind"], "time")
        i = lines.index("Sessions (~/projects/app, opus 5.5 only)")
        self.assertEqual(lines[i + 1:i + 3], [
            "        first-call input median 73,000 [71,500-74,500] n=7  (few sessions)",
            "        -> too few sessions (0 of 10)"])
        self.assertEqual(lines[-2], f"Next    walle measure --around {at} (after 10 sessions)")
        self.assertEqual(measure.split_point(self.f.out, at)["windows"], s["measure"]["split"]["windows"])

    def test_no_session_of_the_folder_in_the_windows(self):
        self.sessions(3, 0, folder=self.web, tag="w")
        lines, data = self.around_apply()
        self.assertIn("No sessions for ~/projects/app in these windows.", lines)
        self.assertEqual(lines[-2], f"Next    walle measure --around {APPLY} (after 10 sessions)")
        self.assertEqual(data["sessions"]["measure"]["rows"], [])


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
