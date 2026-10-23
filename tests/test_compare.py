"""walle context --compare (plan C6) on anonymized transcripts: the 630f787e fixture and synthetic sessions."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sessions import claude_session, codex_rollout, slug  # noqa: E402
from test_measure import APPLY, AT, Home, day, fill  # noqa: E402

from walle import cost, runs  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "session-630f787e.jsonl"
SID = "630f787e-f784-445a-9d87-19be85313a8b"


def without_skill(src: Path, dst: Path, sid: str) -> int:
    """A copy of the fixture whose skill list lacks its first skill. Returns the bytes of that skill's line."""
    lines, cut = [], 0
    for raw in src.read_text().splitlines():
        d = json.loads(raw)
        a = d.get("attachment") or {}
        if d.get("type") == "attachment" and a.get("type") == "skill_listing":
            name = a["names"][0]
            body = a["content"].split("\n")
            keep = [x for x in body if not x.startswith(f"- {name}: ")]
            cut = len("\n".join(body).encode()) - len("\n".join(keep).encode())
            a["names"] = a["names"][1:]
            a["content"] = "\n".join(keep)
        if d.get("sessionId") == SID:
            d["sessionId"] = sid
        lines.append(json.dumps(d, ensure_ascii=False))
    dst.write_text("\n".join(lines) + "\n")
    return cut


class PairTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()
        d = self.h.projects / "-fixture"
        d.mkdir(parents=True)
        self.a = d / f"{SID}.jsonl"
        self.a.write_bytes(FIXTURE.read_bytes())
        self.sid_b = "730f787e-0000-4000-8000-000000000000"
        self.cut = without_skill(FIXTURE, d / f"{self.sid_b}.jsonl", self.sid_b)

    def tearDown(self):
        self.h.close()

    def compare(self, *refs, code=0, extra=()):
        out, err = self.h.cli("context", "--compare", *refs, "--transcripts", str(self.h.projects), *extra, code=code)
        if code:
            return err, None
        data = json.loads((Path(out.strip().splitlines()[-1]).parent / "compare.json").read_text())
        return out, data

    def test_two_sessions_source_by_source(self):
        out, data = self.compare("630f787e", "730f787e")
        c = data["compare"]
        rows = {r["source"]: r for r in c["sources"]}
        self.assertEqual(rows["skill_listing"]["a"]["bytes"], 29698)
        self.assertEqual(rows["skill_listing"]["delta_bytes"], -self.cut)
        self.assertEqual(rows["skill_listing"]["b"]["items"], 63)
        self.assertLess(rows["skill_listing"]["delta_tokens"], 0)
        self.assertEqual(rows["deferred_tools"]["delta_bytes"], 0)
        self.assertTrue(rows["first_prompt"]["per_session"])
        self.assertEqual((c["measured"]["a"], c["measured"]["same_family"]), (90499, True))
        self.assertEqual(len(c["loaded"]["skills"]["a_only"]), 1)
        self.assertEqual(c["loaded"]["skills"]["b_only"], [])
        self.assertEqual(data["run"]["kind"], "compare")
        self.assertIn("estimator bytes-v2 = bytes-v2  counting revision 1 = 1", out)
        self.assertIn("* changes with every session. It is not a setting.", out)
        self.assertIn("Measured first call  90,499 -> 90,499 tokens (0)\n"
                      "Billed as            121,431 -> 121,431 input-equivalent tokens (0)\n"
                      f"                     $0.4857 -> $0.4857 at opus 5.5 list prices of {cost.PRICES_AS_OF}\n"
                      "One session on each side is not a median", out)
        self.assertEqual(c["measured"]["billed"]["delta"], 0)
        self.assertTrue(all(len(x) <= 80 for x in out.splitlines()[:-1]), out)

    def test_estimator_and_counting_revision_mismatch(self):
        a, _ = self.h.cli("context", "--session", "630f787e", "--estimator", "v1", "--transcripts", str(self.h.projects))
        b, _ = self.h.cli("context", "--session", "730f787e", "--transcripts", str(self.h.projects))
        ra, rb = Path(a.strip()).parent, Path(b.strip()).parent
        # Context runs by id: bytes compare, estimated tokens do not.
        out, data = self.compare(ra.name, rb.name)
        c = data["compare"]
        self.assertEqual((c["comparable"], c["estimator"]), ({"bytes": True, "tokens": False}, ["bytes-v1", "bytes-v2"]))
        self.assertTrue(all(r["delta_tokens"] is None for r in c["sources"]))
        self.assertEqual({r["source"]: r["delta_bytes"] for r in c["sources"]}["skill_listing"], -self.cut)
        self.assertIn("estimator changed (bytes-v1 -> bytes-v2): token totals are not comparable.", out)
        # Another counting revision: the sources split another way, so no source compares. The measured call does.
        j = rb / "context.json"
        d = json.loads(j.read_text())
        d["run"]["counting_revision"] = 2
        j.write_text(json.dumps(d))
        out, data = self.compare(ra.name, str(rb))
        c = data["compare"]
        self.assertEqual((c["comparable"]["bytes"], c["sources"], c["counting_revision"]), (False, [], [1, 2]))
        self.assertEqual(c["measured"]["delta"], 0)
        self.assertIn("The first call was split another way: source bytes are not comparable.", out)
        self.assertIn("token counting changed (revision 1 -> 2)", out)

    def test_refusals(self):
        u, _ = self.h.cli("context", "--usage", "--transcripts", str(self.h.projects))
        err, _ = self.compare(Path(u.strip()).parent.name, "630f787e", code=2)
        self.assertIn("is a --usage run", err)
        err, _ = self.compare("ffffffff", "630f787e", code=2)
        self.assertIn("is not a context run or a session", err)
        err, _ = self.compare("a", "b", "c", code=2)
        self.assertIn("give two runs or session ids", err)
        err, _ = self.compare("not-a-date", code=2)
        self.assertIn("is not an apply id or a date", err)


