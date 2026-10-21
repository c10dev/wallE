import hashlib
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_triage import Home, words  # noqa: E402

from walle import cli, keep, runs  # noqa: E402
from walle.collect import remote_url  # noqa: E402
from walle.scan import scan  # noqa: E402

STEP = "- Always run the linter before every task."


def tree_hash(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


class KeepCliTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()
        self.out = self.h.root / "walle" / "runs"
        self.repo = self.h.repo("app")
        self.md = self.h.write("projects/app/CLAUDE.md", f"# App\n\n{STEP}\n\n{words('a1')}.\n")
        self.patches = [mock.patch("walle.cli.HOME", self.h.home),
                        mock.patch("walle.cli.scan", lambda roots, **kw: scan(roots, self.h.home, **kw)),
                        mock.patch.object(Path, "cwd", return_value=self.repo)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.h.close()

    def cli(self, *args, code=0) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            got = cli.main(["--out", str(self.out), *args])
        self.assertEqual(got, code, (args, out.getvalue(), err.getvalue()))
        return out.getvalue(), err.getvalue()

    def scan(self) -> tuple[dict, str]:
        out, err = self.cli("scan", str(self.repo), "--format", "path")
        return json.loads(Path(out.strip()).with_name("scan.json").read_text()), err

    def stack(self, data):
        return data["triage"]["stacks"][f"{self.repo}@claude"]

    def step_item(self, data):
        return next(it for it in data["items"] if it["label"] == "every_task_step")

    def test_keep_round_trip(self):
        data, _ = self.scan()
        it = self.step_item(data)
        self.assertEqual(self.stack(data)["start_here"], [it["id"]])
        before = tree_hash(self.h.projects)
        out, _ = self.cli("keep", "1", "--why", "the linter is fast")  # the number of the newest scan works
        self.assertIn(f"kept {it['id']} (", out)
        entry = keep.load(keep.path_for(self.out))["entries"][0]
        self.assertEqual((entry["id"], entry["rule"], entry["line"], entry["note"]),
                         (it["id"], "always_step", 3, "the linter is fast"))
        self.assertEqual(keep.path_for(self.out), self.out.parent / "keep.json")
        data, _ = self.scan()
        it = self.step_item(data)
        e = self.stack(data)
        self.assertEqual((it["state"], it["keep"]["by"], it["keep"]["note"]), ("kept", "personal", "the linter is fast"))
        self.assertEqual((e["start_here"], e["groups"]["kept"], e["counts"]["kept"], e["to_read"]), ([], [it["id"]], 1, 0))
        self.assertEqual((e["verdict"], e["checks"]["candidates"]), ("LEAN", 0))
        self.assertEqual(runs.read_index(self.out)[-1]["kept"], 1)
        md = (self.out / "latest" / "scan.md").read_text()
        self.assertIn("1 kept lines are not counted", md)
        out, _ = self.cli("keep", "--list")
        self.assertIn(it["id"], out)
        self.assertIn("the linter is fast", out)
        out, _ = self.cli("keep", it["id"])
        self.assertIn("kept already", out)
        out, _ = self.cli("unkeep", it["id"])
        self.assertIn(f"unkept {it['id']}", out)
        self.cli("unkeep", it["id"], code=2)
        self.assertEqual(self.step_item(self.scan()[0])["state"], "open")
        # keep, list and unkeep wrote nothing into the scanned folder.
        self.assertEqual(tree_hash(self.h.projects), before)
        self.cli("keep", "nope42", code=2)
        self.cli("keep", code=2)

    def test_a_row_id_of_six_digits_is_a_plan_row(self):
        run = self.out / "20261021-071729"
        run.mkdir(parents=True)
        (run / "plan.json").write_text(json.dumps({"rows": [
            {"id": "557199", "group": "source", "source": {"type": "claude.ai plugin", "key": "design", "name": "design"}}]}))
        out, _ = self.cli("keep", "557199", "--why", "design work")
        self.assertIn("kept 557199", out)
        e = keep.load(keep.path_for(self.out))["entries"][0]
        self.assertEqual((e["id"], e["kind"], e["name"]), ("557199", "source", "design"))
        self.cli("keep", "999999", code=2)  # neither a scan item nor a row

    def test_a_changed_line_releases_its_keep_once(self):
        it = self.step_item(self.scan()[0])
        self.cli("keep", it["id"])
        self.md.write_text(f"# App\n\n- Always run the formatter before every task.\n\n{words('a1')}.\n")
        data, err = self.scan()
        self.assertIn(f"{it['id']} ", err)
        self.assertIn("keep released: line changed", err)
        new = self.step_item(data)
        self.assertNotEqual(new["id"], it["id"])
        self.assertEqual((new["state"], new["keep"]["released"]), ("open", "line_changed"))
        self.assertEqual(data["triage"]["keep_released"][0]["id"], it["id"])
        self.assertIn(new["id"], self.stack(data)["start_here"])
        self.assertEqual(keep.load(keep.path_for(self.out))["entries"], [])
        data, err = self.scan()
        self.assertNotIn("released", err)
        self.assertNotIn("keep", self.step_item(data).get("keep") or {})

    def test_a_scan_from_walle_app_does_not_write_keep_json(self):
        it = self.step_item(self.scan()[0])
        self.cli("keep", it["id"])
        kept = keep.path_for(self.out).read_bytes()
        self.md.write_text(f"# App\n\n- Always run the formatter before every task.\n\n{words('a1')}.\n")
        for _ in range(2):  # the entry stays, so each scan from the app says it again
            out, err = self.cli("scan", str(self.repo), "--format", "path", "--keep-read-only")
            data = json.loads(Path(out.strip()).with_name("scan.json").read_text())
            self.assertIn(f"{it['id']} ", err)
            self.assertIn("walle app leaves keep.json as it is", err)
            self.assertEqual(data["triage"]["keep_released"][0]["id"], it["id"])
            self.assertEqual(self.step_item(data)["state"], "open")
            self.assertEqual(keep.path_for(self.out).read_bytes(), kept)
        _data, err = self.scan()  # a scan in a terminal removes the entry
        self.assertIn("keep released: line changed", err)
        self.assertNotIn("walle app", err)
        self.assertEqual(keep.load(keep.path_for(self.out))["entries"], [])

    def test_a_group_item_is_released_by_its_hash(self):
        big = "# App\n\n## Big\n\n" + "\n\n".join(f"{words(f'big{n}', 40)}." for n in range(36)) + "\n"
        self.md.write_text(big)
        data, _ = self.scan()
        it = next(i for i in data["items"] if i["label"] == "large_layer")
        self.cli("keep", it["id"])
        self.assertEqual(next(i for i in self.scan()[0]["items"] if i["id"] == it["id"])["state"], "kept")
        self.md.write_text(big.replace(words("big3", 40), words("new3", 40)))
        data, err = self.scan()
        same = next(i for i in data["items"] if i["id"] == it["id"])
        self.assertEqual((same["state"], same["keep"]["released"]), ("open", "line_changed"))
        self.assertIn("keep released", err)

    def test_alarm_does_not_name_a_kept_large_layer(self):
        big = "# App\n\n## Big\n\n" + "\n\n".join(f"{words(f'big{n}', 40)}." for n in range(36)) + "\n"
        self.md.write_text(big)
        data, _ = self.scan()
        it = next(i for i in data["items"] if i["label"] == "large_layer")

        def alarm():
            out = io.StringIO()
            with redirect_stdout(out), mock.patch.dict("os.environ", {"CLAUDE_PLUGIN_OPTION_SESSION_ALARM": "true",
                                                                      "CLAUDE_PLUGIN_OPTION_BUDGET_TOKENS": "100"}), \
                    mock.patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(self.repo)}))), \
                    mock.patch("walle.cli.DEFAULT_OUT", self.out):
                cli.main(["alarm"])
            return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]

        self.assertIn("largest", alarm())
        self.cli("keep", it["id"])
        self.assertNotIn("largest", alarm())

    def test_check_leaves_out_a_drift_pair_of_kept_lines(self):
        a = {"file": "/x/CLAUDE.md", "line": 3}
        b = {"file": "/x/CLAUDE.md", "line": 5}
        data = {"stacks": [], "pairs": [{"kind": "drift", "score": 0.9, "a": a, "b": b}],
                "items": [{"state": "kept", "locations": [a, b]}]}
        out = io.StringIO()
        with redirect_stdout(out):
            cli.check(data, 100, "all")
        self.assertNotIn("drift", out.getvalue())
        data["items"][0]["state"] = "open"
        out = io.StringIO()
        with redirect_stdout(out):
            cli.check(data, 100, "all")
        self.assertIn("drift 0.9", out.getvalue())


