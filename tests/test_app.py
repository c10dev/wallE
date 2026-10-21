"""walle app: the token, the Host and Origin checks, the command allowlist, argv without a shell, the progress
stream, escaping of paths and labels, an empty ~/.walle, and the views built from real run files."""

import http.client
import io
import json
import re
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from appfixture import LABEL, MARKUP, Fixture  # noqa: E402

from walle import app as appmod  # noqa: E402
from walle import appdata  # noqa: E402

FX = None


def setUpModule():
    global FX
    FX = Fixture()


def tearDownModule():
    FX.close()


class FakeProc:
    """A Popen stand-in: stdout and stderr lines, then an exit code."""

    def __init__(self, out=(), err=(), code=0):
        self.stdout = io.StringIO("".join(line + "\n" for line in out))
        self.stderr = io.StringIO("".join(line + "\n" for line in err))
        self.pid, self.returncode = 2 ** 22 + 7, code

    def wait(self, timeout=None):
        return self.returncode


class Served(unittest.TestCase):
    """One server on a free port for the class, on the fixture home."""

    launch = None

    @classmethod
    def setUpClass(cls):
        cls.home, cls.out = FX.home, FX.out
        cls.app = appmod.App(cls.out, cls.home, cls.launch or FX.markup, 0)
        threading.Thread(target=cls.app.serve, daemon=True).start()
        cls.port, cls.token = cls.app.port, cls.app.token

    @classmethod
    def tearDownClass(cls):
        cls.app.close()

    def req(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Host": f"127.0.0.1:{self.port}" if host is None else host, **(headers or {})}
        if body is not None and not isinstance(body, (bytes, str)):
            body = json.dumps(body)
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r, data

    def get(self, rest, **kw):
        return self.req("GET", f"/{self.token}/{rest}", **kw)

    def run_cmd(self, command, folder=None, origin=True):
        headers = {"Origin": f"http://127.0.0.1:{self.port}"} if origin else {}
        body = {"command": command} if folder is None else {"command": command, "folder": folder}
        r, data = self.req("POST", f"/{self.token}/api/run", body=body, headers=headers)
        events = [json.loads(line) for line in data.decode().splitlines() if line.strip()] \
            if r.getheader("Content-Type", "").startswith("application/x-ndjson") else None
        return r, data, events


class TokenTest(Served):
    def test_a_request_without_the_token_gets_403(self):
        for path in ("/", "/api/state", "/app.js", "/wrong/api/state", f"/{self.token[:-1]}/api/state",
                     f"/{self.token}x/api/state", f"/x/{self.token}/api/state"):
            r, _ = self.req("GET", path)
            self.assertEqual(r.status, 403, path)
        r, _ = self.req("POST", "/api/run", body={"command": "walle runs"}, headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(r.status, 403)

    def test_the_token_gives_the_page_and_the_api(self):
        r, body = self.get("")
        self.assertEqual(r.status, 200)
        self.assertIn(b'<div id="root">', body)
        r, body = self.get("api/state")
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(body)["version"], appmod.__version__)

    def test_the_token_without_a_slash_redirects(self):
        r, _ = self.req("GET", f"/{self.token}")
        self.assertEqual(r.status, 301)
        self.assertEqual(r.getheader("Location"), f"/{self.token}/")

    def test_each_start_makes_another_token(self):
        a = appmod.App(self.out, self.home, FX.app, 0)
        try:
            self.assertNotEqual(a.token, self.token)
            self.assertGreaterEqual(len(a.token), 32)
            self.assertTrue(a.url.startswith("http://127.0.0.1:"))
            self.assertEqual(a.httpd.server_address[0], "127.0.0.1")
        finally:
            a.httpd.server_close()


class HostOriginTest(Served):
    def test_host_must_be_loopback_with_this_port(self):
        for host in ("evil.example:%d" % self.port, "127.0.0.1", f"127.0.0.1:{self.port + 1}", "attacker.localhost:%d" % self.port,
                     f"localhost.evil.example:{self.port}", ""):
            r, _ = self.get("api/state", host=host)
            self.assertEqual(r.status, 403, host)
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"):
            r, _ = self.get("api/state", host=host)
            self.assertEqual(r.status, 200, host)

    def test_other_origins_and_sites_are_refused(self):
        r, _ = self.get("api/state", headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status, 403)
        r, _ = self.get("api/state", headers={"Origin": "null"})
        self.assertEqual(r.status, 403)
        r, _ = self.get("api/state", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status, 403)
        r, _ = self.get("api/state", headers={"Origin": f"http://localhost:{self.port}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status, 200)

    def test_a_post_needs_a_same_origin(self):
        with mock.patch("walle.app.subprocess.Popen") as popen:
            r, _, _ = self.run_cmd("walle runs", origin=False)
            self.assertEqual(r.status, 403)
            r, _ = self.req("POST", f"/{self.token}/api/run", body={"command": "walle runs"},
                            headers={"Origin": "http://evil.example"})
            self.assertEqual(r.status, 403)
            r, _ = self.req("POST", f"/{self.token}/api/run", body="command=walle+runs",
                            headers={"Origin": f"http://127.0.0.1:{self.port}",
                                     "Content-Type": "application/x-www-form-urlencoded"})
            self.assertEqual(r.status, 415)
            popen.assert_not_called()

    def test_headers_block_other_origins(self):
        r, _ = self.get("")
        csp = r.getheader("Content-Security-Policy")
        for part in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "font-src 'self'",
                     "connect-src 'self'", "frame-ancestors 'none'"):
            self.assertIn(part, csp)
        self.assertNotIn("http", csp)
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")


class AllowlistTest(unittest.TestCase):
    def test_read_commands_pass(self):
        cases = {
            "walle scan .": ["scan", ".", "--progress", "json"],
            "walle scan ~/projects --label 'before trim' --progress json": ["scan", "~/projects", "--label", "before trim", "--progress", "json"],
            "walle context --last --json": ["context", "--last", "--json"],
            "walle context --usage --since 2026-10-01 --json": ["context", "--usage", "--since", "2026-10-01", "--json"],
            "walle context --compare 20261021-041924": ["context", "--compare", "20261021-041924"],
            "walle eve . --around 20261021-041924": ["eve", ".", "--around", "20261021-041924"],
            "walle measure --days=14": ["measure", "--days=14"],
            "walle runs --limit 5": ["runs", "--limit", "5"],
            "walle diff": ["diff"],
            "walle show 1fdf9c": ["show", "1fdf9c"],
            "walle trim --plan --since 2026-10-01": ["trim", "--plan", "--since", "2026-10-01"],
            "walle judge --dry-run": ["judge", "--dry-run"],
            "walle scan --help": ["scan", "--help"],
        }
        for text, args in cases.items():
            self.assertEqual(appmod.check_command(text), args, text)

    def test_commands_that_change_files_are_refused(self):
        for text in ("walle apply 20261021-052705 --only c12dc3", "walle undo 20261021-052801", "walle keep 1fdf9c",
                     "walle unkeep 1fdf9c", "walle pin 20261021-041702", "walle judge --import", "walle judge --imp",
                     "walle judge", "walle judge --dry-run --import", "walle trim --import 20261021-052705",
                     "walle trim --plan --import", "walle trim", "walle trim --plan 20261021-052705",
                     "walle --out /tmp/x scan .", "walle scan . --out /tmp/x", "walle scan --out=/tmp/x",
                     "walle check .", "walle alarm", "walle app", "walle", "rm -rf ~", "scan .", "walle scan . ; rm -rf ~",
                     "walle scan $(rm -rf ~)", "walle context --compare a b --json", "walle scan --json=1",
                     "walle scan --label", "walle context --compare", "walle scan --", "walle show a b",
                     "walle runs extra", "walle scan 'unclosed"):
            with self.assertRaises(appmod.Refused, msg=text):
                appmod.check_command(text)

    def test_the_reason_names_the_command(self):
        with self.assertRaises(appmod.Refused) as e:
            appmod.check_command("walle apply 20261021-052705 --only c12dc3")
        self.assertIn("apply changes settings files", str(e.exception))
        with self.assertRaises(appmod.Refused) as e:
            appmod.check_command("walle judge --import")
        self.assertIn("--import", str(e.exception))

    def test_child_argv_is_a_list_with_the_out_folder(self):
        argv = appmod.child_argv(Path("/x/runs"), ["scan", "a b; rm -rf ~"])
        self.assertEqual(argv[0], sys.executable)
        self.assertIn("-I", argv)
        self.assertEqual(argv[-4:], ["--out", "/x/runs", "scan", "a b; rm -rf ~"])


class RunTest(Served):
    def test_a_refused_command_runs_nothing(self):
        with mock.patch("walle.app.subprocess.Popen") as popen:
            for text in ("walle apply 20261021-052705 --only c12dc3", "walle undo 20261021-052801", "walle keep x",
                         "walle unkeep x", "walle pin x", "walle judge --import", "walle trim --import x",
                         "walle scan . --out /tmp/x"):
                r, _, events = self.run_cmd(text)
                self.assertEqual(r.status, 200, text)
                self.assertEqual([e["event"] for e in events], ["refused"], text)
                self.assertEqual(events[0]["command"], text)
            popen.assert_not_called()

    def test_argv_without_a_shell_in_a_known_folder(self):
        with mock.patch("walle.app.subprocess.Popen", return_value=FakeProc(["ok"])) as popen:
            r, _, events = self.run_cmd("walle scan . --label 'a b; rm -rf ~ $(id)'", folder=str(FX.app))
        self.assertEqual(r.status, 200)
        args, kw = popen.call_args
        argv = args[0]
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[-8:], ["--out", str(self.out), "scan", ".", "--label", "a b; rm -rf ~ $(id)", "--progress", "json"])
        self.assertFalse(kw.get("shell", False))
        self.assertEqual(kw["cwd"], str(FX.app))
        self.assertEqual(kw["env"]["HOME"], str(self.home))
        self.assertEqual(kw["stdin"], appmod.subprocess.DEVNULL)
        self.assertEqual([e["event"] for e in events], ["start", "stdout", "exit"])

    def test_an_unknown_folder_runs_in_the_launch_folder(self):
        with mock.patch("walle.app.subprocess.Popen", return_value=FakeProc()) as popen:
            self.run_cmd("walle runs", folder="/etc")
        self.assertEqual(popen.call_args[1]["cwd"], str(self.app.launch))

    def test_progress_events_stream_in_order(self):
        err = [json.dumps({"event": "step", "step": "collect", "state": "start", "t": 0.0}),
               json.dumps({"event": "step", "step": "collect", "state": "end", "files": 12, "t": 0.4}),
               "a plain line on stderr",
               json.dumps({"event": "done", "files": 12, "pairs": 3, "items": 4, "scan": "/x/scan.json", "t": 0.9})]
        with mock.patch("walle.app.subprocess.Popen", return_value=FakeProc(["/x/scan.md"], err, 0)):
            r, _, events = self.run_cmd("walle scan .")
        self.assertEqual(r.getheader("Content-Type"), "application/x-ndjson; charset=utf-8")
        progress = [e["data"] for e in events if e["event"] == "progress"]
        self.assertEqual([(p["event"], p.get("state")) for p in progress], [("step", "start"), ("step", "end"), ("done", None)])
        self.assertEqual(progress[1]["files"], 12)
        self.assertIn({"event": "stderr", "text": "a plain line on stderr"}, events)
        self.assertEqual(events[0]["event"], "start")
        self.assertEqual(events[-1], {"event": "exit", "code": 0, "seconds": events[-1]["seconds"]})

    def test_one_command_at_a_time(self):
        self.app.lock.acquire()
        try:
            with mock.patch("walle.app.subprocess.Popen") as popen:
                r, data, _ = self.run_cmd("walle runs")
            self.assertEqual(r.status, 409)
            popen.assert_not_called()
        finally:
            self.app.lock.release()

    def test_a_real_read_command_runs_with_the_fake_home(self):
        r, _, events = self.run_cmd("walle runs --limit 3", folder=str(FX.app))
        self.assertEqual(events[-1]["event"], "exit")
        self.assertEqual(events[-1]["code"], 0, events)
        text = "\n".join(e.get("text", "") for e in events)
        self.assertIn(str(self.out / "index.jsonl"), text)


class EscapeTest(Served):
    launch = None  # the markup folder

    def test_the_page_title_escapes_the_folder(self):
        _, body = self.get("")
        page = body.decode()
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", page)
        self.assertNotIn("<img", page)

    def test_json_escapes_markup_and_keeps_the_text(self):
        for rest in ("api/state", "api/runs", "api/folder?path=" + quote(str(FX.projects), safe=""),
                     "api/folder?path=" + quote(str(FX.markup), safe="")):
            r, body = self.get(rest)
            self.assertEqual(r.status, 200, rest)
            self.assertTrue(r.getheader("Content-Type").startswith("application/json"))
            self.assertNotIn(b"<", body, rest)
            self.assertNotIn(b">", body, rest)
        state = json.loads(self.get("api/state")[1])
        self.assertIn(MARKUP, [f["short"] for f in state["folders"]])
        runs = json.loads(self.get("api/runs")[1])["runs"]
        self.assertIn(LABEL, [r["label"] for r in runs])

    def test_a_refused_command_echo_is_escaped(self):
        r, data, events = self.run_cmd("walle apply </script><script>alert(1)</script>")
        self.assertNotIn(b"<", data)
        self.assertEqual(events[0]["command"], "walle apply </script><script>alert(1)</script>")


class StaticTest(Served):
    def test_the_page_loads_nothing_from_another_origin(self):
        for rest in ("", "app.js", "app.css", "tokens.css", "vendor/walle-ds/bundle.css", "vendor/walle-ds/bundle.js"):
            r, body = self.get(rest)
            self.assertEqual(r.status, 200, rest)
            self.assertNotIn(b"@import", body, rest)
            self.assertNotRegex(body.decode(), r"(src|href)=\"(https?:)?//", rest)
            self.assertNotIn(b"fonts.googleapis", body, rest)
        r, body = self.get("favicon.svg")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "image/svg+xml"))
        self.assertIn(b'href="favicon.svg"', self.get("")[1])  # the browser asks for no /favicon.ico without the token
        r, _ = self.get("fonts/IBMPlexMono-Latin-600.woff2")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "font/woff2"))

    def test_no_film_names_in_the_page_files(self):
        film = re.compile(r"WALL-E|Wall-E|WALL·E|\bEVE\b")
        ui = Path(appmod.__file__).resolve().parent / "appui"
        hits = [f"{p.relative_to(ui)}:{n}" for p in sorted(ui.rglob("*")) if p.is_file() and "react" not in p.name.lower()
                for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1) if film.search(line)]
        self.assertEqual(hits, [])

    def test_tokens_css_has_both_themes(self):
        css = self.get("tokens.css")[1].decode()
        self.assertIn("--walle-orange: #E85F28;", css)
        self.assertIn("--walle-orange: #F0703F;", css)
        self.assertIn("@media (prefers-color-scheme: dark)", css)
        self.assertIn("--link: var(--eve);", css)
        self.assertIn("--font-mono:", css)
        self.assertIn("--space-12: 48px;", css)

    def test_only_listed_files_are_served(self):
        for rest in ("../app.py", "vendor/../app.py", "fonts/../cli.py", "fonts/OFL.txt", "vendor/", "appui/index.html",
                     "api/nothing"):
            r, _ = self.get(rest)
            self.assertIn(r.status, (403, 404), rest)


