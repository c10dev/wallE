import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

from walle import cli, codex_context, estimate, rollout
from walle.stack import CODEX_PROJECT_DOC_MAX, codex_settings, codex_stack

FIXTURES = Path(__file__).parent / "fixtures" / "codex"
BASELINE = "00000000-0000-7000-8000-000000000101"  # Desktop, content_item_kinds, the plan's baseline rollout
NO_KINDS = "00000000-0000-7000-8000-000000000201"  # Desktop, 2026-08: no kinds, memory, ponytail hook
FORKED = "00000000-0000-7000-8000-000000000301"  # a subagent thread forked with its parent's history
RESUMED = "00000000-0000-7000-8000-000000000401"  # a later page that resumes its thread (history_base)


def fixture(thread: str) -> Path:
    return next(FIXTURES.glob(f"rollout-*-{thread}*.jsonl"))


def sources(thread: str):
    fc = rollout.first_call(rollout.load(fixture(thread)))
    return fc, {s.kind: s for s in fc.sources}


class BaselineFixtureTest(unittest.TestCase):
    """The baseline rollout of plan 3.2, with its text replaced by filler of the same UTF-8 length."""

    def setUp(self):
        self.fc, self.src = sources(BASELINE)

    def test_reproduces_the_plan_numbers(self):
        self.assertEqual(self.fc.measured, {"input": 31800, "cached": 0, "cache_write": 0, "total": 31800})
        # Plan 3.2 counts characters / 4 against 31,800 tokens.
        chars = {k: len(s.text) for k, s in self.src.items()}
        share = {k: round(100 * chars[k] / 4 / 31800, 1) for k in chars}
        self.assertEqual((chars["skill_listing"], self.src["skill_listing"].count, share["skill_listing"]), (40295, 106, 31.7))
        self.assertEqual((chars["base_instructions"], share["base_instructions"]), (21769, 17.1))
        self.assertEqual((chars["agents_md"], share["agents_md"]), (20092, 15.8))
        self.assertEqual((chars["app_context"], share["app_context"]), (10716, 8.4))
        # Plan 3.3: the unity, figma and work-pets lines are 19,875 characters.
        lines = [l for l in self.src["skill_listing"].text.split("\n") if l.startswith(("- unity:", "- figma:", "- work-pets:"))]
        self.assertEqual((len(lines), sum(len(l) + 1 for l in lines)), (48, 19875))

    def test_deferred_dynamic_tools_are_not_sent(self):
        tn = self.src["tool_namespaces"].detail
        self.assertEqual(tn["dynamic_deferred"], [{"name": "codex_app", "tools": 28, "bytes": 27727}])
        self.assertEqual(tn["dynamic_sent"], ["attach_artifact", "remove_artifact", "list_artifacts", "read_thread_terminal"])
        self.assertEqual(len(tn["namespaces"]), 18)
        # The <tools> block and the 4 schemas without deferLoading; the 28 deferred schemas would be 27,727 bytes more.
        self.assertEqual(self.src["tool_namespaces"].bytes, 3522 + 2380)

    def test_the_rest_goes_to_its_own_rows(self):
        self.assertEqual(self.src["other"].detail["kinds"], {"additional_content.codex_apps_client_time_context": 1,
                                                             "additional_content.codex_apps_open_page": 1,
                                                             "additional_content.codex_apps_open_page_instructions": 1})
        self.assertEqual((self.src["first_prompt"].bytes, self.src["history"].count), (188, 0))
        self.assertEqual(self.src["memory"].count, 0)

    def test_bytes_v1_estimates_and_residual(self):
        data = codex_context.session_context(self.fc, "bytes-v1", home=Path(tempfile.mkdtemp()))
        rows = {r["source"]: r for r in data["sources"]}
        self.assertEqual((rows["skill_listing"]["tokens_est"], rows["skill_listing"]["share"]), (10097, 31.8))
        self.assertEqual(data["estimated_total"], 26258)
        self.assertEqual(data["residual"]["tokens"], 31800 - 26258)
        groups = {g["plugin"]: g for g in data["skill_plugins"]}
        self.assertEqual(sum(groups[p]["tokens_est"] for p in ("unity", "figma", "work-pets")), 4980)  # 15.7% of 31,800
        self.assertEqual(groups["(own skills)"]["skills"], 6)
        self.assertEqual(groups["(system skills)"]["skills"], 4)


