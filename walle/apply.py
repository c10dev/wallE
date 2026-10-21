"""walle apply and walle undo: change the settings keys that the user picked from a trim plan, after a backup.

Owner decision (b) of 2026-10-20 (context plan 4.1, brief 1.1 principle 4): walle changes only the settings
keys that the user picks, after a backup, and walle undo puts them back. walle never edits an instruction file
(those rows stay patches), never writes into a folder that it scans, and never deletes data.

walle apply <run> --only <id>[,<id>]
- Every id must be a row of the plan. There is no default choice: the user's answer is the approval.
- Manual rows (a place in an app or on the web) print a checklist with that place. A place that walle could
  not confirm is marked [unconfirmed]. Patch rows print how the agent applies them. walle changes neither.
- Rows that walle can apply change keys of two files: ~/.claude/settings.json (enabledPlugins, skillOverrides)
  and ~/.claude.json (an mcpServers entry). Any other file or key is refused, so a changed plan.json cannot
  make walle write a hook or a permission.
- walle refuses a target whose sha256 differs from the one the plan recorded, a target inside a scanned
  folder, and a file that is not a JSON object. Nothing is written when one target is refused.
- ~/.claude.json: only with --app-closed and when no Claude process runs. The app rewrites the file while
  it runs. After the write walle reads the file again: another hash is reported as "apply lost".
- Before the first write, every target is copied to backups/<apply-id>/before/ (backups/ is next to the runs
  folder: ~/.walle/backups). The new file is written to a temporary file and renamed, with the same mode,
  then copied to after/. undo.json holds the paths, the sha256 before and after, and the rows. The backup
  folder is 0700 and its files 0600: ~/.claude.json holds tokens.
- runs/<apply-id>/apply.json and apply.md record the apply, and the run index gets a line of kind "apply"
  whose parent_id is the plan run. walle runs and walle diff show it.

walle undo <apply-id> restores the before/ copies only when every target still has its after-apply sha256.
Else it lists the JSON key paths that changed since the apply, never their values (values can be secrets),
and stops. An undo adds an index line of kind "undo" whose parent_id is the apply.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__, runs, trim
from .collect import HOME
from .strings import resolve_lang, t

UNDO = "undo.json"
SETTINGS = "~/.claude/settings.json"
CLAUDE_JSON = "~/.claude.json"
# The keys walle may change, per file: the first key of a "set", or the shape of an mcpServers entry.
SET_KEYS = {SETTINGS: ("enabledPlugins", "skillOverrides")}
SERVER_FILES = (CLAUDE_JSON,)
# Files that a running Claude app or session rewrites: written only with --app-closed and no Claude process.
APP_FILES = (CLAUDE_JSON,)
RECHECK_SECONDS = 1.0  # read the file again after this long: the app may rewrite it a moment later
DIFF_LINES = 20
MISSING = object()


class ApplyError(Exception):
    """A refusal: a string key and its fields, and more lines to print (each a key and fields)."""

    def __init__(self, key: str, fields: dict | None = None, lines: list[tuple[str, dict]] | None = None):
        super().__init__(key)
        self.key, self.fields, self.lines = key, fields or {}, lines or []


# ---------------------------------------------------------------------- files
def sha256(path: Path) -> str | None:
    return trim.file_sha256(path)


def _short(path: str | Path, home: Path) -> str:
    return trim._short(str(path), home)


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def scanned_roots(out: Path, plan: dict) -> list[Path]:
    """Folders that walle scans: the roots of every scan in the run index and of the plan's scan."""
    roots = {r for row in runs.scans(out) for r in row.get("roots") or []}
    scan_path = (plan.get("run") or {}).get("scan")
    if scan_path:
        try:
            roots |= set((json.loads(Path(scan_path).read_text()).get("run") or {}).get("roots") or [])
        except (OSError, ValueError):
            pass
    return [Path(r) for r in sorted(roots)]


def in_scan(path: Path, roots: list[Path], home: Path) -> Path | None:
    """The scanned root that holds path, else None.

    A root that holds the home folder (walle scan ~) holds every settings file. The user's own settings are
    not a project folder, so such a root does not count. It counts for a settings file that is a link into
    a project folder, because walle checks the file that the link points to.
    """
    for root in roots:
        if _inside(home, root):
            continue
        if _inside(path, root):
            return root
    return None


