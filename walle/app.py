"""walle app: a local web app on 127.0.0.1 that shows the runs and runs walle read commands.

Phase 1 (2026-10-21). The page is the walle design system (walle/appui/vendor/walle-ds) on React 18, both
shipped in the package, with the IBM Plex fonts of walle/fonts. The page loads nothing from another origin:
the Content-Security-Policy allows only the app itself.

Safety:
- The server listens on 127.0.0.1 only. It makes a random token at start, and every path starts with it:
  /<token>/ is the page, /<token>/api/... the API. A request without the token gets 403.
- The Host header must be 127.0.0.1, localhost or [::1] with the port of the server (DNS rebinding). An Origin
  header must be the same origin, and a POST must carry one (or Sec-Fetch-Site: same-origin).
- POST /<token>/api/run takes one `walle ...` command. The command is split with shlex, checked against an
  allowlist of read commands (ALLOW), and run as an argv list without a shell, in the folder that the page
  shows. apply, undo, keep, unkeep, pin, judge --import and trim --import are refused before anything runs:
  the stream has one "refused" event with the reason.
  The server sets --out; a command cannot change it. One command runs at a time.
- The views read the run index and the run files (appdata.py). They run no command.
"""

from __future__ import annotations

import hmac
import json
import os
import queue
import re
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__, appdata, brief, keep, runs
from .collect import find_checkout

UI = resources.files("walle") / "appui"
FONTS = resources.files("walle") / "fonts"
PKG_PARENT = str(Path(__file__).resolve().parent.parent)
# The child imports walle from PKG_PARENT, whatever its folder holds: -I leaves the folder out of sys.path.
CHILD = "import sys; sys.path.insert(0, sys.argv.pop(1)); from walle.cli import main; sys.exit(main())"
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
TIMEOUT = 1800  # seconds a command may run
OUTPUT_CAP = 2_000_000  # characters of stdout and stderr sent to the page
BODY_CAP = 65536

STATIC = {
    "": ("index.html", "text/html; charset=utf-8"),
    "app.js": ("app.js", "text/javascript; charset=utf-8"),
    "app.css": ("app.css", "text/css; charset=utf-8"),
    "vendor/react.production.min.js": ("vendor/react.production.min.js", "text/javascript; charset=utf-8"),
    "vendor/react-dom.production.min.js": ("vendor/react-dom.production.min.js", "text/javascript; charset=utf-8"),
    "vendor/walle-ds/bundle.js": ("vendor/walle-ds/bundle.js", "text/javascript; charset=utf-8"),
    "vendor/walle-ds/bundle.css": ("vendor/walle-ds/bundle.css", "text/css; charset=utf-8"),
}
FONT_FILES = (("IBM Plex Sans", "400 600", "IBMPlexSans-Latin.woff2"),
              ("IBM Plex Mono", "400", "IBMPlexMono-Latin-400.woff2"),
              ("IBM Plex Mono", "600 700", "IBMPlexMono-Latin-600.woff2"))

# ---------------------------------------------------------------------- allowlist
V, S, MANY = "value", "switch", "many"
HELP = {"-h": S, "--help": S}
MEASURE_OPTIONS = {"--around": V, "--days": V, "--since": V, "--before": V, "--agent": V, "--across-folders": S,
       "--transcripts": V, "--label": V, "--lang": V}
