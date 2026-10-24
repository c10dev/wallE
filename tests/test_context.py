import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_walle import Tree

from walle import cli, context, estimate, transcript
from walle.scan import scan
from walle.stack import claude_loaded, claude_stack, estimate_tokens, memory_slug

FIXTURE = Path(__file__).parent / "fixtures" / "session-630f787e.jsonl"
SID = "630f787e-f784-445a-9d87-19be85313a8b"
UUID = "aaaaaaaa-1111-4222-8333-444444444444"


class FirstCallFixtureTest(unittest.TestCase):
    """Session 630f787e, with its text replaced by filler of the same byte length."""

    def setUp(self):
        self.fc = transcript.first_call(FIXTURE)
        self.src = {s.kind: s for s in self.fc.sources}

    def test_reproduces_the_research_numbers(self):
        cost = self.fc.measured.pop("cost")
        self.assertEqual(self.fc.measured, {"input": 2, "cache_creation": 59951, "cache_write_1h": 59951,
                                            "cache_read": 30546, "total": 90499})
        # 2 + 2 x 59951 (1-hour writes) + 0.05 x 30546 (Opus 5.5 reads at $0.20 of $4)
        self.assertEqual(cost["input_equiv"], 121431)
        self.assertEqual(cost["usd"], 0.4857)
        self.assertEqual((self.src["skill_listing"].count, self.src["skill_listing"].bytes), (64, 29698))
        self.assertEqual((self.src["deferred_tools"].count, self.src["deferred_tools"].bytes), (784, 43350))
        self.assertEqual(self.src["deferred_tools"].detail["mcp_names"], 763)
        self.assertEqual(self.src["system_prompt"].bytes, 21257)
        self.assertEqual((self.src["mcp_instructions"].count, self.src["mcp_instructions"].bytes), (10, 16465))
        self.assertEqual((self.src["instructions"].count, self.src["instructions"].bytes), (2, 6308))
        self.assertEqual((self.src["agent_listing"].count, self.src["agent_listing"].bytes), (6, 2528))
        self.assertEqual(self.src["first_prompt"].bytes, 7483)

    def test_tools_come_from_the_snapshot_after_the_first_call(self):
        b, m = self.src["builtin_tools"], self.src["mcp_tools"]
        self.assertEqual((b.count, m.count), (17, 10))
        self.assertEqual(sum(len(x) for x in b.text.split("\n")), 118581)  # characters of the 17 tool JSONs
        self.assertEqual((b.recorded, m.recorded), ("after_first_call", "after_first_call"))

    def test_bytes_v2_puts_deferred_names_first(self):
        est = {k: estimate.estimate(s.text, k) for k, s in self.src.items()}
        self.assertEqual(max(est, key=est.get), "deferred_tools")
        self.assertEqual(est["deferred_tools"], round(43350 * 0.62))
        # bytes-v2 leaves a small residual on this session; bytes-v1 leaves more than a quarter unexplained.
        v1 = sum(estimate.estimate(s.text, k, "bytes-v1") for k, s in self.src.items())
        self.assertLess(90499 - sum(est.values()), 0.05 * 90499)
        self.assertGreater(90499 - v1, 0.25 * 90499)


