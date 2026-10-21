import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_context import att, call, line, prompt  # noqa: E402
from test_keep import tree_hash  # noqa: E402
from test_triage import Home, words  # noqa: E402

from walle import cli, context, estimate, judge, keep, trim  # noqa: E402
from walle.scan import scan  # noqa: E402

VERCEL = [f"mcp__claude_ai_Vercel__tool_{n:02d}" for n in range(30)]
SUPA = [f"mcp__claude_ai_Supa__tool_{n:02d}" for n in range(30)]
REA = [f"mcp__rea__op_{n:02d}" for n in range(20)]
LISTING = ("- pptx-like: Make slide decks from notes and outlines.\n"
           "- sales:call-prep: Prepare for a sales call with the account history.\n"
           "- typesafe:typesafe-ai: Build typed AI features.\n"
           "- secret-redact:redact: Redact secrets before they leave the machine.\n"
           "- loop: Run a prompt again and again.\n"
           "- design:ux-copy: Write or review microcopy, error messages and empty states.\n"
           "- legal:review-contract: Review a contract against the playbook of the team.\n"
           "- anthropic-skills:pdf-like: Read, merge, split and fill PDF files of every kind.")
NAMES = ["pptx-like", "sales:call-prep", "typesafe:typesafe-ai", "secret-redact:redact", "loop", "design:ux-copy",
         "legal:review-contract", "anthropic-skills:pdf-like"]
# skillOverrides keys in the fake settings: two plugin skills (no effect) and a skill without a plugin.
OVERRIDES = {"anthropic-skills:pdf-like": "user-invocable-only", "sales:call-prep": "user-invocable-only",
             "loop": "user-invocable-only"}