class ViewTest(Served):
    def test_folders_come_from_the_run_index(self):
        state = json.loads(self.get("api/state")[1])
        rows = {f["path"]: f for f in state["folders"]}
        ctl = rows[str(FX.projects / "atc" / "controller")]
        self.assertEqual((ctl["verdict"], ctl["short"]), ("OVER BUDGET", "atc/controller"))
        self.assertTrue(ctl["tokens_text"].startswith("~"))
        self.assertTrue(rows[str(FX.markup)]["launch"])
        self.assertEqual(state["keep"], 1)

    def test_folder_view_of_atc(self):
        v = json.loads(self.get("api/folder?path=" + str(FX.app))[1])
        self.assertEqual(v["scan"]["label"], LABEL)
        self.assertEqual(v["scan"]["stacks"][0]["agent"], "claude")
        judge = v["judge"]["items"]
        self.assertEqual([c["verdict"] and c["verdict"]["real"] for c in judge], [True, None, False])
        self.assertTrue(all(r["file"].startswith("~/projects/atc") for c in judge for r in c["refs"]))
        m = v["measure"]
        self.assertEqual(m["sides"], ["before", "after"])
        self.assertIn("[", m["rows"][0]["sides"]["before"]["cells"]["first_call"]["text"])
        self.assertTrue(m["rows"][0]["change"]["first_call"]["text"])
        t = v["trim"]
        self.assertTrue(t["rows"])
        self.assertTrue(all(r["command"] == f"walle apply {t['id']} --only {r['id']}" for r in t["rows"]))
        ctx = json.loads(FX.context.stdout)
        self.assertEqual(v["context"]["first_call"]["data"], ctx)
        self.assertTrue(v["context"]["first_call"]["here"])
        self.assertGreater(v["context"]["usage"]["data"]["usage"]["sessions"], 0)
        self.assertEqual(v["keep"][0]["id"], "86ac3d")

    def test_a_folder_without_a_scan(self):
        v = json.loads(self.get("api/folder?path=/nowhere/at/all")[1])
        self.assertIsNone(v["scan"])
        self.assertIsNone(v["judge"])
        self.assertIsNone(v["measure"])

    def test_apply_command_of_a_claude_json_row(self):
        row = {"id": "c12dc3", "applies": "walle", "change": {"file": "~/.claude.json"}}
        self.assertEqual(appdata._apply_command("20261021-052705", row), "walle apply 20261021-052705 --only c12dc3 --app-closed")


