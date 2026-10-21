"""walle eve --around (plan C6, brief step 9) on anonymized synthetic transcripts in a fake home."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sessions import claude_session, codex_rollout  # noqa: E402
from test_keep import tree_hash  # noqa: E402

from walle import cli, eve, measure, runs  # noqa: E402

APPLY = "20261021-090000"
AT = "2026-10-21T09:00:00+00:00"


class Home:
    """A fake home: two repositories, Claude transcripts and Codex rollouts, a runs folder with one apply."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.projects = self.home / ".claude" / "projects"
        self.codex = self.home / ".codex"
        self.app = self.home / "projects" / "app"
        self.web = self.home / "projects" / "web"
        for repo in (self.app, self.web):
            (repo / ".git").mkdir(parents=True)
            (repo / "CLAUDE.md").write_text("- rule\n")
        self.out = root / "walle" / "runs"
        self.patches = [mock.patch("walle.cli.HOME", self.home), mock.patch("walle.eve.HOME", self.home),
                        mock.patch("walle.context.HOME", self.home), mock.patch("walle.compare.HOME", self.home),
                        mock.patch("walle.eve.SCRATCH_ROOTS", ())]
        for p in self.patches:
            p.start()

    def apply_row(self, rid: str = APPLY, when: str = AT, kind: str = "apply", parent: str = "20261021-041703"):
        runs.append_row(self.out, {"id": rid, "kind": kind, "parent_id": parent, "generated": when, "roots": [],
                                   "rows": ["e94298"], "status": "applied"})

    def cli(self, *args, code=0) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            got = cli.main(["--out", str(self.out), *args])
        assert got == code, (args, got, out.getvalue(), err.getvalue())
        return out.getvalue(), err.getvalue()

    def measure(self, *args, code=0) -> tuple[str, dict | None]:
        out, _ = self.cli("measure", *args, "--transcripts", str(self.projects), code=code)
        last = Path(out.strip().splitlines()[-1]) if out.strip() else None
        return out, json.loads((last.parent / "eve.json").read_text()) if last and last.name == "eve.md" else None

    def close(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


def day(d: int, h: int) -> str:
    return f"2026-10-{d:02d}T{h:02d}:00:00Z"


def fill(h: Home):
    """12 opus sessions before the apply and 11 since in app; 3 sonnet since; one resumed; 6 in web."""
    for i in range(12):
        claude_session(h.projects, f"b{i:02d}", h.app, day(10 + i % 10, i % 9), total=90000 + 500 * i,
                       calls=("Read", "Edit"))
    for i in range(11):
        claude_session(h.projects, f"a{i:02d}", h.app, day(21, 10 + i % 9), total=80000 + 400 * i, calls=("Read",))
    for i in range(3):
        claude_session(h.projects, f"s{i:02d}", h.app, day(21, 10 + i), model="claude-sonnet-5-5", total=60000)
    claude_session(h.projects, "r00", h.app, day(21, 12), resumed_from="b00", total=400000)
    for i in range(6):
        claude_session(h.projects, f"w{i:02d}", h.web, day(15 + i, 3), total=50000)


class PartsTest(unittest.TestCase):
    def test_n_rule(self):
        self.assertEqual(measure.stats([1, 2, 3, 4]), {"n": 4, "median": None, "p25": None, "p75": None, "few": False})
        few = measure.stats([10, 20, 30, 40, 50])
        self.assertEqual((few["median"], few["p25"], few["p75"], few["few"]), (30, 20, 40, True))
        full = measure.stats(list(range(1, 11)))
        self.assertEqual((full["median"], full["p25"], full["p75"], full["few"]), (6, 3, 8, False))
        self.assertEqual(measure.stats([1.25] * 6, digits=1)["median"], 1.3)

    def test_change_needs_ten_on_each_side_and_uses_the_overlap_of_the_intervals(self):
        a = measure.stats(list(range(100, 110)))
        self.assertEqual(measure.change(a, measure.stats(list(range(90, 99))))["state"], "too_few")
        self.assertEqual(measure.change(a, measure.stats(list(range(90, 99))))["need"], 1)
        c = measure.change(a, measure.stats(list(range(95, 105))))
        self.assertEqual((c["state"], c["delta"], c["overlap"]), ("within_noise", -5, True))
        self.assertEqual(c["half_iqr"], "lower")  # the brief's half-IQR rule would call it lower
        c = measure.change(a, measure.stats(list(range(80, 90))))
        self.assertEqual((c["state"], c["delta"], c["overlap"]), ("lower", -20, False))
        self.assertEqual(measure.change(a, measure.stats(list(range(130, 140))))["state"], "higher")

    def test_family_keeps_the_version_and_drops_the_date(self):
        cases = {"claude-opus-5-5": "opus 5.5", "claude-haiku-4-5-20251001": "haiku 4.5", "claude-sonnet-5": "sonnet 5",
                 "claude-ocx-opencode-go--muse-spark-1.3-contributor": "claude-ocx-opencode-go--muse-spark-1.3-contributor",
                 "gpt-5.1-codex": "gpt-5.1-codex", "": "unknown"}
        self.assertEqual({k: measure.family(k) for k in cases}, cases)

    def test_times(self):
        self.assertEqual(measure.utc(measure.parse_when("2026-10-21T09:00:00Z")), "2026-10-21T09:00:00Z")
        self.assertIsNotNone(measure.parse_when("2026-10-21").tzinfo)  # a date is local midnight
        self.assertIsNone(measure.parse_when("next week"))


class SplitTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()

    def tearDown(self):
        self.h.close()

    def test_an_apply_id_splits_at_its_time_and_other_changes_end_the_windows(self):
        self.h.apply_row("20261018-090000", "2026-10-18T09:00:00+00:00")
        self.h.apply_row()
        self.h.apply_row("20261023-090000", "2026-10-23T09:00:00+00:00", kind="undo", parent=APPLY)
        sp = measure.split_point(self.h.out, APPLY)
        self.assertEqual((sp["kind"], sp["at"], sp["apply"]["parent_id"]), ("apply", "2026-10-21T09:00:00Z", "20261021-041703"))
        self.assertEqual(sp["windows"]["before"], {"from": "2026-10-18T09:00:00Z", "to": "2026-10-21T09:00:00Z"})
        self.assertEqual(sp["windows"]["after"], {"from": "2026-10-21T09:00:00Z", "to": "2026-10-23T09:00:00Z"})
        self.assertEqual((sp["bounded_by"]["before"]["id"], sp["bounded_by"]["after"]["kind"]), ("20261018-090000", "undo"))
        self.assertEqual(measure.side_of("2026-10-20T00:00:00Z", sp), "before")
        self.assertEqual(measure.side_of("2026-10-24T00:00:00Z", sp), None)

    def test_a_date_and_refusals(self):
        sp = measure.split_point(self.h.out, "2026-10-21T09:00+00:00", days=7)
        self.assertEqual((sp["kind"], sp["apply"], sp["windows"]["before"]["from"]), ("time", None, "2026-10-14T09:00:00Z"))
        with self.assertRaises(measure.SplitError) as e:
            measure.split_point(self.h.out, "20261021-090001")  # a run id that is not an apply
        self.assertEqual(e.exception.key, "measure.not_apply")
        with self.assertRaises(measure.SplitError) as e:
            measure.split_point(self.h.out, "yesterday")
        self.assertEqual(e.exception.key, "measure.bad_split")

    def test_an_apply_found_in_the_backups_folder(self):
        b = self.h.out.parent / "backups" / APPLY
        b.mkdir(parents=True)
        (b / "undo.json").write_text(json.dumps({"version": 1, "id": APPLY, "created": AT, "plan": {"id": "p"},
                                                 "rows": [{"id": "x", "changed": True}], "status": "applied"}))
        self.assertEqual(measure.split_point(self.h.out, APPLY)["apply"]["rows"], ["x"])


class AroundTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()
        fill(self.h)
        self.h.apply_row()

    def tearDown(self):
        self.h.close()

    def test_before_and_since_one_row_per_model_family(self):
        out, data = self.h.measure(str(self.h.app), "--around", APPLY)
        m = data["measure"]
        self.assertEqual(data["run"]["kind"], "measure")
        self.assertEqual(m["folder"]["key"], "app")
        self.assertEqual([r["family"] for r in m["rows"]], ["opus 5.5", "sonnet 5.5"])
        opus, sonnet = m["rows"]
        self.assertEqual((opus["before"]["sessions"], opus["after"]["sessions"]), (12, 11))
        self.assertEqual(opus["before"]["first_call"]["median"], 92750)
        self.assertEqual(opus["after"]["first_call"]["median"], 82000)
        self.assertEqual((opus["change"]["first_call"]["state"], opus["change"]["first_call"]["delta"]), ("lower", -10750))
        self.assertEqual(opus["after"]["calls_before_edit"]["no_edit"], 11)
        # n < 5: no value at all, in the JSON and on screen.
        self.assertEqual(sonnet["after"]["sessions"], 3)
        self.assertIsNone(sonnet["after"]["first_call"]["median"])
        self.assertIn("Too few sessions for a value: sonnet 5.5 (0 before, 3 since)", out)
        # The resumed session and the other folder are not in the rows.
        self.assertEqual(m["left_out"]["resumed"], 1)
        self.assertNotIn("across", m)
        self.assertNotIn("Do not rank folders", out)
        lines = out.splitlines()
        self.assertEqual(lines[0], f"walle measure ~/projects/app --around {APPLY}  claude")
        self.assertIn("change            -10,750 lower", out)
        self.assertIn("models  before: opus 5.5 12 | since: opus 5.5 11, sonnet 5.5 3", out)
        self.assertIn("A change in median is not proof of cause: task mix and connectors move it.", out)
        self.assertTrue(all(len(x) <= 80 for x in lines[:-1]), out)
        # The index line links the run to the apply.
        row = runs.read_index(self.h.out)[-1]
        self.assertEqual((row["kind"], row["parent_id"], row["roots"]), ("measure", APPLY, [str(self.h.app)]))

    def test_few_sessions_and_the_sessions_still_needed(self):
        out, data = self.h.measure(str(self.h.web), "--around", "2026-10-20T00:00Z")
        r = data["measure"]["rows"][0]
        self.assertEqual((r["before"]["sessions"], r["after"]["sessions"]), (5, 1))
        self.assertTrue(r["before"]["first_call"]["few"])
        self.assertIsNone(r["after"]["first_call"]["median"])
        self.assertIn("opus 5.5  before  5         50,000 [50,000-50,000]  (few sessions)", out)
        self.assertIn("          since   1         too few sessions (1 of 10)", out)
        self.assertIn("Need 10 sessions on each side. 14 more to go.", out)
        out, data = self.h.measure(str(self.h.web))
        self.assertEqual(data["measure"]["rows"][0]["period"]["first_call"]["few"], True)
        self.assertIn("50,000 [50,000-50,000]  (few sessions)", out)

    def test_across_folders_only_with_the_flag(self):
        out, data = self.h.measure(str(self.h.app), "--around", APPLY, "--across-folders")
        self.assertEqual(sorted({r["folder"] for r in data["measure"]["across"]}), ["app", "web"])
        self.assertIn("Folders differ in model, connectors and task mix. Do not rank folders by this.", out)

    def test_same_transcripts_give_the_same_output(self):
        a_out, a = self.h.measure(str(self.h.app), "--around", APPLY, "--across-folders")
        b_out, b = self.h.measure(str(self.h.app), "--around", APPLY, "--across-folders")
        self.assertEqual(json.dumps(a["measure"], sort_keys=True), json.dumps(b["measure"], sort_keys=True))
        self.assertEqual(a_out.splitlines()[:-1], b_out.splitlines()[:-1])
        self.assertNotEqual(a["run"]["id"], b["run"]["id"])
        # The legacy all-folder summary that scan.html reads is still there.
        self.assertEqual({g["group"] for g in a["groups"]}, {"app", "web"})

    def test_korean_and_errors(self):
        out, _ = self.h.measure(str(self.h.app), "--around", APPLY, "--lang", "ko")
        self.assertIn("이 폴더만 본다. 모델 계열마다 한 행이다.", out)
        _out, err = self.h.cli("measure", str(self.h.app), "--around", "20261021-090001", code=2)
        self.assertIn("is not an apply in", err)
        _out, err = self.h.cli("measure", str(self.h.app), "--since", "soon", code=2)
        self.assertIn("is not a date", err)

    def test_nothing_is_written_into_the_folder(self):
        before = tree_hash(self.h.home / "projects")
        self.h.measure(str(self.h.app), "--around", APPLY)
        self.assertEqual(tree_hash(self.h.home / "projects"), before)
        _out, err = self.h.cli("--out", str(self.h.app / "runs"), "measure", str(self.h.app), code=2)
        self.assertIn("is inside", err)
        self.assertIn("the folder this command measures", err)
        _out, err = self.h.cli("--out", str(self.h.app / "runs"), "context", "--compare", APPLY, "--root", str(self.h.app),
                               "--transcripts", str(self.h.projects), code=2)
        self.assertIn("the folder this command measures", err)
        self.assertFalse((self.h.app / "runs").exists())

    def test_no_transcripts(self):
        out, _ = self.h.cli("measure", str(self.h.app), "--transcripts", str(self.h.home / "none"))
        self.assertIn("No session transcripts in ~/none.", out)


class DeletedWorktreeTest(unittest.TestCase):
    """Sessions of worktrees that are gone count for their repository (the desktop app's worktrees folder)."""

    def setUp(self):
        self.h = Home()
        self.wts = self.h.home / "projects" / "worktrees"

    def tearDown(self):
        self.h.close()

    def worktree(self, path: Path, repo: Path) -> Path:
        path.mkdir(parents=True)
        (repo / ".git" / "worktrees" / path.name).mkdir(parents=True)
        (path / ".git").write_text(f"gitdir: {repo / '.git' / 'worktrees' / path.name}\n")
        return path

    def test_a_live_worktree_or_the_folder_name_names_the_repository(self):
        box = self.wts / "app-0123456789abcdef"
        self.worktree(box / "w1", self.h.app)
        self.assertEqual(eve.folder_key(str(box / "w1")), "app")
        self.assertEqual(eve.folder_key(str(box / "w2-deleted")), "app")
        self.assertEqual(eve.folder_key(str(box / "w2-deleted" / "src")), "app")
        # The <repo>-<16 hex> folder is gone too.
        self.assertEqual(eve.folder_key(str(self.wts / "web-fedcba9876543210" / "w3")), "web")
        # Worktrees of two repositories in one folder: a deleted one stays as it is.
        mixed = self.wts / "mixed"
        self.worktree(mixed / "a1", self.h.app)
        self.worktree(mixed / "b1", self.h.web)
        self.assertEqual(eve.folder_key(str(mixed / "gone")), str(mixed / "gone"))

    def test_their_sessions_count_in_the_folder_rows(self):
        box = self.wts / "app-0123456789abcdef"
        self.worktree(box / "w1", self.h.app)
        for i in range(5):
            claude_session(self.h.projects, f"d{i}", box / f"gone-{i}", day(12, i), total=70000)
        claude_session(self.h.projects, "l0", self.h.app, day(12, 9), total=70000)
        _out, data = self.h.measure(str(self.h.app), "--across-folders")
        self.assertEqual(data["measure"]["rows"][0]["period"]["sessions"], 6)
        self.assertEqual({r["folder"] for r in data["measure"]["across"]}, {"app"})


class CodexAroundTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()
        self.h.apply_row()

    def tearDown(self):
        self.h.close()

    def test_codex_threads_first_call_only(self):
        for i in range(10):
            codex_rollout(self.h.codex, f"00000000-0000-7000-8000-0000000001{i:02d}", self.h.app, day(11 + i, 2),
                          total=30000 + 100 * i)
        for i in range(10):
            codex_rollout(self.h.codex, f"00000000-0000-7000-8000-0000000002{i:02d}", self.h.app, day(21, 10 + i),
                          total=20000 + 100 * i)
        codex_rollout(self.h.codex, "00000000-0000-7000-8000-000000000301", self.h.app, day(21, 11), resumed=True)
        codex_rollout(self.h.codex, "00000000-0000-7000-8000-000000000302", self.h.app, day(21, 11), subagent=True)
        out, _ = self.h.cli("measure", str(self.h.app), "--around", APPLY, "--agent", "codex",
                            "--transcripts", str(self.h.codex))
        data = json.loads((Path(out.strip().splitlines()[-1]).parent / "eve.json").read_text())
        m = data["measure"]
        self.assertEqual(m["metrics"], ["first_call"])
        r = m["rows"][0]
        self.assertEqual((r["family"], r["before"]["sessions"], r["after"]["sessions"]), ("gpt-5.1-codex", 10, 10))
        self.assertEqual(r["change"]["first_call"]["state"], "lower")
        self.assertEqual((m["left_out"]["resumed"], m["left_out"]["files"]["subagent"]), (1, 1))
        self.assertIn("Codex threads: first-call input only.", out)
        self.assertEqual(data["groups"], [])


if __name__ == "__main__":
    unittest.main()