# subcommand -> (options and what they take, most positional arguments)
ALLOW: dict[str, tuple[dict[str, str], int]] = {
    "scan": ({"--top": V, "--html": S, "--max-tokens": V, "--estimator": V, "--label": V, "--format": V,
              "--json": S, "--all": S, "--color": V, "-q": S, "--quiet": S, "--progress": V, "--lang": V}, 20),
    "context": ({"--last": S, "--session": V, "--agent": V, "--usage": S, "--since": V, "--before": V,
                 "--estimator": V, "--transcripts": V, "--label": V, "--compare": MANY, "--root": V, "--days": V,
                 "--lang": V, "--json": S}, 0),
    "eve": (MEASURE_OPTIONS, 1),
    "measure": (MEASURE_OPTIONS, 1),
    "runs": ({"--root": V, "--limit": V, "--html": S, "--lang": V}, 0),
    "diff": ({"--root": V, "--transcripts": V, "--color": V, "--lang": V}, 2),
    "show": ({"--color": V, "--lang": V}, 1),
    # The positional run of walle trim is for --import only.
    "trim": ({"--plan": S, "--since": V, "--before": V, "--scan": V, "--transcripts": V, "--batch-size": V,
              "--judge-name": V, "--label": V}, 0),
    "judge": ({"--dry-run": S, "--batch-size": V, "--deep": S, "--judge-name": V}, 1),
}
REQUIRED = {"trim": "--plan", "judge": "--dry-run"}
READ_ONLY = "The app runs read commands only in this version. Run it in a terminal."
REFUSED = {
    "apply": "walle apply changes settings files.",
    "undo": "walle undo changes settings files.",
    "keep": "walle keep changes keep.json.",
    "unkeep": "walle unkeep changes keep.json.",
    "pin": "walle pin changes pins.json.",
}


class Refused(ValueError):
    """A command that the app does not run. The message says why, for the page."""


def check_command(text: str) -> list[str]:
    """The arguments after `walle` of an allowed command, else Refused. Nothing runs here."""
    try:
        tokens = shlex.split(text or "")
    except ValueError as e:
        raise Refused(f"The command does not parse: {e}.") from None
    if not tokens or tokens[0] != "walle":
        raise Refused("A command starts with walle.")
    args = tokens[1:]
    if not args:
        raise Refused("Give a subcommand, for example walle scan .")
    cmd = args[0]
    if any(a == "--out" or a.startswith("--out=") for a in args):
        raise Refused("The app sets --out. Leave it out of the command.")
    if cmd in REFUSED:
        raise Refused(f"{REFUSED[cmd]} {READ_ONLY}")
    if cmd not in ALLOW:
        raise Refused(f"The app does not run walle {cmd}. It runs " + ", ".join(sorted(ALLOW)) + ".")
    options, most = ALLOW[cmd]
    options = {**options, **HELP}
    seen, positional, i = set(), 0, 1
    while i < len(args):
        a = args[i]
        if a.startswith("-") and a != "-":
            name, eq, _value = a.partition("=")
            kind = options.get(name)
            if a == "--" or kind is None:
                if name == "--import":
                    raise Refused(f"walle {cmd} --import writes the answers of your agent into the run. {READ_ONLY}")
                raise Refused(f"The app does not run walle {cmd} with {name}.")
            seen.add(name)
            if kind == S:
                if eq:
                    raise Refused(f"{name} takes no value.")
            elif kind == V and not eq:
                if i + 1 >= len(args):
                    raise Refused(f"{name} needs a value.")
                i += 1
            elif kind == MANY:
                if eq:
                    raise Refused(f"Give the values of {name} after a space.")
                n = 0
                while i + 1 < len(args) and not args[i + 1].startswith("-"):
                    i, n = i + 1, n + 1
                if not n:
                    raise Refused(f"{name} needs a value.")
        else:
            positional += 1
            if positional > most:
                raise Refused(f"walle {cmd} takes {most} folder or run argument{'s' if most != 1 else ''} at most."
                              if most else f"walle {cmd} takes no positional argument here.")
        i += 1
    help_only = bool(seen & set(HELP))
    if cmd in REQUIRED and REQUIRED[cmd] not in seen and not help_only:
        raise Refused(f"The app runs walle {cmd} only with {REQUIRED[cmd]}. {READ_ONLY}")
    if cmd == "context" and {"--compare", "--json"} <= seen:
        raise Refused("walle context --compare does not take --json.")
    if cmd == "scan" and "--progress" not in seen and not help_only:
        args = [*args, "--progress", "json"]
    return args


def child_argv(out: Path, args: list[str]) -> list[str]:
    """The argv list of a walle child process. No shell reads it."""
    return [sys.executable, "-I", "-X", "utf8", "-c", CHILD, PKG_PARENT, "--out", str(out), *args]


