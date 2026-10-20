"""scan.html (design brief 3.8, 4, 7, 9): page data snapshot, self-contained page, colour contrast."""

import io
import json
import os
import re
import sys
import unittest
from contextlib import redirect_stdout
from importlib import resources
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from real0817 import Tree as RealTree  # noqa: E402

from walle import cli, report_html, runs  # noqa: E402
from walle.scan import scan  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "report_html_real0817.json"
TEMPLATE = resources.files("walle") / "report.html"


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
    return {"budget": D["budget"], "default": D["default"], "views": views, "items": dict(sorted(items.items()))}


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = RealTree()
        cls.data = scan([cls.t.projects], cls.t.home)
        cls.data["run"]["id"] = "20261020-081752"
        cls.page = report_html.render(cls.data, None, cls.t.home)
        cls.D = report_html.page_data(cls.data, None, cls.t.home)

    @classmethod
    def tearDownClass(cls):
        cls.t.close()

    def test_page_data_matches_the_snapshot(self):
        # The memory folder name holds a slug of the random temp folder.
        got = json.loads(re.sub(r"tmp[A-Za-z0-9_-]*?-+home", "tmpX-home", json.dumps(snapshot_of(self.D), ensure_ascii=False)))
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


class CliTest(unittest.TestCase):
    def setUp(self):
        self.t = RealTree()
        self.out = self.t.home.parent / "runs"

    def tearDown(self):
        self.t.close()

    def run_cli(self, *args):
        with redirect_stdout(io.StringIO()), mock.patch("walle.cli.scan", lambda roots: scan(roots, self.t.home)):
            self.assertEqual(cli.main(["--out", str(self.out), "scan", *args]), 0)

    def test_scan_writes_scan_html_with_history_baseline_and_measure(self):
        app = str(self.t.projects / "runup")
        self.run_cli(app, "--label", "before")
        (self.out / "20990101-000000").mkdir()
        (self.out / "20990101-000000" / "eve.json").write_text(json.dumps(
            {"sessions": 3, "groups": [{"group": "runup", "control": False, "n": 3, "baseline_median": 50000}]}))
        self.run_cli(app)
        rows = runs.read_index(self.out)
        latest = Path(rows[-1]["path"]).parent
        page = (latest / "scan.html").read_text(encoding="utf-8")
        m = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S)
        D = json.loads(m.group(1))
        self.assertEqual(D["run"]["baseline"], rows[0]["id"])
        self.assertEqual([h["id"] for h in D["history"]], [r["id"] for r in rows])
        self.assertEqual(D["history"][0]["label"], "before")
        self.assertEqual(D["eve_ref"]["id"], "20990101-000000")


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
         ("bar-owned", "sunk"), ("bar-memory", "sunk"), ("bar-shared", "sunk"), ("orange", "sunk"),
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
