"""The terminal brief, brief-md, scan.md order, walle show and the session alarm (design brief 3.1-3.7, 3.11).

Set WALLE_UPDATE_SNAPSHOTS=1 to write the snapshot files again after a deliberate change.
"""

import io
import json
import os
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from real0817 import Tree as RealTree  # noqa: E402

from walle import brief, cli, report  # noqa: E402
from walle.scan import scan  # noqa: E402
from walle.stack import estimate_tokens  # noqa: E402

SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"
SINGLE = ("runup", "tennis-web", "DesignLAB", "atc/controller", "vocado_RN")
MANY = (None, "atc", "claude-config")  # None: all of projects/
FIXED = "2026-10-20T08:17:52"


class Tty(io.StringIO):
    def isatty(self):
        return True


class Fixture:
    """One RealTree for the module: scans of each folder, with a fixed time and run id."""

    tree = None
    cache: dict = {}

    @classmethod
    def data(cls, name: str | None) -> dict:
        if name not in cls.cache:
            root = cls.tree.projects / name if name else cls.tree.projects
            d = scan([root], cls.tree.home)
            d["generated"] = FIXED
            d["run"]["id"] = "20261020-081752"
            cls.cache[name] = d
        return cls.cache[name]

    @classmethod
    def report_path(cls) -> str:
        return str(cls.tree.home / ".walle" / "runs" / "20261020-081752" / "scan.md")


def setUpModule():
    Fixture.tree = RealTree()


def tearDownModule():
    Fixture.tree.close()
    Fixture.cache.clear()


def render(name, lang="en", **kw):
    t = Fixture.tree
    folder = t.projects / name if name else t.projects
    with mock.patch("walle.brief.__version__", "0.2"):
        return brief.render_brief(Fixture.data(name), lang, home=t.home, report=Fixture.report_path(),
                                  cwd=folder, **kw)


class SnapshotTest(unittest.TestCase):
    def check(self, name: str, text: str):
        path = SNAPSHOTS / name
        if os.environ.get("WALLE_UPDATE_SNAPSHOTS"):
            path.write_text(text)
        self.assertEqual(text, path.read_text())

    def test_english_runup(self):
        self.check("brief_runup_en.txt", render("runup", "en"))

    def test_korean_designlab(self):
        self.check("brief_designlab_ko.txt", render("DesignLAB", "ko"))

    def test_brief_reads_like_the_design(self):
        text = render("runup")
        lines = text.splitlines()
        self.assertEqual(lines[0], "walle 0.2  scan ~/projects/runup  claude  2026-10-20 08:17")
        self.assertEqual(lines[2], "LEAN  ~2,500 est. tokens load before your first prompt (budget 8,000).")
        self.assertIn("1  CHECK  CLAUDE.md:8", text)
        self.assertIn("every-task step? candidate", text)
        self.assertIn("   8 | - Open a PR for every change.", text)
        self.assertEqual(lines[-2], "Next    walle show 1 | walle scan --all")
        self.assertEqual(lines[-1], "~/.walle/runs/20261020-081752/scan.md")  # the last line is the path alone


class LayoutTest(unittest.TestCase):
    def test_80_columns_and_line_caps(self):
        for name in SINGLE:
            for lang in ("en", "ko"):
                text = render(name, lang)
                lines = text.splitlines()
                self.assertTrue(all(brief.width(l) <= 80 for l in lines), f"{name} {lang}")
                n = len(Fixture.data(name)["triage"]["stacks"][f"{Fixture.tree.projects / name}@claude"]["start_here"])
                self.assertLessEqual(len(lines), 30 if n >= 3 else 24, f"{name} {lang}: {len(lines)} lines")
                # ASCII structure only: no box drawing, no block bars, no one-character ellipsis.
                self.assertFalse(set(text) & set("│─┌┐└┘├┤█▌…"), name)

    def test_several_folders_and_all_stay_in_80_columns(self):
        for name in MANY:
            for kw in ({}, {"show_all": True}):
                for lang in ("en", "ko"):
                    text = render(name, lang, **kw)
                    self.assertTrue(all(brief.width(l) <= 80 for l in text.splitlines()), f"{name} {kw} {lang}")
        text = render(None)
        self.assertIn("Start here (by reach", text)
        self.assertIn("atc/controller", text)
        self.assertIn("OVER BUDGET", text)

    def test_all_lists_ids_and_rule_ids(self):
        text = render("vocado_RN", show_all=True)
        data = Fixture.data("vocado_RN")
        codex = data["triage"]["stacks"][f"{Fixture.tree.projects / 'vocado_RN'}@codex"]
        # The other agent's Start here is not on screen in the brief: --all lists it.
        for i in codex["start_here"]:
            self.assertIn(i, text)
        self.assertIn("[repeat]", text)
        self.assertTrue(all(brief.width(l) <= 80 for l in text.splitlines()))

    def test_display_width(self):
        self.assertEqual(brief.width("가벼움"), 6)
        self.assertEqual(brief.width("\x1b[1;32mLEAN\x1b[0m"), 4)
        self.assertEqual(brief.wrap("aaa bbb ccc", 7), ["aaa bbb", "ccc"])
        self.assertEqual(brief.wrap("~/a/bb/cc/MEMORY.md:2", 12), ["~/a/bb/cc/", "MEMORY.md:2"])
        for line in brief.wrap("가나다 라마바 사아자 차카타", 10, "  ", "  "):
            self.assertLessEqual(brief.width(line), 10)

    def test_empty_states(self):
        h = Fixture.tree.home
        lean = {"generated": FIXED, "roots": ["/x"], "artifact_count": 1, "items": [], "stacks": [],
                "triage": {"budget": 8000, "verdicts": {}, "hidden": {"elsewhere": 0}, "stacks": {
                    "/x@claude": {"folder": "/x", "agent": "claude", "verdict": "LEAN", "tokens_est": 2000,
                                  "budget": 8000, "split": {"your_files": 500, "shared": 1500, "skills": 0},
                                  "to_read": 0, "start_here": [], "counts": {}, "groups": {
                                      g: [] for g in brief.GROUPS}}}}}
        text = brief.render_brief(lean, home=h)
        self.assertIn("LEAN  ~2,000 est. tokens (budget 8,000). Checked: duplicates", text)
        self.assertNotIn("Start here", text)
        empty = {**lean, "artifact_count": 0, "triage": {**lean["triage"], "stacks": {}}}
        self.assertIn("No instruction files found in /x.", brief.render_brief(empty, home=h))


