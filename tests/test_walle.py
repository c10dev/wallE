import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from walle import cli, eve, report, report_html
from walle.classify import classify_unit
from walle.collect import collect, find_checkout
from walle.compact import copy_groups, rule_pairs
from walle.scan import scan
from walle.segment import Unit, segment
from walle.stack import CODEX_PROJECT_DOC_MAX, claude_stack, codex_stack, memory_slug

RULE = "PR 본문에는 바꾼 것, 확인한 것, 확인하지 않은 것을 순서대로 쓴다."


class Tree:
    """A fake home with a projects folder, one repo, one linked worktree."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.projects = self.home / "projects"
        self.repo = self.projects / "app"
        (self.repo / ".git" / "worktrees" / "wt1").mkdir(parents=True)
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".codex").mkdir(parents=True)

    def write(self, rel: str, text: str) -> Path:
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def worktree(self, name: str) -> Path:
        wt = self.projects / "worktrees" / name
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {self.repo / '.git' / 'worktrees' / 'wt1'}\n")
        return wt

    def close(self):
        self.tmp.cleanup()


class SegmentTest(unittest.TestCase):
    def test_bullets_tables_and_fences(self):
        text = "---\nname: x\n---\n# Head\n- one rule\n  continued here\n- two rule\n\n```\n- not a rule\n```\n| head one | head two |\n|---|---|\n| cell one | cell two |\n\nA paragraph rule.\n"
        units = segment(text, "f")
        self.assertEqual([(u.kind, u.text) for u in units],
                         [("heading", "Head"), ("text", "one rule continued here"), ("text", "two rule"),
                          ("code", "- not a rule"), ("text", "head one | head two"),
                          ("text", "cell one | cell two"), ("text", "A paragraph rule.")])
        self.assertEqual(units[1].line, 5)
        self.assertEqual(units[1].heading, "Head")
        self.assertEqual(units[3].line, 10)


class ClassifyTest(unittest.TestCase):
    def kinds(self, text):
        return {(f.cause, f.kind) for f in classify_unit(Unit("f", 1, "", text))}

    def test_vague_quality(self):
        self.assertIn((3, "vague_quality"), self.kinds("화면을 깔끔하게 만들고 AI스럽지 않게 한다."))
        self.assertIn((3, "vague_quality"), self.kinds("Think carefully before you answer."))

    def test_vague_words_that_are_not_vague(self):
        self.assertNotIn((3, "vague_quality"), self.kinds("`npm run lint` is clean before the PR."))
        self.assertNotIn((3, "vague_quality"), self.kinds("Clean up only your own mess."))
        self.assertNotIn((3, "vague_quality"), self.kinds("DO NOT comment on code quality, architecture, or best practices."))

    def test_legacy_and_cost(self):
        k = self.kinds("NEVER push. ALWAYS run tests. 2026-09-01 기준 Opus 결과.")
        self.assertIn((4, "emphasis_stack"), k)
        self.assertIn((4, "dated"), k)
        self.assertIn((4, "model_name"), k)
        self.assertIn((1, "always_step"), k)
        self.assertIn((1, "always_step"), self.kinds("## Done criteria (every task)"))
        self.assertEqual(classify_unit(Unit("f", 1, "", "NEVER ALWAYS 2026-01-01", "code")), [])


class CompactTest(unittest.TestCase):
    def test_duplicate_drift_repeat(self):
        a = Unit("a.md", 1, "", RULE)
        b = Unit("b.md", 3, "", RULE)
        c = Unit("c.md", 5, "", RULE.replace("순서대로 쓴다", "표로 정리해 맨 위에 쓴다"))
        d = Unit("a.md", 9, "", RULE + " ")
        kinds = {(p.a.file, p.b.file, p.kind) for p in rule_pairs([a, b, c, d])}
        self.assertIn(("a.md", "b.md", "duplicate"), kinds)
        self.assertIn(("a.md", "c.md", "drift"), kinds)
        self.assertIn(("a.md", "a.md", "repeat"), kinds)


class TreeTest(unittest.TestCase):
    def setUp(self):
        self.t = Tree()

    def tearDown(self):
        self.t.close()

    def test_find_checkout_maps_worktree_to_main_repo(self):
        wt = self.t.worktree("app-feature")
        checkout, repo, is_wt = find_checkout(wt)
        self.assertEqual((checkout, repo, is_wt), (wt, self.t.repo, True))

    def test_claude_stack_layers(self):
        t = self.t
        t.write(".claude/CLAUDE.md", "- global rule\n")
        t.write("projects/app/CLAUDE.md", "- repo rule\n@docs/extra.md\n")
        t.write("projects/app/docs/extra.md", "- imported rule\n")
        t.write("projects/app/sub/CLAUDE.md", "- sub rule\n")
        t.write("projects/app/below/CLAUDE.md", "- not loaded from sub\n")
        slug = memory_slug(t.repo / "sub")
        t.write(f".claude/projects/{slug}/memory/MEMORY.md", "\n".join(f"- m{i}" for i in range(250)))
        t.write(".claude/skills/s1/SKILL.md", "---\nname: s1\ndescription: does s1\n---\nbody\n")
        t.write(".claude/skills/s2/SKILL.md", "---\nname: s2\ndescription: hidden\ndisable-model-invocation: true\n---\n")
        st = claude_stack(t.repo / "sub", t.home)
        labels = [(l.label, l.path.name if l.path else None) for l in st.layers]
        self.assertEqual(labels, [("global", "CLAUDE.md"), ("ancestor", "CLAUDE.md"), ("import", "extra.md"),
                                  ("ancestor", "CLAUDE.md"), ("memory", "MEMORY.md"), ("skill_catalog", None)])
        self.assertIn("truncated to 200", st.layers[4].note)
        self.assertIn("1 local skills", st.layers[5].note)

    def test_codex_stack_cap(self):
        t = self.t
        t.write(".codex/AGENTS.md", "- g\n")
        t.write("projects/app/AGENTS.md", "x" * (CODEX_PROJECT_DOC_MAX - 10))
        t.write("projects/app/sub/AGENTS.md", "y" * 100)
        st = codex_stack(t.repo / "sub", t.home)
        self.assertEqual([l.label for l in st.layers], ["codex_global", "codex_project", "codex_project"])
        self.assertTrue(st.layers[2].note.startswith("over"))

    def test_scan_end_to_end(self):
        t = self.t
        t.write(".claude/CLAUDE.md", f"- {RULE}\n")
        t.write("projects/app/CLAUDE.md", f"- {RULE}\n- 화면은 깔끔하게 만든다.\n")
        t.write("projects/app/AGENTS.md", f"- {RULE.replace('순서대로', '표로')}\n")
        t.write("projects/app/.cursor/rules/style.mdc", "- Use polished UI.\n")
        wt = t.worktree("app-old")
        (wt / "CLAUDE.md").write_text("- old copy\n")
        data = scan([t.projects], t.home)
        kinds = {p["kind"] for p in data["pairs"]}
        self.assertIn("duplicate", kinds)
        self.assertIn("drift", kinds)
        self.assertTrue(any(f["cause"] == 3 and f["file"].endswith("style.mdc") for f in data["flags"]))
        self.assertEqual(data["copy_count"], 1)
        self.assertEqual(data["copies"][0]["variants"], 2)
        md = report.render_scan(data)
        self.assertIn("### Worktree copies (1)", md)
        page = report_html.render({**data, "flags": data["flags"] + [{**data["flags"][0], "text": "</script><b>"}]})
        self.assertNotIn("</script><b>", page)

    def test_check_exit_code(self):
        t = self.t
        t.write("projects/app/CLAUDE.md", "- rule\n" * 2000)
        with redirect_stdout(io.StringIO()), mock.patch("walle.scan.HOME", t.home):
            data = scan([t.projects], t.home)
            self.assertEqual(cli.check(data, 100, "all"), 1)
            self.assertEqual(cli.check(data, 10**6, "all"), 0)

    def test_alarm_is_silent_when_off(self):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_SESSION_ALARM": "false"}):
            self.assertEqual(cli.alarm(), 0)
        self.assertEqual(out.getvalue(), "")


class EveTest(unittest.TestCase):
    @mock.patch("walle.eve.SCRATCH_ROOTS", ())
    def test_parse_and_summarize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            proj = Path(tmp) / "repo"
            (proj / ".git").mkdir(parents=True)
            (proj / "CLAUDE.md").write_text("- r\n")
            ctl = Path(tmp) / "plain"
            ctl.mkdir()

            def session(name, cwd, base, tools):
                lines = [{"type": "user", "cwd": str(cwd), "timestamp": "2026-10-01T00:00:00Z",
                          "message": {"role": "user", "content": "hi"}}]
                for i, tool in enumerate(tools):
                    lines.append({"type": "assistant", "cwd": str(cwd), "message": {
                        "id": f"m{i}", "model": "claude-opus-5-5",
                        "usage": {"input_tokens": 1, "cache_creation_input_tokens": base, "cache_read_input_tokens": 0,
                                  "output_tokens": 5},
                        "content": [{"type": "tool_use", "id": f"t{i}", "name": tool[0], "input": tool[1]}]}})
                    lines.append({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}"}]}})
                lines.append({"type": "assistant", "isSidechain": True, "message": {"id": "side", "usage": {"input_tokens": 999999}}})
                d = root / name.split("/")[0]
                d.mkdir(parents=True, exist_ok=True)
                (d / (name.split("/")[1] + ".jsonl")).write_text("\n".join(json.dumps(l) for l in lines))

            for i in range(3):
                session(f"ctl/c{i}", ctl, 1000, [("Read", {})])
            for i in range(3):
                session(f"p/p{i}", proj, 1500, [("Bash", {"command": "npm test"}), ("Edit", {}), ("Read", {})])
            s = eve.parse_session(root / "p" / "p0.jsonl")
            self.assertEqual((s.baseline, s.tool_calls, s.check_calls, s.calls_before_edit, s.prompts), (1501, 3, 1, 1, 1))
            out = eve.run(transcripts=root, cache_dir=Path(tmp) / "cache")
            rows = {g["group"]: g for g in out["groups"]}
            self.assertEqual(rows["repo"]["baseline_delta_median"], 500)
            self.assertTrue(rows["(control: no project CLAUDE.md)"]["control"])
            # The cache is used on the second run.
            self.assertEqual(eve.run(transcripts=root, cache_dir=Path(tmp) / "cache")["sessions"], 6)

    def test_scratch_sessions_are_not_control(self):
        self.assertEqual(eve.group_key("/tmp/probe/x"), ("(scratch: /tmp)", False))


class MemoryScopeTest(unittest.TestCase):
    def test_only_memory_of_folders_under_the_roots(self):
        t = Tree()
        try:
            t.write("projects/app/CLAUDE.md", "- r\n")
            t.write("projects/app/sub/CLAUDE.md", "- r\n")
            t.write("projects/app-web/CLAUDE.md", "- a sibling whose slug starts like app's\n")
            for folder in (t.repo, t.repo / "sub", t.projects / "app-web", t.projects / "other"):
                t.write(f".claude/projects/{memory_slug(folder)}/memory/MEMORY.md", "- m\n")
            found = sorted(a.path.parent.parent.name for a in collect([t.repo], t.home) if a.kind == "memory_index")
            self.assertEqual(found, sorted([memory_slug(t.repo), memory_slug(t.repo / "sub")]))
        finally:
            t.close()


class CollectTest(unittest.TestCase):
    def test_skips_node_modules(self):
        t = Tree()
        try:
            t.write("projects/app/node_modules/x/CLAUDE.md", "- vendored\n")
            t.write("projects/app/CLAUDE.md", "- mine\n")
            paths = [a.path.name for a in collect([t.projects], t.home) if "node_modules" in str(a.path)]
            self.assertEqual(paths, [])
            self.assertEqual(copy_groups([]), [])
        finally:
            t.close()


if __name__ == "__main__":
    unittest.main()