class ConnectorNameTest(unittest.TestCase):
    """A desktop session names a claude.ai connector by UUID, a CLI session by name. Without the --usage cache,
    compare still shows one connector."""

    UUID = "12345678-aaaa-4bbb-8ccc-1234567890ab"

    def setUp(self):
        self.h = Home()
        tools = ["search", "read_thread", "send"]
        claude_session(self.h.projects, "desk", self.h.app, day(20, 1), servers={self.UUID: tools},
                       instructions={self.UUID: "Gmail connector instructions " * 4})
        claude_session(self.h.projects, "cli", self.h.app, day(21, 1), entrypoint="cli",
                       servers={"claude_ai_Gmail": tools}, instructions={"claude.ai Gmail": "Gmail connector instructions " * 2})
        self.assertFalse((self.h.out.parent / "cache" / "context-usage.json").exists())

    def tearDown(self):
        self.h.close()

    def check(self, data):
        c = data["compare"]
        self.assertEqual((c["loaded"]["servers"]["a_only"], c["loaded"]["servers"]["b_only"]), ([], []))
        inside = {r["source"]: r["inside"] for r in c["sources"]}
        # Per tool name: "mcp__" + server + "__" + tool + newline; the tools have 21 characters in all.
        self.assertEqual(inside["deferred_tools"], [["claude.ai Gmail", 3 * (len(self.UUID) + 8) + 21, 3 * (15 + 8) + 21]])
        self.assertEqual([e[0] for e in inside["mcp_instructions"]], ["claude.ai Gmail"])

    def test_two_sessions(self):
        out, _ = self.h.cli("context", "--compare", "desk", "cli", "--transcripts", str(self.h.projects))
        self.check(json.loads((Path(out.strip().splitlines()[-1]).parent / "compare.json").read_text()))
        self.assertIn("claude.ai Gmail -63", out)
        self.assertNotIn("(connector id)", out)

    def test_around_an_apply_on_a_machine_without_the_cache(self):
        self.h.apply_row(APPLY, "2026-10-20T12:00:00+00:00")
        out, _ = self.h.cli("context", "--compare", APPLY, "--root", str(self.h.app), "--transcripts", str(self.h.projects))
        data = json.loads((Path(out.strip().splitlines()[-1]).parent / "compare.json").read_text())
        self.assertEqual((data["a"]["session"]["id"], data["b"]["session"]["id"]), ("desk", "cli"))
        self.check(data)

    def test_a_context_run_made_without_the_cache(self):
        run, _ = self.h.cli("context", "--session", "desk", "--transcripts", str(self.h.projects))
        self.assertIn("(connector id)", (Path(run.strip()).parent / "context.json").read_text())
        out, _ = self.h.cli("context", "--compare", Path(run.strip()).parent.name, "cli",
                            "--transcripts", str(self.h.projects))
        self.check(json.loads((Path(out.strip().splitlines()[-1]).parent / "compare.json").read_text()))