class EmptyHomeTest(unittest.TestCase):
    def test_an_empty_walle_folder(self):
        tmp = tempfile.mkdtemp()
        try:
            home = Path(tmp).resolve() / "home"
            launch = home / "projects" / "new"
            launch.mkdir(parents=True)
            out = home / ".walle" / "runs"
            app = appmod.App(out, home, launch, 0)
            threading.Thread(target=app.serve, daemon=True).start()
            try:
                def get(rest):
                    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
                    conn.request("GET", f"/{app.token}/{rest}", headers={"Host": f"127.0.0.1:{app.port}"})
                    r = conn.getresponse()
                    return r.status, json.loads(r.read())
                status, state = get("api/state")
                self.assertEqual(status, 200)
                self.assertEqual(state["runs"], 0)
                self.assertEqual(state["keep"], 0)
                self.assertEqual([(f["path"], f["has_scan"]) for f in state["folders"]], [(str(launch), False)])
                status, view = get("api/folder?path=" + str(launch))
                self.assertEqual(status, 200)
                self.assertEqual([view[k] for k in ("scan", "judge", "measure", "trim")], [None] * 4)
                self.assertEqual(view["context"], {"first_call": None, "usage": None})
                self.assertEqual((view["runs"], view["keep"]), ([], []))
                self.assertFalse(out.exists())  # reading made no folder
            finally:
                app.close()
        finally:
            shutil.rmtree(tmp)


class CliTest(unittest.TestCase):
    def test_walle_app_has_a_port_and_no_open(self):
        from appfixture import walle
        p = walle(FX.home, FX.out, "app", "--help")
        self.assertEqual(p.returncode, 0)
        self.assertIn("--port", p.stdout)
        self.assertIn("--no-open", p.stdout)


if __name__ == "__main__":
    unittest.main()