class TeamKeyTest(unittest.TestCase):
    def setUp(self):
        self.h = Home()

    def tearDown(self):
        self.h.close()

    def clone(self, path: str, url: str) -> Path:
        repo = self.h.home / path
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n'
                                              '\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
        (repo / "CLAUDE.md").write_text(f"# App\n\n{STEP}\n\n{words('a1')}.\n")
        return repo

    def test_remote_url_is_the_same_for_every_form(self):
        forms = ["git@github.com:c10dev/walle.git", "https://github.com/c10dev/walle", "ssh://git@github.com/c10dev/walle.git",
                 "https://x-access-token:s3cr3t@GitHub.com/c10dev/walle.git"]
        urls = set()
        for n, u in enumerate(forms):
            urls.add(remote_url(self.clone(f"c{n}", u)))
        self.assertEqual(urls, {"github.com/c10dev/walle"})
        self.assertIsNone(remote_url(self.h.repo("plain")))

    def test_two_clones_in_other_places_have_one_team_key_and_wallekeep_keeps_it(self):
        a = self.clone("projects/app", "git@github.com:c10dev/app.git")
        b = self.clone("elsewhere/deep/app-copy", "https://github.com/c10dev/app")
        ia = next(i for i in scan([a], self.h.home)["items"] if i["label"] == "every_task_step")
        ib = next(i for i in scan([b], self.h.home)["items"] if i["label"] == "every_task_step")
        self.assertNotEqual(ia["id"], ib["id"])  # the personal id names the local path
        self.assertEqual(ia["team_key"], ib["team_key"])
        self.assertIsNotNone(ia["team_key"])
        # A line in .wallekeep keeps the item in every clone. walle reads the file and never writes it.
        (b / ".wallekeep").write_text(f"{ib['team_key']}  # always_step CLAUDE.md:3 the linter is fast\n")
        it = next(i for i in scan([b], self.h.home)["items"] if i["label"] == "every_task_step")
        self.assertEqual((it["state"], it["keep"]["by"], it["keep"]["note"]),
                         ("kept", "team", "always_step CLAUDE.md:3 the linter is fast"))
        # keep --print gives that line.
        out = self.h.root / "runs"
        with mock.patch("walle.cli.HOME", self.h.home), \
                mock.patch("walle.cli.scan", lambda roots, **kw: scan(roots, self.h.home, **kw)), \
                redirect_stdout(io.StringIO()):
            cli.main(["--out", str(out), "scan", str(a), "--format", "path"])
        buf = io.StringIO()
        with mock.patch("walle.cli.HOME", self.h.home), redirect_stdout(buf):
            self.assertEqual(cli.main(["--out", str(out), "keep", "--print", ia["id"], "--why", "fast"]), 0)
        self.assertEqual(buf.getvalue().strip(), f"{ia['team_key']}  # always_step CLAUDE.md:3 fast")
        self.assertFalse((a / ".wallekeep").exists())

    def test_a_repository_without_a_remote_has_no_team_key(self):
        r = self.h.repo("plain")
        (r / "CLAUDE.md").write_text(f"# App\n\n{STEP}\n")
        it = next(i for i in scan([r], self.h.home)["items"] if i["label"] == "every_task_step")
        self.assertIsNone(it["team_key"])


if __name__ == "__main__":
    unittest.main()