class AroundApplyTest(unittest.TestCase):
    """--compare <apply-id>: the newest session on each side, the applied rows, the folder's medians."""

    def setUp(self):
        self.h = Home()
        fill(self.h)
        skills = ("anthropic-skills:pptx", "anthropic-skills:xlsx")
        claude_session(self.h.projects, "zb", self.h.app, day(21, 8), total=95000, skills=skills,
                       servers={"rea": ["open", "close"], "portboard": ["list"]}, calls=("mcp__portboard__list",))
        claude_session(self.h.projects, "za", self.h.app, day(21, 23), total=85000, skills=skills[1:],
                       servers={"portboard": ["list"]})
        claude_session(self.h.projects, "zw", self.h.web, day(21, 22), total=40000, skills=skills)
        self.h.apply_row()
        plan = self.h.out / "20261021-041703"
        plan.mkdir(parents=True)
        (plan / "plan.json").write_text(json.dumps({"rows": [
            {"id": "e94298", "source": {"type": "claude.ai skill", "key": "anthropic-skills:pptx",
                                        "name": "anthropic-skills:pptx"}, "title": "Hide the skill"},
            {"id": "f00001", "source": {"type": "local MCP server", "key": "rea", "name": "rea"}, "title": "Remove rea"},
            {"id": "f00002", "source": {"type": "hook output", "key": "h", "name": "h"}, "title": "Trim a hook"}]}))
        (self.h.out / APPLY).mkdir()
        (self.h.out / APPLY / "apply.json").write_text(json.dumps({"rows": [
            {"id": "e94298", "changed": True}, {"id": "f00001", "changed": True}, {"id": "f00002", "changed": True}],
            "plan": {"id": "20261021-041703", "path": str(plan / "plan.json")}}))

    def tearDown(self):
        self.h.close()

    def test_newest_pair_in_this_folder_and_applied_rows(self):
        out, _ = self.h.cli("context", "--compare", APPLY, "--root", str(self.h.app),
                            "--transcripts", str(self.h.projects))
        data = json.loads((Path(out.strip().splitlines()[-1]).parent / "compare.json").read_text())
        self.assertEqual((data["a"]["session"]["id"], data["b"]["session"]["id"]), ("zb", "za"))
        self.assertTrue(data["same_folder"])
        c = data["compare"]
        self.assertEqual(c["loaded"]["servers"]["a_only"], ["rea"])
        self.assertEqual(c["loaded"]["skills"]["a_only"], ["anthropic-skills:pptx"])
        applied = {r["id"]: r for r in data["applied"]}
        # Sessions in the windows, all folders: 12 + zb + 6 in web before; 11 + 3 + 1 resumed + za + zw since.
        self.assertEqual(applied["e94298"]["before"], {"sessions": 19, "loaded": 1, "used": 0})
        self.assertEqual(applied["e94298"]["after"], {"sessions": 17, "loaded": 1, "used": 0})
        self.assertEqual((applied["f00001"]["before"]["loaded"], applied["f00001"]["after"]["loaded"]), (1, 0))
        self.assertIsNone(applied["f00002"]["kind"])
        m = data["measure"]
        self.assertEqual(m["rows"][0]["family"], "opus 5.5")
        self.assertEqual((m["rows"][0]["before"]["sessions"], m["rows"][0]["after"]["sessions"]), (13, 12))
        self.assertIn("Applied rows: sessions that loaded and used each, before -> since (all folders).", out)
        self.assertIn("  e94298 skill anthropic-skills:pptx: loaded 1 of 19 -> 1 of 17, used 0 -> 0\n"
                      "    newest sessions: gone (before claude-desktop, since claude-desktop)\n", out)
        self.assertEqual(applied["e94298"]["pair"]["state"], "gone")
        self.assertIn("  f00001 server rea: loaded 1 of 19 -> 0 of 17, used 0 -> 0", out)
        self.assertIn("  f00002 - h: sessions do not show this row. See its check in the plan.", out)
        self.assertIn("Measured first call in ~/projects/app, one model family per row:", out)
        self.assertTrue(all(len(x) <= 80 for x in out.splitlines()[:-1]), out)
        row = runs.read_index(self.h.out)[-1]
        self.assertEqual((row["kind"], row["parent_id"]), ("compare", APPLY))

    def test_a_folder_without_sessions_on_one_side_takes_any_folder(self):
        out, _ = self.h.cli("context", "--compare", APPLY, "--root", str(self.h.home / "projects" / "none"),
                            "--transcripts", str(self.h.projects))
        self.assertIn("This folder has no session on one side, so the two sessions come from any folder.", out)

    def test_no_session_since_yet(self):
        out, _ = self.h.cli("context", "--compare", "2026-11-30", "--transcripts", str(self.h.projects))
        self.assertIn("No session since", out)


