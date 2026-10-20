import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from walle import cli, judge, report

LONG_RULE = "PR 본문에는 바꾼 것, 확인한 것, 확인하지 않은 것을 순서대로 쓴다. " * 6


def answer_all(files, only=None, real_for="conflict"):
    """A fake agent: read each batch file and write one verdict per item to its answer file."""
    for f in files:
        batch = json.loads(Path(f["batch"]).read_text())
        verdicts = [{"id": it["id"], "real": it["type"] == real_for, "keep": "a", "fix": "합친다", "why": "test"}
                    for it in batch["items"] if only is None or it["id"] in only]
        Path(batch["answer_file"]).write_text(json.dumps({"verdicts": verdicts}, ensure_ascii=False))


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()  # the CLI resolves --out; macOS temp folders sit behind a symlink
        self.a = root / "a" / "CLAUDE.md"
        self.b = root / "b" / "CLAUDE.md"
        self.a.parent.mkdir()
        self.b.parent.mkdir()
        self.a.write_text(f"# Rules\n- {LONG_RULE}\n\n## Done (every task)\n- run `npm test`\n- update the docs\n")
        self.b.write_text(f"- {LONG_RULE.replace('순서대로', '표로')}\n")
        self.cache = root / "cache" / "judge.json"
        self.runs = root / "runs"
        self.scan_path = self.write_scan(self.runs / "20261020-080000")

    def tearDown(self):
        self.tmp.cleanup()

    def write_scan(self, run_dir: Path) -> Path:
        a, b = str(self.a), str(self.b)
        scan = {
            "generated": "2026-10-20T08:00:00",
            "files": [{"path": a, "kind": "claude_md", "loaded_in_stacks": 1},
                      {"path": b, "kind": "claude_md", "loaded_in_stacks": 1}],
            "stacks": [{"layers": [{"path": a}, {"path": None}]}, {"layers": [{"path": b}]}],
            "pairs": [
                {"kind": "drift", "score": 0.8, "a": {"file": a, "line": 2, "text": f"{LONG_RULE}"[:200]},
                 "b": {"file": b, "line": 1, "text": LONG_RULE.replace("순서대로", "표로")[:200]}},
                {"kind": "duplicate", "score": 1.0, "a": {"file": a, "line": 2, "text": "x"},
                 "b": {"file": b, "line": 1, "text": "x"}},
            ],
            "flags": [
                {"cause": 1, "kind": "always_step", "file": a, "line": 4, "heading": "Done (every task)",
                 "match": "every task", "candidate": True, "text": "Done (every task)"},
                {"cause": 3, "kind": "vague_quality", "file": a, "line": 2, "heading": "Rules",
                 "match": "x", "candidate": False, "text": "x"},
            ],
        }
        run_dir.mkdir(parents=True)
        (run_dir / "scan.json").write_text(json.dumps(scan, ensure_ascii=False))
        return run_dir / "scan.json"

    def export(self, **kw):
        return judge.export(self.scan_path, cache_file=self.cache, **kw)

    def imp(self, **kw):
        return judge.import_answers(self.scan_path, cache_file=self.cache, **kw)

    def test_select_items_reads_whole_rules(self):
        items = judge.select_items(json.loads(self.scan_path.read_text()))
        self.assertEqual([it["type"] for it in items], ["conflict", "cost"])
        conflict, cost = items
        # scan.json cut the rule at 200 characters; the judge sees all of it.
        self.assertEqual(conflict["ask"]["a"]["text"], LONG_RULE.strip())
        self.assertEqual(conflict["ask"]["loaded"], "never")
        self.assertFalse(conflict["stale"])
        # A flagged heading is sent with the start of its section.
        self.assertIn("run `npm test`", cost["ask"]["text"])
        self.assertEqual(cost["ask"]["loaded"], "session_start")
        self.assertNotIn("line", json.dumps(cost["ask"]))

    def test_dry_run_writes_nothing(self):
        data = judge.plan(self.scan_path, cache_file=self.cache)
        p = data["plan"]
        self.assertEqual((p["items"], p["conflict"], p["cost"], p["to_judge"], p["batches"]), (2, 1, 1, 2, 1))
        self.assertGreater(p["input_tokens_est"], 100)
        self.assertFalse(self.cache.exists())
        self.assertFalse((self.scan_path.parent / judge.JUDGE_DIR).exists())
        self.assertIn("Nothing was written", judge.describe_plan(data))

    def test_export_writes_self_contained_batches(self):
        data = self.export(batch_size=1)
        self.assertEqual(len(data["files"]), 2)
        batch = json.loads(Path(data["files"][0]["batch"]).read_text())
        # The batch file alone is enough for a subagent: rubric, schema, items and where to answer.
        self.assertEqual(batch["rubric"], judge.RUBRIC)
        self.assertEqual(batch["schema"], judge.SCHEMA)
        self.assertEqual([it["id"] for it in batch["items"]], ["1"])
        self.assertTrue(batch["answer_file"].endswith("batch-001.answer.json"))
        self.assertIn("walle judge --import", judge.describe_export(data))
        # A new export gets its own folder; the old one stays, with any answers in it.
        Path(batch["answer_file"]).write_text("{}")
        second = self.export(batch_size=20)
        self.assertEqual(len(second["files"]), 1)
        self.assertTrue(Path(batch["answer_file"]).is_file())
        self.assertNotEqual(Path(second["files"][0]["batch"]).parent, Path(data["files"][0]["batch"]).parent)
        current = json.loads((self.scan_path.parent / "judge" / "manifest.json").read_text())
        self.assertEqual(current["files"][0]["batch"], second["files"][0]["batch"])

    def test_import_caches_and_next_export_holds_only_changed_rules(self):
        answer_all(self.export()["files"])
        data = self.imp(judge_name="claude-opus-5-5")
        self.assertEqual(data["errors"], [])
        self.assertEqual([it["verdict"]["real"] for it in data["items"]], [True, False])
        self.assertEqual({it["judge"] for it in data["items"]}, {"claude-opus-5-5"})
        self.assertFalse(any(it["from_cache"] for it in data["items"]))
        self.assertEqual(data["plan"]["judged_now"], 2)

        # Same text, same rubric: nothing to export.
        again = self.export()
        self.assertEqual((again["plan"]["to_judge"], again["files"]), (0, []))
        self.assertTrue(all(it["verdict"] for it in again["items"]))

        # A rule that only moved down a line keeps its judgment.
        self.a.write_text("\n" + self.a.read_text())
        self.write_scan_lines_shifted()
        self.assertEqual(self.export()["plan"]["to_judge"], 0)

        # A changed rule is exported again, alone.
        self.a.write_text(self.a.read_text().replace("update the docs", "update the changelog"))
        data = self.export()
        self.assertEqual(data["plan"]["to_judge"], 1)
        self.assertIn("changelog", Path(data["files"][0]["batch"]).read_text())

    def write_scan_lines_shifted(self):
        scan = json.loads(self.scan_path.read_text())
        for p in scan["pairs"]:
            if p["a"]["file"] == str(self.a):
                p["a"]["line"] += 1
        for f in scan["flags"]:
            f["line"] += 1
        self.scan_path.write_text(json.dumps(scan, ensure_ascii=False))

    def test_missing_answer_and_missing_verdict_are_reported_not_cached(self):
        files = self.export(batch_size=1)["files"]
        answer_all(files[:1])
        data = self.imp()
        self.assertEqual(data["errors"], ["no answer yet: batch-002.answer.json"])
        self.assertEqual(data["plan"]["cached"], 0)
        self.assertEqual([it["verdict"] is None for it in data["items"]], [False, True])
        self.assertIn("판정 못 함 · 1건", report.render_judge(data))
        self.assertEqual(self.export()["plan"]["to_judge"], 1)

        files = self.export()["files"]
        answer_all(files, only=set())
        data = self.imp()
        self.assertEqual(data["errors"], ["batch-001.answer.json: no valid verdict for id 1"])

    def test_answer_with_prose_or_bad_fields(self):
        self.assertEqual(judge.parse_answer('Here:\n```json\n{"verdicts": [{"id": "1", "real": true, "keep": "b", '
                                            '"fix": "x", "why": "y"}]}\n```'),
                         {"1": {"real": True, "keep": "b", "fix": "x", "why": "y"}})
        self.assertEqual(judge.parse_answer('[{"id": "1", "real": "yes", "keep": "a", "fix": "x"}]'), {})
        with self.assertRaises(judge.JudgeError):
            judge.parse_answer("no json here")

    def test_batch_items_carry_lines_depth_and_on_demand_state(self):
        scan = json.loads(self.scan_path.read_text())
        scan["files"][1]["kind"] = "skill"  # b is a skill body: it loads with a when the skill runs
        self.scan_path.write_text(json.dumps(scan, ensure_ascii=False))
        data = self.export(depth="deep")
        batch = json.loads(Path(data["files"][0]["batch"]).read_text())
        self.assertEqual(batch["depth"], "deep")
        conflict = batch["items"][0]
        self.assertEqual(conflict["loaded"], "on_demand")
        self.assertEqual([w.rsplit(":", 1)[1] for w in conflict["where"]], ["2", "1"])
        # Line numbers and depth are outside the cache key.
        key = data["files"][0]["keys"]["1"]
        self.assertEqual(self.export()["files"][0]["keys"]["1"], key)

    def test_loaded_follows_where_a_skill_is_visible(self):
        home = Path(self.tmp.name) / "home"
        g = str(home / ".claude" / "skills" / "s" / "SKILL.md")
        cx = str(home / ".codex" / "skills" / "s" / "SKILL.md")
        proj = str(Path(self.tmp.name) / "x" / ".claude" / "skills" / "s" / "SKILL.md")
        a, b = str(self.a), str(self.b)
        rule = {"text": LONG_RULE[:200]}
        scan = {
            "files": [{"path": a, "kind": "claude_md", "loaded_in_stacks": 1},
                      {"path": b, "kind": "claude_md", "loaded_in_stacks": 1},
                      {"path": g, "kind": "skill"}, {"path": cx, "kind": "skill"}, {"path": proj, "kind": "skill"},
                      {"path": "/r/.cursorrules", "kind": "other_agent"}],
            "stacks": [{"agent": "claude", "folder": str(self.a.parent), "layers": [{"path": a}]},
                       {"agent": "codex", "folder": str(self.a.parent), "layers": []}],
            "pairs": [{"kind": "drift", "score": 0.7, "a": {"file": a, "line": 2, **rule}, "b": {"file": f, "line": 1, **rule}}
                      for f in (g, cx, proj, "/r/.cursorrules")],
            "flags": [],
        }
        got = [it["ask"]["loaded"] for it in judge.select_items(scan, home=home)]
        # Global Claude skill: on demand next to a. Codex skill and another repo's skill: never. Cursor: unmodeled.
        self.assertEqual(got, ["on_demand", "never", "never", "unmodeled"])

    def test_ask_is_a_valid_keep(self):
        self.assertEqual(judge.parse_answer('{"verdicts": [{"id": "1", "real": true, "keep": "ask", "fix": "x", "why": "y"}]}')
                         ["1"]["keep"], "ask")

    def test_cli_cache_follows_out(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--out", str(self.runs), "judge"]), 0)
        manifest = json.loads((self.scan_path.parent / "judge" / "manifest.json").read_text())
        answer_all(manifest["files"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--out", str(self.runs), "judge", "--import"]), 0)
        self.assertTrue((self.runs.parent / "cache" / "judge.json").is_file())

    def test_import_without_export_is_an_error(self):
        with self.assertRaises(judge.JudgeError):
            self.imp()

    def test_answers_written_before_a_second_export_still_import(self):
        first = self.export(batch_size=1)["files"]
        answer_all(first)
        self.export(batch_size=1)  # someone runs `walle judge` again before importing
        data = self.imp()
        self.assertTrue(all(it["verdict"] for it in data["items"]))
        self.assertEqual(data["errors"], [])

    def test_late_answer_of_an_old_export_lands_on_its_own_items(self):
        # Batches: item 1 (conflict), item 2 (cost). Answer only batch 1, import, export again.
        first = self.export(batch_size=1)["files"]
        answer_all(first[:1])
        self.imp()
        second = self.export(batch_size=1)["files"]
        self.assertEqual(len(second), 1)
        # The old subagent for batch 2 answers late, in the old export folder: real=true for the cost item.
        answer_all(first[1:], real_for="cost")
        data = self.imp()
        rows = {it["type"]: it["verdict"] for it in data["items"]}
        self.assertEqual((rows["conflict"]["real"], rows["cost"]["real"]), (True, True))
        self.assertEqual(data["errors"], [])

    def test_line_shift_before_import_keeps_keys(self):
        answer_all(self.export()["files"])
        self.a.write_text("<!-- edited -->\n" + self.a.read_text())
        data = self.imp()
        self.assertTrue(all(it["verdict"] for it in data["items"]))
        self.assertEqual(data["plan"]["to_judge"], 0)

    def test_same_question_twice_is_asked_once(self):
        scan = json.loads(self.scan_path.read_text())
        scan["flags"].append({**scan["flags"][0]})
        scan["flags"][-1]["line"] = 99  # same text, reported at another line
        self.scan_path.write_text(json.dumps(scan, ensure_ascii=False))
        data = self.export()
        self.assertEqual(data["plan"]["questions"], 2)
        self.assertEqual(data["plan"]["to_judge"], 3)
        answer_all(data["files"])
        out = self.imp()
        self.assertTrue(all(it["verdict"] for it in out["items"]))
        self.assertEqual((out["plan"]["judged_now"], out["plan"]["cached"]), (3, 0))

    def test_reanswer_of_missing_id_only(self):
        files = self.export()["files"]
        answer_all(files, only={"1"})
        self.assertEqual(self.imp()["errors"], ["batch-001.answer.json: no valid verdict for id 2"])
        answer_all(files, only={"2"})  # the agent re-answers only the missing id
        data = self.imp()
        self.assertEqual(data["errors"], [])
        self.assertEqual(data["plan"]["to_judge"], 0)

    def test_deep_rejudges_quick_verdicts(self):
        answer_all(self.export()["files"])
        self.imp()
        self.assertEqual(self.export()["plan"]["to_judge"], 0)
        deep = self.export(depth="deep")
        self.assertEqual(deep["plan"]["to_judge"], 2)
        answer_all(deep["files"])
        rows = self.imp()["items"]
        self.assertEqual({r["depth"] for r in rows}, {"deep"})
        self.assertEqual(self.export(depth="deep")["plan"]["to_judge"], 0)

    def test_cli_export_then_import_next_to_the_newest_scan(self):
        # `latest` and newer runs can hold only eve.json.
        (self.runs / "20261020-090000").mkdir()
        (self.runs / "20261020-090000" / "eve.json").write_text("{}")
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(judge, "CACHE_FILE", self.cache):
            self.assertEqual(cli.main(["--out", str(self.runs), "judge", "--dry-run"]), 0)
        self.assertIn("2 items from " + str(self.scan_path), out.getvalue())

        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(judge, "CACHE_FILE", self.cache):
            self.assertEqual(cli.main(["--out", str(self.runs), "judge"]), 0)
        self.assertIn("batch-001.json", out.getvalue())
        self.assertFalse((self.scan_path.parent / "judge.md").exists())

        manifest = json.loads((self.scan_path.parent / "judge" / "manifest.json").read_text())
        answer_all(manifest["files"])
        with redirect_stdout(io.StringIO()), mock.patch.object(judge, "CACHE_FILE", self.cache):
            self.assertEqual(cli.main(["--out", str(self.runs), "judge", "--import"]), 0)
        md = (self.scan_path.parent / "judge.md").read_text()
        self.assertIn("판정: 에이전트", md)
        self.assertIn("## 1. 실제 충돌 · 1건", md)
        self.assertIn("## 2. 작업과 무관한 상시 비용 · 0건", md)
        self.assertEqual(len(json.loads((self.scan_path.parent / "judge.json").read_text())["items"]), 2)

        # Everything cached: a plain `walle judge` writes the report at once.
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(judge, "CACHE_FILE", self.cache):
            self.assertEqual(cli.main(["--out", str(self.runs), "judge"]), 0)
        self.assertTrue(out.getvalue().strip().endswith("judge.md"))

    def test_cli_without_scan(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(cli.main(["--out", str(Path(self.tmp.name) / "empty"), "judge", "--dry-run"]), 2)
        self.assertIn("walle scan", err.getvalue())


if __name__ == "__main__":
    unittest.main()