class ScanCountsTheRecordedTextTest(unittest.TestCase):
    """walle scan counts an instruction file as the session recorded it, not as the disk holds it.

    The fixture records ~/.claude/CLAUDE.md and MEMORY.md as Claude Code sent them. The real
    ~/.claude/CLAUDE.md has a comment line at the top and at the end, which Claude Code drops.
    """

    def setUp(self):
        att = next(d["attachment"] for d in map(json.loads, FIXTURE.read_text().splitlines())
                   if d.get("type") == "attachment" and d["attachment"].get("type") == "instructions")
        self.recorded = {f["type"]: f["content"] for f in att["files"]}
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve() / "home"
        self.folder = self.home / "projects"
        self.folder.mkdir(parents=True)
        (self.home / ".claude").mkdir()
        self.disk = ("<!-- claude-config:begin sha256=92abb94f682243f4 (managed block: edit global/CLAUDE.md in the "
                     "claude-config repo, then run install.py) -->\n" + self.recorded["User"]
                     + "\n<!-- claude-config:end -->\n")
        (self.home / ".claude" / "CLAUDE.md").write_text(self.disk)
        mem = self.home / ".claude" / "projects" / memory_slug(self.folder) / "memory" / "MEMORY.md"
        mem.parent.mkdir(parents=True)
        mem.write_text("<!-- index of memory files -->\n" + self.recorded["AutoMem"] + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_layers_have_the_recorded_bytes_and_tokens(self):
        self.assertEqual(len(self.recorded["User"].encode()), 4919)
        self.assertEqual(claude_loaded(self.disk), self.recorded["User"])
        for est in estimate.ESTIMATORS:
            st = claude_stack(self.folder, self.home, est)
            layers = {l.label: l for l in st.layers}
            for label, kind in (("global", "User"), ("memory", "AutoMem")):
                text = self.recorded[kind]
                self.assertEqual(layers[label].bytes, len(text.encode()), (est, label))
                self.assertEqual(layers[label].tokens, estimate.estimate(text, "instructions", est), (est, label))
            # The disk text counts the two comment lines too.
            self.assertGreater(estimate.estimate(self.disk, "instructions", est), layers["global"].tokens)
            # walle context counts the same files, joined by a line break, in one row.
            row = next(r for r in context.session_context(transcript.first_call(FIXTURE), est, self.home)["sources"]
                       if r["source"] == "instructions")
            self.assertEqual(row["bytes"], layers["global"].bytes + 1 + layers["memory"].bytes)
            self.assertLessEqual(abs(row["tokens_est"] - layers["global"].tokens - layers["memory"].tokens), 1)
            # One file, one number: the files list of a scan agrees with the layer.
            data = scan([self.folder], self.home, estimator=est)
            files = {f["path"]: f["tokens_est"] for f in data["files"]}
            self.assertEqual(files[str(self.home / ".claude" / "CLAUDE.md")], layers["global"].tokens)

    def test_comment_blocks_go_as_claude_code_drops_them(self):
        # Expected texts from Claude Code's rule run with the marked 18 lexer.
        cases = {
            "<!-- a -->\n# T\n\n- x\n<!-- e -->\n": "# T\n\n- x",
            "para\n<!-- c -->\nnext": "para\nnext",
            "text\n<!-- c -->\n\n<!-- d -->\nz": "text\n\n\nz",
            "a\n\n<!--\nmulti\nline\n-->\n\nb": "a\n\n\n\nb",
            "<!-- c --> tail\nb": "tail\nb",
            "x\n\n<!-- c --> tail\nb": "x\n\n tail\nb",
            "# T\n\n```\n<!-- k -->\n```": "# T\n\n```\n<!-- k -->\n```",
            "text <!-- k --> more": "text <!-- k --> more",
            "- item\n  <!-- k -->\nnext": "- item\n  <!-- k -->\nnext",
            "1. one\n  <!-- c -->": "1. one",
            "para\n  <!-- k -->\nnext": "para\n  <!-- k -->\nnext",
            "<div>\n<!-- k -->\n</div>": "<div>\n<!-- k -->\n</div>",
            "a\n\n    <!-- k -->": "a\n\n    <!-- k -->",
            "a\n\n<!-- open\nb": "a\n\n<!-- open\nb",
            "  plain text  \n": "plain text",
        }
        for text, want in cases.items():
            self.assertEqual(claude_loaded(text), want, text)


class EstimateTest(unittest.TestCase):
    def test_estimators(self):
        text = "plain words and 한국어 글"
        self.assertEqual(estimate.estimate(text, "instructions", "bytes-v1"), estimate_tokens(text))
        ascii_n = sum(1 for c in text if ord(c) < 128)
        other = len(text.encode()) - ascii_n
        self.assertEqual(estimate.estimate(text, "instructions"), round(ascii_n * 0.41 + other * 0.58))
        self.assertEqual(estimate.estimate("x" * 100, "builtin_tools"), 21)
        with self.assertRaises(ValueError):
            estimate.estimate("x", "instructions", "bytes-v9")
        self.assertEqual(estimate.describe("bytes-v2")["fit"]["sessions"], 522)


class ServerKeyTest(unittest.TestCase):
    def test_one_key_for_each_spelling(self):
        k = transcript.server_key
        self.assertEqual(k("plugin_small-business_gmail"), k("plugin:small-business:gmail"))
        self.assertEqual(k("claude_ai_Github_vocado"), k("claude.ai Github(vocado)"))
        self.assertEqual(k(UUID), UUID)
        self.assertEqual(transcript.mcp_server("mcp__claude_ai_Foo__list_items"), "claude_ai_Foo")


def line(**d) -> str:
    return json.dumps(d)


def att(kind, ts="2026-10-01T00:00:00Z", **a) -> str:
    return line(type="attachment", timestamp=ts, cwd="/w", attachment={"type": kind, **a})


def call(mid, *tools, ts="2026-10-01T00:00:01Z", usage=None) -> str:
    content = [{"type": "tool_use", "id": f"{mid}-{i}", "name": n, "input": inp} for i, (n, inp) in enumerate(tools)]
    return line(type="assistant", timestamp=ts, message={"id": mid, "model": "claude-opus-5-5", "content": content,
                                                         "usage": usage or {"input_tokens": 100}})


def prompt(text, ts="2026-10-01T00:00:00Z") -> str:
    return line(type="user", timestamp=ts, cwd="/w", message={"role": "user", "content": text})


class UsageTest(unittest.TestCase):
    """Three sessions: desktop (connector by UUID), CLI (claude_ai_Foo), and one with a subagent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "projects"
        tools = ["list_items", "get_item", "make_item"]
        listing = "- foo:bar: does bar\n- atc/controller:tick: ticks"
        self.write("a", "s1", [
            prompt("hi"),
            att("deferred_tools_delta", addedNames=[f"mcp__{UUID}__{t}" for t in tools],
                addedLines=[f"mcp__{UUID}__{t}" for t in tools], failedMcpServers=[{"name": "plugin:data:dead"}]),
            att("mcp_instructions_delta", addedNames=[UUID], addedBlocks=["## Foo\nUse foo."]),
            att("skill_listing", content=listing, names=["foo:bar", "atc/controller:tick"], skillCount=2),
            call("m1", ("Skill", {"skill": "bar"})),
            prompt("<command-name>/tick</command-name>"),
            prompt("<command-name>/clear</command-name>"),
        ])
        self.write("b", "s2", [
            prompt("hi"),
            att("deferred_tools_delta", addedNames=[f"mcp__claude_ai_Foo__{t}" for t in tools],
                addedLines=[f"mcp__claude_ai_Foo__{t}" for t in tools]),
            att("mcp_instructions_delta", addedNames=["claude.ai Foo"], addedBlocks=["## claude.ai Foo\nUse foo."]),
            call("m1", ("Bash", {"command": "ls"})),
            att("hook_additional_context", content=["[guard] stop that"], hookName="PreToolUse:Bash",
                hookEvent="PreToolUse", toolUseID="t1"),
        ])
        self.write("b", "s3", [
            line(type="user", timestamp="2026-10-05T00:00:00Z", cwd="/w", message={"content": "late"}),
            att("deferred_tools_delta", ts="2026-10-05T00:00:00Z", addedNames=[f"mcp__claude_ai_Foo__{tools[0]}"],
                addedLines=[f"mcp__claude_ai_Foo__{tools[0]}"]),
            call("m1", ts="2026-10-05T00:00:01Z"),
        ])
        sub = self.root / "b" / "s3" / "subagents" / "agent-x.jsonl"
        sub.parent.mkdir(parents=True)
        sub.write_text(call("m9", ("mcp__claude_ai_Foo__list_items", {}), ("mcp__claude_ai_Foo__list_items", {})) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, slug, sid, lines):
        p = self.root / slug / f"{sid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n")

    def run_usage(self, **kw):
        rows = context.load_rows(self.root, Path(self.tmp.name) / "cache" / "u.json")
        return context.usage(rows, **kw)

    def test_connectors_fold_into_one_server(self):
        u = self.run_usage()
        self.assertEqual(u["sessions"], 3)
        foo = next(s for s in u["servers"] if s["key"] == "claude.ai:foo")
        self.assertEqual(foo["server"], "claude.ai Foo")
        self.assertEqual(foo["aliases"], [UUID])
        # Loaded in all three; used once, by a subagent (two calls).
        self.assertEqual((foo["sessions_loaded"], foo["sessions_used"], foo["calls"]), (3, 1, 2))
        self.assertEqual((foo["tools_offered"], foo["tools_called"]), (3, 1))
        dead = next(s for s in u["servers"] if s["key"] == "plugin:data:dead")
        self.assertEqual((dead["sessions_failed"], dead["sessions_loaded"]), (1, 0))
        items = next(t for t in u["mcp_tools"] if t["tool"] == "list_items")
        self.assertEqual((items["sessions_loaded"], items["sessions_used"], items["calls"]), (3, 1, 2))

    def test_skills_hooks_and_period(self):
        u = self.run_usage()
        sk = {s["skill"]: s for s in u["skills"]}
        self.assertEqual((sk["foo:bar"]["sessions_used"], sk["foo:bar"]["calls"]), (1, 1))
        self.assertEqual(sk["atc/controller:tick"]["calls"], 1)  # the slash command names a listed skill
        self.assertNotIn("clear", sk)  # a built-in command, not a skill
        self.assertEqual(sk["foo:bar"]["line_bytes_median"], len("- foo:bar: does bar") + 1)
        self.assertEqual(next(p for p in u["plugins"] if p["plugin"] == "foo")["skill_calls"], 1)
        guard = next(h for h in u["hooks"] if h["by"] == "source" and h["hook"] == "[guard]")
        self.assertEqual((guard["sessions"], guard["injections"]), (1, 1))
        self.assertEqual(u["hooks_total"]["sessions"], 1)
        self.assertEqual(self.run_usage(since="2026-10-02")["sessions"], 1)
        self.assertEqual(self.run_usage(before="2026-10-02")["sessions"], 2)

    def test_cache_is_reused(self):
        cache = Path(self.tmp.name) / "cache" / "u.json"
        context.load_rows(self.root, cache)
        data = json.loads(cache.read_text())
        key = str(self.root / "a" / "s1.jsonl")
        data[key]["row"]["tools"] = {"Marker": 1}
        cache.write_text(json.dumps(data))
        rows = context.load_rows(self.root, cache)
        self.assertEqual(rows[key]["tools"], {"Marker": 1})

    def cache(self) -> Path:
        return Path(self.tmp.name) / "cache" / "u.json"

    def foo(self, u) -> tuple[int, int, int]:
        s = next(s for s in u["servers"] if s["key"] == "claude.ai:foo")
        return s["sessions_loaded"], s["sessions_used"], s["calls"]

    def source(self, u, label) -> int:
        return next((h["sessions"] for h in u["hooks"] if h["by"] == "source" and h["hook"] == label), 0)

    def test_a_deleted_transcript_still_counts(self):
        self.run_usage()
        (self.root / "b" / "s2.jsonl").unlink()
        for _ in range(2):  # and on every later run
            u = self.run_usage()
            self.assertEqual((u["sessions"], u["sessions_deleted"]), (3, 1))
            self.assertEqual(self.foo(u), (3, 1, 2))
            # No session on disk shows the [guard] output, so the record keeps its digest; the hook still counts.
            digest = next(h for h in u["hooks"] if h["by"] == "digest")
            self.assertEqual((self.source(u, "[guard]"), digest["hook"], digest["sessions"], u["hooks_total"]["sessions"]),
                             (0, transcript.label_digest("[guard]"), 1, 1))
        self.assertEqual(self.run_usage(before="2026-10-02")["sessions"], 2)

    def test_a_deleted_hook_label_adds_to_the_same_label_on_disk(self):
        self.write("b", "s5", [prompt("hi"), att("hook_additional_context", content=["[guard] again"],
                                                  hookName="PreToolUse:Bash", hookEvent="PreToolUse", toolUseID="t5")])
        self.run_usage()
        (self.root / "b" / "s2.jsonl").unlink()
        u = self.run_usage()
        self.assertEqual((self.source(u, "[guard]"), u["sessions_deleted"]), (2, 1))
        self.assertNotIn("[guard] stop", self.cache().read_text())

    def test_a_relative_root_counts_only_its_own_deleted_sessions(self):
        tmp = Path(self.tmp.name)
        for d, sid in (("a", "s7"), ("b", "s8")):
            (tmp / d / "proj" / "x").mkdir(parents=True)
            (tmp / d / "proj" / "x" / f"{sid}.jsonl").write_text(prompt("hi") + "\n")
        cwd = os.getcwd()
        try:
            os.chdir(tmp / "a")
            context.load_rows(Path("proj"), self.cache())
            (tmp / "a" / "proj" / "x" / "s7.jsonl").unlink()
            here = context.usage(context.load_rows(Path("proj"), self.cache()))
            os.chdir(tmp / "b")
            u = context.usage(context.load_rows(Path("proj"), self.cache()))
        finally:
            os.chdir(cwd)
        self.assertEqual((here["sessions"], here["sessions_deleted"]), (1, 1))
        self.assertEqual((u["sessions"], u["sessions_deleted"]), (1, 0))

    def test_a_cached_relative_path_counts_for_no_root(self):
        self.run_usage()
        data = json.loads(self.cache().read_text())
        data[f"{self.root.name}/a/s6.jsonl"] = data[str(self.root / "a" / "s1.jsonl")]
        self.cache().write_text(json.dumps(data))
        cwd = os.getcwd()
        try:
            os.chdir(self.root.parent)  # the relative key would name a file under the root from here
            u = self.run_usage()
        finally:
            os.chdir(cwd)
        self.assertEqual((u["sessions"], u["sessions_deleted"]), (3, 0))

    def test_a_slug_folder_through_a_link_keeps_its_deleted_session(self):
        outside = Path(self.tmp.name) / "disk2" / "c"
        outside.mkdir(parents=True)
        (outside / "s9.jsonl").write_text(prompt("hi") + "\n")
        (self.root / "c").symlink_to(outside)
        self.run_usage()
        (outside / "s9.jsonl").unlink()
        u = self.run_usage()
        self.assertEqual((u["sessions"], u["sessions_deleted"]), (4, 1))

    def test_a_subagent_file_counts_once_through_two_spellings_of_the_root(self):
        link = Path(self.tmp.name) / "link"
        link.symlink_to(self.root)
        self.run_usage()
        for _ in range(2):
            u = context.usage(context.load_rows(link, self.cache()))
            self.assertEqual((u["sessions_deleted"], u["subagent_files"]), (0, 1))
            self.assertEqual(self.foo(u), (3, 1, 2))
            u = self.run_usage()
            self.assertEqual((u["sessions_deleted"], u["subagent_files"]), (0, 1))

    def test_a_file_that_comes_back_unchanged_is_read_again(self):
        self.write("b", "s4", [prompt("hi"), att("hook_additional_context", content=["notes for 7f3a only"],
                                                  hookName="SessionStart", hookEvent="SessionStart", toolUseID="t9")])
        self.run_usage()
        p, away = self.root / "b" / "s4.jsonl", Path(self.tmp.name) / "s4.jsonl"
        p.rename(away)  # the size and mtime stay
        self.assertEqual(self.run_usage()["sessions_deleted"], 1)
        away.rename(p)
        u = self.run_usage()
        self.assertEqual((u["sessions_deleted"], self.source(u, "notes for 7f3a only")), (0, 1))

    def test_a_deleted_subagent_file_still_counts_with_its_session(self):
        self.run_usage()
        (self.root / "b" / "s3" / "subagents" / "agent-x.jsonl").unlink()
        u = self.run_usage()
        self.assertEqual((u["sessions"], u["sessions_deleted"], u["subagent_files"]), (3, 0, 1))
        self.assertEqual(self.foo(u), (3, 1, 2))

    def test_a_cached_subagent_row_without_its_main_row_does_not_count(self):
        self.run_usage()
        shutil.rmtree(self.root / "b" / "s3")
        (self.root / "b" / "s3.jsonl").unlink()
        data = json.loads(self.cache().read_text())
        del data[str(self.root / "b" / "s3.jsonl")]
        self.cache().write_text(json.dumps(data))
        rows = context.load_rows(self.root, self.cache())
        self.assertFalse(any("subagents" in k for k in rows))
        self.assertEqual(context.usage(rows)["sessions"], 2)

    def test_another_roots_deleted_row_keeps_no_hook_output_text(self):
        other = Path(self.tmp.name) / "other"
        (other / "z").mkdir(parents=True)
        (other / "z" / "s9.jsonl").write_text("\n".join([prompt("hi"), att(
            "hook_additional_context", content=["notes for 7f3a only"], hookName="SessionStart",
            hookEvent="SessionStart", toolUseID="t9")]) + "\n")
        context.load_rows(other, self.cache())
        self.run_usage()
        self.assertIn("notes for 7f3a only", self.cache().read_text())  # its file is on disk: kept as it is
        (other / "z" / "s9.jsonl").unlink()
        self.run_usage()
        self.assertNotIn("7f3a", self.cache().read_text())
        u = context.usage(context.load_rows(other, self.cache()))
        self.assertEqual((u["sessions"], u["sessions_deleted"]), (1, 1))

    def test_a_deleted_session_keeps_its_subagent_calls(self):
        self.run_usage()
        shutil.rmtree(self.root / "b" / "s3")
        (self.root / "b" / "s3.jsonl").unlink()
        u = self.run_usage()
        self.assertEqual((u["sessions"], u["sessions_deleted"], u["subagent_files"]), (3, 1, 1))
        self.assertEqual(self.foo(u), (3, 1, 2))

    def test_a_moved_or_restored_transcript_counts_once(self):
        self.run_usage()
        src, dst = self.root / "a" / "s1.jsonl", self.root / "c" / "s1.jsonl"
        dst.parent.mkdir()
        src.rename(dst)
        u = self.run_usage()
        self.assertEqual((u["sessions"], u["sessions_deleted"]), (3, 0))
        dst.rename(src)
        u = self.run_usage()
        self.assertEqual((u["sessions"], u["sessions_deleted"]), (3, 0))

    def test_two_deleted_copies_of_a_session_count_once(self):
        shutil.copy(self.root / "b" / "s3.jsonl", self.root / "a" / "s3.jsonl")
        shutil.copytree(self.root / "b" / "s3", self.root / "a" / "s3")
        self.assertEqual(self.run_usage()["sessions"], 4)  # two files on disk count twice
        for slug in ("a", "b"):
            (self.root / slug / "s3.jsonl").unlink()
            shutil.rmtree(self.root / slug / "s3")
        u = self.run_usage()
        self.assertEqual((u["sessions"], u["sessions_deleted"], u["subagent_files"]), (3, 1, 1))
        self.assertEqual(self.foo(u), (3, 1, 2))

    def test_a_deleted_row_holds_no_hook_output_text(self):
        for sid, text in (("s4", "notes for 7f3a only"), ("s6", "Deploy notes 7f3b:\nmore"), ("s7", "[7f3c tag] x")):
            self.write("b", sid, [prompt("hi"), att("hook_additional_context", content=[text], hookName="SessionStart",
                                                    hookEvent="SessionStart", toolUseID=f"t{sid}")])
        self.run_usage()
        self.assertIn("notes for 7f3a only", self.cache().read_text())
        for sid in ("s4", "s6", "s7"):
            (self.root / "b" / f"{sid}.jsonl").unlink()
        u = self.run_usage()
        self.assertEqual((u["sessions"], u["sessions_deleted"]), (6, 3))
        for marker in ("7f3a", "7f3b", "7f3c"):
            self.assertNotIn(marker, self.cache().read_text())
        start = next(h for h in u["hooks"] if h["by"] == "name" and h["hook"] == "SessionStart")
        self.assertEqual(start["sessions"], 3)

    def test_another_root_neither_counts_nor_drops_deleted_rows(self):
        self.run_usage()
        (self.root / "b" / "s2.jsonl").unlink()
        self.run_usage()
        other = Path(self.tmp.name) / "other"
        (other / "z").mkdir(parents=True)
        (other / "z" / "s9.jsonl").write_text(prompt("hi") + "\n")
        u = context.usage(context.load_rows(other, self.cache()))
        self.assertEqual((u["sessions"], u["sessions_deleted"]), (1, 0))
        u = self.run_usage()
        self.assertEqual((u["sessions"], u["sessions_deleted"]), (3, 1))

    def test_a_root_through_a_symlink_has_no_deleted_session(self):
        self.run_usage()
        link = Path(self.tmp.name) / "link"
        link.symlink_to(self.root)
        u = context.usage(context.load_rows(link, self.cache()))
        self.assertEqual((u["sessions"], u["sessions_deleted"]), (3, 0))


class ContextCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "projects"
        (self.root / "-home-user-projects").mkdir(parents=True)
        shutil.copy(FIXTURE, self.root / "-home-user-projects" / f"{SID}.jsonl")
        self.out = Path(self.tmp.name) / "runs"
        # The on-disk marks read settings and skills of a home: use an empty one, not the real one.
        self.home = unittest.mock.patch("walle.context.HOME", Path(self.tmp.name) / "home")
        self.home.start()

    def tearDown(self):
        self.home.stop()
        self.tmp.cleanup()

    def run_cli(self, *args) -> Path:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--out", str(self.out), "context", "--transcripts", str(self.root), *args])
        self.assertEqual(code, 0)
        return Path(buf.getvalue().strip())

    def test_writes_context_json_and_md(self):
        md = self.run_cli("--session", "630f787e")
        data = json.loads((md.parent / "context.json").read_text())
        self.assertEqual(data["run"]["kind"], "context")
        self.assertEqual(data["run"]["estimator"], "bytes-v2")
        fc = data["first_call"]
        self.assertEqual(fc["measured"]["total"], 90499)
        self.assertEqual(fc["residual"]["tokens"], 90499 - sum(r["tokens_est"] for r in fc["sources"]))
        rows = {r["source"]: r for r in fc["sources"]}
        self.assertEqual(rows["system_prompt"]["on_disk"]["mark"], "not on disk")
        self.assertIn("Residual (measured minus estimates)", md.read_text())
        # A context run does not move `latest`. Its index line has kind context, so it is not a scan.
        self.assertFalse((self.out / "latest").exists())
        from walle import runs
        self.assertEqual(runs.scans(self.out), [])
        row = runs.read_index(self.out)[-1]
        self.assertEqual((row["kind"], row["id"], row["estimator"], row["counting_revision"], row["session"]),
                         ("context", data["run"]["id"], "bytes-v2", 1, SID))
        md1 = self.run_cli("--last", "--estimator", "bytes-v1")
        self.assertEqual(json.loads((md1.parent / "context.json").read_text())["run"]["estimator"], "bytes-v1")

    def test_estimator_takes_the_short_names_of_scan(self):
        for short, full in (("v1", "bytes-v1"), ("v2", "bytes-v2")):
            data = json.loads((self.run_cli("--last", "--estimator", short).parent / "context.json").read_text())
            self.assertEqual((data["run"]["estimator"], data["run"]["estimator_detail"]["name"]), (full, full))
            rows = {r["source"]: r for r in data["first_call"]["sources"]}
            self.assertEqual(rows["deferred_tools"]["basis"], "bytes-v1" if short == "v1" else "fit")
        with redirect_stdout(io.StringIO()), unittest.mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["--out", str(self.out), "context", "--transcripts", str(self.root), "--estimator", "v9"])

    def test_json_prints_what_context_json_holds(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--out", str(self.out), "context", "--transcripts", str(self.root),
                             "--session", "630f787e", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["first_call"]["measured"]["total"], 90499)
        self.assertEqual(data["session"]["id"], SID)
        # The run is still written and indexed, and stdout is the same object as its context.json.
        from walle import runs
        row = runs.read_index(self.out)[-1]
        self.assertEqual(json.loads(Path(row["path"]).read_text()), data)
        self.assertEqual(data["run"]["id"], row["id"])

    def test_json_with_usage(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--out", str(self.out), "context", "--transcripts", str(self.root), "--usage", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("servers", data["usage"])
        self.assertNotIn("first_call", data)

    def test_json_refuses_compare(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), unittest.mock.patch("sys.stderr", err):
            code = cli.main(["--out", str(self.out), "context", "--compare", "a", "b", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("--json does not work with --compare", err.getvalue())
        self.assertFalse(self.out.exists())

    def test_errors(self):
        buf = io.StringIO()
        with redirect_stdout(buf), unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(cli.main(["--out", str(self.out), "context", "--transcripts", str(self.root),
                                       "--session", "nope"]), 2)
            self.assertEqual(cli.main(["--out", str(self.out), "context", "--transcripts", str(self.root),
                                       "--agent", "codex"]), 2)


class MeasuredValuesStayOutTest(unittest.TestCase):
    """Brief 2.1, rule L4: measured values never enter the verdict, Start here or the alarm."""

    def test_scan_is_the_same_with_context_runs_present(self):
        t = Tree()
        try:
            t.write("projects/app/CLAUDE.md", "# App\n\n- Always run the linter before every task.\n")
            with unittest.mock.patch("walle.context.HOME", t.home):
                before = scan([t.repo], t.home)
                out = t.home / ".walle" / "runs"
                root = t.home / ".claude" / "projects" / "-x"
                root.mkdir(parents=True)
                shutil.copy(FIXTURE, root / f"{SID}.jsonl")
                with redirect_stdout(io.StringIO()):
                    cli.main(["--out", str(out), "context", "--transcripts", str(root.parent)])
                after = scan([t.repo], t.home)
            strip = lambda d: {k: v for k, v in d["triage"]["stacks"].items()}
            self.assertEqual(strip(before), strip(after))
            self.assertNotIn("context", after)
            self.assertNotIn("first_call", json.dumps(after["triage"]))
        finally:
            t.close()


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
