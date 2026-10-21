"""walle app: the token, the Host and Origin checks, the command allowlist, argv without a shell, the progress
stream, escaping of paths and labels, an empty ~/.walle, and the views built from real run files."""

import http.client
import io
import json
import re
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
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

    def poll(self):
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
        for path in ("/", "/api/state", "/app.js", "/favicon.ico", "/favicon.svg", "/wrong/api/state", f"/{self.token[:-1]}/api/state",
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
        r, _ = self.get("api/state", headers={"Sec-Fetch-Site": "same-site"})  # a page on another 127.0.0.1 port
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
            "walle scan .": ["scan", ".", "--progress", "json", "--keep-read-only"],
            "walle scan ~/projects --label 'before trim' --progress json": ["scan", "~/projects", "--label", "before trim", "--progress", "json",
                                                                          "--keep-read-only"],
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
        self.assertEqual(argv[-9:], ["--out", str(self.out), "scan", ".", "--label", "a b; rm -rf ~ $(id)", "--progress", "json",
                                     "--keep-read-only"])
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

    def test_bad_requests_run_nothing(self):
        origin = {"Origin": f"http://127.0.0.1:{self.port}"}
        with mock.patch("walle.app.subprocess.Popen") as popen:
            # No body after these headers: the server answers before it reads one.
            for length in ("0", str(appmod.BODY_CAP + 1), "x"):
                r, _ = self.req("POST", f"/{self.token}/api/run",
                                headers={**origin, "Content-Type": "application/json", "Content-Length": length})
                self.assertEqual(r.status, 400, length)
            for body in (b"not json", b'{"command": 1}', b'["walle runs"]'):
                r, _ = self.req("POST", f"/{self.token}/api/run", body=body,
                                headers={**origin, "Content-Type": "application/json"})
                self.assertEqual(r.status, 400, body)
            r, _ = self.req("POST", f"/{self.token}/api/x", headers=origin)
            self.assertEqual(r.status, 404)
            popen.assert_not_called()
        for rest in ("api/folder", "api/folder?path=rel", f"api/folder?path={quote('../' + FX.app.name)}"):
            r, _ = self.get(rest)
            self.assertEqual(r.status, 400, rest)


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
        for flag in ("--port", "--no-open", "--allow-host"):
            self.assertIn(flag, p.stdout)

    def test_a_bad_allow_host_stops_before_listening(self):
        from appfixture import walle
        for name in ("https://vocado-dev.ts.net", "vocado-dev.ts.net:8760", "a/b", "*.ts.net", ""):
            p = walle(FX.home, FX.out, "app", "--no-open", "--allow-host", name)
            self.assertEqual(p.returncode, 2, name)
            self.assertIn("--allow-host takes a host name", p.stderr, name)

    def test_allow_host_reaches_the_server(self):
        from appfixture import child
        name = "vocado-dev.tail82e74d.ts.net"
        argv, env = child(FX.home, FX.out, "app", "--no-open", "--allow-host", name)
        with subprocess.Popen(argv, cwd=str(FX.home), env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True) as p:
            try:
                m = re.fullmatch(r"walle app: http://127\.0\.0\.1:(\d+)/([^/]+)/", p.stdout.readline().strip())
                self.assertIsNotNone(m)
                port, token = int(m[1]), m[2]
                self.assertIn(f"https://{name}:{port}/{token}/", p.stdout.readline())
                for host, status in ((f"{name}:{port}", 200), (f"other.tail82e74d.ts.net:{port}", 403)):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
                    conn.request("GET", f"/{token}/api/state", headers={"Host": host})
                    self.assertEqual(conn.getresponse().status, status, host)
                    conn.close()
            finally:
                p.terminate()
                p.wait(timeout=30)


SILENT = [sys.executable, "-c", "import time; time.sleep(60)"]


class SilentChildTest(Served):
    """A command that prints nothing must still stop when the page goes away or the server stops."""

    def start_silent(self):
        procs = []
        real = subprocess.Popen

        def spy(*a, **kw):
            procs.append(real(*a, **kw))
            return procs[-1]
        patches = [mock.patch("walle.app.child_argv", return_value=SILENT),
                   mock.patch("walle.app.subprocess.Popen", side_effect=spy)]
        for x in patches:
            x.start()
        self.addCleanup(lambda: [x.stop() for x in patches])
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        body = json.dumps({"command": "walle runs"}).encode()
        sock.sendall((f"POST /{self.token}/api/run HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                      f"Origin: http://127.0.0.1:{self.port}\r\nContent-Type: application/json\r\n"
                      f"Content-Length: {len(body)}\r\n\r\n").encode() + body)
        got = b""
        while b'"start"' not in got:
            got += sock.recv(4096)
        for _ in range(100):
            if procs:
                break
            time.sleep(0.05)
        self.addCleanup(lambda: [p.kill() for p in procs if p.poll() is None])
        return sock, procs[0]

    def wait_for(self, check, seconds=8):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if check():
                return True
            time.sleep(0.1)
        return False

    def test_a_closed_page_stops_a_silent_command(self):
        sock, proc = self.start_silent()
        self.assertIsNone(proc.poll())
        sock.close()
        self.assertTrue(self.wait_for(lambda: proc.poll() is not None), "the child still runs")
        self.assertTrue(self.wait_for(lambda: not self.app.lock.locked()), "the lock is still held")
        with mock.patch("walle.app.subprocess.Popen", return_value=FakeProc(["ok"])):
            r, _, events = self.run_cmd("walle runs")
        self.assertEqual(r.status, 200)
        self.assertEqual(events[-1]["event"], "exit")

    def test_stopping_the_server_stops_a_running_command(self):
        sock, proc = self.start_silent()
        self.addCleanup(sock.close)
        self.assertIn(proc, self.app.children)
        self.app.stop_children()
        self.assertTrue(self.wait_for(lambda: proc.poll() is not None), "the child still runs")
        self.assertEqual(proc.returncode, -15)


class AllowHostTest(Served):
    NAME = "vocado-dev.tail82e74d.ts.net"

    @classmethod
    def setUpClass(cls):
        cls.home, cls.out = FX.home, FX.out
        cls.app = appmod.App(cls.out, cls.home, FX.app, 0, allow_hosts=[cls.NAME.upper()])
        threading.Thread(target=cls.app.serve, daemon=True).start()
        cls.port, cls.token = cls.app.port, cls.app.token

    def test_the_name_with_the_port_is_a_host(self):
        self.assertEqual(self.app.httpd.server_address[0], "127.0.0.1")
        for host in (f"{self.NAME}:{self.port}", f"{self.NAME.upper()}:{self.port}", f"127.0.0.1:{self.port}"):
            r, _ = self.get("api/state", host=host)
            self.assertEqual(r.status, 200, host)
        for host in (self.NAME, f"{self.NAME}:{self.port + 1}", f"evil.{self.NAME}:{self.port}",
                     f"other.tail82e74d.ts.net:{self.port}"):
            r, _ = self.get("api/state", host=host)
            self.assertEqual(r.status, 403, host)

    def test_the_name_is_an_origin_over_https_and_http(self):
        host = f"{self.NAME}:{self.port}"
        for origin in (f"https://{self.NAME}:{self.port}", f"http://{self.NAME}:{self.port}"):
            r, _ = self.get("api/state", host=host, headers={"Origin": origin})
            self.assertEqual(r.status, 200, origin)
            with mock.patch("walle.app.subprocess.Popen", return_value=FakeProc(["ok"])):
                r, _ = self.req("POST", f"/{self.token}/api/run", body={"command": "walle runs"},
                                headers={"Origin": origin}, host=host)
            self.assertEqual(r.status, 200, origin)
        for origin in (f"https://{self.NAME}", f"https://{self.NAME}:443", f"https://evil.example:{self.port}"):
            r, _ = self.get("api/state", host=host, headers={"Origin": origin})
            self.assertEqual(r.status, 403, origin)

    def test_the_token_is_still_needed(self):
        r, _ = self.req("GET", "/api/state", host=f"{self.NAME}:{self.port}")
        self.assertEqual(r.status, 403)

    def test_bad_names_are_refused(self):
        for name in ("https://x.ts.net", "x.ts.net:1", "x/y", "-x.ts.net", "x..ts.net", ""):
            with self.assertRaises(ValueError, msg=name):
                appmod.check_host_name(name)


class OpenBrowserTest(unittest.TestCase):
    def test_the_browser_gets_a_private_file_not_the_token(self):
        url = "http://127.0.0.1:8760/SECRET-token_value/"
        with mock.patch("webbrowser.open") as wb:
            folder = appmod.open_browser(url)
        try:
            opened = wb.call_args[0][0]
            self.assertTrue(opened.startswith("file://"))
            self.assertNotIn("SECRET", opened)
            page = Path(opened[len("file://"):])
            self.assertEqual(stat.S_IMODE(page.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
            self.assertIn(f'url={url}"', page.read_text())
        finally:
            shutil.rmtree(folder)


class DataTest(unittest.TestCase):
    """appdata against hand-made runs: unreadable scans, the cache limits, rows already set."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = self.tmp / "runs"
        self.out.mkdir()

    def index(self, *rows):
        (self.out / "index.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_folders_and_the_scan_view_agree_on_unreadable_scans(self):
        rows = [json.loads(l) for l in (FX.out / "index.jsonl").read_text().splitlines()]
        good = [r for r in rows if r["kind"] == "scan"][-1]
        shutil.copytree(FX.out / good["id"], self.out / good["id"])
        good = {**good, "path": str(self.out / good["id"] / "scan.json")}  # only the copy
        old = {"id": "20991231-000000", "kind": "scan", "roots": [str(FX.projects)], "generated": "2099-12-31T00:00:00",
               "per_folder": {f"{FX.app}@claude": 10, "/gone/folder@claude": 20},
               "verdicts": {f"{FX.app}@claude": "OVER BUDGET", "/gone/folder@claude": "LEAN"},
               "path": str(self.out / "20991231-000000" / "scan.json")}
        (self.out / old["id"]).mkdir()
        (self.out / old["id"] / "scan.json").write_text(json.dumps({"schema": 1, "stacks": []}))  # no triage
        self.index(good, old)
        rows = {f["path"]: f for f in appdata.folders(self.out, FX.home)}
        self.assertEqual(rows[str(FX.app)]["scan_id"], good["id"])  # the newest readable scan
        self.assertTrue(rows[str(FX.app)]["has_scan"])
        self.assertEqual(appdata.scan_view(self.out, FX.home, str(FX.app))[0]["id"], good["id"])
        self.assertFalse(rows["/gone/folder"]["has_scan"])
        self.assertIsNone(rows["/gone/folder"]["verdict"])
        self.assertIsNone(appdata.scan_view(self.out, FX.home, "/gone/folder")[0])
        (self.out / good["id"] / "scan.json").unlink()  # a missing file reads as no scan too
        rows = {f["path"]: f for f in appdata.folders(self.out, FX.home)}
        self.assertFalse(rows[str(FX.app)]["has_scan"])
        self.assertIsNone(appdata.scan_view(self.out, FX.home, str(FX.app))[0])

    def test_the_cache_has_limits_and_forgets_gone_files(self):
        files = []
        for i in range(5):
            f = self.tmp / f"f{i}.json"
            f.write_text(json.dumps({"i": i, "pad": "x" * 1000}))
            files.append(f)
        with mock.patch.object(appdata, "CACHE_FILES", 3), mock.patch.dict(appdata._cache, clear=True):
            for f in files:
                self.assertEqual(appdata.load_json(f)["i"], int(f.stem[1:]))
            self.assertEqual(list(appdata._cache), [str(f) for f in files[2:]])  # least recently used went first
            appdata.load_json(files[2])
            files[3].unlink()
            appdata.load_json(files[0])
            self.assertNotIn(str(files[3]), appdata._cache)
            self.assertLessEqual(len(appdata._cache), 3)
        with mock.patch.object(appdata, "CACHE_BYTES", 1500), mock.patch.dict(appdata._cache, clear=True):
            appdata.load_json(files[0])
            appdata.load_json(files[1])
            self.assertEqual(list(appdata._cache), [str(files[1])])

    def test_trim_rows_already_set_have_no_saving_and_no_command(self):
        rid = "20261021-052705"
        plan = {"generated": "2026-10-21T05:27:06", "period": {"sessions": 912}, "notes": [], "rows": [
            {"id": "0d7265", "title": "Hide the skill a", "applies": "walle", "action": "T5",
             "change": {"file": "~/.claude/settings.json", "already": True, "set_at": {"time": "2026-10-21T04:19:24"}},
             "saving": {"tokens_per_session_min": 70, "unused_tokens_min": 0, "counted": False,
                        "not_counted": "the key is already set"},
             "set_status": [{"entrypoint": "cli", "state": "not measured", "text": "set, not measured on cli"}]},
            {"id": "c12dc3", "title": "Remove the MCP server rea", "applies": "walle", "action": "T2",
             "change": {"file": "~/.claude.json", "already": False},
             "saving": {"tokens_per_session_min": 4174, "unused_tokens_min": 279658}},
            {"id": "9a9a9a", "title": "Hide the skill b", "applies": "walle", "action": "T5", "change": {},
             "saving": {"tokens_per_session_min": 5, "unused_tokens_min": 0, "counted": False,
                        "not_counted": "not in the current bucket"}},
        ]}
        (self.out / rid).mkdir()
        (self.out / rid / "plan.json").write_text(json.dumps(plan))
        self.index({"id": rid, "kind": "trim", "roots": [], "path": str(self.out / rid / "plan.json")})
        rows = appdata.trim_view(self.out, FX.home, str(FX.app))["rows"]
        self.assertEqual([r["id"] for r in rows], ["c12dc3", "9a9a9a", "0d7265"])  # rows already set last
        done = rows[-1]
        self.assertTrue(done["already"])
        self.assertIsNone(done["command"])
        self.assertFalse(done["counted"])
        self.assertEqual(done["set_status"][0]["text"], "set, not measured on cli")
        self.assertEqual(rows[0]["command"], f"walle apply {rid} --only c12dc3 --app-closed")
        self.assertTrue(rows[0]["counted"])
        self.assertEqual((rows[1]["counted"], rows[1]["not_counted"]), (False, "not in the current bucket"))

    def test_a_lean_stack_with_nothing_to_read_has_its_sentence(self):
        v = appdata.scan_view(FX.out, FX.home, str(FX.projects))[0]
        lean = [s for s in v["stacks"] if s["verdict"] == "LEAN" and not s["start_here"]]
        self.assertTrue(lean)
        for s in lean:
            self.assertEqual(len(s["lead"]), 1)
            self.assertIn("Checked:", s["lead"][0])
        v = appdata.scan_view(FX.out, FX.home, str(FX.projects / "DesignLAB"))[0]
        trim = v["stacks"][0]
        self.assertEqual(trim["lead"], trim["sentences"][1:])  # the head shows the first sentence


if __name__ == "__main__":
    unittest.main()