class PluginApplyTest(unittest.TestCase):
    """--compare <apply-id> after enabledPlugins["p@synced"] = false and skillOverrides["s1"] = "name-only".

    The apply.json and the plan are not in the runs folder: the index line has the path of the apply.json, as in a
    runs folder that holds a copy of index.jsonl only. The plan row of the skill is missing: its key names it.
    """

    def setUp(self):
        self.h = Home()
        elsewhere = Path(self.h.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "plan.json").write_text(json.dumps({"rows": [
            {"id": "aa0001", "source": {"type": "claude.ai plugin", "key": "p", "name": "p"},
             "title": "Turn off the claude.ai plugin p in Claude Code",
             "change": {"kind": "settings_key", "keys": ["enabledPlugins", "p@synced"], "value": False}}]}))
        (elsewhere / "apply.json").write_text(json.dumps({"rows": [
            {"id": "aa0001", "changed": True, "keys": ["enabledPlugins", "p@synced"]},
            {"id": "aa0002", "changed": True, "keys": ["skillOverrides", "s1"]}],
            "plan": {"id": "20261021-041703", "path": str(elsewhere / "plan.json")}}))
        runs.append_row(self.h.out, {"id": APPLY, "kind": "apply", "parent_id": "20261021-041703", "generated": AT,
                                     "roots": [], "rows": ["aa0001", "aa0002"], "status": "applied",
                                     "path": str(elsewhere / "apply.json")})
        # Before: a cli session loads p by its MCP server and calls it; the desktop session lists the p:* skills.
        claude_session(self.h.projects, "pc", self.h.app, day(20, 1), entrypoint="cli",
                       servers={"plugin_p_srv": ["x"]}, calls=("mcp__plugin_p_srv__x",))
        claude_session(self.h.projects, "pb", self.h.app, day(21, 1), skills=("p:a", "p:b", "s1"))
        # Since: the cli session lists no p:* skill.
        claude_session(self.h.projects, "qc", self.h.app, day(21, 10), entrypoint="cli", skills=("s1",))

    def tearDown(self):
        self.h.close()

    def compare(self, since_skills: tuple) -> tuple[str, dict]:
        claude_session(self.h.projects, "qd", self.h.app, day(21, 12), skills=since_skills)
        out, _ = self.h.cli("context", "--compare", APPLY, "--root", str(self.h.app),
                            "--transcripts", str(self.h.projects))
        data = json.loads((Path(out.strip().splitlines()[-1]).parent / "compare.json").read_text())
        self.assertEqual((data["a"]["session"]["id"], data["b"]["session"]["id"]), ("pb", "qd"))
        self.assertTrue(all(len(x) <= 80 for x in out.splitlines()[:-1]), out)
        return out, {r["id"]: r for r in data["applied"]}

    def test_the_since_session_still_lists_the_plugin(self):
        out, applied = self.compare(("p:a", "p:b", "s1"))
        p = applied["aa0001"]
        self.assertEqual((p["kind"], p["name"]), ("plugin", "p"))
        self.assertEqual(p["before"], {"sessions": 2, "loaded": 2, "used": 1})
        self.assertEqual(p["after"], {"sessions": 2, "loaded": 1, "used": 0})
        self.assertEqual(p["surfaces"]["cli"], {"before": {"sessions": 1, "loaded": 1, "used": 1},
                                                "after": {"sessions": 1, "loaded": 0, "used": 0}})
        self.assertEqual(p["pair"]["state"], "still_loaded")
        self.assertEqual((p["pair"]["before"]["entrypoint"], p["pair"]["after"]["entrypoint"]),
                         ("claude-desktop", "claude-desktop"))
        self.assertIn("  aa0001 plugin p: loaded 2 of 2 -> 1 of 2, used 1 -> 0\n"
                      "    claude-desktop: loaded 1 of 1 -> 1 of 1, used 0 -> 0\n"
                      "    cli: loaded 1 of 1 -> 0 of 1, used 1 -> 0\n"
                      "    newest sessions: still loaded (before claude-desktop, since claude-desktop)\n", out)
        s = applied["aa0002"]
        self.assertEqual((s["kind"], s["name"]), ("skill", "s1"))
        self.assertEqual((s["before"]["loaded"], s["after"]["loaded"]), (1, 2))
        self.assertIn("  aa0002 skill s1: loaded 1 of 2 -> 2 of 2, used 0 -> 0\n", out)
        self.assertNotIn("sessions do not show this row", out)

    def test_the_since_session_lists_no_plugin_skill(self):
        out, applied = self.compare(("s1",))
        p = applied["aa0001"]
        self.assertEqual((p["kind"], p["before"]["loaded"]), ("plugin", 2))
        self.assertEqual(p["after"], {"sessions": 2, "loaded": 0, "used": 0})
        self.assertEqual(p["pair"]["state"], "gone")
        self.assertIn("  aa0001 plugin p: loaded 2 of 2 -> 0 of 2, used 1 -> 0\n", out)
        self.assertIn("    newest sessions: gone (before claude-desktop, since claude-desktop)\n", out)