class Fixture:
    """A home with transcripts of 12 sessions, settings key names, plugins, and one repository."""

    def __init__(self):
        self.h = Home()
        self.home = self.h.home
        self.out = self.h.root / "walle" / "runs"
        self.tx = self.home / ".claude" / "projects"
        for n in range(12):
            lines = [prompt("hi", ts=f"2026-10-{n + 1:02d}T00:00:00Z"),
                     att("deferred_tools_delta", addedNames=VERCEL + SUPA + REA, addedLines=VERCEL + SUPA + REA),
                     att("mcp_instructions_delta", addedNames=["claude.ai Vercel"], addedBlocks=["## Vercel\n" + "x" * 400]),
                     att("skill_listing", content=LISTING, names=NAMES, skillCount=len(NAMES))]
            if n == 0:
                lines.append(call("m1", (VERCEL[0], {})))
                lines.append(call("m2", *[(SUPA[1], {})] * 40))
            if n == 1:
                lines.append(call("m3", ("Skill", {"skill": "loop"})))
            if n < 3:
                lines.append(att("hook_additional_context", content=["[guard] " + "y" * 8000],
                                 hookName="PreToolUse:Bash", hookEvent="PreToolUse", toolUseID=f"t{n}"))
            p = self.tx / "-w" / f"s{n:02d}.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(lines) + "\n")
        # Key names on disk. The values are secrets in real files: walle must not print them.
        (self.home / ".claude.json").write_text(json.dumps(
            {"projects": {"/w": {"mcpServers": {"rea": {"command": "rea", "env": {"TOKEN": "s3cr3t-value"}}}}}}))
        tp = self.home / ".claude" / "plugins" / "cache" / "typesafe"
        (tp / "skills" / "typesafe-ai").mkdir(parents=True)
        (tp / "skills" / "typesafe-ai" / "SKILL.md").write_text("---\nname: typesafe-ai\ndescription: Build typed AI features.\n---\n")
        sr = self.home / ".claude" / "plugins" / "cache" / "secret-redact"
        (sr / "skills" / "redact").mkdir(parents=True)
        (sr / "skills" / "redact" / "SKILL.md").write_text("---\nname: redact\ndescription: Redact secrets.\n---\n")
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {
            "typesafe@typesafe-ai": [{"installPath": str(tp)}],
            "secret-redact@secret-redact": [{"installPath": str(sr)}]}}))
        (self.home / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {
            "typesafe@typesafe-ai": True, "secret-redact@secret-redact": True}, "skillOverrides": OVERRIDES}))
        # claude.ai plugins synced to Claude Code: design (available) and legal (required by the organization).
        # An older bucket of another account holds a plugin that the bucket in use does not have.
        old = self.home / ".claude" / "plugins" / "synced" / "org1_acct0"
        (old / "finance" / "skills" / "close").mkdir(parents=True)
        (old / ".last-complete-round").write_text("1")
        os.utime(old / ".last-complete-round", (1_000_000, 1_000_000))
        self.bucket = self.home / ".claude" / "plugins" / "synced" / "org1_acct1"
        for name in ("design~g2", "legal"):
            (self.bucket / name / "skills" / "x").mkdir(parents=True)
            (self.bucket / name / "skills" / "x" / "SKILL.md").write_text("---\nname: x\ndescription: X.\n---\n")
        (self.bucket / "manifest.json").write_text(json.dumps({"plugins": [
            {"name": "design", "installationPreference": "available", "generation": 2},
            {"name": "legal", "installationPreference": "required"}]}))
        (self.bucket / ".last-complete-round").write_text("2")
        sales = self.home / ".claude" / "remote" / "plugins" / "host" / "h1"
        (sales / ".claude-plugin").mkdir(parents=True)
        (sales / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "sales"}))
        (sales / "skills" / "call-prep").mkdir(parents=True)
        (sales / "skills" / "call-prep" / "SKILL.md").write_text("---\nname: call-prep\ndescription: Prepare.\n---\n")
        # One repository with a line loaded twice: the repo copy is the one to delete.
        self.same = f"- Keep commit messages short: {words('same')}."
        self.h.write(".claude/CLAUDE.md", f"# Global\n\n{self.same}\n")
        self.repo = self.h.repo("app")
        self.claude_md = self.h.write("projects/app/CLAUDE.md", f"# App\n\n{self.same}\n\n{words('a1')}.\n")
        self.patches = [mock.patch("walle.trim.HOME", self.home), mock.patch("walle.cli.HOME", self.home),
                        mock.patch("walle.cli.scan", lambda roots, **kw: scan(roots, self.home, **kw))]
        for p in self.patches:
            p.start()

    def cli(self, *args, code=0) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            got = cli.main(["--out", str(self.out), *args])
        assert got == code, (args, got, out.getvalue(), err.getvalue())
        return out.getvalue(), err.getvalue()

    def plan(self, *args) -> tuple[dict, Path]:
        out, _err = self.cli("trim", "--plan", "--transcripts", str(self.tx), *args)
        md = Path(out.strip().splitlines()[-1])
        return json.loads((md.parent / "plan.json").read_text()), md.parent

    def close(self):
        for p in self.patches:
            p.stop()
        self.h.close()