# ---------------------------------------------------------------------- page parts
def tokens_css() -> str:
    """CSS custom properties from the design system's tokens.json: light on :root, dark under
    prefers-color-scheme and under [data-theme=dark]. Then the IBM Plex @font-face rules."""
    tok = json.loads((UI / "vendor" / "walle-ds" / "tokens.json").read_text(encoding="utf-8"))
    light, dark = ["color-scheme: light;"], ["color-scheme: dark;"]
    for c in tok["color"]["tokens"]:
        v = c["value"]
        if isinstance(v, str):
            m = re.fullmatch(r"\{([a-z0-9-]+)\}", v)
            light.append(f"--{c['name']}: {'var(--' + m.group(1) + ')' if m else v};")
        else:
            light.append(f"--{c['name']}: {v['light']};")
            dark.append(f"--{c['name']}: {v['dark']};")
    for name, value in tok["type"]["families"].items():
        light.append(f"--font-{name}: {value};")
    for group in ("spacing", "radius"):
        for x in tok[group]["tokens"]:
            light.append(f"--{x['name']}: {x['value']};")
    for x in tok["shadow"]["tokens"]:
        light.append(f"--{x['name']}: {x['value']['light']};")
        dark.append(f"--{x['name']}: {x['value']['dark']};")
    faces = [f"@font-face {{ font-family: \"{family}\"; font-weight: {weight}; font-style: normal; "
             f"font-display: swap; src: url(\"fonts/{name}\") format(\"woff2\"); }}" for family, weight, name in FONT_FILES]
    return "\n".join([
        "/* Generated by walle app from vendor/walle-ds/tokens.json. */",
        ":root {\n  " + "\n  ".join(light) + "\n}",
        "@media (prefers-color-scheme: dark) {\n  :root:not([data-theme=\"light\"]) {\n    " + "\n    ".join(dark) + "\n  }\n}",
        ":root[data-theme=\"dark\"] {\n  " + "\n  ".join(dark) + "\n}",
        *faces, ""])


def branch_of(folder: Path) -> str | None:
    """The branch name of the checkout that holds the folder, a short sha when HEAD is detached, else None."""
    checkout = find_checkout(folder)[0]
    if not checkout:
        return None
    git = checkout / ".git"
    try:
        gitdir = git
        if git.is_file():
            m = re.match(r"gitdir:\s*(.+)", git.read_text(errors="replace").strip())
            if not m:
                return None
            gitdir = Path(m.group(1))
            gitdir = gitdir if gitdir.is_absolute() else (checkout / gitdir).resolve()
        head = (gitdir / "HEAD").read_text(errors="replace").strip()
    except OSError:
        return None
    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/"):]
    return head[:8] or None


def dumps(data) -> bytes:
    """JSON for the API. <, > and & are escaped as \\u003c and so on, so no response reads as markup."""
    text = json.dumps(data, ensure_ascii=False, default=str)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").encode("utf-8")


