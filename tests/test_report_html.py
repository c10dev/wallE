"""scan.html (design brief 3.8, 4, 7, 9): page data snapshot, self-contained page, colour contrast."""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from importlib import resources
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from real0817 import Tree as RealTree  # noqa: E402
from sessions import claude_session  # noqa: E402

from walle import cli, eve, measure, report_html, runs  # noqa: E402
from walle.scan import scan  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "report_html_real0817.json"
TEMPLATE = resources.files("walle") / "report.html"
AT = "2026-10-21T09:00:00Z"


@contextmanager
def tree_only(root: Path):
    """Keep walle eve inside a test tree. The tree lives in TMPDIR, which walle eve treats as scratch, and
    walle eve looks for CLAUDE.md and .git in every folder above a session folder. So here /tmp is not
    scratch, and a CLAUDE.md or a checkout above root does not count."""
    find, is_file = eve.find_checkout, Path.is_file

    def inside(p: Path) -> bool:
        return p == root or root in p.parents

    def find_checkout(start: Path):
        got = find(start)
        return got if got[0] is None or inside(got[0]) else (None, None, False)

    def tree_is_file(p: Path) -> bool:
        return is_file(p) and (p.name != "CLAUDE.md" or inside(p))

    with mock.patch("walle.eve.SCRATCH_ROOTS", ()), mock.patch("walle.eve.find_checkout", find_checkout), \
            mock.patch.object(Path, "is_file", tree_is_file):
        yield


def day(d: int, h: int) -> str:
    return f"2026-10-{d:02d}T{h:02d}:00:00Z"


def session(cwd: Path, start: str, total: int, model: str = "claude-opus-5-5") -> eve.Session:
    return eve.Session(f"{cwd}/{start}.jsonl", str(cwd), start, model, total, 1, 1, 2, 0, 1, 5, total)


def sessions_of(t: RealTree) -> list:
    """runup: opus 12 before AT and 11 since, haiku 6 before (few), sonnet 3 since (too few).
    atc/controller: opus 7 before (few)."""
    runup, ctl = t.projects / "runup", t.projects / "atc" / "controller"
    ss = [session(runup, day(10 + i % 10, i % 9), 90000 + 500 * i) for i in range(12)]
    ss += [session(runup, day(21, 10 + i % 9), 80000 + 400 * i) for i in range(11)]
    ss += [session(runup, day(15 + i, 3), 70000 + 100 * i, "claude-haiku-4-5-20251001") for i in range(6)]
    ss += [session(runup, day(21, 10 + i), 60000, "claude-sonnet-5-5") for i in range(3)]
    ss += [session(ctl, day(15 + i, 3), 50000) for i in range(7)]
    return ss


def write_eve(out: Path, rid: str, folder: Path, sessions: list, around: str | None = None, across: bool = False) -> dict:
    """A schema 2 eve.json as walle eve writes it: the all-folder groups and the measure block of one folder.
    The runs folder sits in the test tree, next to home."""
    with tree_only(out.parent):
        split = measure.split_point(out, around) if around else None
        block = eve.measure_block(sessions, "claude", folder, split, across=across)
        data = {"schema": 2, **eve.summarize(sessions),
                "run": {"id": rid, "kind": "measure", "agent": "claude", "folder": str(folder), "around": around},
                "measure": block}
    (out / rid).mkdir(parents=True)
    (out / rid / "eve.json").write_text(json.dumps(data))
    return data