class ColorTest(unittest.TestCase):
    def test_no_color_and_never_have_no_escape(self):
        self.assertFalse(brief.use_color("auto", True, {"NO_COLOR": "1"}))
        self.assertFalse(brief.use_color("auto", False, {}))
        self.assertFalse(brief.use_color("never", True, {}))
        self.assertTrue(brief.use_color("auto", True, {}))
        self.assertTrue(brief.use_color("always", False, {"NO_COLOR": "1"}))
        self.assertNotIn("\x1b", render("atc/controller", color=False))

    def test_color_marks_only_words_and_keeps_the_text(self):
        plain, colored = render("atc/controller"), render("atc/controller", color=True)
        self.assertIn("\x1b[1;31mOVER BUDGET\x1b[0m", colored)
        self.assertEqual(brief.ANSI.sub("", colored), plain)


class CliTest(unittest.TestCase):
    """cli.main end to end, with the scan bound to the fixture's home."""

    def setUp(self):
        self.out = Fixture.tree.home.parent / f"runs-{self.id().rsplit('.', 1)[1]}"
        self.folder = Fixture.tree.projects / "runup"
        self.patches = [mock.patch("walle.cli.scan", lambda roots: scan(roots, Fixture.tree.home)),
                        mock.patch("walle.cli.HOME", Fixture.tree.home),
                        mock.patch.object(Path, "cwd", return_value=self.folder)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def run_cli(self, *args, stdout=None, env=None):
        out, err = stdout or io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.dict(os.environ, env or {}):
            code = cli.main(["--out", str(self.out), *args])
        return code, out.getvalue(), err.getvalue()

    def newest(self) -> Path:
        return sorted(p for p in self.out.iterdir() if p.is_dir() and not p.is_symlink())[-1]

    def test_non_tty_prints_the_report_path_alone(self):
        code, text, err = self.run_cli("scan", str(self.folder))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(text, f"{self.newest() / 'scan.md'}\n")
        # scan.html is always written; --html stays as a hidden no-op, and the output is the same.
        code, text, _err = self.run_cli("scan", str(self.folder), "--html")
        self.assertEqual(text, f"{self.newest() / 'scan.md'}\n")

    def test_terminal_default_is_the_brief_and_no_color_has_no_escape(self):
        env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
        with mock.patch.dict(os.environ, env, clear=True):
            _c, colored, _e = self.run_cli("scan", str(self.folder), stdout=Tty())
        self.assertIn("\x1b[", colored)
        _c, text, _e = self.run_cli("scan", str(self.folder), stdout=Tty(), env={"NO_COLOR": "1"})
        self.assertNotIn("\x1b", text)
        self.assertTrue(text.startswith("walle "))
        self.assertIn("Start here", text)
        self.assertEqual(text.splitlines()[-1], str(self.newest() / "scan.html"))  # the report the user opens

    def test_formats(self):
        _c, text, _e = self.run_cli("scan", str(self.folder), "--json")
        self.assertEqual(json.loads(text)["schema"], 2)
        _c, text, _e = self.run_cli("scan", str(self.folder), "--format", "md")
        self.assertEqual(text, (self.newest() / "scan.md").read_text())
        _c, text, _e = self.run_cli("scan", str(self.folder), "--format", "brief-md", "--lang", "ko")
        self.assertTrue(text.startswith("walle: 가벼움 · runup 폴더는"))
        self.assertIn(f"리포트: {self.newest() / 'scan.md'}", text)

    def test_walle_alone_scans_the_current_folder(self):
        code, text, _e = self.run_cli()
        self.assertEqual(code, 0)
        data = json.loads((self.newest() / "scan.json").read_text())
        self.assertEqual(data["roots"], [str(self.folder)])
        self.assertEqual(text, f"{self.newest() / 'scan.md'}\n")

    def test_show_by_number_and_by_id(self):
        self.run_cli("scan", str(self.folder))
        data = json.loads((self.newest() / "scan.json").read_text())
        first = data["triage"]["stacks"][f"{self.folder}@claude"]["start_here"][0]
        code, text, _e = self.run_cli("show", "1")
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith(f"1 -> {first} (run {self.newest().name})"))
        self.assertIn(">  8 | - Open a PR for every change.", text)
        self.assertIn("   5 |", text)  # three lines of context
        self.assertTrue(all(brief.width(l) <= 80 for l in text.splitlines()))
        code, by_id, _e = self.run_cli("show", first)
        self.assertEqual(code, 0)
        self.assertEqual(by_id, text.split("\n", 1)[1])
        self.assertEqual(self.run_cli("show", "9")[0], 2)
        self.assertEqual(self.run_cli("show", "abcdef")[0], 2)

    def test_show_from_a_folder_without_a_scan_asks_for_the_id(self):
        self.run_cli("scan", str(self.folder))
        with mock.patch.object(Path, "cwd", return_value=Fixture.tree.projects / "tennis-web"):
            code, text, err = self.run_cli("show", "1")
        self.assertEqual((code, text), (2, ""))
        self.assertEqual(err, "walle show: no recent scan for this folder; use the id\n")

    def test_show_says_when_the_file_changed(self):
        tree = RealTree()
        try:
            folder = tree.projects / "runup"
            with mock.patch("walle.cli.scan", lambda roots: scan(roots, tree.home)), \
                    mock.patch.object(Path, "cwd", return_value=folder):
                self.run_cli("scan", str(folder))
                md = folder / "CLAUDE.md"
                md.write_text(md.read_text().replace("Open a PR for every change", "Open a PR for each change"))
                code, text, _e = self.run_cli("show", "1")
        finally:
            tree.close()
        self.assertEqual((code, text), (1, "file changed since scan; run walle scan\n"))

    def test_progress_line_only_on_a_terminal_after_a_second(self):
        data, path = Fixture.data("runup"), Path(Fixture.report_path())

        class Args:
            lang, quiet, color, show_all = "en", False, "never", False

        for elapsed, quiet, tty, want in ((1.5, False, True, True), (1.5, True, True, False),
                                          (0.2, False, True, False), (1.5, False, False, False)):
            Args.quiet = quiet
            err = Tty() if tty else io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                brief.print_scan(Args, data, path, "path", elapsed, Fixture.tree.home)
            line = err.getvalue()
            self.assertEqual(bool(line), want, (elapsed, quiet, tty))
            if want:
                self.assertRegex(line, r"^walle: collected \d+ files, compacted \d+ pairs, sorted \d+ items \(1\.5s\)\n$")