class PlanRowsTest(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def test_rows_have_every_field_and_the_right_owner(self):
        data, run_dir = self.f.plan()
        rows = {r["source"]["name"]: r for r in data["rows"]}
        for r in data["rows"]:
            for k in ("id", "source", "change", "applies", "where", "saving", "sessions", "risk", "undo", "check", "judge"):
                self.assertIn(k, r, r["title"])
            self.assertIn(r["applies"], ("walle", "manual", "patch"))
            self.assertTrue(r["undo"] and r["check"] and r["risk"]["why"], r["title"])
        # A connector is manual, in the app; it was loaded in 12 sessions and used in 1.
        v = rows["claude.ai Vercel"]
        self.assertEqual((v["action"], v["applies"], v["change"]["kind"]), ("T1", "manual", "app"))
        self.assertIn("claude.ai Settings > Connectors > Vercel", v["where"])
        self.assertEqual((v["sessions"]["loaded"], v["sessions"]["used"], v["sessions"]["calls"]), (12, 1, 1))
        self.assertEqual(v["id"], trim.row_id("server", "claude.ai:vercel"))
        # Lower bound: median bytes of names and instructions times the low end of each bytes-v2 rate.
        name_bytes = sum(len(n) + 1 for n in VERCEL)
        instr = len("## Vercel\n" + "x" * 400)
        low = estimate.BYTES_V2
        self.assertEqual(v["saving"]["tokens_per_session_min"],
                         round(name_bytes * low["deferred_tools"]["range"][0] + instr * low["mcp_instructions"]["range"][0]))
        self.assertEqual(v["saving"]["unused_sessions"], 11)
        # 40 calls in one session: used less than once per loaded session fails, so no row.
        self.assertNotIn("claude.ai Supa", rows)
        # A local MCP server: walle can apply it, a key of ~/.claude.json. The value (a secret) is never written.
        rea = rows["rea"]
        self.assertEqual((rea["action"], rea["applies"]), ("T2", "walle"))
        self.assertEqual(rea["change"]["keys"], ["projects", "/w", "mcpServers", "rea"])
        self.assertNotIn("s3cr3t-value", (run_dir / "plan.json").read_text() + (run_dir / "plan.md").read_text())
        # A local plugin is one settings key; a synced plugin is manual; a safety plugin has no row.
        ts = rows["typesafe"]
        self.assertEqual((ts["action"], ts["applies"], ts["change"]["keys"], ts["change"]["value"]),
                         ("T4", "walle", ["enabledPlugins", "typesafe@typesafe-ai"], False))
        self.assertEqual((rows["sales"]["action"], rows["sales"]["applies"]), ("T3", "manual"))
        self.assertNotIn("secret-redact", rows)
        # A skill no session called: hidden from the model by a settings key. A called skill has no row.
        sk = rows["pptx-like"]
        self.assertEqual((sk["action"], sk["change"]["keys"], sk["change"]["value"]),
                         ("T5", ["skillOverrides", "pptx-like"], "user-invocable-only"))
        self.assertEqual(sk["saving"]["tokens_per_session_min"],
                         round((len("- pptx-like: Make slide decks from notes and outlines.") + 1) * 0.40))
        self.assertNotIn("loop", rows)
        # Hook output: a patch the agent writes, per session that gets it.
        hk = rows["[guard]"]
        self.assertEqual((hk["action"], hk["applies"], hk["sessions"]["loaded"]), ("T9", "patch", 3))
        # Sources first, by unused tokens over the period.
        src = [r for r in data["rows"] if r["group"] == "source"]
        self.assertEqual(src, sorted(src, key=lambda r: -r["saving"]["unused_tokens_min"]))

    def test_rows_that_need_a_judge_go_to_batch_files_and_answers_come_back(self):
        data, run_dir = self.f.plan()
        need = [r for r in data["rows"] if r["judge"]["needed"]]
        self.assertTrue(need)
        self.assertEqual(data["judge"]["to_judge"], len(need))
        batch = json.loads(Path(data["judge"]["files"][0]["batch"]).read_text())
        self.assertEqual(batch["rubric"], trim.RUBRIC)
        first = batch["items"][0]
        self.assertEqual(first["type"], "need")
        self.assertIn("usage", first)
        verdicts = [{"id": it["id"], "real": True, "keep": "neither", "fix": "Turn it off.", "why": "Unused."}
                    for it in batch["items"]]
        Path(batch["answer_file"]).write_text(json.dumps({"verdicts": verdicts}))
        out, _ = self.f.cli("trim", "--import")
        data = json.loads((run_dir / "plan.json").read_text())
        judged = [r for r in data["rows"] if r["judge"].get("verdict")]
        self.assertEqual(len(judged), len(batch["items"]))
        self.assertIn("Judge: not needed: Unused.", Path(out.strip()).read_text())
        # The next plan reads the cache: the same rows need no new batch.
        again, _ = self.f.plan()
        self.assertEqual(again["judge"]["cached"], len(batch["items"]))

    def test_instruction_rows_are_patches_and_no_file_changes(self):
        before_home = tree_hash(self.f.home / "projects")
        global_md = (self.f.home / ".claude" / "CLAUDE.md").read_bytes()
        self.f.cli("scan", str(self.f.repo), "--format", "path")
        data, run_dir = self.f.plan()
        dele = [r for r in data["rows"] if r["action"] == "T10"]
        self.assertEqual(len(dele), 1)
        r = dele[0]
        self.assertEqual((r["applies"], r["group"]), ("patch", "instruction"))
        patch = Path(r["change"]["patch"])
        self.assertEqual(patch.parent, run_dir / "patches")
        text = patch.read_text()
        self.assertIn("--- a/CLAUDE.md", text)
        self.assertIn("-" + self.f.same, text)
        self.assertEqual(r["change"]["base"], str(self.f.repo))
        self.assertGreater(r["saving"]["tokens_per_session_min"], 0)
        # walle wrote nothing into the scanned folder and changed no instruction file.
        self.assertEqual(tree_hash(self.f.home / "projects"), before_home)
        self.assertEqual((self.f.home / ".claude" / "CLAUDE.md").read_bytes(), global_md)
        # The patch applies to a copy of the checkout and leaves one copy of the rule.
        if shutil.which("git"):
            copy = Path(self.f.h.root) / "copy"
            shutil.copytree(self.f.repo, copy)
            subprocess.run(["git", "-C", str(copy), "apply", str(patch)], check=True, capture_output=True)
            self.assertNotIn(self.f.same, (copy / "CLAUDE.md").read_text())
            subprocess.run(["git", "-C", str(copy), "apply", "-R", str(patch)], check=True, capture_output=True)
            self.assertEqual((copy / "CLAUDE.md").read_text(), self.f.claude_md.read_text())

    def test_a_patch_is_not_written_when_the_file_changed_since_the_scan(self):
        self.f.cli("scan", str(self.f.repo), "--format", "path")
        self.f.claude_md.write_text(f"# App\n\n- {words('other')}.\n\n{words('a1')}.\n")
        data, run_dir = self.f.plan()
        r = next(r for r in data["rows"] if r["action"] == "T10")
        self.assertTrue(r["stale"])
        self.assertIsNone(r["change"]["patch"])
        self.assertFalse((run_dir / "patches").exists())

    def test_a_kept_source_and_a_kept_line_leave_the_plan(self):
        self.f.cli("scan", str(self.f.repo), "--format", "path")
        data, _ = self.f.plan()
        vercel = next(r for r in data["rows"] if r["source"]["name"] == "claude.ai Vercel")
        line_row = next(r for r in data["rows"] if r["action"] == "T10")
        out, _ = self.f.cli("keep", vercel["id"], "--why", "deploys")
        self.assertIn(f"kept {vercel['id']}", out)
        self.f.cli("keep", line_row["id"])
        entries = keep.load(keep.path_for(self.f.out))["entries"]
        self.assertEqual({(e["id"], e["kind"]) for e in entries}, {(vercel["id"], "source"), (line_row["id"], "item")})
        again, run_dir = self.f.plan()
        ids = {r["id"] for r in again["rows"]}
        self.assertNotIn(vercel["id"], ids)
        self.assertNotIn(line_row["id"], ids)
        self.assertEqual({k["id"] for k in again["kept"]}, {vercel["id"], line_row["id"]})
        self.assertIn("## Kept", (run_dir / "plan.md").read_text())
        # A kept line also leaves the next scan's Start here.
        self.f.cli("scan", str(self.f.repo), "--format", "path")
        scan_data = json.loads((self.f.out / "latest" / "scan.json").read_text())
        it = next(i for i in scan_data["items"] if i["id"] == line_row["id"])
        self.assertEqual(it["state"], "kept")
        e = scan_data["triage"]["stacks"][f"{self.f.repo}@claude"]
        self.assertNotIn(line_row["id"], e["start_here"])
        self.assertEqual(e["groups"]["kept"], [line_row["id"]])

    def test_plan_without_a_scan_and_errors(self):
        data, run_dir = self.f.plan()
        self.assertIsNone(data["run"]["scan"])
        self.assertFalse([r for r in data["rows"] if r["group"] == "instruction"])
        self.assertIn("No scan.json", (run_dir / "plan.md").read_text())
        _out, err = self.f.cli("trim", "--plan", "--transcripts", str(self.f.h.root / "none"), code=2)
        self.assertIn("no transcripts", err)
        self.f.cli("trim", code=2)

    def test_period_filters_sessions(self):
        data, _ = self.f.plan("--since", "2026-10-06")
        self.assertEqual(data["period"]["sessions"], 7)


class PluginRowsTest(unittest.TestCase):
    """claude.ai plugins, plugin skills and skillOverrides keys that have no effect (fake home only)."""

    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def rows(self, data: dict) -> dict:
        return {r["source"]["name"]: r for r in data["rows"]}

    def test_a_synced_claude_ai_plugin_is_one_settings_key(self):
        data, run_dir = self.f.plan()
        d = self.rows(data)["design"]
        self.assertEqual((d["action"], d["applies"], d["source"]["type"]), ("T4", "walle", "claude.ai plugin"))
        self.assertEqual({k: d["change"][k] for k in ("kind", "file", "op", "keys", "value")},
                         {"kind": "settings_key", "file": "~/.claude/settings.json", "op": "set",
                          "keys": ["enabledPlugins", "design@synced"], "value": False})
        self.assertIn(trim.DOC_PLUGIN_DISABLE, d["where"])
        for text in ("organization-required plugin ignores the key", "survives the next sync",
                     "walle context --last", "the whole plugin goes"):
            self.assertIn(text, d["risk"]["why"])
        self.assertEqual(d["change"]["sha256"], trim.file_sha256(self.f.home / ".claude" / "settings.json"))
        self.assertFalse(d["change"]["already"])
        # An installed plugin keeps its own key, and a plugin of an older sync bucket is not a synced plugin.
        self.assertEqual(self.rows(data)["typesafe"]["change"]["keys"], ["enabledPlugins", "typesafe@typesafe-ai"])
        cat = trim.plugin_catalog(self.f.home)
        self.assertNotIn("finance", cat)
        self.assertEqual((cat["design"]["key"], cat["design"]["synced"]), ("design@synced", True))
        # After the key is set, the next plan finds the plugin on disk still, and says the key is set.
        settings = self.f.home / ".claude" / "settings.json"
        doc = json.loads(settings.read_text())
        doc["enabledPlugins"]["design@synced"] = False
        settings.write_text(json.dumps(doc))
        again, _ = self.f.plan()
        d = self.rows(again)["design"]
        self.assertEqual((d["applies"], d["change"]["already"]), ("walle", True))

    def test_a_required_plugin_and_a_desktop_only_plugin_stay_manual(self):
        data, run_dir = self.f.plan()
        rows = self.rows(data)
        legal, sales = rows["legal"], rows["sales"]
        self.assertEqual((legal["applies"], legal["change"]["kind"]), ("manual", "app"))
        self.assertIn("Your organization requires legal", legal["where"])
        self.assertEqual((sales["action"], sales["applies"]), ("T3", "manual"))
        for text in ('"Installed by default"', '"Required" plugin needs the organization Owner', trim.DOC_ORG_PLUGINS):
            self.assertIn(text, sales["where"])
        self.assertNotIn(trim.UNCONFIRMED, sales["where"])
        self.assertNotIn("sha256", sales["change"])

    def test_plugin_skills_get_the_plugin_row_not_skill_overrides(self):
        data, run_dir = self.f.plan()
        rows = self.rows(data)
        cat = trim.plugin_catalog(self.f.home)
        hide = [r for r in data["rows"] if r["change"].get("op") == "set" and r["change"]["keys"][0] == "skillOverrides"]
        self.assertEqual([r["source"]["name"] for r in hide], ["pptx-like"])
        self.assertFalse(any(trim.plugin_skill(r["source"]["name"], cat) for r in hide))
        for name in ("design:ux-copy", "anthropic-skills:pdf-like", "legal:review-contract"):
            self.assertNotIn(trim.row_id("skill", name), {r["id"] for r in data["rows"]})
        # The claude.ai skills are one manual row: walle does not set syncClaudeAiSkills.
        a = rows["anthropic-skills"]
        self.assertEqual((a["action"], a["applies"], a["source"]["type"]), ("T3", "manual", "claude.ai skills"))
        self.assertIn("syncClaudeAiSkills: false", a["where"])
        self.assertIn(trim.DOC_SKILL_OVERRIDES, a["where"])
        self.assertIn("go together", a["risk"]["why"])
        self.assertTrue(trim.plugin_skill("anthropic-skills:pdf-like", cat))
        self.assertFalse(trim.plugin_skill("atc:status", cat))  # a subfolder skill, no plugin of that name
        self.assertFalse(trim.plugin_skill("loop", cat))

    def test_a_skill_overrides_key_of_a_plugin_skill_is_set_no_effect(self):
        data, run_dir = self.f.plan()
        rows = self.rows(data)
        idle = [r for r in data["rows"] if r.get("state") == trim.NO_EFFECT]
        self.assertEqual(sorted(r["source"]["name"] for r in idle), ["anthropic-skills:pdf-like", "sales:call-prep"])
        r = rows["anthropic-skills:pdf-like"]
        self.assertEqual((r["applies"], r["change"]["op"], r["change"]["keys"]),
                         ("walle", "unset", ["skillOverrides", "anthropic-skills:pdf-like"]))
        self.assertFalse(r["change"]["already"])
        self.assertEqual((r["saving"]["tokens_per_session_min"], r["saving"]["unused_tokens_min"]), (0, 0))
        self.assertFalse(r["judge"]["needed"])
        self.assertIn(f"That row is {rows['anthropic-skills']['id']}.", r["note"])
        self.assertEqual(r["id"], trim.row_id("override", "anthropic-skills:pdf-like"))
        # A key of a skill without a plugin is not one: "loop" has no row (a session called it).
        self.assertNotIn("loop", rows)
        # The rows come last among the sources, and plan.md does not count them as "already".
        src = [x["id"] for x in data["rows"] if x["group"] == "source"]
        self.assertEqual(set(src[-2:]), {x["id"] for x in idle})
        md = (run_dir / "plan.md").read_text()
        self.assertIn("(2 set, no effect)", md)
        self.assertNotIn("already set in the file", md)
        self.assertIn("| walle (set, no effect) |", md)
        self.assertIn("- In the file: set, no effect.", md)
        self.assertIn("- Saving: not counted: the skill still loads.", md)
        self.assertIn('remove skillOverrides["sales:call-prep"] from ~/.claude/settings.json', md)


class GateTest(unittest.TestCase):
    def test_gate(self):
        self.assertTrue(trim.gate(464, 32, 310))  # Vercel
        self.assertFalse(trim.gate(582, 53, 788))  # Supabase: more calls than sessions
        self.assertFalse(trim.gate(745, 205, 3563))  # Linear: used in 28%
        self.assertFalse(trim.gate(9, 0, 0))  # too few sessions

    def test_prose_min_is_below_the_estimate(self):
        for text in ("plain ascii words here", "한국어 문장이다"):
            self.assertLessEqual(trim.prose_min(text), estimate.prose(text))

    def test_common_folder(self):
        self.assertEqual(trim._common_folder(["/a/b/c", "/a/b/d"]), "/a/b")
        self.assertIsNone(trim._common_folder(["/a", "/b"]))


if __name__ == "__main__":
    unittest.main()