def dumps_like(doc: dict, text: str) -> str:
    """doc as JSON in the layout of text: its indent (Claude Code writes two spaces) and its final newline."""
    body = text.strip()
    indent = None
    for line in body.splitlines()[1:2]:
        lead = line[:len(line) - len(line.lstrip())]
        indent = "\t" if lead.startswith("\t") else len(lead) or None
    if indent is None and "\n" not in body:
        sep = (", ", ": ") if '": ' in body else (",", ":")
        out = json.dumps(doc, ensure_ascii=False, separators=sep)
    else:
        out = json.dumps(doc, ensure_ascii=False, indent=indent if indent is not None else 2)
    return out + ("\n" if text.endswith("\n") else "")


def write_atomic(path: Path, data: bytes, mode: int) -> None:
    """Write data to a temporary file next to path, with mode, then rename it over path."""
    tmp = path.with_name(f".{path.name}.walle-{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def copy_private(src: Path, dst: Path) -> None:
    shutil.copy2(src, dst)
    os.chmod(dst, 0o600)


# ---------------------------------------------------------------------- processes
def claude_processes(home: Path = HOME) -> list[str] | None:
    """The Claude Code CLI, desktop app and session processes of this user, as "pid name". None: unknown.

    Linux reads /proc; other systems run ps. A process counts when its program is named claude or Claude,
    lives under ~/.claude/remote/ (the desktop app's session binaries and server) or
    ~/.local/share/claude/ (the CLI's versions), sits in Claude.app, or is node running the claude-code
    package. walle's own process does not count; the agent session that runs walle does.
    """
    procs: list[tuple[int, list[str]]] = []
    proc = Path("/proc")
    if proc.is_dir() and (proc / "self").exists():
        uid = os.getuid()
        for d in proc.iterdir():
            if not d.name.isdigit():
                continue
            try:
                if d.stat().st_uid != uid:
                    continue
                argv = (d / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            argv = [a.decode(errors="replace") for a in argv if a]
            if argv:
                procs.append((int(d.name), argv))
    else:
        try:
            out = subprocess.run(["ps", "-x", "-o", "pid=,args="], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        for line in out.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                procs.append((int(parts[0]), parts[1].split()))
    me = os.getpid()
    return [f"{pid} {os.path.basename(argv[0])}" for pid, argv in procs if pid != me and is_claude(argv, home)]


def is_claude(argv: list[str], home: Path = HOME) -> bool:
    """argv is a Claude Code CLI, a Claude desktop app or session process, or node running claude-code."""
    if not argv:
        return False
    prog = argv[0]
    marks = (str(home / ".claude" / "remote") + "/", str(home / ".local" / "share" / "claude") + "/", "/Claude.app/")
    joined = " ".join(argv[:3])
    return (os.path.basename(prog) in ("claude", "Claude", "claude-desktop") or any(m in prog for m in marks)
            or "@anthropic-ai/claude-code" in joined or "claude-code/cli" in joined)


# ---------------------------------------------------------------------- JSON keys
def key_text(keys: list[str]) -> str:
    return trim._keys([str(k) for k in keys])


def get_at(doc, keys: list[str]):
    cur = doc
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return MISSING
        cur = cur[k]
    return cur


def set_at(doc: dict, keys: list[str], value, file: str) -> None:
    cur = doc
    for k in keys[:-1]:
        nxt = cur.get(k, MISSING)
        if nxt is MISSING:
            nxt = cur[k] = {}
        if not isinstance(nxt, dict):
            raise ApplyError("apply.conflict", {"keys": key_text(keys), "file": file})
        cur = nxt
    cur[keys[-1]] = value


def pop_at(doc: dict, keys: list[str]):
    parent = get_at(doc, keys[:-1]) if len(keys) > 1 else doc
    if not isinstance(parent, dict) or keys[-1] not in parent:
        return MISSING
    return parent.pop(keys[-1])


def key_paths_diff(a, b, path: list[str] | None = None) -> list[tuple[str, list[str]]]:
    """(kind, key path) for each key that differs between two JSON values. Values are not returned."""
    path = path or []
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in a:
            if k not in b:
                out.append(("removed", path + [k]))
            else:
                out += key_paths_diff(a[k], b[k], path + [k])
        out += [("added", path + [k]) for k in b if k not in a]
        return out
    return [] if a == b else [("changed", path)]


def _server_keys(keys: list) -> bool:
    """keys name one MCP server entry: mcpServers.<name> or projects.<folder>.mcpServers.<name>."""
    return ((len(keys) == 2 and keys[0] == "mcpServers")
            or (len(keys) == 4 and keys[0] == "projects" and keys[2] == "mcpServers"))


def check_change(c: dict) -> str | None:
    """Why a change is not one that walle makes (a string key), else None."""
    if c.get("kind") != "settings_key" or c.get("file") not in (*SET_KEYS, *SERVER_FILES):
        return "apply.not_allowed"
    keys = [str(k) for k in c.get("keys") or []]
    if c.get("op") == "set":
        ok = c["file"] in SET_KEYS and len(keys) == 2 and keys[0] in SET_KEYS[c["file"]]
        ok = ok and (isinstance(c.get("value"), bool) or c.get("value") == trim.SKILL_OVERRIDE)
    elif c.get("op") == "remove":
        ok = c["file"] in SERVER_FILES and _server_keys(keys)
    elif c.get("op") == "move":
        to = [str(k) for k in c.get("to") or []]
        ok = c["file"] in SERVER_FILES and _server_keys(keys) and _server_keys(to) and keys[-1] == to[-1]
    else:
        ok = False
    return None if ok else "apply.key_not_allowed"


def change_text(c: dict, before) -> str:
    """One line for a change. Values show only for the switches walle sets (true, false, a skill mode)."""
    keys = [str(k) for k in c["keys"]]
    where = f"{c['file']} {key_text(keys)}"
    if c["op"] == "set":
        old = "(none)" if before is MISSING else json.dumps(before, ensure_ascii=False)
        if not (before is MISSING or isinstance(before, bool) or before in (None, trim.SKILL_OVERRIDE)
                or (isinstance(before, str) and len(before) <= 40)):
            old = "(value not shown)"
        return f"{where}: {old} -> {json.dumps(c['value'], ensure_ascii=False)}"
    if c["op"] == "remove":
        return f"remove {where} (value not shown)"
    return f"move {where} -> {key_text([str(k) for k in c['to']])} (value not shown)"


def apply_change(doc: dict, c: dict) -> tuple[bool, object]:
    """Apply one change to doc. (changed, value before) ; False when the key already has the planned state."""
    keys = [str(k) for k in c["keys"]]
    before = get_at(doc, keys)
    if c["op"] == "set":
        if before is not MISSING and before == c["value"] and type(before) is type(c["value"]):
            return False, before
        set_at(doc, keys, c["value"], c["file"])
        return True, before
    if c["op"] == "remove":
        if before is MISSING:
            return False, before
        pop_at(doc, keys)
        return True, before
    to = [str(k) for k in c["to"]]
    there = get_at(doc, to)
    if before is MISSING and there is not MISSING:
        return False, before
    if before is MISSING:
        raise ApplyError("apply.missing", {"keys": key_text(keys), "file": c["file"]})
    if there is not MISSING:
        raise ApplyError("apply.conflict", {"keys": key_text(to), "file": c["file"]})
    set_at(doc, to, pop_at(doc, keys), c["file"])
    return True, before


# ---------------------------------------------------------------------- apply
def _ids(only: list[str] | None) -> list[str]:
    out = []
    for chunk in only or []:
        for x in chunk.split(","):
            x = x.strip()
            if x and x not in out:
                out.append(x)
    return out


def _app_guard(files: list[str], app_closed: bool, procs) -> None:
    """Refuse a file that the app rewrites, unless --app-closed and no Claude process runs."""
    hit = [f for f in files if f in APP_FILES]
    if not hit:
        return
    if not app_closed:
        raise ApplyError("apply.need_app_closed", {"file": hit[0]})
    found = procs()
    if found is None:
        raise ApplyError("apply.ps_failed")
    if found:
        more = f", +{len(found) - 3}" if len(found) > 3 else ""
        raise ApplyError("apply.app_running", {"procs": ", ".join(found[:3]) + more})


def new_apply_id(out: Path) -> tuple[str, Path]:
    """A run id that is free both as a run folder and as a backup folder."""
    backups = out.parent / "backups"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name, n = stamp, 2
    while (out / name).exists() or (backups / name).exists():
        name, n = f"{stamp}-{n}", n + 1
    return name, backups / name


def apply(out: Path, plan_dir: Path, ids: list[str], *, app_closed: bool = False, dry_run: bool = False,
          home: Path = HOME, procs=None) -> dict:
    """Apply the rows ids of the plan in plan_dir. Returns the record; raises ApplyError on a refusal."""
    procs = procs or (lambda: claude_processes(home))
    plan = json.loads((plan_dir / "plan.json").read_text())
    plan_id = (plan.get("run") or {}).get("id") or plan_dir.name
    by_id = {r["id"]: r for r in plan.get("rows") or []}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise ApplyError("apply.unknown_id", {"ids": ", ".join(unknown), "plan": plan_id})
    rows = [by_id[i] for i in ids]
    mine = [r for r in rows if r["applies"] == "walle"]
    manual = [r for r in rows if r["applies"] == "manual"]
    patch = [r for r in rows if r["applies"] == "patch"]

    # Every check runs before the first write. One refusal stops all rows.
    files: dict[str, dict] = {}
    roots = scanned_roots(out, plan)
    for r in mine:
        c = r["change"]
        why = check_change(c)
        if why:
            raise ApplyError(why, {"file": c.get("file"), "keys": key_text([str(k) for k in c.get("keys") or []])})
        f = files.setdefault(c["file"], {"rows": [], "path": trim.target_path(c["file"], home)})
        f["rows"].append(r)
        if "sha256" not in c:
            raise ApplyError("apply.no_hash", {"plan": plan_id, "file": c["file"]})
        f["planned"] = c["sha256"]
    for name, f in files.items():
        path = f["path"]
        real = path.resolve()
        root = in_scan(real, roots, home)
        if root:
            raise ApplyError("apply.in_scan", {"path": _short(real, home), "root": _short(root, home)})
        f["real"] = real
        try:
            raw = real.read_bytes()
        except OSError:
            raw = None
        now = hashlib.sha256(raw).hexdigest() if raw is not None else None
        if now is None or now != f["planned"]:
            raise ApplyError("apply.hash_differs", {"file": name, "plan": plan_id,
                                                    "a": (f["planned"] or "none")[:8], "b": (now or "none")[:8]})
        try:
            doc = json.loads(raw.decode())
        except (UnicodeDecodeError, ValueError):
            doc = None
        if not isinstance(doc, dict):
            raise ApplyError("apply.bad_json", {"file": name})
        f.update(raw=raw, sha_before=now, mode=real.stat().st_mode, doc=doc, results=[])
        for r in f["rows"]:
            changed, before = apply_change(doc, r["change"])
            f["results"].append({"id": r["id"], "changed": changed, "what": change_text(r["change"], before),
                                 "keys": [str(k) for k in r["change"]["keys"]],
                                 "to": [str(k) for k in r["change"].get("to") or []] or None})
        f["new"] = dumps_like(doc, raw.decode()).encode() if any(x["changed"] for x in f["results"]) else None
    writes = [n for n, f in files.items() if f["new"] is not None]
    record = {"plan": {"id": plan_id, "path": str(plan_dir / "plan.json")}, "files": files, "writes": writes,
              "manual": manual, "patch": patch, "dry_run": dry_run}
    if dry_run or not writes:
        return record
    _app_guard(writes, app_closed, procs)
    own = (home / ".walle").resolve()
    for d in (out, out.parent / "backups"):  # the record and the backup stay out of scanned folders too
        root = next((r for r in roots if _inside(d, r) and not (_inside(own, r) and _inside(d, own))), None)
        if root:
            raise ApplyError("apply.in_scan", {"path": _short(d, home), "root": _short(root, home)})

    apply_id, backup = new_apply_id(out)
    private_dir(backup)
    private_dir(backup / "before")
    private_dir(backup / "after")
    entries = []
    for n, name in enumerate(writes, 1):
        f = files[name]
        stem = f"{n}-{f['real'].name.lstrip('.')}"
        copy_private(f["real"], backup / "before" / stem)
        entries.append({"file": name, "path": str(f["path"]), "real": str(f["real"]), "before": f"before/{stem}",
                        "after": f"after/{stem}", "sha256_before": f["sha_before"],
                        "sha256_after": hashlib.sha256(f["new"]).hexdigest(), "mode": f["mode"] & 0o7777,
                        "rows": [x["id"] for x in f["results"] if x["changed"]]})
    undo = {"version": 1, "id": apply_id, "created": datetime.now().isoformat(timespec="seconds"),
            "walle_version": __version__, "plan": record["plan"],
            "rows": [{"id": x["id"], "file": name, "changed": x["changed"], "what": x["what"], "keys": x["keys"],
                      "to": x["to"]} for name, f in files.items() for x in f["results"]],
            "files": entries, "status": "writing", "lost": [], "undone": None}
    _save_undo(backup, undo)
    done: list[dict] = []
    try:
        for e in entries:
            write_atomic(Path(e["real"]), files[e["file"]]["new"], e["mode"])
            done.append(e)
    except OSError as err:
        for e in done:
            write_atomic(Path(e["real"]), (backup / e["before"]).read_bytes(), e["mode"])
        undo["status"] = "failed"
        _save_undo(backup, undo)
        raise ApplyError("apply.write_failed", {"file": entries[len(done)]["file"], "error": err.strerror or err})
    for e in entries:
        copy_private(Path(e["real"]), backup / e["after"])
    undo["status"] = "applied"
    _save_undo(backup, undo)
    lost = _recheck(entries, "sha256_after")
    if lost:
        undo["status"] = "lost"
        undo["lost"] = lost
        _save_undo(backup, undo)
    record.update(id=apply_id, backup=str(backup), undo=undo, lost=lost)
    return record


def _recheck(entries: list[dict], key: str) -> list[str]:
    """Files whose hash is not entries[key] now, and again after RECHECK_SECONDS for files the app rewrites."""
    lost = [e["file"] for e in entries if sha256(Path(e["real"])) != e[key]]
    if any(e["file"] in APP_FILES for e in entries) and RECHECK_SECONDS:
        time.sleep(RECHECK_SECONDS)
        lost += [e["file"] for e in entries if e["file"] not in lost and sha256(Path(e["real"])) != e[key]]
    return lost


def _save_undo(backup: Path, undo: dict) -> None:
    tmp = backup / (UNDO + ".tmp")
    tmp.write_text(json.dumps(undo, ensure_ascii=False, indent=1) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(backup / UNDO)


# ---------------------------------------------------------------------- undo
def load_undo(out: Path, apply_id: str) -> tuple[Path, dict]:
    backup = out.parent / "backups" / apply_id
    try:
        rec = json.loads((backup / UNDO).read_text())
    except (OSError, ValueError):
        raise ApplyError("undo.no_backup", {"id": apply_id, "path": str(out.parent / "backups")}) from None
    ok = isinstance(rec, dict) and rec.get("version") == 1 and isinstance(rec.get("files"), list)
    ok = ok and all(isinstance(e, dict) and {"file", "real", "before", "after", "sha256_before", "sha256_after"} <= set(e)
                    for e in rec["files"])
    if not ok:  # a backup that walle apply did not write, for example one made by hand
        raise ApplyError("undo.not_walle", {"id": apply_id, "path": str(backup)})
    return backup, rec


def undo(out: Path, apply_id: str, *, app_closed: bool = False, home: Path = HOME, procs=None) -> dict:
    """Restore the files of an apply. Raises ApplyError, with the key paths that changed, when a file differs."""
    procs = procs or (lambda: claude_processes(home))
    backup, rec = load_undo(out, apply_id)
    if rec.get("undone"):
        raise ApplyError("undo.already", {"id": apply_id, "at": rec["undone"].get("at")})
    changed = []
    for e in rec["files"]:
        if sha256(Path(e["real"])) != e["sha256_after"]:
            changed.append(e)
    if changed:
        e = changed[0]
        raise ApplyError("undo.changed_since", {"file": e["file"], "id": apply_id}, _undo_diff(backup, rec, e, home))
    _app_guard([e["file"] for e in rec["files"]], app_closed, procs)
    for e in rec["files"]:
        write_atomic(Path(e["real"]), (backup / e["before"]).read_bytes(), e.get("mode", 0o600))
    lost = _recheck(rec["files"], "sha256_before")
    undo_id = runs.new_run_dir(out).name
    rec["undone"] = {"at": datetime.now().isoformat(timespec="seconds"), "id": undo_id, "lost": lost}
    _save_undo(backup, rec)
    return {"id": undo_id, "apply": apply_id, "files": rec["files"], "lost": lost, "backup": str(backup)}


def _undo_diff(backup: Path, rec: dict, e: dict, home: Path) -> list[tuple[str, dict]]:
    """Lines that name the key paths of e's file that changed since the apply, without values.

    A key that this apply changed and that changed again comes first: undo would also put it back.
    """
    lines: list[tuple[str, dict]] = []
    try:
        after = json.loads((backup / e["after"]).read_text())
        now = json.loads(Path(e["real"]).read_text())
    except (OSError, ValueError):
        after = now = None
    if after is not None:
        for r in rec.get("rows") or []:
            if r.get("file") != e["file"] or not r.get("changed"):
                continue
            for keys in (r.get("keys"), r.get("to")):
                if keys and get_at(after, keys) != get_at(now, keys):
                    lines.append(("undo.touched", {"keys": key_text(keys)}))
        diff = key_paths_diff(after, now)
        for kind, keys in diff[:DIFF_LINES]:
            lines.append(("undo.diff_line", {"kind": ("diff.kind." + kind,), "keys": key_text(keys) if keys else "(file)"}))
        if len(diff) > DIFF_LINES:
            lines.append(("undo.diff_more", {"n": len(diff) - DIFF_LINES}))
    lines.append(("undo.by_hand", {"path": _short(backup / e["before"], home)}))
    return lines


def render_line(key: str, fields: dict, lang: str) -> str:
    """t() for a line whose field values may be string keys themselves: a 1-tuple (key,) is translated."""
    return t(key, lang, **{k: t(v[0], lang) if isinstance(v, tuple) else v for k, v in fields.items()})


# ---------------------------------------------------------------------- CLI
USAGE_ERRORS = ("apply.need_only", "apply.unknown_id", "undo.no_backup", "undo.not_walle")


def add_parser(sub) -> None:
    a = sub.add_parser("apply", help="change the settings keys of the plan rows you pick, after a backup "
                                     "(manual rows: a checklist)")
    a.add_argument("run", help="the trim run: its id, its folder or its plan.json")
    a.add_argument("--only", action="append", metavar="ID[,ID]",
                   help="the row ids to apply (required; there is no default choice)")
    a.add_argument("--app-closed", action="store_true",
                   help="every Claude app and session is closed: walle may write ~/.claude.json "
                        "(it checks that no Claude process runs)")
    a.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
    a.add_argument("--lang", choices=("en", "ko"))
    u = sub.add_parser("undo", help="put back the files of a walle apply, when nothing changed them since")
    u.add_argument("apply_id", help="the id that walle apply printed (also in walle runs)")
    u.add_argument("--app-closed", action="store_true", help="as for walle apply: needed for ~/.claude.json")
    u.add_argument("--lang", choices=("en", "ko"))


def _say(lines: list[str], text: str, stream=None) -> None:
    lines.append(text)
    print(text, file=stream or sys.stdout)


def _refusal(e: ApplyError, lang: str) -> int:
    print(render_line(e.key, e.fields, lang), file=sys.stderr)
    for key, fields in e.lines:
        print(render_line(key, fields, lang), file=sys.stderr)
    return 2 if e.key in USAGE_ERRORS else 1


def run_cli(args, home: Path = HOME) -> int:
    lang = resolve_lang(args.lang)
    if args.cmd == "undo":
        return _undo_cli(args, home, lang)
    ids = _ids(args.only)
    if not ids:
        print(t("apply.need_only", lang), file=sys.stderr)
        return 2
    try:
        plan_dir = trim._plan_dir(args.out, args.run)
    except trim.TrimError as e:
        print(f"walle apply: {e}", file=sys.stderr)
        return 2
    try:
        rec = apply(args.out, plan_dir, ids, app_closed=args.app_closed, dry_run=args.dry_run, home=home)
    except ApplyError as e:
        return _refusal(e, lang)
    lines: list[str] = []
    plan_id = rec["plan"]["id"]
    done = bool(rec.get("id"))
    if args.dry_run:
        _say(lines, t("apply.head_dry", lang, plan=plan_id))
    else:  # no id when nothing was written: only manual or patch rows, or every key had its planned value
        _say(lines, t("apply.head", lang, id=rec["id"] if done else "-", plan=plan_id))
    for name, f in rec["files"].items():
        for x in f["results"]:
            key = "apply.already" if not x["changed"] else ("apply.changed" if done else "apply.would")
            _say(lines, t(key, lang, id=x["id"], what=x["what"]))
    if rec["files"] and not rec["writes"]:
        _say(lines, t("apply.nothing", lang))
    if args.dry_run:
        for f in rec["writes"]:
            if f in APP_FILES:
                _say(lines, t("apply.need_app_closed", lang, file=f))
    if rec["manual"]:
        _say(lines, t("apply.manual_head", lang))
        for r in rec["manual"]:
            _say(lines, f"[ ] {r['id']}  {r['title']}")
            _say(lines, f"      {r['where']}")
            if trim.UNCONFIRMED in r["where"]:
                _say(lines, "      " + t("apply.unconfirmed", lang))
            _say(lines, "      " + t("apply.manual_undo", lang, undo=r["undo"]))
    if rec["patch"]:
        _say(lines, t("apply.patch_head", lang))
        for r in rec["patch"]:
            c = r["change"]
            _say(lines, f"  {r['id']}  {c.get('apply') or c.get('how') or r['where']}")
    code = 0
    if done:
        _say(lines, t("apply.backup", lang, path=_short(rec["backup"], home), id=rec["id"]))
        _say(lines, t("apply.check", lang))
        for f in rec["lost"]:
            msg = t("apply.lost", lang, file=f, id=rec["id"])
            lines.append(msg)
            print(msg, file=sys.stderr)
            code = 1
        run_dir = args.out / rec["id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        undo_rec = rec["undo"]
        data = {"schema": 1, "generated": undo_rec["created"],
                "run": {"id": rec["id"], "kind": "apply", "parent_id": plan_id, "walle_version": __version__},
                "plan": rec["plan"], "status": undo_rec["status"], "backup": rec["backup"],
                "rows": undo_rec["rows"], "manual": [r["id"] for r in rec["manual"]],
                "patch": [r["id"] for r in rec["patch"]],
                "files": [{k: e[k] for k in ("file", "path", "sha256_before", "sha256_after", "rows")}
                          for e in undo_rec["files"]]}
        (run_dir / "apply.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
        (run_dir / "apply.md").write_text("# walle apply\n\n```text\n" + "\n".join(lines) + "\n```\n")
        runs.append_row(args.out, {"id": rec["id"], "kind": "apply", "label": None, "parent_id": plan_id,
                                   "generated": undo_rec["created"], "roots": [], "walle_version": __version__,
                                   "rows": [x["id"] for x in undo_rec["rows"] if x["changed"]],
                                   "files": [e["file"] for e in undo_rec["files"]], "status": undo_rec["status"],
                                   "backup": rec["backup"], "path": str(run_dir / "apply.json")})
        print(run_dir / "apply.md")
    else:
        print(plan_dir / "plan.md")
    return code


def _undo_cli(args, home: Path, lang: str) -> int:
    try:
        res = undo(args.out, args.apply_id, app_closed=args.app_closed, home=home)
    except ApplyError as e:
        return _refusal(e, lang)
    lines = [t("undo.head", lang, id=args.apply_id)]
    print(lines[0])
    for e in res["files"]:
        _say(lines, t("undo.restored", lang, file=e["file"]))
    code = 0
    for f in res["lost"]:
        msg = t("undo.lost", lang, file=f)
        lines.append(msg)
        print(msg, file=sys.stderr)
        code = 1
    run_dir = args.out / res["id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "undo.md").write_text("# walle undo\n\n```text\n" + "\n".join(lines) + "\n```\n")
    runs.append_row(args.out, {"id": res["id"], "kind": "undo", "label": None, "parent_id": args.apply_id,
                               "generated": datetime.now().isoformat(timespec="seconds"), "roots": [],
                               "walle_version": __version__, "files": [e["file"] for e in res["files"]],
                               "status": "lost" if res["lost"] else "restored",
                               "path": str(Path(res["backup"]) / UNDO)})
    print(Path(res["backup"]) / UNDO)
    return code