class BriefMdTest(unittest.TestCase):
    def test_size_and_content(self):
        for name in SINGLE:
            for lang in ("en", "ko"):
                data = Fixture.data(name)
                text = brief.render_brief_md(data, lang, Fixture.tree.home, Fixture.report_path())
                self.assertLessEqual(estimate_tokens(text), brief.BRIEF_MD_TOKENS, f"{name} {lang}")
                self.assertTrue(text.startswith("walle: "))
                lead, _o = brief.primary(data)
                for n, i in enumerate(lead["start_here"], 1):
                    self.assertIn(f"{n}. **", text)
                    self.assertIn(f"id {i}", text)
        text = brief.render_brief_md(Fixture.data("runup"), "en", Fixture.tree.home, Fixture.report_path())
        self.assertIn("walle: LEAN · runup loads ~2,500 est. tokens before your first prompt (budget 8,000). "
                      "2 items to read in your files.", text)
        self.assertIn("1. **CHECK** `CLAUDE.md:8` · every-task step? · candidate", text)
        self.assertIn("   > 8: - Open a PR for every change.", text)
        self.assertTrue(text.endswith(f"Report: {Fixture.report_path()}\n"))

    def test_long_quotes_shrink_until_the_brief_fits(self):
        data = json.loads(json.dumps(Fixture.data("atc/controller")))
        for it in data["items"]:
            for loc in it["locations"]:
                if loc.get("text"):
                    loc["text"] = "\n".join(("가나다라 " * 60) for _ in range(12))
        text = brief.render_brief_md(data, "ko", Fixture.tree.home, Fixture.report_path())
        self.assertLessEqual(estimate_tokens(text), brief.BRIEF_MD_TOKENS)


