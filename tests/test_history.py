"""The run history page runs/index.html (brief 3.13, 3.8.7): snapshot, tokens, links, rewrite after each run.

Set WALLE_UPDATE_SNAPSHOTS=1 to write the snapshot file again after a deliberate change.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runs import RunsFixture  # noqa: E402

from walle import history, runs  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "history_index.html"
A, B = "a" * 40, "b" * 40


def index_rows(root: str) -> list[dict]:
    scan = {"kind": "scan", "roots": [root], "estimator": "bytes-v2", "counting_revision": 3, "parent_id": None}
    return [
        {**scan, "id": "20261020-090000", "label": "baseline", "rules": A, "stack_sum": 31000, "open": 4, "kept": 1},
        {**scan, "id": "20261020-100000", "label": None, "rules": B, "stack_sum": 30950, "open": 3, "kept": 1},
        {**scan, "id": "20261020-110000", "label": "<script>x</script>", "rules": B, "estimator": "bytes-v1",
         "stack_sum": 16100, "open": 3, "kept": 1},
        {"id": "20261020-120000", "kind": "trim", "label": "first trim", "roots": [], "rows": 61, "estimator": "bytes-v2"},
        {"id": "20261021-090000", "kind": "apply", "parent_id": "20261020-120000", "roots": [], "rows": ["e94298", "9bd380"],
         "status": "applied", "generated": "2026-10-21T09:00:00"},
        {"id": "20261021-091000", "kind": "context", "roots": [], "agent": "claude", "estimator": "bytes-v2",
         "counting_revision": 1, "session": "630f787e-f784-445a-9d87-19be85313a8b"},
        {"id": "20261021-100000", "kind": "measure", "parent_id": "20261021-090000", "roots": [root], "agent": "claude",
         "around": "20261021-090000"},
        {"id": "20261021-100500", "kind": "compare", "parent_id": "20261021-090000", "roots": [root], "agent": "claude",
         "refs": ["20261021-090000"], "estimator": "bytes-v2", "counting_revision": 1},
        {"id": "20261021-110000", "kind": "undo", "parent_id": "20261021-090000", "roots": [], "status": "restored"},
        {"id": "20261021-120000", "kind": "diff", "parent_id": "20261020-110000", "baseline_id": "20261020-090000",
         "roots": [root]},
    ]


class PageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.out = self.home / ".walle" / "runs"
        self.out.mkdir(parents=True)
        self.root = str(self.home / "projects" / "app")
        rows = index_rows(self.root)
        (self.out / runs.INDEX).write_text("".join(json.dumps(r) + "\n" for r in rows))
        runs.save_pins(self.out, {runs.roots_key([self.root]): "20261020-090000"})
        files = {"20261020-090000": "scan.html", "20261020-100000": "scan.md", "20261020-120000": "plan.md",
                 "20261021-090000": "apply.md", "20261021-091000": "context.md", "20261021-100000": "eve.md",
                 "20261021-100500": "compare.md", "20261021-120000": "diff.md"}
        for rid, name in files.items():
            (self.out / rid).mkdir()
            (self.out / rid / name).write_text("x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_matches_the_snapshot(self):
        page = history.render(self.out, self.home, fonts=False)
        page = page.replace(history.tokens_css(), "/* tokens of report.html */")
        if os.environ.get("WALLE_UPDATE_SNAPSHOTS"):
            SNAPSHOT.parent.mkdir(exist_ok=True)
            SNAPSHOT.write_text(page)
        self.assertEqual(page, SNAPSHOT.read_text())

    def test_rows_dividers_pins_and_applies(self):
        page = history.render(self.out, self.home, fonts=False)
        # Newest first; every run has a row; a run's report is a relative link.
        self.assertLess(page.index("20261021-120000"), page.index("20261020-090000"))
        self.assertIn('<a href="20261020-090000/scan.html">scan.html</a>', page)
        self.assertIn('<a href="20261021-100500/compare.md">compare.md</a>', page)
        # A divider above the scan whose rules changed, and above the one whose estimator changed.
        self.assertIn("rules changed aaaa -&gt; bbbb: open counts not comparable", page)
        self.assertIn("estimator changed bytes-v2 -&gt; bytes-v1: token totals not comparable", page)
        # Each divider sits between the two scans it compares: below the newer one in this newest-first table.
        at = page.index
        self.assertLess(at('id">20261020-110000'), at("estimator changed"))
        self.assertLess(at("estimator changed"), at('id">20261020-100000'))
        self.assertLess(at('id">20261020-100000'), at("rules changed"))
        self.assertLess(at("rules changed"), at('id">20261020-090000'))
        self.assertIn('<span class="tag">pinned</span>', page)
        self.assertIn("<li><code>20261020-090000</code> · ~/projects/app</li>", page)
        # The apply: its rows, its undo, the two commands, and links to the runs made around it.
        self.assertIn("<code>walle context --compare 20261021-090000</code>", page)
        self.assertIn("<code>walle eve --around 20261021-090000</code> (after 10 sessions)", page)
        self.assertIn('<a href="20261021-100000/eve.md">measure 20261021-100000</a>', page)
        self.assertIn("undo <code>20261021-110000</code>", page)
        # Estimator and counting revision for each kind that has one; measured runs carry no estimate.
        self.assertIn('<td data-l="Estimator" class="mono">measured</td>', page)
        self.assertIn("~31,000", page)
        self.assertNotIn("<script>x", page)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", page)

    def test_tokens_come_from_report_html(self):
        css = history.tokens_css()
        self.assertIn("--bg:#E7E5E0", css)
        self.assertIn('@media (prefers-color-scheme: dark)', css)
        self.assertIn(':root[data-theme="dark"]', css)
        page = history.render(self.out, self.home)
        self.assertIn("@font-face{font-family:'IBM Plex Mono'", page)
        self.assertNotIn("<script", page.replace("&lt;script", ""))
        self.assertNotIn("http", page.replace('http-equiv', ''))


class DividerAndBadLineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "runs"
        self.out.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def index(self, *rows):
        (self.out / runs.INDEX).write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))

    def test_the_divider_is_between_the_scans_whose_rules_differ(self):
        scan = {"kind": "scan", "roots": ["/r"], "estimator": "bytes-v2", "counting_revision": 3}
        self.index({**scan, "id": "s1", "rules": A}, {**scan, "id": "s2", "rules": B}, {**scan, "id": "s3", "rules": B})
        page = history.render(self.out, Path(self.tmp.name), fonts=False)
        order = [page.index(x) for x in ('id">s3', 'id">s2', "rules changed aaaa -&gt; bbbb", 'id">s1')]
        self.assertEqual(order, sorted(order))
        self.assertEqual(page.count('class="div"'), 1)

    def test_broken_lines_never_stop_a_run(self):
        self.index({"id": "a1", "kind": "apply", "rows": []},
                   {"kind": "measure", "parent_id": "a1"},  # no id
                   {"kind": "scan", "roots": ["/r"], "rules": B},  # no id, after a rules change
                   {"id": 7, "kind": "scan"}, {"id": "x2", "kind": ["scan"]}, "not json", "[1, 2]",
                   {"id": "s9", "kind": "scan", "roots": "/r", "rules": 5, "stack_sum": "big", "open": None},
                   {"id": "t1", "kind": "trim", "rows": {"n": 1}})
        page = history.render(self.out, Path(self.tmp.name), fonts=False)
        self.assertIn('id">a1', page)
        self.assertIn('id">s9', page)
        self.assertNotIn("x2", page)
        row = runs.append_row(self.out, {"id": "s10", "kind": "scan", "roots": ["/r"], "rules": A})
        self.assertEqual(row["id"], "s10")
        self.assertIn('id">s10', (self.out / "index.html").read_text())
        # Even a page that fails for another reason leaves the run and its index line alone.
        from unittest import mock
        with mock.patch("walle.history.render", side_effect=KeyError("id")):
            runs.append_row(self.out, {"id": "s11", "kind": "scan", "roots": ["/r"]})
        self.assertEqual(runs.read_index(self.out)[-1]["id"], "s11")


class RewriteTest(unittest.TestCase):
    def setUp(self):
        self.f = RunsFixture()

    def tearDown(self):
        self.f.close()

    def test_each_run_writes_the_page_under_out(self):
        a = self.f.scan()
        page = (self.f.out / "index.html").read_text()
        self.assertIn(f'<a href="{a["id"]}/scan.html">scan.html</a>', page)
        self.f.scan()
        self.f.diff()
        page = (self.f.out / "index.html").read_text()
        self.assertEqual(page.count('<span class="kind">'), 3)
        (self.f.out / "index.html").unlink()
        out, _ = self.f.cli("runs", "--html")
        self.assertEqual(out.strip(), str(self.f.out / "index.html"))
        self.assertTrue((self.f.out / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()
