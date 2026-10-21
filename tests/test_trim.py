import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
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
           "- anthropic-skills:pdf-like: Read, merge, split and fill PDF files of every kind.\n"
           "- anthropic-skills:xlsx-like: Read, clean and chart spreadsheet files of every kind.\n"
           "- anthropic-skills:learn-like: Teach a topic step by step with short quizzes.")
NAMES = ["pptx-like", "sales:call-prep", "typesafe:typesafe-ai", "secret-redact:redact", "loop", "design:ux-copy",
         "legal:review-contract", "anthropic-skills:pdf-like", "anthropic-skills:xlsx-like",
         "anthropic-skills:learn-like"]
# skillOverrides keys in the fake settings: two synced claude.ai skills (learn-like is not in the current
# bucket), a skill of a real plugin (no row) and a skill without a plugin.
OVERRIDES = {"anthropic-skills:pdf-like": "user-invocable-only", "anthropic-skills:learn-like": "user-invocable-only",
             "sales:call-prep": "user-invocable-only", "loop": "user-invocable-only"}
# The entrypoint (surface) of each fake session: 8 cli, 2 sdk-cli, 2 claude-desktop.
SURFACE = ["cli"] * 8 + ["sdk-cli"] * 2 + ["claude-desktop"] * 2


def listing(n: int) -> tuple[str, list[str]]:
    """The skill listing of session n. The sdk-cli sessions (8, 9) no longer list anthropic-skills:pdf-like."""
    if SURFACE[n] != "sdk-cli":
        return LISTING, NAMES
    return ("\n".join(x for x in LISTING.split("\n") if "pdf-like" not in x),
            [x for x in NAMES if x != "anthropic-skills:pdf-like"])


