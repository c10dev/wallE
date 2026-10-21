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
# .synced times of the desktop host's copies. They pick the copy that gives a plugin's skill list; the sessions
# tell which plugins claude-desktop loads.
NOW_SET = datetime(2026, 10, 21, 7, 3, 10, tzinfo=timezone.utc).timestamp()
OLD_SET = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()


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
        # The desktop host's copies (~/.claude/remote/plugins/<host>/<hash>), one or more per plugin name.
        self.remote = self.home / ".claude" / "remote" / "plugins" / "host"
        self.desktop_copy("h1", "sales", ["call-prep"], OLD_SET)
        self.desktop_copy("h0", "design", ["ux-copy", "old-skill"], OLD_SET)
        self.desktop_copy("h2", "design", ["ux-copy"], NOW_SET)
        self.desktop_copy("h3", "legal", ["review-contract"], NOW_SET)
        self.desktop_copy("h4", "secret-redact", [], NOW_SET)
        self.desktop_copy("h5", "anthropic-skills", ["pdf-like"], NOW_SET)
        self.desktop_copy("h6", None, ["x"], NOW_SET)  # no manifest name: never a key
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

    def desktop_copy(self, folder: str, name: str | None, skill_names: list[str], synced: float) -> Path:
        d = self.remote / folder
        (d / ".claude-plugin").mkdir(parents=True)
        (d / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name} if name else {"version": "1"}))
        for sk in skill_names:
            (d / "skills" / sk).mkdir(parents=True)
            (d / "skills" / sk / "SKILL.md").write_text(f"---\nname: {sk}\ndescription: {sk}.\n---\n")
        (d / ".synced").write_text("")
        os.utime(d / ".synced", (synced, synced))
        return d

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
        self.assertIn("claude.ai Customize > Connectors > Vercel (claude.ai/customize/connectors;", v["where"])
        self.assertEqual((v["sessions"]["loaded"], v["sessions"]["used"], v["sessions"]["calls"]), (12, 1, 1))
        self.assertEqual(v["id"], trim.row_id("server", "claude.ai:vercel"))
        # Lower bound: median bytes of names and instructions times the low end of each bytes-v2 rate.
        name_bytes = sum(len(n) + 1 for n in VERCEL)
        instr = len("## Vercel\n" + "x" * 400)
        low = estimate.BYTES_V2
        self.assertEqual(v["saving"]["tokens_per_session_min"],
                         round(name_bytes * low["deferred_tools"]["range"][0] + instr * low["mcp_instructions"]["range"][0]))
        # 11 sessions left it unused: the manual row counts the 2 claude-desktop ones, where no setting reaches a
        # connector; a deniedMcpServers row counts the 9 cli and sdk-cli ones.
        self.assertEqual(v["saving"]["unused_sessions"], 2)
        self.assertEqual(v["url"], trim.CONNECTORS_URL)
        self.assertIn("claude.ai on the web and phone", v["risk"]["why"])
        deny = next(r for r in data["rows"] if r["id"] == trim.row_id("connector-cli", "claude.ai:vercel"))
        self.assertIn(deny["id"], v["note"])
        self.assertEqual((deny["applies"], deny["change"]["op"], deny["change"]["keys"], deny["change"]["value"]),
                         ("walle", "append", ["deniedMcpServers"], {"serverName": "claude.ai Vercel"}))
        self.assertEqual((deny["saving"]["unused_sessions"], deny["saving"]["confirmed_surfaces"]), (9, ["cli", "sdk-cli"]))
        self.assertEqual(deny["saving"]["tokens_per_session_min"], v["saving"]["tokens_per_session_min"])
        self.assertIn("not on claude-desktop", deny["where"])
        self.assertFalse(deny["judge"]["needed"])  # the connector's manual row carries the judge question
        # 40 calls in one session: used less than once per loaded session fails, so no row.
        self.assertNotIn("claude.ai Supa", rows)
        # A local MCP server: walle can apply it, a key of ~/.claude.json. The value (a secret) is never written.
        rea = rows["rea"]
        self.assertEqual((rea["action"], rea["applies"]), ("T2", "walle"))
        self.assertEqual(rea["change"]["keys"], ["projects", "/w", "mcpServers", "rea"])
        self.assertNotIn("s3cr3t-value", (run_dir / "plan.json").read_text() + (run_dir / "plan.md").read_text())
        # A local plugin is one settings key; a plugin that claude-desktop loads from the host's copy is the
        # @inline key; a safety plugin has no row, not even an @inline one.
        ts = rows["typesafe"]
        self.assertEqual((ts["action"], ts["applies"], ts["change"]["keys"], ts["change"]["value"]),
                         ("T4", "walle", ["enabledPlugins", "typesafe@typesafe-ai"], False))
        self.assertEqual((rows["sales@inline"]["action"], rows["sales@inline"]["applies"]), ("T4", "walle"))
        self.assertNotIn("sales", rows)
        self.assertNotIn("secret-redact", rows)
        self.assertNotIn("secret-redact@inline", rows)
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

    def test_a_kept_plugin_row_keeps_its_inline_row_too(self):
        data, _ = self.f.plan()
        rows = {r["source"]["name"]: r for r in data["rows"]}
        self.f.cli("keep", rows["design"]["id"], "--why", "design work")  # the @synced row
        self.f.cli("keep", rows["sales@inline"]["id"])  # the @inline row of a plugin without its own key
        again, run_dir = self.f.plan()
        names = {r["source"]["name"] for r in again["rows"]}
        for name in ("design", "design@inline", "sales", "sales@inline"):
            self.assertNotIn(name, names)
        self.assertEqual({k["name"] for k in again["kept"]}, {"design", "design@inline", "sales@inline"})
        self.assertIn("typesafe", names)  # other plugins stay

    def test_a_kept_source_and_a_kept_line_leave_the_plan(self):
        self.f.cli("scan", str(self.f.repo), "--format", "path")
        data, _ = self.f.plan()
        vercel = next(r for r in data["rows"] if r["source"]["name"] == "claude.ai Vercel" and r["applies"] == "manual")
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
        # A kept connector keeps its deniedMcpServers row too.
        deny_id = trim.row_id("connector-cli", "claude.ai:vercel")
        self.assertNotIn(deny_id, ids)
        self.assertEqual({k["id"] for k in again["kept"]}, {vercel["id"], deny_id, line_row["id"]})
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