# ---------------------------------------------------------------------- server
class App:
    def __init__(self, out: Path, home: Path, launch: Path, port: int = 0, token: str | None = None):
        self.out, self.home, self.launch = out, home, launch
        self.token = token or secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.httpd.app = self
        self.port = self.httpd.server_address[1]
        self.hosts = {f"{h}:{self.port}" for h in LOOPBACK}
        self.origins = {f"http://{h}" for h in self.hosts}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/"

    def serve(self) -> None:
        self.httpd.serve_forever()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def folder(self, path: str | None) -> Path:
        """A folder the page may run a command in: the launch folder or a folder of the run index."""
        if path and appdata.known_folder(self.out, self.home, path, self.launch) and Path(path).is_dir():
            return Path(path)
        return self.launch

    def state(self) -> dict:
        try:
            load = round(os.getloadavg()[0], 2)
        except OSError:
            load = None
        agents = [name for name, d in (("Claude Code", ".claude"), ("Codex", ".codex")) if (self.home / d).is_dir()]
        return {
            "version": __version__, "out": brief.shown(str(self.out), None, self.home),
            "launch": {"path": str(self.launch), "display": brief.shown(str(self.launch), None, self.home),
                       "branch": branch_of(self.launch)},
            "device": {"name": socket.gethostname(), "load": load}, "agents": agents,
            "folders": appdata.folders(self.out, self.home, self.launch),
            "runs": len(runs.read_index(self.out)), "keep": len(keep.load(keep.path_for(self.out))["entries"]),
            "running": self.lock.locked(),
            "commands": {"allowed": sorted(ALLOW), "refused": sorted(REFUSED)},
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "walle-app"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002 - the name comes from BaseHTTPRequestHandler
        pass  # no request log: the URLs hold the token

    # ---------------------------------------------------------------- replies
    def _head(self, code: int, ctype: str, length: int | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self._head(code, ctype, len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, data) -> None:
        self._send(code, dumps(data), "application/json; charset=utf-8")

    def _deny(self, code: int, text: str) -> None:
        self._send(code, (text + "\n").encode(), "text/plain; charset=utf-8")

    # ---------------------------------------------------------------- checks
    def _route(self) -> str | None:
        """The path after /<token>/, or None after a 403 or a redirect."""
        app: App = self.server.app
        if self.headers.get("Host", "") not in app.hosts:
            self._deny(403, "walle app: this host name is not allowed. Open the URL that walle app printed.")
            return None
        origin = self.headers.get("Origin")
        if origin is not None and origin not in app.origins:
            self._deny(403, "walle app: requests from other origins are refused.")
            return None
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            self._deny(403, "walle app: requests from other sites are refused.")
            return None
        path = urlsplit(self.path).path
        first, _, rest = path.lstrip("/").partition("/")
        if not hmac.compare_digest(first.encode(), app.token.encode()):
            self._deny(403, "walle app: the token is missing or wrong. Open the URL that walle app printed.")
            return None
        if path == "/" + first:  # /<token> without the slash: relative URLs of the page need it
            self.send_response(301)
            self.send_header("Location", f"/{app.token}/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return rest

    # ---------------------------------------------------------------- GET
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        rest = self._route()
        if rest is None:
            return
        app: App = self.server.app
        query = parse_qs(urlsplit(self.path).query)
        if rest in STATIC:
            name, ctype = STATIC[rest]
            body = (UI / name).read_bytes()
            if rest == "":
                title = "walle · " + brief.shown(str(app.launch), None, app.home)
                body = body.replace(b"<title>walle</title>",
                                    f"<title>{_html(title)}</title>".encode("utf-8"))
            return self._send(200, body, ctype)
        if rest == "tokens.css":
            return self._send(200, tokens_css().encode("utf-8"), "text/css; charset=utf-8")
        if rest.startswith("fonts/"):
            name = rest[len("fonts/"):]
            if name in {f[2] for f in FONT_FILES}:
                return self._send(200, (FONTS / name).read_bytes(), "font/woff2")
        if rest == "api/state":
            return self._json(200, app.state())
        if rest == "api/folder":
            path = (query.get("path") or [""])[0]
            if not path or not path.startswith("/"):
                return self._json(400, {"error": "Give an absolute folder path."})
            view = appdata.folder_view(app.out, app.home, path)
            view["branch"] = branch_of(Path(path)) if Path(path).is_dir() else None
            return self._json(200, view)
        if rest == "api/runs":
            return self._json(200, {"runs": appdata.runs_view(app.out, app.home)})
        self._json(404, {"error": "Not found."})

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        rest = self._route()
        if rest is None:
            return
        app: App = self.server.app
        if self.headers.get("Origin") is None and self.headers.get("Sec-Fetch-Site") != "same-origin":
            return self._deny(403, "walle app: a POST needs an Origin header.")
        if rest != "api/run":
            return self._json(404, {"error": "Not found."})
        if self.headers.get_content_type() != "application/json":
            return self._json(415, {"error": "Send the command as application/json."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= BODY_CAP:
            return self._json(400, {"error": "The request body is empty or too large."})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self._json(400, {"error": "The request body is not JSON."})
        if not isinstance(body, dict) or not isinstance(body.get("command"), str):
            return self._json(400, {"error": "Give the command as a string."})
        try:
            args = check_command(body["command"])
        except Refused as e:
            # The run stream says it: a refused command is an answer, not a failed request (no console error).
            self._head(200, "application/x-ndjson; charset=utf-8")
            self.wfile.write(dumps({"event": "refused", "text": str(e), "command": body["command"]}) + b"\n")
            return
        if not app.lock.acquire(blocking=False):
            return self._json(409, {"error": "Another command is running. Wait for it to end."})
        try:
            self._stream(args, app.folder(body.get("folder")))
        finally:
            app.lock.release()

    def _stream(self, args: list[str], cwd: Path) -> None:
        """Run one command and send its output as NDJSON: start, progress (walle scan --progress json),
        stdout and stderr lines, then exit."""
        app: App = self.server.app
        self._head(200, "application/x-ndjson; charset=utf-8")

        def send(event: dict) -> bool:
            try:
                self.wfile.write(dumps(event) + b"\n")
                self.wfile.flush()
                return True
            except OSError:
                return False

        started = time.monotonic()
        shown_cwd = brief.shown(str(cwd), None, app.home)
        if not send({"event": "start", "command": "walle " + shlex.join(args), "cwd": shown_cwd}):
            return
        env = {**os.environ, "HOME": str(app.home), "NO_COLOR": "1"}
        try:
            proc = subprocess.Popen(child_argv(app.out, args), cwd=str(cwd), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True,
                                    encoding="utf-8", errors="replace", bufsize=1, start_new_session=True)
        except OSError as e:
            send({"event": "error", "text": f"walle did not start: {e}"})
            return
        lines: queue.Queue = queue.Queue()

        def read(stream, kind):
            for line in stream:
                lines.put((kind, line.rstrip("\n")))
            lines.put((kind, None))

        readers = [threading.Thread(target=read, args=(stream, kind), daemon=True)
                   for stream, kind in ((proc.stdout, "stdout"), (proc.stderr, "stderr"))]
        for t in readers:
            t.start()
        open_streams, sent, cut, alive = 2, 0, False, True
        while open_streams:
            if time.monotonic() - started > TIMEOUT:
                _stop(proc)
                alive = send({"event": "error", "text": f"walle stopped after {TIMEOUT} s."})
                break
            try:
                kind, line = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                open_streams -= 1
                continue
            event = None
            if kind == "stderr" and line.startswith("{"):
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and isinstance(obj.get("event"), str):
                        event = {"event": "progress", "data": obj}
                except ValueError:
                    pass
            if event is None:
                sent += len(line)
                if sent > OUTPUT_CAP:
                    if not cut:
                        cut = True
                        alive = send({"event": "note", "text": f"The output is longer than {OUTPUT_CAP:,} characters. "
                                                               "The rest is not shown."})
                    continue
                event = {"event": kind, "text": line}
            alive = send(event)
            if not alive:
                _stop(proc)  # the page went away
                break
        try:
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _stop(proc)
            code = proc.wait()
        for t in readers:
            t.join(timeout=5)
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        if alive:
            send({"event": "exit", "code": code, "seconds": round(time.monotonic() - started, 2)})


def _stop(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def _html(text: str) -> str:
    import html
    return html.escape(text, quote=True)


def run_cli(args, home: Path) -> int:
    launch = Path.cwd().resolve()
    try:
        app = App(args.out, home, launch, args.port)
    except OSError as e:
        print(f"walle app: cannot listen on 127.0.0.1:{args.port}: {e.strerror or e}", file=sys.stderr)
        return 2
    print(f"walle app: {app.url}")
    print(f"Read commands only. Runs folder {brief.shown(str(args.out), None, home)}. Stop with Ctrl-C.")
    print(f"From another machine: ssh -N -L {app.port}:127.0.0.1:{app.port} {socket.gethostname()}, "
          "then open the URL above there.")
    sys.stdout.flush()
    if not args.no_open:
        try:
            import webbrowser
            webbrowser.open(app.url)
        except Exception:  # noqa: BLE001 - no browser is fine: the URL is printed
            pass
    try:
        app.serve()
    except KeyboardInterrupt:
        pass
    finally:
        app.httpd.server_close()
    return 0