class Fixture:
    """A home with transcripts of 12 sessions, settings key names, plugins, and one repository."""

    def __init__(self):
        self.h = Home()
        self.home = self.h.home
        self.out = self.h.root / "walle" / "runs"
        self.tx = self.home / ".claude" / "projects"
        for n in range(12):
            text, names = listing(n)
            lines = [line(type="user", timestamp=f"2026-10-{n + 1:02d}T00:00:00Z", cwd="/w", entrypoint=SURFACE[n],
                          message={"role": "user", "content": "hi"}),
                     att("deferred_tools_delta", addedNames=VERCEL + SUPA + REA, addedLines=VERCEL + SUPA + REA),
                     att("mcp_instructions_delta", addedNames=["claude.ai Vercel"], addedBlocks=["## Vercel\n" + "x" * 400]),
                     att("skill_listing", content=text, names=names, skillCount=len(names))]
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
        # The synced claude.ai skills of the current bucket: pdf-like and xlsx-like, not learn-like.
        skills_bucket = self.home / ".claude" / "skills" / "synced" / "org1_acct1"
        for name in ("pdf-like", "xlsx-like"):
            (skills_bucket / name).mkdir(parents=True)
            (skills_bucket / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}.\n---\n")
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
    """claude.ai plugins, plugin skills, synced claude.ai skills and keys that are already set (fake home only)."""

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
        # The saving floor counts cli and sdk-cli sessions; claude-desktop is a separate, unconfirmed number.
        sv = d["saving"]
        self.assertEqual((sv["unused_sessions"], sv["unconfirmed"]["sessions"]), (10, {"claude-desktop": 2}))
        self.assertIn("claude-desktop is not confirmed", sv["unconfirmed"]["why"])
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

    def test_plugin_skills_get_the_plugin_row_and_synced_skills_get_skill_overrides(self):
        data, run_dir = self.f.plan()
        rows = self.rows(data)
        cat = trim.plugin_catalog(self.f.home)
        hide = [r for r in data["rows"] if r["change"].get("op") == "set" and r["change"]["keys"][0] == "skillOverrides"]
        self.assertEqual(sorted(r["source"]["name"] for r in hide),
                         ["anthropic-skills:learn-like", "anthropic-skills:pdf-like", "anthropic-skills:xlsx-like",
                          "pptx-like"])
        self.assertFalse(any(trim.plugin_skill(r["source"]["name"], cat) for r in hide))
        # A skill of a real plugin gets no skillOverrides row, even with a key set (sales:call-prep).
        for name in ("design:ux-copy", "legal:review-contract", "sales:call-prep"):
            self.assertNotIn(trim.row_id("skill", name), {r["id"] for r in data["rows"]})
            self.assertTrue(trim.plugin_skill(name, cat))
        self.assertNotIn("anthropic-skills", rows)  # the synced skills are not one plugin row
        self.assertFalse(trim.plugin_skill("anthropic-skills:pdf-like", cat))
        self.assertFalse(trim.plugin_skill("atc:status", cat))  # a subfolder skill, no plugin of that name
        self.assertFalse(trim.plugin_skill("loop", cat))
        # No row deletes a key.
        self.assertFalse([r for r in data["rows"] if r["change"].get("op") not in (None, "set", "remove", "move")])

    def test_a_synced_skill_saving_counts_only_cli_and_sdk_cli(self):
        data, run_dir = self.f.plan()
        x = self.rows(data)["anthropic-skills:xlsx-like"]
        self.assertEqual((x["action"], x["applies"], x["source"]["type"]), ("T5", "walle", "claude.ai skill"))
        self.assertFalse(x["change"]["already"])
        self.assertEqual(x["sessions"]["by_entrypoint"], {"claude-desktop": {"loaded": 2, "used": 0},
                                                          "cli": {"loaded": 8, "used": 0},
                                                          "sdk-cli": {"loaded": 2, "used": 0}})
        sv, per = x["saving"], x["saving"]["tokens_per_session_min"]
        self.assertEqual((sv["unused_sessions"], sv["unused_tokens_min"]), (10, 10 * per))
        self.assertEqual((sv["unconfirmed"]["sessions"], sv["unconfirmed"]["unused_tokens_min"]),
                         ({"claude-desktop": 2}, 2 * per))
        self.assertIn("2.1.299, 2026-10-21", sv["unconfirmed"]["why"])
        self.assertIn("no cli or sdk-cli session has confirmed it yet", x["note"])
        # The docs link is only for real plugin skills: never on a synced skill row.
        self.assertNotIn(trim.DOC_SKILL_OVERRIDES, json.dumps(x))
        md = (run_dir / "plan.md").read_text()
        self.assertIn("10 sessions on cli and sdk-cli loaded it without using it", md)
        self.assertIn("- Unconfirmed, not in the saving: 2 sessions (claude-desktop 2)", md)
        self.assertIn("on confirmed surfaces", md)
        self.assertIn("more on unconfirmed surfaces", md)
        conf, other = trim.totals(data["rows"])
        self.assertGreaterEqual(other, 2 * per)

    def test_a_key_that_is_set_gets_a_status_per_entrypoint(self):
        settings = self.f.home / ".claude" / "settings.json"
        # learn-like: no walle apply record, so the file's modification time (2026-10-10 12:00 UTC) is the set time.
        t = datetime(2026, 10, 10, 12, tzinfo=timezone.utc).timestamp()
        os.utime(settings, (t, t))
        # pdf-like: the earliest walle apply record that set it (2026-10-08 12:00 UTC, written in local time).
        local = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).astimezone().replace(tzinfo=None).isoformat()
        rec = self.f.out / "20261008-120000"
        rec.mkdir(parents=True)
        (rec / "apply.json").write_text(json.dumps({"generated": local, "rows": [
            {"id": "x", "file": "~/.claude/settings.json", "changed": True,
             "keys": ["skillOverrides", "anthropic-skills:pdf-like"]}]}))
        (self.f.out / "index.jsonl").write_text(json.dumps({"id": "20261008-120000", "kind": "apply", "generated": local,
                                                            "path": str(rec / "apply.json")}) + "\n")
        data, run_dir = self.f.plan()
        rows = self.rows(data)
        pdf = rows["anthropic-skills:pdf-like"]
        self.assertTrue(pdf["change"]["already"])
        self.assertEqual(pdf["change"]["set_at"],
                         {"time": "2026-10-08T12:00:00", "from": "walle apply 20261008-120000", "exact": True})
        st = {x["entrypoint"]: x for x in pdf["set_status"]}
        # Sessions 8-11 started after: the sdk-cli ones do not list it, the desktop ones still do, no cli one.
        # No sdk-cli session started before the key, so there is no baseline: absence after proves nothing.
        self.assertEqual(st["sdk-cli"]["state"], "no baseline")
        self.assertEqual(st["claude-desktop"]["text"],
                         "set, still listed on claude-desktop (2 sessions since 2026-10-08T12:00:00 UTC)")
        self.assertEqual(st["cli"]["text"], "set, not measured on cli")
        self.assertEqual((pdf["saving"]["counted"], pdf["saving"]["unused_tokens_min"]), (False, 0))
        self.assertEqual(pdf["saving"]["unconfirmed"]["unused_tokens_min"], 0)
        self.assertEqual(pdf["change"]["op"], "set")  # the row never deletes the key
        learn = rows["anthropic-skills:learn-like"]
        self.assertEqual(learn["change"]["set_at"]["from"], "the last change of ~/.claude/settings.json")
        self.assertFalse(learn["change"]["set_at"]["exact"])
        st = {x["entrypoint"]: x["state"] for x in learn["set_status"]}
        self.assertEqual(st, {"cli": "not measured", "sdk-cli": "not measured", "claude-desktop": "still listed"})
        # learn-like is not in the current bucket: its loaded count is past.
        self.assertTrue(learn["past"])
        self.assertIn("is not in the current bucket", learn["note"])
        md = (run_dir / "plan.md").read_text()
        self.assertIn("(past sessions: the skill is not in the current bucket)", md)
        self.assertIn("| 12 (past) |", md)
        self.assertIn("- Saving: not counted: the key is already set.", md)
        self.assertIn("  - set, still listed on claude-desktop (2 sessions since 2026-10-08T12:00:00 UTC)", md)
        self.assertIn("(set 2026-10-08T12:00:00 UTC by walle apply 20261008-120000)", md)
        self.assertIn("(set at or before 2026-10-10T12:00:00 UTC, the last change of ~/.claude/settings.json)", md)
        self.assertNotIn("still loads", md)
        self.assertIn("(2 already set in the file)", md)
        # Rows whose key is set count no saving, so they come last among the sources.
        src = [x for x in data["rows"] if x["group"] == "source"]
        self.assertEqual(src, sorted(src, key=lambda r: -r["saving"]["unused_tokens_min"]))


