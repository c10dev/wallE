"""View data of `walle app`: the folders, the newest scan, judge answers, measure rows, the trim plan, context and
the run history, read from the run index and the run files.

Nothing here runs a command or writes a file. Every function takes the runs folder (out) and home, so a test can
give a fake home. A run file is read from out/<id>/ first (a copy of a runs folder keeps working), else from the
folder of the `path` that the index line records. Text that the CLI also prints (reasons, verdict sentences,
measure cells) comes from the same functions, so the page and the terminal say the same thing.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from pathlib import Path

from . import brief, eve, keep, runs, strings
from .strings import round_tokens, t

RUN_ID = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")
VERDICT_RANK = {"OVER BUDGET": 2, "TRIM": 1, "LEAN": 0}
ITEM_LIMIT = 200
TEXT_LIMIT = 600
# The parsed run files that the views read again and again (scan.json is often over 1 MB). The cache holds at most
# CACHE_FILES files and CACHE_BYTES bytes of file text, the least recently used goes first, and the entry of a file
# that is gone or changed is dropped. _valid keeps only one bool per scan.json: is it a scan that the views read.
CACHE_FILES = 8
CACHE_BYTES = 16 * 1024 * 1024
_cache: "OrderedDict[str, tuple[tuple[int, int], object]]" = OrderedDict()
_valid: dict[str, tuple[tuple[int, int], bool]] = {}


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _prune() -> None:
    """Drop the entries of files that are gone, then the least recently used until the cache is in its limits."""
    for key in [k for k in _cache if _stamp(Path(k)) is None]:
        del _cache[key]
    for key in [k for k in _valid if _stamp(Path(k)) is None]:
        del _valid[key]
    while _cache and (len(_cache) > CACHE_FILES or sum(v[0][1] for v in _cache.values()) > CACHE_BYTES):
        _cache.popitem(last=False)


def load_json(path: Path | None):
    """The JSON object of a file, or None. Kept in a small cache until the file changes (size or mtime)."""
    if path is None:
        return None
    key, stamp = str(path), _stamp(path)
    if stamp is None:
        _cache.pop(key, None)
        return None
    hit = _cache.get(key)
    if hit and hit[0] == stamp:
        _cache.move_to_end(key)
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        _cache.pop(key, None)
        return None
    _cache[key] = (stamp, data)
    _prune()
    return data


def readable_scan(out: Path, row: dict) -> Path | None:
    """scan.json of a scan row when the views can read it (schema 2: items and triage), else None. A missing
    file and a scan.json without triage (an old schema) are not readable."""
    path = run_file(out, row, "scan.json")
    stamp = _stamp(path) if path else None
    if stamp is None:
        return None
    hit = _valid.get(str(path))
    if not hit or hit[0] != stamp:
        data = load_json(path)
        hit = _valid[str(path)] = (stamp, isinstance(data, dict) and "triage" in data and "items" in data)
    return path if hit[1] else None


def run_file(out: Path, row: dict, name: str) -> Path | None:
    """The file `name` of a run: out/<id>/<name>, else next to the path of the index line."""
    rid = row.get("id")
    if isinstance(rid, str) and RUN_ID.match(rid):
        p = out / rid / name
        if p.is_file():
            return p
    path = row.get("path")
    if isinstance(path, str) and path:
        p = Path(path).with_name(name)
        if p.is_file():
            return p
    return None


def when(stamp: str | None) -> str:
    """2026-10-21T04:17:02 as 2026-10-21 04:17."""
    return str(stamp or "").replace("T", " ")[:16]


def tokens_text(n, budget: int | None = None) -> str:
    return "~" + round_tokens(int(n), budget) if isinstance(n, (int, float)) and n > 0 else "0"


def _rows(out: Path) -> list[dict]:
    return [r for r in runs.read_index(out) if isinstance(r.get("id"), str) and isinstance(r.get("kind"), str)]


def _scans(out: Path) -> list[dict]:
    return [r for r in _rows(out) if r["kind"] == "scan" and isinstance(r.get("per_folder"), dict)]


def _root_of(folder: str, roots: list) -> str:
    inside = [r for r in roots if isinstance(r, str) and (folder == r or folder.startswith(r.rstrip("/") + "/"))]
    return max(inside, key=len) if inside else folder


# ---------------------------------------------------------------------- folders
def folders(out: Path, home: Path, launch: Path | None = None) -> list[dict]:
    """One row per folder of the scans in the index, from the newest scan that holds it.

    A row has the stacks (agent: verdict, tokens) of that scan and the verdict and tokens of its worst stack.
    The folder the app started in comes first when no scan holds it (has_scan false).
    """
    by: dict[str, dict] = {}
    unreadable: set[str] = set()
    for r in _scans(out):  # oldest first: a newer scan replaces the row
        if readable_scan(out, r) is None:
            # A scan that the views cannot read gives no verdict: its folders show as having no scan.
            unreadable.update(k.rpartition("@")[0] for k in r["per_folder"] if isinstance(k, str) and "@" in k)
            continue
        verdicts = r.get("verdicts") if isinstance(r.get("verdicts"), dict) else {}
        for key, tokens in r["per_folder"].items():
            if not isinstance(key, str) or "@" not in key:
                continue
            folder, _, agent = key.rpartition("@")
            row = by.get(folder)
            if not row or row["scan_id"] != r["id"]:
                root = _root_of(folder, r.get("roots") or [])
                row = by[folder] = {"path": folder, "display": brief.shown(folder, None, home),
                                    "short": brief.shown(folder, None, home), "root": root,
                                    "scan_id": r["id"], "generated": when(r.get("generated")),
                                    "has_scan": True, "stacks": {}}
            row["stacks"][agent] = {"verdict": verdicts.get(key), "tokens": tokens if isinstance(tokens, int) else 0}
    for row in by.values():
        worst = max(row["stacks"].items(), key=lambda kv: (VERDICT_RANK.get(kv[1]["verdict"], -1), kv[1]["tokens"]))
        row["agent"], row["verdict"], row["tokens"] = worst[0], worst[1]["verdict"], worst[1]["tokens"]
        row["tokens_text"] = tokens_text(row["tokens"])
    for folder in sorted(unreadable - set(by)):
        by[folder] = {"path": folder, "display": brief.shown(folder, None, home), "short": brief.shown(folder, None, home),
                      "root": folder, "scan_id": None, "has_scan": False, "stacks": {}, "verdict": None,
                      "tokens": None, "tokens_text": ""}
    rows = sorted(by.values(), key=lambda r: r["path"])
    # A short name is relative to the outermost listed folder above it: ~/projects, then atc/controller.
    for row in rows:
        above = [p for p in by if row["path"].startswith(p.rstrip("/") + "/")]
        if above:
            row["short"] = brief.shown(row["path"], min(above, key=len), home)
    if launch is not None:
        lp = str(launch)
        if lp in by:
            by[lp]["launch"] = True
        else:
            rows.insert(0, {"path": lp, "display": brief.shown(lp, None, home), "short": brief.shown(lp, None, home),
                            "root": lp, "scan_id": None, "has_scan": False, "stacks": {}, "verdict": None,
                            "tokens": None, "tokens_text": "", "launch": True})
    return rows


def known_folder(out: Path, home: Path, folder: str, launch: Path | None = None) -> bool:
    return any(r["path"] == folder for r in folders(out, home, launch))


# ---------------------------------------------------------------------- scan
def _item(it: dict, folder: str, home: Path) -> dict:
    loc = (it.get("locations") or [{}])[0]
    text = str(loc.get("text") or "")
    return {
        "id": it.get("id"), "verb": it.get("verb"), "verb_text": t(f"verb.{it['verb']}", "en") if it.get("verb") else "-",
        "label": strings.label(it, "en", lambda p: brief.shown(p, folder, home)), "rule": it.get("rule"),
        "certainty": it.get("certainty"), "certainty_text": brief.certainty(it, "en"),
        "reason": brief.reason(it, "en", folder, home), "location": brief.location(it, folder, home),
        "file": brief.shown(loc.get("file"), None, home), "line": loc.get("line"),
        "text": text[:TEXT_LIMIT] + ("…" if len(text) > TEXT_LIMIT else ""),
        "tokens_est": it.get("tokens_est"), "state": it.get("state"),
        "command": f"walle show {it.get('id')}",
    }


def _stack(e: dict, data: dict, folder: str, home: Path) -> dict:
    items = {it["id"]: it for it in data.get("items", [])}
    start = [i for i in e.get("start_here") or [] if i in items]
    sentences = brief._verdict_sentences(e, "en", folder, home, data)
    lead = sentences[1:]  # the first sentence gives the tokens and the budget: the head of the card shows them
    if e["verdict"] == brief.LEAN and not start:
        sentences = [t("empty.lean", "en", tokens=round_tokens(e["tokens_est"], e["budget"]),
                       budget=f"{e['budget']:,}").partition(" · ")[2]] + brief.all_kept(e, "en")
        lead = sentences
    groups = []
    for g, members in brief.collect_groups([e], start).items():
        ids = [m for m in members if isinstance(m, str)] if g != "worktree_copies" else []
        if g in ("shared", "copies"):
            ids = [i for r in members for i in r["items"]]
        n = brief.group_count(g, members)
        if not n:
            continue
        groups.append({"group": g, "name": t(f"group.{g}", "en"), "n": n,
                       "note": t(f"group.{g}_note", "en") if g in ("on_demand", "other_agents") else None,
                       "items": [_item(items[i], folder, home) for i in ids[:ITEM_LIMIT] if i in items]})
    return {
        "key": f"{e['folder']}@{e['agent']}", "agent": e["agent"], "verdict": e["verdict"],
        "verdict_text": t(f"verdict.{e['verdict']}", "en"), "tokens_est": e["tokens_est"],
        "tokens_text": tokens_text(e["tokens_est"], e["budget"]), "budget": e["budget"],
        "sentences": sentences, "lead": lead, "split": brief.split_line(e, "en"), "to_read": e.get("to_read", 0),
        "shared": [brief.shared_text(r, "en", home) for r in brief.shared_rows(e)],
        "start_here": [_item(items[i], folder, home) for i in start], "groups": groups,
    }


def _newest_scan_row(out: Path, folder: str) -> dict | None:
    """The newest scan row that holds the folder and whose scan.json the views can read (as folders() picks it)."""
    for r in reversed(_scans(out)):
        if any(isinstance(k, str) and k.rpartition("@")[0] == folder for k in r["per_folder"]) \
                and readable_scan(out, r):
            return r
    return None


def scan_view(out: Path, home: Path, folder: str) -> tuple[dict | None, dict | None, Path | None]:
    """(view, scan.json data, path) of the newest scan that holds the folder, else (None, None, None)."""
    row = _newest_scan_row(out, folder)
    path = readable_scan(out, row) if row else None
    data = load_json(path)
    if not isinstance(data, dict) or "triage" not in data:
        return None, None, None
    stacks = [e for e in data["triage"].get("stacks", {}).values() if e.get("folder") == folder]
    stacks.sort(key=lambda e: (e.get("agent") != "claude", e.get("agent")))
    run = data.get("run") or {}
    view = {
        "id": row["id"], "label": row.get("label"), "generated": when(data.get("generated")),
        "estimator": run.get("estimator") or runs.UNRECORDED_ESTIMATOR, "budget": data["triage"].get("budget"),
        "roots": [brief.shown(r, None, home) for r in run.get("roots") or data.get("roots") or []],
        "path": brief.shown(str(path), None, home), "files": data.get("artifact_count"),
        "stacks": [_stack(e, data, folder, home) for e in stacks],
    }
    return view, data, path


# ---------------------------------------------------------------------- judge
def _ref(ref: str, text) -> dict:
    file, _, line = str(ref).rpartition(":")
    if not file or not line.isdigit():
        file, line = str(ref), "0"
    return {"file": file, "line": int(line), "text": text if isinstance(text, str) else None}


def judge_view(scan_path: Path | None, home: Path, folder: str) -> dict | None:
    """The judge answers next to a scan.json, as JudgeAnswer props: items whose refs are in the folder,
    real problems first, then not judged, then not a problem."""
    if scan_path is None:
        return None
    path = scan_path.with_name("judge.json")
    data = load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    pre = folder.rstrip("/") + "/"
    cards = []
    for it in data["items"]:
        if not isinstance(it, dict):
            continue
        refs = it.get("refs") or []
        texts = it.get("texts") or []
        if not any(str(r).startswith(pre) for r in refs):
            continue
        v = it.get("verdict") if isinstance(it.get("verdict"), dict) else None
        cards.append({
            "id": it.get("id"), "type": it.get("type") if it.get("type") in ("conflict", "cost") else "conflict",
            "pair": it.get("pair"), "judge": it.get("judge"), "cached": bool(it.get("from_cache")),
            "refs": [{**_ref(r, texts[i] if i < len(texts) else None),
                      "file": brief.shown(_ref(r, None)["file"], None, home)} for i, r in enumerate(refs)],
            "verdict": v,
        })
    order = {True: 0, None: 1, False: 2}
    cards.sort(key=lambda c: order[(c["verdict"] or {}).get("real") if c["verdict"] else None])
    return {"path": brief.shown(str(path), None, home), "generated": when(data.get("generated")),
            "items": cards, "real": sum(1 for c in cards if c["verdict"] and c["verdict"].get("real")),
            "not_judged": sum(1 for c in cards if not c["verdict"]), "errors": len(data.get("errors") or [])}


# ---------------------------------------------------------------------- measure
def measure_view(out: Path, home: Path, folder: str) -> dict | None:
    """The newest `walle eve` run of the folder: one row per model family, median [p25-p75] and n."""
    for r in reversed(_rows(out)):
        if r["kind"] != "measure" or folder not in (r.get("roots") or []):
            continue
        data = load_json(run_file(out, r, "eve.json"))
        m = data.get("measure") if isinstance(data, dict) else None
        if not isinstance(m, dict):
            continue
        sides, metrics = m.get("sides") or ["period"], m.get("metrics") or []
        rows = []
        for row in m.get("rows") or []:
            out_row = {"family": row.get("family"), "sides": {}, "change": {}}
            for s in sides:
                cell = row.get(s) or {}
                n = cell.get("sessions", 0)
                out_row["sides"][s] = {"sessions": n, "cells": {k: {**(cell.get(k) or {}),
                                                                     "text": eve.cell(cell[k], k, n, "en")}
                                                                 for k in metrics if isinstance(cell.get(k), dict)}}
            for k, c in (row.get("change") or {}).items():
                if isinstance(c, dict):
                    text = eve.change_cell(c, "en") if c.get("state") != "too_few" else \
                        f"too few sessions to compare (need {c.get('need', 0)} more)"
                    out_row["change"][k] = {**c, "text": text}
            rows.append(out_row)
        return {"id": r["id"], "label": r.get("label"), "generated": when(data.get("generated")),
                "agent": m.get("agent"), "sides": sides, "split": m.get("split"), "period": m.get("period"),
                "rules": m.get("rules"), "metrics": metrics, "rows": rows, "left_out": m.get("left_out"),
                "around": (data.get("run") or {}).get("around"), "sessions": data.get("sessions")}
    return None


# ---------------------------------------------------------------------- trim
def _apply_command(run_id: str, row: dict) -> str:
    cmd = f"walle apply {run_id} --only {row.get('id')}"
    change = row.get("change") or {}
    if row.get("applies") == "walle" and str(change.get("file", "")).endswith(".claude.json"):
        cmd += " --app-closed"
    return cmd


def trim_view(out: Path, home: Path, folder: str) -> dict | None:
    """The newest trim plan, rows read-only, each with the `walle apply` command that would apply it."""
    for r in reversed(_rows(out)):
        if r["kind"] != "trim":
            continue
        data = load_json(run_file(out, r, "plan.json"))
        if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
            continue
        rows = []
        for row in data["rows"]:
            if not isinstance(row, dict):
                continue
            keys = (row.get("change") or {}).get("keys") or []
            used = row.get("used_in") or []
            change = row.get("change") or {}
            saving = row.get("saving") or {}
            already = bool(change.get("already"))
            rows.append({
                "id": row.get("id"), "title": row.get("title"), "group": row.get("group"), "action": row.get("action"),
                "source": row.get("source"), "applies": row.get("applies"), "where": row.get("where"),
                "already": already, "set_at": (change.get("set_at") or {}).get("time"),
                "set_status": [{"entrypoint": x.get("entrypoint"), "state": x.get("state"), "text": x.get("text")}
                               for x in row.get("set_status") or [] if isinstance(x, dict)],
                "counted": saving.get("counted") is not False, "not_counted": saving.get("not_counted"),
                "past": bool(row.get("past")),
                "saving": saving, "sessions": row.get("sessions"), "risk": row.get("risk"),
                "undo": row.get("undo"), "check": row.get("check"),
                "verdict": (row.get("judge") or {}).get("verdict"), "judge": (row.get("judge") or {}).get("judge"),
                "here": folder in used or folder in keys,
                "used_in": [brief.shown(u, None, home) for u in used if isinstance(u, str)],
                # A key that already has the planned state needs no apply.
                "command": None if already else _apply_command(r["id"], row),
            })
        # Rows to apply first, by tokens loaded and not used; rows already set last.
        rows.sort(key=lambda x: (x["already"], -((x["saving"] or {}).get("unused_tokens_min") or 0)))
        return {"id": r["id"], "label": r.get("label"), "generated": when(data.get("generated")),
                "period": data.get("period"), "rows": rows, "kept": data.get("kept") or [],
                "report_only": data.get("report_only") or [], "notes": data.get("notes") or [],
                "path": brief.shown(str(run_file(out, r, "plan.json")), None, home)}
    return None


# ---------------------------------------------------------------------- context
def context_view(out: Path, home: Path, folder: str) -> dict:
    """context.json of the newest first-call run (one of this folder when there is one) and of the newest usage
    run, as walle wrote them: ContextPanel reads them as they are."""
    first = usage = None
    rows = [r for r in reversed(_rows(out)) if r["kind"] == "context"]
    for want_here in (True, False):
        for r in rows:
            if r.get("usage") or (want_here and r.get("cwd") != folder):
                continue
            data = load_json(run_file(out, r, "context.json"))
            if isinstance(data, dict) and data.get("first_call"):
                first = {"id": r["id"], "generated": when(r.get("generated")), "here": r.get("cwd") == folder,
                         "cwd": brief.shown(r.get("cwd"), None, home), "data": data}
                break
        if first:
            break
    for r in rows:
        if not r.get("usage"):
            continue
        data = load_json(run_file(out, r, "context.json"))
        if isinstance(data, dict) and data.get("usage"):
            usage = {"id": r["id"], "generated": when(r.get("generated")), "data": data}
            break
    return {"first_call": first, "usage": usage}


# ---------------------------------------------------------------------- runs and keep
def _what(r: dict) -> str:
    k = r["kind"]
    if k == "scan":
        return f"{tokens_text(r.get('stack_sum'))} est. tokens in all stacks · {r.get('open', 0)} open"
    if k == "trim":
        a = r.get("applies") or {}
        n = r.get("rows", 0)
        return f"{n} row{'' if n == 1 else 's'} (" + ", ".join(f"{v} {k2}" for k2, v in a.items()) + ")"
    if k in ("apply", "undo"):
        return f"{r.get('status') or k} · {len(r.get('rows') or [])} rows · " + ", ".join(map(str, r.get("files") or []))
    if k == "measure":
        n = r.get("rows", 0)
        return f"{n} row{'' if n == 1 else 's'}" + (f" around {r['around']}" if r.get("around") else "")
    if k == "context":
        return "usage" if r.get("usage") else f"first call of {r.get('session') or '-'}"
    return ""


def runs_view(out: Path, home: Path, folder: str | None = None) -> list[dict]:
    """Index lines, newest first. `here` marks the runs of the folder."""
    out_rows = []
    for r in reversed(_rows(out)):
        roots = [x for x in r.get("roots") or [] if isinstance(x, str)]
        here = bool(folder) and (any(k.rpartition("@")[0] == folder for k in (r.get("per_folder") or {}))
                                 or folder in roots or r.get("cwd") == folder)
        out_rows.append({"id": r["id"], "kind": r["kind"], "label": r.get("label"), "parent_id": r.get("parent_id"),
                         "generated": when(r.get("generated")), "roots": [brief.shown(x, None, home) for x in roots],
                         "estimator": r.get("estimator"), "what": _what(r), "here": here})
    return out_rows


def keep_view(out: Path, home: Path) -> list[dict]:
    rows = []
    for e in keep.load(keep.path_for(out))["entries"]:
        if isinstance(e, dict):
            rows.append({"id": e.get("id"), "kind": e.get("kind"), "rule": e.get("rule"), "name": e.get("name"),
                         "source": e.get("source"), "file": brief.shown(e.get("file"), None, home) if e.get("file") else None,
                         "line": e.get("line"), "why": e.get("why") or e.get("note"), "date": e.get("date")})
    return rows


def folder_view(out: Path, home: Path, folder: str) -> dict:
    scan, _data, path = scan_view(out, home, folder)
    return {
        "path": folder, "display": brief.shown(folder, None, home), "scan": scan,
        "judge": judge_view(path, home, folder), "measure": measure_view(out, home, folder),
        "trim": trim_view(out, home, folder), "context": context_view(out, home, folder),
        "runs": runs_view(out, home, folder), "keep": keep_view(out, home),
    }