class CodexPairTest(unittest.TestCase):
    def test_two_threads(self):
        h = Home()
        try:
            a = "00000000-0000-7000-8000-000000000501"
            b = "00000000-0000-7000-8000-000000000502"
            codex_rollout(h.codex, a, h.app, day(20, 1), agents_md="- rule one\n" * 50, total=31000)
            codex_rollout(h.codex, b, h.app, day(21, 1), agents_md="- rule one\n" * 10, total=30000)
            out, _ = h.cli("context", "--compare", a, b, "--agent", "codex", "--transcripts", str(h.codex))
            data = json.loads((Path(out.strip().splitlines()[-1]).parent / "compare.json").read_text())
            rows = {r["source"]: r for r in data["compare"]["sources"]}
            self.assertEqual(rows["agents_md"]["delta_bytes"], -440)
            self.assertEqual(data["compare"]["estimator"], ["bytes-v1", "bytes-v1"])
            self.assertEqual(data["compare"]["measured"]["delta"], -1000)
            # The environment context and the skill bodies a prompt invoked change with each thread: starred.
            env = "<environment_context>\n  <cwd>{}</cwd>\n  <current_date>{}</current_date>\n</environment_context>"
            c = "00000000-0000-7000-8000-000000000503"
            d = "00000000-0000-7000-8000-000000000504"
            codex_rollout(h.codex, c, h.app, day(20, 2), total=33000, context=(
                env.format(h.app, "2026-10-20"), "<skill>\n<name>foo</name>\n" + "Step one of foo.\n" * 80 + "</skill>"))
            codex_rollout(h.codex, d, h.app, day(21, 2), total=30000, context=(env.format(h.web, "2026-10-21"),))
            out, _ = h.cli("context", "--compare", c, d, "--agent", "codex", "--transcripts", str(h.codex))
            data = json.loads((Path(out.strip().splitlines()[-1]).parent / "compare.json").read_text())
            rows = {r["source"]: r for r in data["compare"]["sources"]}
            self.assertTrue(rows["skill_bodies"]["per_session"] and rows["environment"]["per_session"])
            self.assertLess(rows["skill_bodies"]["delta_bytes"], -1000)
            self.assertIn("Skill bodies the prompt invoked *", out)
            self.assertIn("Environment context *", out)
            settings = [r["source"] for r in data["compare"]["sources"] if not r["per_session"]]
            self.assertNotIn("skill_bodies", settings)
            # A Claude session and a Codex thread do not compare.
            claude_session(h.projects, "c1", h.app, day(20, 1))
            c, _ = h.cli("context", "--session", "c1", "--transcripts", str(h.projects))
            _out, err = h.cli("context", "--compare", Path(c.strip()).parent.name, a, "--agent", "codex",
                              "--transcripts", str(h.codex), code=2)
            self.assertIn("claude and codex sessions do not compare", err)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