class GateTest(unittest.TestCase):
    def test_set_status_per_entrypoint(self):
        marks = [("2026-10-02T00:00:00", "cli", {"a:x"}, ["rea"]), ("2026-10-03T00:00:00", "cli", set(), []),
                 ("2026-09-29T00:00:00", "claude-desktop", set(), ["rea"]),
                 ("2026-10-03T00:00:00", "claude-desktop", {"a:x"}, ["plugin:a:srv"]),
                 ("2026-09-30T00:00:00", "sdk-cli", {"a:x"}, ["rea"])]
        since = "2026-10-01T00:00:00"
        st = {x["entrypoint"]: (x["state"], x["sessions"], x["listed"]) for x in trim.set_status(marks, since, ("server", "rea"))}
        self.assertEqual(st, {"cli": ("still listed", 2, 1), "sdk-cli": ("not measured", 0, 0),
                              "claude-desktop": ("acts", 1, 0)})
        st = {x["entrypoint"]: x["state"] for x in trim.set_status(marks, since, ("plugin", "a"))}
        self.assertEqual(st, {"cli": "still listed", "sdk-cli": "not measured", "claude-desktop": "still listed"})
        self.assertEqual(trim._utc("2026-10-20T08:03:42.203Z"), "2026-10-20T08:03:42")

    def test_no_acts_without_a_baseline_or_for_a_gone_source(self):
        marks = [("2026-10-17T00:00:00", "claude-desktop", {"s:learn"}, []),
                 ("2026-10-19T00:00:00", "claude-desktop", set(), []),
                 ("2026-10-21T05:00:00", "claude-desktop", set(), []),
                 ("2026-10-21T05:00:00", "cli", set(), [])]
        since = "2026-10-21T04:19:24"
        st = {x["entrypoint"]: x for x in trim.set_status(marks, since, ("skill", "s:learn"))}
        # A session before the key listed it: the set time can be later than the real one, so that counts.
        self.assertEqual(st["claude-desktop"]["state"], "acts")
        self.assertEqual(st["cli"]["state"], "no baseline")  # no cli session before the key listed it
        self.assertIn("no session before 2026-10-21T04:19:24 UTC listed it", st["cli"]["text"])
        # learn-like: the skill is not in the current bucket, so its absence says nothing about the key.
        st = {x["entrypoint"]: x for x in trim.set_status(marks, since, ("skill", "s:learn"), gone=True)}
        self.assertEqual(st["claude-desktop"]["state"], "gone")
        self.assertIn("not in the current bucket", st["claude-desktop"]["text"])

    def test_set_time_starts_again_after_an_undo(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            lines = []
            for rid, when in (("20261021-051807", "2026-10-21T05:18:07+00:00"),
                              ("20261021-051900", "2026-10-21T05:19:00+00:00")):
                (out / rid).mkdir()
                (out / rid / "apply.json").write_text(json.dumps({"generated": when, "rows": [
                    {"file": "~/.claude/settings.json", "changed": True, "keys": ["skillOverrides", "x"]}]}))
                lines.append({"id": rid, "kind": "apply", "generated": when, "path": str(out / rid / "apply.json")})
            lines.insert(1, {"id": "20261021-051850", "kind": "undo", "parent_id": "20261021-051807"})
            (out / "index.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
            got = trim.apply_set_times(out)[("~/.claude/settings.json", ("skillOverrides", "x"))]
            self.assertEqual(got, ("2026-10-21T05:19:00", "20261021-051900"))

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