def snapshot_of(D: dict) -> dict:
    """The parts of the page data that must not change by accident.

    Times, run ids and the rules hash are left out. Item ids hash the file path, and the
    temp folder name is random, so items are keyed by location and label.
    """
    name = {i: f"{it['loc']['file'].split('/home/', 1)[-1]}:{it['loc']['line']} {it['label']}" for i, it in D["items"].items()}
    views = {}
    for k, v in sorted(D["views"].items()):
        views[k] = {
            "verdict": v["verdict"], "tokens": v["tokens"], "sentence": v["sentence"], "sums": v["sums"],
            "counts": v["counts"], "start": [name[i] for i in v["start"]], "rest": len(v["rest"]),
            "file_copy": len(v["file_copy"]), "shared": [(r["file"], r["findings"]) for r in v["shared"]],
            "copies": len(v["copies"]),
            "layers": [(L["label"], L["rel"], L["tokens"], L["own"], L["share"]) for L in v["layers"]],
        }
    items = {name[i]: {"verb": it["verb"], "cert": it["cert"], "impact": it["impact"], "reason": it["reason"],
                       "blocks": [b["head"] for b in it["blocks"]]}
             for i, it in D["items"].items()}
    return {"budget": D["budget"], "default": D["default"], "views": views, "items": dict(sorted(items.items())),
            "measure": D["measure"], "eve_ref": D["eve_ref"]}


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = RealTree()
        cls.data = scan([cls.t.projects], cls.t.home)
        cls.data["run"]["id"] = "20261020-081752"
        cls.page = report_html.render(cls.data, None, cls.t.home)
        cls.D = report_html.page_data(cls.data, None, cls.t.home)
        # The snapshot page has a schema 2 measure run of runup with a before and a since side.
        out = cls.t.home.parent / "runs"
        write_eve(out, "20261021-120000", cls.t.projects / "runup", sessions_of(cls.t), around=AT)
        with tree_only(cls.t.home.parent):
            cls.DM = report_html.page_data(cls.data, out, cls.t.home)

    @classmethod
    def tearDownClass(cls):
        cls.t.close()

    def test_page_data_matches_the_snapshot(self):
        # The memory folder name holds a slug of the random temp folder. Replace that exact slug, so a TMPDIR
        # that has "home" in its path does not change the result.
        root = re.sub(r"[^A-Za-z0-9]", "-", str(self.t.home.parent))
        got = json.loads(json.dumps(snapshot_of(self.DM), ensure_ascii=False).replace(root + "-home", "-tmpX-home"))
        if os.environ.get("WALLE_UPDATE_SNAPSHOTS"):
            SNAPSHOT.parent.mkdir(exist_ok=True)
            SNAPSHOT.write_text(json.dumps(got, ensure_ascii=False, indent=1) + "\n")
        self.assertEqual(got, json.loads(SNAPSHOT.read_text()))

    def test_verdicts_and_sentences(self):
        V = self.D["views"]
        self.assertEqual(self.D["default"], "~/projects@claude")
        self.assertEqual(V["runup@claude"]["verdict"], "LEAN")
        self.assertEqual(V["atc/controller@claude"]["verdict"], "OVER BUDGET")
        self.assertTrue(V["atc/controller@claude"]["sentence"].startswith("~26,500 est. tokens load before your first prompt (budget 15,000). Largest: "))
        first = self.D["items"][V["atc/controller@claude"]["start"][0]]
        self.assertEqual((first["verb"], first["label"]), ("MOVE", "large layer"))
        for it in self.D["items"].values():
            self.assertTrue(it["label"] and it["reason"], it["id"])

    def test_page_is_one_file_with_no_requests(self):
        p = self.page
        self.assertTrue(p.startswith("<!doctype html>"))
        self.assertNotRegex(p, r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""")
        self.assertNotRegex(p, r"url\(\s*['\"]?(?:https?:)?//")
        self.assertNotIn("@import", p)
        self.assertNotRegex(p, r"<link[^>]+stylesheet")
        self.assertEqual(p.count("src:url(data:font/woff2;base64,"), 3)
        for marker in ("/*__FONTS__*/", "/*__DATA__*/", "/*__TITLE__*/", "/*__LANG__*/"):
            self.assertNotIn(marker, p)
        self.assertIn('<html lang="en">', p)
        self.assertIn("<title>walle scan · ", p)

    def test_no_film_name_and_no_mockup_text(self):
        for s in (self.page, TEMPLATE.read_text(encoding="utf-8")):
            self.assertIsNone(re.search(r"WALL[-·]E|Wall-E|Pixar|Disney|inspired by", s, re.I))
            self.assertNotIn("Mockup built from", s)
            self.assertNotIn("c18d7c", s)

    def test_data_cannot_close_the_script(self):
        data = json.loads(json.dumps(self.data))
        data["items"][0]["locations"][0]["text"] = "</script><b>x"
        page = report_html.render(data, None, self.t.home)
        self.assertNotIn("</script><b>", page)

    def test_first_run_has_no_baseline_and_no_measure(self):
        self.assertIsNone(self.D["run"]["baseline"])
        self.assertIsNone(self.D["eve_ref"])
        self.assertEqual([r["id"] for r in self.D["history"]], ["20261020-081752"])
        self.assertEqual(self.D["features"], {"keep": False})
        self.assertIn('if (D.eve_ref) html += `<section id="measure"', TEMPLATE.read_text(encoding="utf-8"))

    def test_schema_1_is_refused(self):
        with self.assertRaises(ValueError):
            report_html.render({k: v for k, v in self.data.items() if k not in ("items", "triage")})

    def test_rounding_matches_the_page(self):
        js = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("same rounding as walle/strings.py round_tokens()", js)
        f = lambda n: report_html.fmt(n, 8000)
        self.assertEqual([f(0), f(689), f(7951), f(7999), f(8000), f(12732)], ["~0", "~690", "~7,950", "~7,990", "~8,000", "~12,700"])


class MeasureTest(unittest.TestCase):
    """The Measure section (brief 3.8.6): panels per folder and model family from a schema 2 eve.json, and the
    folder group rows of an eve.json without a measure block."""

    @classmethod
    def setUpClass(cls):
        cls.t = RealTree()
        cls.data = scan([cls.t.projects], cls.t.home)
        cls.sessions = sessions_of(cls.t)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.t.close()

    def out(self) -> Path:
        MeasureTest.n += 1
        return self.t.home.parent / f"runs{self.n}"

    def page(self, out: Path) -> dict:
        with tree_only(self.t.home.parent):
            return report_html.page_data(self.data, out, self.t.home)

    def test_one_panel_per_model_family_with_before_and_since(self):
        out = self.out()
        write_eve(out, "20261021-120000", self.t.projects / "runup", self.sessions, around=AT)
        D = self.page(out)
        ref, M = D["eve_ref"], D["measure"]["runup@claude"]
        self.assertEqual((ref["schema"], ref["folder"], ref["sides"]), (2, "~/projects/runup", ["before", "after"]))
        self.assertEqual((ref["split"]["kind"], ref["split"]["at"], ref["split"]["apply"]), ("time", AT, None))
        self.assertEqual((ref["min_show"], ref["min_compare"], ref["across"]), (5, 10, None))
        self.assertEqual((M["state"], M["source"]), ("panels", "folder"))
        self.assertEqual([r["family"] for r in M["rows"]], ["opus 5.5", "haiku 4.5", "sonnet 5.5"])
        opus, haiku, sonnet = M["rows"]
        self.assertEqual(opus["sides"]["before"], {"sessions": 12, "n": 12, "median": 92750, "p25": 91375, "p75": 94125, "few": False})
        self.assertEqual((opus["sides"]["after"]["n"], opus["sides"]["after"]["median"]), (11, 82000))
        self.assertEqual((opus["change"]["state"], opus["change"]["delta"]), ("lower", -10750))
        # 5 to 9 sessions: a value marked few (a hollow dot); under 5: no value at all.
        self.assertEqual((haiku["sides"]["before"]["n"], haiku["sides"]["before"]["few"]), (6, True))
        self.assertIsNone(haiku["sides"]["after"]["median"])
        self.assertEqual(haiku["change"]["state"], "too_few")
        self.assertEqual((sonnet["sides"]["after"]["n"], sonnet["sides"]["after"]["median"]), (3, None))
        # A folder the run did not measure gets its folder group row, all model families together.
        ctl = D["measure"]["atc/controller@claude"]
        self.assertEqual((ctl["state"], ctl["group"], ctl["row"]["n"]), ("row", "atc/controller", 7))
        self.assertEqual(D["measure"]["~/projects@claude"]["state"], "none")
        self.assertNotIn("vocado_RN@codex", D["measure"])
        # Measured values stay out of the verdict and Start here.
        P = report_html.page_data(self.data, None, self.t.home)
        self.assertEqual({k: (v["verdict"], v["start"]) for k, v in D["views"].items()},
                         {k: (v["verdict"], v["start"]) for k, v in P["views"].items()})

    def test_across_folders_rows_give_other_folders_panels(self):
        out = self.out()
        write_eve(out, "20261021-120000", self.t.projects / "runup", self.sessions, around=AT, across=True)
        D = self.page(out)
        ctl = D["measure"]["atc/controller@claude"]
        self.assertEqual((ctl["state"], ctl["source"]), ("panels", "across"))
        self.assertEqual([(r["family"], r["sides"]["before"]["n"], r["sides"]["before"]["few"]) for r in ctl["rows"]],
                         [("opus 5.5", 7, True)])
        self.assertEqual(sorted({r["folder"] for r in D["eve_ref"]["across"]}), ["atc/controller", "runup"])
        self.assertTrue(all("change" in r for r in D["eve_ref"]["across"]))

    def test_a_period_has_one_side_and_no_change(self):
        out = self.out()
        write_eve(out, "20261021-120000", self.t.projects / "runup", self.sessions)
        D = self.page(out)
        M = D["measure"]["runup@claude"]
        self.assertEqual((D["eve_ref"]["sides"], D["eve_ref"]["split"]), (["period"], None))
        self.assertEqual([(r["family"], r["sides"]["period"]["n"]) for r in M["rows"]],
                         [("opus 5.5", 23), ("haiku 4.5", 6), ("sonnet 5.5", 3)])
        self.assertTrue(all("change" not in r for r in M["rows"]))

    def test_the_newest_run_of_a_scanned_folder_wins(self):
        out = self.out()
        elsewhere = self.t.home / "elsewhere"
        write_eve(out, "20261021-120000", self.t.projects / "runup", self.sessions, around=AT)
        write_eve(out, "20261022-120000", elsewhere, [session(elsewhere, day(20, 1), 1000)])
        D = self.page(out)
        self.assertEqual((D["eve_ref"]["id"], D["measure"]["runup@claude"]["state"]), ("20261021-120000", "panels"))
        # No run of a scanned folder: the newest run, which gives the folder group rows.
        out = self.out()
        write_eve(out, "20261022-120000", elsewhere, self.sessions)
        D = self.page(out)
        self.assertEqual(D["eve_ref"]["id"], "20261022-120000")
        self.assertEqual((D["measure"]["runup@claude"]["state"], D["measure"]["runup@claude"]["row"]["n"]), ("row", 32))

    def test_an_eve_json_without_a_measure_block_gives_folder_group_rows(self):
        out = self.out()
        (out / "20261020-082010").mkdir(parents=True)
        (out / "20261020-082010" / "eve.json").write_text(json.dumps({"sessions": 30, "groups": [
            {"group": "runup", "control": False, "n": 25, "baseline_median": 91000, "tool_calls_per_prompt_median": 10,
             "calls_before_first_edit_median": None},
            {"group": "atc/controller", "control": False, "n": 3, "baseline_median": 40000}]}))
        D = self.page(out)
        self.assertEqual((D["eve_ref"]["schema"], D["eve_ref"]["sessions"]), (1, 30))
        self.assertNotIn("split", D["eve_ref"])
        runup = D["measure"]["runup@claude"]
        self.assertEqual((runup["state"], runup["group"], runup["row"]["baseline_median"]), ("row", "runup", 91000))
        self.assertEqual(D["measure"]["atc/controller@claude"]["row"]["n"], 3)
        self.assertEqual(D["measure"]["tennis-web@claude"], {"state": "none", "group": "tennis-web"})

    def test_a_claude_md_above_the_test_tree_changes_nothing(self):
        # The test tree lives in TMPDIR. A CLAUDE.md or a .git in a folder above it is not part of the tree.
        with tempfile.TemporaryDirectory() as tmp:
            anc = Path(tmp).resolve() / "anc"
            (anc / "t").mkdir(parents=True)
            (anc / "CLAUDE.md").write_text("- rule\n")
            with mock.patch.object(tempfile, "tempdir", str(anc / "t")):
                t = RealTree()
            try:
                data = scan([t.projects], t.home)
                out = t.home.parent / "runs"
                write_eve(out, "20261021-120000", t.projects / "runup", sessions_of(t), around=AT, across=True)
                with tree_only(t.home.parent):
                    D = report_html.page_data(data, out, t.home)
            finally:
                t.close()
        self.assertEqual(D["measure"]["~/projects@claude"], {"state": "none", "group": "(control: no project CLAUDE.md)"})
        out = self.out()
        write_eve(out, "20261021-120000", self.t.projects / "runup", self.sessions, around=AT, across=True)
        self.assertEqual(D["measure"], self.page(out)["measure"])

    def test_page_draws_the_panels_by_the_brief(self):
        js = TEMPLATE.read_text(encoding="utf-8")
        body = js[js.index("function drawMeasure()"):js.index("/* ------------------------------------------------------------------ history */")]
        css = js[js.index("/* measure: one panel"):js.index("/* footer */")]
        self.assertIn("width: 8px; height: 8px;", css)  # median dot 8px
        self.assertRegex(css, r"\.mtrack \.wh \{[^}]*height: 2px;")  # IQR whisker 2px
        self.assertIn(".mtrack .dot.hollow", css)
        self.assertIn('v.n < ref.min_compare ? " hollow"', js)  # hollow when n < 10
        self.assertIn("All folders (reference only)", js)
        self.assertIn("sort((a, b) => total(b) - total(a)", js)  # the reference table is sorted by n
        for word in ("delta", "change"):
            self.assertNotIn(word, js[js.index("function measureRefTable"):js.index("function measureIntro")].lower())
        self.assertIn("panelHTML", body)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.t = RealTree()
        self.out = self.t.home.parent / "runs"

    def tearDown(self):
        self.t.close()

    def run_cli(self, *args):
        with redirect_stdout(io.StringIO()), mock.patch("walle.cli.scan", lambda roots: scan(roots, self.t.home)):
            self.assertEqual(cli.main(["--out", str(self.out), "scan", *args]), 0)

    def page_of(self, row: dict) -> dict:
        page = (Path(row["path"]).parent / "scan.html").read_text(encoding="utf-8")
        m = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S)
        return json.loads(m.group(1))

    def test_scan_writes_scan_html_with_history_baseline_and_measure(self):
        app = str(self.t.projects / "runup")
        self.run_cli(app, "--label", "before")
        (self.out / "20990101-000000").mkdir()
        (self.out / "20990101-000000" / "eve.json").write_text(json.dumps(
            {"sessions": 3, "groups": [{"group": "runup", "control": False, "n": 3, "baseline_median": 50000}]}))
        with tree_only(self.t.home.parent):
            self.run_cli(app)
        rows = runs.read_index(self.out)
        D = self.page_of(rows[-1])
        self.assertEqual(D["run"]["baseline"], rows[0]["id"])
        self.assertEqual([h["id"] for h in D["history"]], [r["id"] for r in rows])
        self.assertEqual(D["history"][0]["label"], "before")
        self.assertEqual(D["eve_ref"]["id"], "20990101-000000")
        M = D["measure"][D["default"]]
        self.assertEqual((M["state"], M["group"], M["row"]["baseline_median"]), ("row", "runup", 50000))

    def test_walle_measure_then_walle_scan_shows_panels(self):
        app = self.t.projects / "runup"
        projects = self.t.home / ".claude" / "projects"
        for i in range(12):
            claude_session(projects, f"b{i:02d}", app, day(10 + i % 10, i % 9), total=90000 + 500 * i)
        for i in range(11):
            claude_session(projects, f"a{i:02d}", app, day(21, 10 + i % 9), total=80000 + 400 * i)
        with tree_only(self.t.home.parent), mock.patch("walle.cli.HOME", self.t.home), mock.patch("walle.eve.HOME", self.t.home), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--out", str(self.out), "measure", str(app), "--around", AT,
                                       "--transcripts", str(projects)]), 0)
            self.run_cli(str(app))
        D = self.page_of(runs.read_index(self.out)[-1])
        self.assertEqual((D["eve_ref"]["schema"], D["eve_ref"]["sides"]), (2, ["before", "after"]))
        M = D["measure"][D["default"]]
        self.assertEqual((M["state"], M["source"]), ("panels", "folder"))
        (opus,) = M["rows"]
        self.assertEqual((opus["family"], opus["sides"]["before"]["n"], opus["sides"]["after"]["n"]), ("opus 5.5", 12, 11))
        self.assertEqual((opus["sides"]["before"]["median"], opus["sides"]["after"]["median"]), (92750, 82000))
        self.assertEqual(opus["change"]["state"], "lower")


def _lum(h: str) -> float:
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tokens(block: str) -> dict:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", block))


# Brief 4.1 and 9: text pairs need 4.5:1, marks and controls 3:1.
TEXT = [("fg", "bg"), ("fg", "surface"), ("fg", "sunk"), ("muted", "bg"), ("muted", "surface"), ("muted", "sunk"),
        ("link", "bg"), ("link", "surface"), ("link", "sunk"), ("fg", "mark"),
        ("over", "over-bg"), ("trim", "trim-bg"), ("lean", "lean-bg"), ("safety-ink", "safety"),
        ("fg", "ctl-face"), ("fg", "ctl"), ("muted", "ctl-face"), ("cube-ink", "cube-front")]
MARKS = [("line-strong", "surface"), ("line-strong", "bg"), ("line-strong", "sunk"), ("ctl-edge", "bg"), ("ctl-edge", "surface"),
         ("bar-owned", "sunk"), ("bar-memory", "sunk"), ("bar-shared", "sunk"), ("orange", "sunk"), ("orange", "surface"),
         ("focus", "bg"), ("focus", "surface"), ("cube-front", "surface"), ("leaf", "surface")]


class ContrastTest(unittest.TestCase):
    def setUp(self):
        css = TEMPLATE.read_text(encoding="utf-8")
        light = re.search(r"^:root \{(.*?)^\}", css, re.S | re.M).group(1)
        media = re.search(r':root:not\(\[data-theme="light"\]\) \{(.*?)\}', css, re.S).group(1)
        attr = re.search(r':root\[data-theme="dark"\] \{(.*?)\}', css, re.S).group(1)
        self.modes = {"light": tokens(light), "dark": tokens(media)}
        self.attr = tokens(attr)

    def test_both_dark_blocks_are_the_same(self):
        self.assertEqual(self.modes["dark"], self.attr)

    def test_text_and_marks(self):
        bad = []
        for mode, c in self.modes.items():
            bad += [f"{mode} text {a}/{b} {contrast(c[a], c[b]):.2f}" for a, b in TEXT if contrast(c[a], c[b]) < 4.5]
            bad += [f"{mode} mark {a}/{b} {contrast(c[a], c[b]):.2f}" for a, b in MARKS if contrast(c[a], c[b]) < 3]
        self.assertEqual(bad, [])


class PackageTest(unittest.TestCase):
    def test_fonts_and_licence_ship_with_the_package(self):
        fonts = resources.files("walle") / "fonts"
        for _family, _weight, name in report_html.FONTS:
            self.assertEqual((fonts / name).read_bytes()[:4], b"wOF2")
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", (fonts / "OFL.txt").read_text(encoding="utf-8"))
        toml = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        self.assertIn('"fonts/*.woff2"', toml)
        self.assertIn('"report.html"', toml)


if __name__ == "__main__":
    unittest.main()