class ScanMdTest(unittest.TestCase):
    def test_verdict_and_start_here_in_the_first_25_lines(self):
        for name in (*SINGLE, *MANY):
            data = Fixture.data(name)
            md = report.render_scan(data, home=Fixture.tree.home)
            top = "\n".join(md.splitlines()[:25])
            items = {it["id"]: it for it in data["items"]}
            if brief.is_single(data):
                lead, _o = brief.primary(data)
                ids, word = lead["start_here"], lead["verdict"]
            else:  # several folders: the verdict counts lead
                ids, word = brief.numbered(data), "OVER BUDGET "
            self.assertTrue(ids, name)
            self.assertIn(f"**{word}", top, name)
            for i in ids:
                self.assertIn(f"`{brief.location(items[i], None, Fixture.tree.home)}`", top, name)

    def test_section_order_and_korean_headings(self):
        md = report.render_scan(Fixture.data("DesignLAB"), home=Fixture.tree.home)
        heads = [l for l in md.splitlines() if l.startswith("## ")]
        self.assertEqual(heads, ["## Start here", "## All findings", "## Stacks", "## Measure", "## History",
                                 "## Method and limits"])
        ko = report.render_scan(Fixture.data("DesignLAB"), lang="ko", home=Fixture.tree.home)
        self.assertEqual([l for l in ko.splitlines() if l.startswith("## ")],
                         ["## 여기부터", "## 전체 발견", "## 스택", "## 세션 실측", "## 이력", "## 방법과 한계"])
        self.assertNotIn("⚠", md + ko)
        self.assertNotIn("원인 1", md + ko)


class AlarmTest(unittest.TestCase):
    def alarm(self, folder: Path, env=None, home=None) -> tuple[str, float]:
        env = {"CLAUDE_PLUGIN_OPTION_SESSION_ALARM": "true", "LANG": "en_US.UTF-8", "LC_ALL": "", **(env or {})}
        out = io.StringIO()
        start = time.monotonic()
        with redirect_stdout(out), mock.patch.dict(os.environ, env), \
                mock.patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(folder)}))), \
                mock.patch("walle.cli.HOME", home or Fixture.tree.home):
            self.assertEqual(cli.alarm(), 0)
        return out.getvalue(), time.monotonic() - start

    def test_over_budget_is_one_short_line(self):
        text, secs = self.alarm(Fixture.tree.projects / "atc" / "controller")
        msg = json.loads(text)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(msg, "walle: this folder loads ~16,100 est. tokens of instructions before the first prompt "
                              "(budget 8,000); largest ./CLAUDE.md ~8,100. Mention only if asked about instructions "
                              "or cost.")
        self.assertLessEqual(len(msg), 200)
        self.assertLess(secs, 5)
        ko, _s = self.alarm(Fixture.tree.projects / "atc" / "controller", {"LANG": "ko_KR.UTF-8"})
        self.assertLessEqual(len(json.loads(ko)["hookSpecificOutput"]["additionalContext"]), 200)

    def test_trim_and_lean_print_nothing(self):
        for name in ("DesignLAB", "runup", "tennis-web"):  # TRIM, LEAN, LEAN
            text, _s = self.alarm(Fixture.tree.projects / name)
            self.assertEqual(text, "", name)

    def test_a_long_path_drops_the_largest_layer(self):
        tree = RealTree()
        try:
            # The largest layer is in the parent folder: its path is written from ~, and it is long.
            deep = tree.projects / ("a-very-long-folder-name-" * 2) / "app"
            (deep / ".git").mkdir(parents=True)
            (deep / "CLAUDE.md").write_text("- rule\n" * 6000)
            (deep / "sub").mkdir()
            text, _s = self.alarm(deep / "sub", home=tree.home)
        finally:
            tree.close()
        msg = json.loads(text)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(msg), 200)
        self.assertNotIn("largest", msg)
        self.assertEqual(brief.alarm_text(12732, 8000, "./" + "x" * 300, 8148), brief.alarm_text(12732, 8000, None, 0))

    def test_off_and_broken_input_stay_silent(self):
        text, _s = self.alarm(Fixture.tree.projects / "atc" / "controller",
                              {"CLAUDE_PLUGIN_OPTION_SESSION_ALARM": "false"})
        self.assertEqual(text, "")
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_SESSION_ALARM": "true"}), \
                mock.patch("sys.stdin", io.StringIO("{not json")), mock.patch("walle.cli.claude_stack",
                                                                             side_effect=OSError("boom")):
            self.assertEqual(cli.alarm(), 0)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