class OlderRolloutTest(unittest.TestCase):
    """A 2026-08 rollout: no content_item_kinds, so each part is sorted by its leading tag."""

    def test_tags_sort_the_parts(self):
        fc, src = sources(NO_KINDS)
        self.assertEqual(fc.measured["input"], 29956)
        self.assertEqual((src["memory"].count, src["memory"].bytes), (1, 9332))
        self.assertEqual((src["hook_context"].count, src["hook_context"].bytes), (1, 5252))
        self.assertEqual((src["skill_listing"].count, src["agents_md"].count, src["app_context"].count), (78, 1, 1))
        self.assertEqual(src["plugins_apps"].detail["kinds"],
                         {"apps.instructions": 1, "plugins.usage_instructions": 1, "plugins.recommendations": 1})
        self.assertEqual(src["multi_agent"].detail["kinds"],
                         {"multi_agent.usage_hint": 1, "multi_agent.mode_instructions": 1})
        self.assertEqual(src["other"].count, 0)


class ForkAndResumeTest(unittest.TestCase):
    def test_a_forked_thread_counts_its_parents_history(self):
        fc, src = sources(FORKED)
        self.assertTrue(fc.subagent)
        self.assertIsNotNone(fc.forked_from)
        self.assertIsNone(fc.resumed)
        self.assertEqual(fc.measured["input"], 30573)
        self.assertEqual(src["history"].detail["kinds"], {"user.text": 2, "assistant": 2})
        # The turn that started the call: the parent's last prompt and the agent message to the subagent.
        self.assertEqual(src["first_prompt"].detail["kinds"], {"user.text": 1, "agent_message": 1})
        self.assertEqual(src["memory"].count, 1)

    def test_a_resumed_page_is_marked(self):
        fc, src = sources(RESUMED)
        self.assertEqual(fc.resumed, RESUMED)  # it resumes its own thread's first page
        self.assertEqual(fc.page, "00000000-0000-7000-8000-000000000402")
        self.assertEqual(fc.measured["input"], 220344)
        self.assertEqual(src["skill_listing"].count, 0)  # its context is in the earlier page


def write_rollout(path: Path, thread: str, items: list[dict], meta: dict | None = None, usage: int = 1000) -> Path:
    """A small rollout: session_meta, the given response_items and event_msgs, then a token_count."""
    lines = [{"type": "session_meta", "payload": dict({"id": thread, "originator": "Codex Desktop", "timestamp": "2026-10-01T00:00:00Z",
                                                        "base_instructions": {"text": "base"}}, **(meta or {}))}]
    lines += items
    lines.append({"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": usage}}}})
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    return path


def msg(role: str, *texts: str, kinds: list[str] | None = None) -> dict:
    p = {"type": "message", "role": role, "content": [{"type": "input_text", "text": t} for t in texts]}
    if kinds:
        p["internal_chat_message_metadata_passthrough"] = {"content_item_kinds": kinds}
    return {"type": "response_item", "payload": p}


SKILLS = ("<skills_instructions>\n## Skills\n### Skill roots\n- `r0` = `/h/.codex/skills`\n"
          "- `r1` = `/h/.codex/plugins/cache/m/pl/1.0/skills`\n### Available skills\n"
          "- foo: Does foo. (file: r0/foo/SKILL.md)\n- pl:bar: Does bar. (file: r1/bar/SKILL.md)\n"
          "- pl:baz: Does baz. (file: r1/baz/SKILL.md)\n- quux: Never used. (file: r0/quux/SKILL.md)\n</skills_instructions>")


class SyntheticRolloutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "sessions"
        self.dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def name(self, thread: str, page: str | None = None) -> Path:
        return self.dir / (f"rollout-2026-10-01T00-00-00-{thread}" + (f"_{page}" if page else "") + ".jsonl")

    def test_lines_split_on_newline_only(self):
        # U+2028 and U+2029 inside a JSON string: str.splitlines() would cut the line there.
        text = "first second third"
        p = write_rollout(self.name("aaaaaaaa-0000-7000-8000-000000000001"), "aaaaaaaa-0000-7000-8000-000000000001",
                          [msg("user", text)])
        self.assertIn(" ".encode(), p.read_bytes())
        fc = rollout.first_call(rollout.load(p))
        self.assertEqual({s.kind: s.text for s in fc.sources}["first_prompt"], text)

    def test_skills_and_tools_loaded_and_used(self):
        tid = "aaaaaaaa-0000-7000-8000-000000000002"
        mcp = {"type": "McpToolCall", "id": "m1", "server": "codex_apps", "tool": "linear.get_issue"}
        items = [
            msg("developer", SKILLS, kinds=["host_skills.instructions"]),
            msg("user", "use $foo please", kinds=["user.text"]),
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                                                  "input": "cat /h/.codex/plugins/cache/m/pl/1.0/skills/bar/SKILL.md"}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                                  "arguments": json.dumps({"cmd": "sed -n 1,20p ~/elsewhere/other/SKILL.md"})}},
            msg("developer", "<skill>\n<name>pl:baz</name>\nbody</skill>", kinds=["skills.selected_skill_instructions"]),
            {"type": "event_msg", "payload": {"type": "item_completed", "item": mcp}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": mcp}},  # the same item again
            {"type": "event_msg", "payload": {"type": "item_completed",
                                              "item": {"type": "DynamicToolCall", "id": "d1", "namespace": None, "tool": "linear_graphql"}}},
        ]
        row = rollout.usage_row(rollout.load(write_rollout(self.name(tid), tid, items)))
        self.assertEqual(row["listed"], ["foo", "pl:bar", "pl:baz", "quux"])
        self.assertEqual(row["skills_used"], {"foo": 1, "pl:bar": 1, "pl:baz": 1})
        self.assertEqual(row["skill_reads_unlisted"], {"other/SKILL.md": 1})
        self.assertEqual(row["tools"], {"exec": 1, "exec_command": 1})
        self.assertEqual(row["servers_used"], {"codex_apps:linear": {"get_issue": 1}, "dynamic:linear_graphql": {"linear_graphql": 1}})
        self.assertEqual(row["skill_lines"]["foo"], len("- foo: Does foo. (file: r0/foo/SKILL.md)") + 1)

    def test_usage_folds_pages_and_leaves_out_resumed_first_calls(self):
        a, b = "aaaaaaaa-0000-7000-8000-000000000003", "aaaaaaaa-0000-7000-8000-000000000004"
        page = "aaaaaaaa-0000-7000-8000-000000000005"
        skills = [msg("developer", SKILLS, kinds=["host_skills.instructions"]), msg("user", "hi")]
        write_rollout(self.name(a), a, skills, usage=20000)
        write_rollout(self.name(a, page), a, [msg("user", "use $quux")], meta={"history_base": {"thread_id": a}}, usage=90000)
        write_rollout(self.name(b), b, skills, meta={"history_base": {"thread_id": "elsewhere"}}, usage=300000)
        rows, skipped = codex_context.load_rows(Path(self.tmp.name), Path(self.tmp.name) / "cache" / "u.json")
        u = codex_context.usage(rows, skipped=skipped)
        self.assertEqual((u["sessions"], u["rollout_files"]), (2, 3))
        self.assertEqual(u["first_calls"]["Codex Desktop"]["n"], 1)
        self.assertEqual(u["first_calls"]["Codex Desktop"]["median"], 20000)
        self.assertEqual(u["first_calls_excluded"], {"resumed another thread": 1})
        quux = next(s for s in u["skills"] if s["skill"] == "quux")
        self.assertEqual((quux["sessions_listed"], quux["sessions_used"]), (2, 1))  # used on the later page
        groups = {g["group"]: g for g in u["skill_groups"]}
        self.assertEqual((groups["pl"]["skills"], groups["pl"]["sessions_used"]), (2, 0))
        self.assertEqual(groups["(own skills)"]["skills"], 2)

    def test_compressed_rollouts(self):
        tid = "aaaaaaaa-0000-7000-8000-000000000006"
        plain = write_rollout(self.name(tid), tid, [msg("user", "hello")], usage=1234)
        if shutil.which("zstd"):
            subprocess.run(["zstd", "-q", "--rm", str(plain)], check=True)
            zst = plain.with_name(plain.name + ".zst")
            self.assertEqual(rollout.load(zst).first_usage["input_tokens"], 1234)
        else:
            zst = plain.with_name(plain.name + ".zst")
            plain.rename(zst)  # the reader never gets to its bytes without the zstd program
        # Without the zstd program walle skips the file and says so.
        with unittest.mock.patch("walle.rollout.zstd_program", return_value=None):
            self.assertIsNone(rollout.load(zst))
            rows, skipped = codex_context.load_rows(Path(self.tmp.name), Path(self.tmp.name) / "cache" / "z.json")
            self.assertEqual((rows, skipped), ({}, {"zstd_missing": 1, "unreadable": 0}))
            md = codex_context.render({"run": {"estimator": "bytes-v1"}, "note": "",
                                       "usage": codex_context.usage(rows, skipped=skipped)})
            self.assertIn("Skipped 1 zstd-compressed files (install the zstd program to read them)", md)
            with self.assertRaisesRegex(codex_context.CodexError, "install the zstd program"):
                codex_context.find_rollout(Path(self.tmp.name), tid)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "runs"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args) -> Path:
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["--out", str(self.out), "context", "--agent", "codex",
                                       "--transcripts", str(FIXTURES), *args]), 0)
        return Path(buf.getvalue().strip())

    def test_one_session(self):
        md = self.run_cli("--session", BASELINE)
        data = json.loads((md.parent / "context.json").read_text())
        self.assertEqual((data["run"]["agent"], data["run"]["estimator"]), ("codex", "bytes-v1"))
        self.assertEqual(data["run"]["codex_fit"], estimate.CODEX_FIT)
        self.assertEqual(data["first_call"]["measured"]["total"], 31800)
        self.assertEqual(data["session"]["originator"], "Codex Desktop")
        text = md.read_text()
        self.assertIn("# walle context: Codex session 00000000", text)
        self.assertIn("| Skill list | 40,383 | 106 | 10,097 | 31.8% |", text)
        self.assertIn("## Skill list by plugin", text)
        # bytes-v2 on request, named as Claude's rates.
        data = json.loads((self.run_cli("--session", BASELINE, "--estimator", "v2").parent / "context.json").read_text())
        self.assertEqual(data["first_call"]["sources"][0]["basis"], "bytes-v2 prose rates (fitted on Claude tokens)")

    def test_last_skips_resumed_rollouts(self):
        folder = Path(self.tmp.name) / "rollouts"
        shutil.copytree(FIXTURES, folder)
        for i, thread in enumerate((FORKED, BASELINE, NO_KINDS, RESUMED)):  # the resumed page is the newest file
            os.utime(folder / fixture(thread).name, (1_000_000 + i, 1_000_000 + i))
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["--out", str(self.out), "context", "--agent", "codex", "--transcripts", str(folder), "--last"])
        data = json.loads((Path(buf.getvalue().strip()).parent / "context.json").read_text())
        self.assertEqual(data["session"]["id"], NO_KINDS)

    def test_usage(self):
        data = json.loads((self.run_cli("--usage").parent / "context.json").read_text())
        u = data["usage"]
        self.assertEqual((u["sessions"], u["rollout_files"]), (4, 4))
        self.assertEqual(u["first_calls_excluded"], {"resumed another thread": 1})
        self.assertEqual(u["first_calls"]["Codex Desktop"]["n"], 3)
        self.assertTrue((self.out.parent / "cache" / "context-usage-codex.json").is_file())

    def test_errors(self):
        with redirect_stdout(io.StringIO()), unittest.mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(cli.main(["--out", str(self.out), "context", "--agent", "codex", "--transcripts",
                                       str(FIXTURES), "--session", "ffff"]), 2)
            self.assertEqual(cli.main(["--out", str(self.out), "context", "--agent", "codex", "--transcripts",
                                       str(FIXTURES), "--session", "00000000"]), 2)
            self.assertEqual(cli.main(["--out", str(self.out), "context", "--agent", "codex", "--transcripts",
                                       self.tmp.name]), 2)
        self.assertIn("matches 4 Codex sessions", err.getvalue())
        self.assertIn("no Codex rollouts under", err.getvalue())


class CodexStackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve() / "home"
        self.codex = self.home / ".codex"
        self.codex.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def skill(self, rel: str, front: str, yaml: str | None = None):
        d = self.codex / "skills" / rel
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: Does {d.name}.\n{front}---\nbody\n")
        if yaml is not None:
            (d / "agents").mkdir()
            (d / "agents" / "openai.yaml").write_text(yaml)

    def test_settings_read_the_cap_and_no_secret_values(self):
        self.assertEqual(codex_settings(self.home)["project_doc_max_bytes"], CODEX_PROJECT_DOC_MAX)
        (self.codex / "config.toml").write_text(
            'project_doc_max_bytes = 100\napproval_policy = "never"\n[features]\nmemories = false\n'
            '[features.multi_agent_v2]\nenabled = true\n[mcp_servers.db]\nurl = "https://example.invalid/?token=SECRET1"\n'
            '[mcp_servers.db.env]\nTOKEN = "SECRET2"\n[shell_environment_policy.filters]\nKEY = "SECRET3"\n'
            '[plugins."x@m"]\nenabled = false\n')
        s = codex_settings(self.home)
        self.assertEqual((s["project_doc_max_bytes"], s["project_doc_max_bytes_set"], s["approval_policy"]), (100, True, "never"))
        self.assertEqual((s["features"], s["mcp_servers"], s["plugins"]),
                         ({"memories": False, "multi_agent_v2": True}, ["db"], {"x@m": False}))
        self.assertNotIn("SECRET", json.dumps(s))
        # The cap comes from config.toml.
        repo = self.home / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "AGENTS.md").write_text("x" * 150)
        st = codex_stack(repo, self.home)
        self.assertEqual(st.layers[-1].note, "over the 100 byte project doc cap: Codex truncates")

    def test_skills_follow_codex_listing_rules(self):
        self.skill("listed", "")
        self.skill("claude-only", "disable-model-invocation: true\n")  # a Claude rule: Codex lists it
        self.skill("explicit", "", "policy:\n  allow_implicit_invocation: false\n")
        self.skill("implicit", "", "policy:\n  allow_implicit_invocation: true\n")
        self.skill(".system/sys", "")
        st = codex_stack(self.home, self.home)
        cat = st.layers[-1]
        self.assertEqual(cat.label, "skill_catalog")
        lines = st.layers[-1].note
        self.assertIn("3 codex skills; 1 not listed (agents/openai.yaml allow_implicit_invocation: false)", lines)
        self.assertIn("1 system skills", lines)
        self.assertEqual(cat.bytes, len("\n".join(f"- {n}: Does {n}. (file: r0/{n}/SKILL.md)"
                                                  for n in ("claude-only", "implicit", "listed")).encode()))


if __name__ == "__main__":
    unittest.main()