def drop_listing(path: Path, name: str) -> None:
    """Take one skill out of the skill listing of a fake transcript."""
    out = []
    for raw in path.read_text().splitlines():
        x = json.loads(raw)
        a = x.get("attachment") or {}
        if a.get("type") == "skill_listing":
            a["content"] = "\n".join(l for l in a["content"].split("\n") if not l.startswith(f"- {name}:"))
            a["names"] = [n for n in a["names"] if n != name]
            a["skillCount"] = len(a["names"])
        out.append(json.dumps(x))
    path.write_text("\n".join(out) + "\n")


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
        inline_id = trim.row_id("plugin", "design@inline")
        for text in ("organization-required plugin ignores the key", "survives the next sync", "the whole plugin goes",
                     f'It does not reach claude-desktop, which loads the host\'s copy as "design@inline": row '
                     f"{inline_id} turns that copy off."):
            self.assertIn(text, d["risk"]["why"])
        self.assertIn(f"(row {inline_id} does)", d["check"])
        self.assertIn('"<name>@synced": false did not', d["note"])
        self.assertEqual(d["change"]["sha256"], trim.file_sha256(self.f.home / ".claude" / "settings.json"))
        self.assertFalse(d["change"]["already"])
        # The saving floor counts cli and sdk-cli sessions. The @inline row counts claude-desktop, so this row
        # leaves it out of its unconfirmed number too: the totals count each session once.
        sv = d["saving"]
        self.assertEqual((sv["unused_sessions"], sv["unconfirmed"]["sessions"]), (10, {}))
        self.assertEqual(d["sessions"]["by_entrypoint"]["claude-desktop"], {"loaded": 2, "used": 0})
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

    def test_a_required_plugin_stays_manual_and_gets_no_inline_row(self):
        data, run_dir = self.f.plan()
        rows = self.rows(data)
        legal = rows["legal"]
        self.assertEqual((legal["applies"], legal["change"]["kind"]), ("manual", "app"))
        self.assertIn("Your organization requires legal", legal["where"])
        # claude-desktop loads legal from a desktop copy, but the @inline key would go around the organization's
        # "Required": no row offers it.
        self.assertIn('walle does not offer enabledPlugins["legal@inline"] = false', legal["where"])
        self.assertIn("go around the organization's policy", legal["where"])
        self.assertNotIn("legal@inline", rows)
        self.assertNotIn("no settings key turns it off", (run_dir / "plan.md").read_text())

    def test_a_required_plugin_named_by_a_uuid_in_another_bucket_gets_no_inline_row(self):
        # A bucket that is not in use lists a plugin by the uuid of its folder; the folder's plugin.json says "sales".
        other = self.f.home / ".claude" / "plugins" / "synced" / "org2_acct9"
        uuid = "2529b2d1-9426-48a5-a8ad-2ebc77af2815"
        (other / f"{uuid}~g3" / ".claude-plugin").mkdir(parents=True)
        (other / f"{uuid}~g3" / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "sales"}))
        (other / "manifest.json").write_text(json.dumps({"plugins": [
            {"name": uuid, "installationPreference": "required", "generation": 3}]}))
        for d in (other / f"{uuid}~g3" / ".claude-plugin", other / f"{uuid}~g3", other):
            os.utime(d, (OLD_SET, OLD_SET))  # older than the bucket in use
        self.assertEqual(trim.org_preferences(self.f.home)["sales"], ["required"])
        self.assertNotIn(uuid, trim.org_preferences(self.f.home))
        data, _ = self.f.plan()
        rows = self.rows(data)
        self.assertNotIn("sales@inline", rows)
        self.assertEqual(rows["sales"]["applies"], "manual")
        self.assertIn('walle does not offer enabledPlugins["sales@inline"] = false', rows["sales"]["where"])
        self.assertIn("design@inline", rows)  # the bucket in use is still org1_acct1

    def test_a_plugin_that_no_recent_desktop_session_loads_is_past(self):
        # Only the newest claude-desktop session counts as recent here, and it no longer lists sales.
        drop_listing(self.f.tx / "-w" / "s11.jsonl", "sales:call-prep")
        with mock.patch("walle.trim.RECENT_DESKTOP", 1):
            data, run_dir = self.f.plan()
        rows = self.rows(data)
        self.assertNotIn("sales@inline", rows)
        sales = rows["sales"]
        self.assertEqual((sales["action"], sales["applies"], sales["past"]), ("T3", "manual", True))
        for text in ("None of the newest 1 claude-desktop sessions of the period (since 2026-10-12T00:00:00 UTC) "
                     "loaded sales", "the last one that did started 2026-10-11T00:00:00 UTC",
                     'enabledPlugins["sales@inline"] = false turns it off', trim.DOC_INLINE,
                     '"Installed by default"', '"Required" plugin needs the organization Owner', trim.DOC_ORG_PLUGINS):
            self.assertIn(text, sales["where"])
        self.assertEqual((sales["saving"]["counted"], sales["saving"]["unused_tokens_min"]), (False, 0))
        self.assertIn("(past sessions: no recent claude-desktop session loads it)", (run_dir / "plan.md").read_text())
        self.assertNotIn("sha256", sales["change"])
        self.assertIn("design@inline", rows)  # the newest desktop session still lists design

    def test_a_desktop_copy_that_the_host_passes_gets_an_inline_row(self):
        data, run_dir = self.f.plan()
        rows = self.rows(data)
        d = rows["design@inline"]
        self.assertEqual((d["id"], d["action"], d["applies"], d["source"]["type"], d["surface"]),
                         (trim.row_id("plugin", "design@inline"), "T4", "walle", "desktop plugin", "claude-desktop"))
        self.assertEqual({k: d["change"][k] for k in ("kind", "file", "op", "keys", "value")},
                         {"kind": "settings_key", "file": "~/.claude/settings.json", "op": "set",
                          "keys": ["enabledPlugins", "design@inline"], "value": False})
        self.assertEqual(d["change"]["sha256"], trim.file_sha256(self.f.home / ".claude" / "settings.json"))
        # The newest copy of the set that the host passes, not the older copy with one more skill.
        self.assertEqual(d["copy"], {"path": str(self.f.remote / "h2"), "synced": "2026-10-21T07:03:10", "skills": 1})
        self.assertIn("design is the manifest name of the desktop copy ~/.claude/remote/plugins/host/h2", d["where"])
        self.assertIn(trim.DOC_INLINE, d["where"])
        for text in ("Every session on this machine that loads a plugin named design with --plugin-dir loses it",
                     "Sessions that are open keep it; the next session drops it.",
                     "an installed or synced copy can load in its place"):
            self.assertIn(text, d["risk"]["why"])
        self.assertNotIn("no installationPreference", d["risk"]["why"])  # the sync says "available"
        for text in ("Open a new claude-desktop session", "walle context --compare <apply-id>", "agents/eve.md",
                     "design:...", "plugin:design:...", '"only in before"'):
            self.assertIn(text, d["check"])
        self.assertIn("walle undo <apply-id>", d["undo"])
        self.assertIn('Do not use `claude plugin enable design@inline`: it writes "design@synced": false', d["undo"])
        # The saving counts the claude-desktop sessions only; the @synced row counts cli and sdk-cli.
        sv = d["saving"]
        self.assertEqual((sv["confirmed_surfaces"], sv["unused_sessions"], sv["unconfirmed"]["sessions"]),
                         (["claude-desktop"], 2, {}))
        self.assertEqual((d["sessions"]["loaded"], d["sessions"]["used"]), (2, 0))
        synced = rows["design"]
        conf, other = trim.totals(data["rows"])
        both = synced["saving"]["unused_tokens_min"] + sv["unused_tokens_min"]
        self.assertEqual(both, 10 * synced["saving"]["tokens_per_session_min"] + 2 * sv["tokens_per_session_min"])
        self.assertGreaterEqual(conf, both)
        # anthropic-skills@inline would take every claude.ai skill out at once: only a note names it.
        self.assertNotIn("anthropic-skills@inline", rows)
        self.assertNotIn("anthropic-skills", rows)
        self.assertTrue(any('"anthropic-skills@inline" = false' in x for x in data["notes"]))
        # A copy without a manifest name never becomes a key.
        copies = trim.desktop_plugins(self.f.home)
        self.assertEqual(sorted(copies), ["anthropic-skills", "design", "legal", "sales", "secret-redact"])
        # claude-desktop sessions also load typesafe, an installed plugin: no desktop copy names it, so no @inline row.
        self.assertNotIn("typesafe@inline", rows)
        md = (run_dir / "plan.md").read_text()
        self.assertIn("- Desktop copy for the skill list: ~/.claude/remote/plugins/host/h2 (.synced 2026-10-21T07:03:10 "
                      "UTC, 1 skill).", md)
        self.assertIn("2 sessions on claude-desktop loaded it without using it", md)
        self.assertIn("claude-desktop for @inline plugin keys", md)

    def test_sessions_not_synced_times_decide_the_inline_rows(self):
        # Another host's sync round is newer than every copy that sessions load, and it holds only finance.
        self.f.remote = self.f.remote.parent / "host2"
        self.f.desktop_copy("f1", "finance", ["close"], NOW_SET + 86_400)
        data, _ = self.f.plan()
        rows = self.rows(data)
        self.assertEqual({k for k in rows if k.endswith("@inline")}, {"design@inline", "sales@inline"})
        self.assertNotIn("finance@inline", rows)  # no session loaded finance

    def test_a_plugin_only_on_the_desktop_gets_an_inline_row_without_its_own_key(self):
        data, _ = self.f.plan()
        rows = self.rows(data)
        self.assertNotIn("sales", rows)
        s = rows["sales@inline"]
        self.assertEqual((s["applies"], s["change"]["keys"]), ("walle", ["enabledPlugins", "sales@inline"]))
        # No other key covers cli and sdk-cli: their sessions are an unconfirmed number.
        sv = s["saving"]
        self.assertEqual((sv["unused_sessions"], sv["unconfirmed"]["sessions"]), (2, {"cli": 8, "sdk-cli": 2}))
        self.assertEqual(sv["unconfirmed"]["why"], trim.INLINE_OTHER)
        # No sync bucket names sales: walle cannot tell if the organization requires it.
        self.assertIn("walle found no installationPreference for sales", s["risk"]["why"])
        self.assertNotIn("apply that row too", s["risk"]["why"])

    def test_a_copy_without_desktop_sessions_in_the_period_is_not_called_past(self):
        data, _ = self.f.plan("--before", "2026-10-11")  # cli and sdk-cli sessions only
        rows = self.rows(data)
        self.assertNotIn("sales@inline", rows)
        s = rows["sales"]
        self.assertEqual((s["applies"], s.get("past")), ("manual", None))
        self.assertIn("walle finds sales in the desktop host's copies, but no claude-desktop session of the period "
                      "loaded it.", s["where"])
        self.assertNotIn("does not pass", s["where"])
        self.assertEqual((s["saving"]["counted"], s["saving"]["unused_tokens_min"]), (False, 0))

    def test_an_inline_key_that_is_set_gets_a_status_per_entrypoint(self):
        settings = self.f.home / ".claude" / "settings.json"
        doc = json.loads(settings.read_text())
        doc["enabledPlugins"]["design@inline"] = False
        settings.write_text(json.dumps(doc))
        # walle apply set the key between the two claude-desktop sessions (2026-10-11 00:00 and 2026-10-12 00:00).
        local = datetime(2026, 10, 11, 12, tzinfo=timezone.utc).astimezone().replace(tzinfo=None).isoformat()
        rec = self.f.out / "20261011-120000"
        rec.mkdir(parents=True)
        (rec / "apply.json").write_text(json.dumps({"generated": local, "rows": [
            {"id": "x", "file": "~/.claude/settings.json", "changed": True, "keys": ["enabledPlugins", "design@inline"]}]}))
        (self.f.out / "index.jsonl").write_text(json.dumps({"id": "20261011-120000", "kind": "apply", "generated": local,
                                                            "path": str(rec / "apply.json")}) + "\n")
        # The claude-desktop session after the key no longer lists the design skills. It is the only recent
        # session here, but the key is set, so the row stays: the key is why the session does not load it.
        drop_listing(self.f.tx / "-w" / "s11.jsonl", "design:ux-copy")
        with mock.patch("walle.trim.RECENT_DESKTOP", 1):
            data, run_dir = self.f.plan()
        d = self.rows(data)["design@inline"]
        self.assertTrue(d["change"]["already"])
        self.assertEqual(d["change"]["set_at"],
                         {"time": "2026-10-11T12:00:00", "from": "walle apply 20261011-120000", "exact": True})
        st = {x["entrypoint"]: x for x in d["set_status"]}
        self.assertEqual({ep: x["state"] for ep, x in st.items()},
                         {"cli": "not measured", "sdk-cli": "not measured", "claude-desktop": "acts"})
        self.assertEqual(st["claude-desktop"]["text"],
                         "already set on claude-desktop: none of 1 sessions since 2026-10-11T12:00:00 UTC lists it")
        self.assertEqual((d["saving"]["counted"], d["saving"]["unused_tokens_min"]), (False, 0))
        md = (run_dir / "plan.md").read_text()
        self.assertIn("  - already set on claude-desktop: none of 1 sessions since 2026-10-11T12:00:00 UTC lists it", md)
        self.assertIn("(set 2026-10-11T12:00:00 UTC by walle apply 20261011-120000)", md)
        # The @synced key is not set: its row is not "already".
        self.assertFalse(self.rows(data)["design"]["change"]["already"])

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
        self.assertFalse([r for r in data["rows"] if r["change"].get("op") not in (None, "set", "remove", "move", "append")])

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

    def test_a_plugin_row_below_min_tokens_is_left_out(self):
        u = {"sessions": 20, "servers": [], "skills": [
            {"skill": "tiny:x", "sessions_listed": 20, "sessions_used": 0, "calls": 0, "line_bytes_median": 10}]}
        copy = {"path": "/h/remote/plugins/a/b", "synced": "2026-10-01T00:00:00", "current": False, "skills": ["x"]}
        plugins = {"tiny": {"sources": {"remote_plugin"}, "key": None, "synced": False, "preference": None,
                            "inline": copy, "required": False}}
        self.assertEqual(trim.plugin_rows(u, plugins), [])  # 10 bytes x 0.40 = 4 tokens per session
        u["skills"][0]["line_bytes_median"] = 100
        self.assertEqual([r["applies"] for r in trim.plugin_rows(u, plugins)], ["manual"])

    def test_prose_min_is_below_the_estimate(self):
        for text in ("plain ascii words here", "한국어 문장이다"):
            self.assertLessEqual(trim.prose_min(text), estimate.prose(text))

    def test_common_folder(self):
        self.assertEqual(trim._common_folder(["/a/b/c", "/a/b/d"]), "/a/b")
        self.assertIsNone(trim._common_folder(["/a", "/b"]))


if __name__ == "__main__":
    unittest.main()
