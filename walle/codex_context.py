"""walle context --agent codex: the first call of one Codex thread, and what the threads of a period loaded and used.

`walle context --agent codex` breaks the first model call of one rollout into its recorded sources
(rollout.py) and compares their estimates with the measured input tokens. The residual row holds what
no rollout records: the built-in tool schemas, message framing and estimate error.

`walle context --agent codex --usage` reads every rollout. It counts first-call tokens by app
(originator), the skills each thread listed against the skills it used, and the tools and app
connectors it called.

A thread is one session. Its later page files (rollout-<time>-<thread>_<page>.jsonl) fold into it.
The first call of a thread that resumes another thread's history is left out of first-call figures.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from . import estimate, rollout
from .collect import HOME
from .stack import codex_settings, codex_stack

CACHE_VERSION = 1
LABELS = {
    "base_instructions": "Base instructions",
    "agents_md": "AGENTS.md chain",
    "skill_listing": "Skill list",
    "app_context": "Desktop app context",
    "tool_namespaces": "Tool namespaces and dynamic tool schemas",
    "plugins_apps": "Plugin and app instructions",
    "permissions": "Permission instructions",
    "collaboration_mode": "Collaboration mode",
    "multi_agent": "Multi-agent instructions",
    "memory": "Memory",
    "environment": "Environment context",
    "hook_context": "Hook output",
    "skill_bodies": "Skill bodies the prompt invoked",
    "history": "History before the first call",
    "first_prompt": "First prompt",
    "other": "Other context items",
}


class CodexError(Exception):
    pass


# ---------- finding a rollout ----------

def find_rollout(root: Path, session: str | None) -> tuple[rollout.Rollout, int]:
    """The rollout of a thread id (or the start of one), or the newest rollout with a first call that does
    not resume another thread. Returns it with the number of compressed files skipped on the way."""
    files = rollout.rollout_files(root)
    if not files:
        raise CodexError(f"no Codex rollouts under {root}")
    if session:
        hits = [p for p in files if any(x and x.startswith(session) for x in rollout.name_parts(p))]
        if not hits:
            raise CodexError(f"no Codex session {session!r} under {root}")
        threads = {rollout.name_parts(p)[0] for p in hits}
        if len(threads) > 1:
            raise CodexError(f"{session!r} matches {len(threads)} Codex sessions; give more of the id")
        # A page id names that page; a thread id names the thread's first file.
        hits.sort(key=lambda p: (not (rollout.name_parts(p)[1] or "").startswith(session),
                                 rollout.name_parts(p)[1] is not None, p.name))
        r = rollout.load(hits[0])
        if r is None:
            raise CodexError(_unreadable(hits[0]))
        if r.first_usage is None:
            raise CodexError(f"{hits[0].name} has no model call yet")
        return r, 0
    skipped = 0
    for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
        r = rollout.load(p)
        if r is None:
            skipped += 1
            continue
        if r.first_usage is not None and not r.resumed:
            return r, skipped
    raise CodexError(f"no Codex rollout under {root} has a first call"
                     + (f"; {skipped} compressed files skipped: install zstd to read them" if skipped else ""))


def _unreadable(path: Path) -> str:
    if path.suffix == ".zst" and not rollout.zstd_program():
        return f"{path.name} is zstd-compressed; install the zstd program to read it"
    return f"cannot read {path.name}"


# ---------- one session ----------

def session_context(fc: rollout.CodexFirstCall, estimator: str, home: Path | None = None) -> dict:
    home = home or HOME
    settings = codex_settings(home)
    cwd = Path(fc.cwd) if fc.cwd else None
    rows = []
    for s in fc.sources:
        tokens = estimate.estimate(s.text, s.kind, estimator)
        rows.append({"source": s.kind, "label": LABELS[s.kind], "bytes": s.bytes, "items": s.count,
                     "recorded": s.recorded, "tokens_est": tokens, "basis": _basis(estimator),
                     "on_disk": _on_disk(s, cwd, settings, home), "detail": _detail(s)})
    est_total = sum(r["tokens_est"] for r in rows)
    measured = fc.measured["total"]
    for r in rows:
        r["share"] = round(100 * r["tokens_est"] / measured, 1) if measured else None
    note = ("measured first-call input minus the sum of the estimates: the built-in tool schemas (rollouts do "
            "not record them), message framing and estimate error")
    if fc.resumed:
        note += (f". This rollout resumes thread {fc.resumed}: the measured input also holds that thread's history, "
                 "which this file does not record")
    return {"measured": fc.measured, "sources": rows, "estimated_total": est_total,
            "residual": {"tokens": measured - est_total,
                         "share": round(100 * (measured - est_total) / measured, 1) if measured else None,
                         "note": note},
            "skill_plugins": skill_groups(next(s for s in fc.sources if s.kind == "skill_listing"), estimator, measured)}


def _basis(estimator: str) -> str:
    if estimator == "bytes-v1":
        return "bytes-v1 (Codex fit: 0.258 per ASCII character, 0.707 per other)"
    return "bytes-v2 prose rates (fitted on Claude tokens)"


def _detail(s) -> dict:
    d = dict(s.detail)
    if s.kind == "skill_listing":
        d["skills"] = [{"name": x["name"], "bytes": x["bytes"], "path": x["path"]} for x in d.get("skills", [])]
    return d


def plugin_of(name: str, path: str, home: Path | None = None) -> str:
    """The group of a listed skill: its plugin (the name before ":"), else system or own skills."""
    if ":" in name:
        return name.split(":", 1)[0]
    if "/.codex/skills/.system/" in path:
        return "(system skills)"
    if "/.codex/skills/" in path:
        return "(own skills)"
    return "(other)"


def skill_groups(src, estimator: str, measured: int) -> list[dict]:
    """Skill list lines by plugin, largest first."""
    groups: dict[str, dict] = {}
    lines = _lines_by_name(src.text)
    for x in src.detail.get("skills", []):
        g = groups.setdefault(plugin_of(x["name"], x["path"]), {"skills": 0, "bytes": 0, "text": []})
        g["skills"] += 1
        g["bytes"] += x["bytes"]
        g["text"].append(lines.get(x["name"], ""))
    out = []
    for k, g in groups.items():
        tok = estimate.estimate("\n".join(g["text"]), "skill_listing", estimator)
        out.append({"plugin": k, "skills": g["skills"], "bytes": g["bytes"], "tokens_est": tok,
                    "share": round(100 * tok / measured, 1) if measured else None})
    return sorted(out, key=lambda g: -g["bytes"])


def _lines_by_name(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.split("\n"):
        if line.startswith("- ") and not line.startswith("- `"):
            name = line[2:].split(": ", 1)[0]
            out.setdefault(name, line)
    return out


def _on_disk(s, cwd: Path | None, settings: dict, home: Path) -> dict:
    """Which part of a source walle can find on disk (a file or a config.toml key), and so could trim."""
    cfg = "~/.codex/config.toml"
    feats = settings["features"]
    if s.kind == "base_instructions":
        return {"mark": "not on disk", "why": "built into Codex for the model"}
    if s.kind in ("app_context", "collaboration_mode"):
        return {"mark": "not on disk", "why": "the Codex app adds it" if s.kind == "app_context" else "Codex adds it for the mode"}
    if s.kind in ("first_prompt", "history", "environment", "other", "skill_bodies"):
        return {"mark": "-"}
    if s.kind == "agents_md":
        files = []
        if cwd and cwd.is_dir():
            files = [{"path": str(l.path), "bytes": l.bytes} for l in codex_stack(cwd, home).layers
                     if l.label in ("codex_global", "codex_project")]
        elif (home / ".codex" / "AGENTS.md").is_file():
            files = [{"path": str(home / ".codex" / "AGENTS.md"), "bytes": (home / ".codex" / "AGENTS.md").stat().st_size}]
        return {"mark": "on disk" if files else ("not on disk" if s.count else "-"), "files": files,
                "project_doc_max_bytes": settings["project_doc_max_bytes"],
                "why": f"project_doc_max_bytes {'from ' + cfg if settings['project_doc_max_bytes_set'] else 'unset (32 KiB)'}"}
    if s.kind == "skill_listing":
        skills = s.detail.get("skills", [])
        missing = [x["name"] for x in skills if not skill_on_disk(x["path"])]
        return {"mark": _mark(len(skills) - len(missing), len(skills)), "on_disk": len(skills) - len(missing),
                "not_on_disk": missing,
                "why": "SKILL.md files under ~/.codex/skills and the plugin caches; [plugins] in " + cfg}
    if s.kind == "tool_namespaces":
        names = s.detail.get("namespaces", [])
        mine = [n for n in names if rollout.server_of_namespace(n) in settings["mcp_servers"]]
        return {"mark": _mark(len(mine), len(names)) if names else ("not on disk" if s.count else "-"),
                "config_servers": settings["mcp_servers"],
                "why": f"MCP servers of {cfg} are on disk; app connectors (codex_apps) and the app's own tools are not"}
    if s.kind == "plugins_apps":
        return {"mark": "on disk" if s.count else "-", "why": f"[plugins] and features.apps in {cfg}",
                "plugins": settings["plugins"], "apps": feats.get("apps")}
    if s.kind == "permissions":
        return {"mark": "on disk" if s.count else "-", "approval_policy": settings["approval_policy"],
                "rules": (home / ".codex" / "rules" / "default.rules").is_file(),
                "why": f"approval_policy in {cfg} and ~/.codex/rules"}
    if s.kind == "multi_agent":
        return {"mark": "on disk" if s.count else "-", "why": f"features.multi_agent and multi_agent_v2 in {cfg}",
                "multi_agent": feats.get("multi_agent"), "multi_agent_v2": feats.get("multi_agent_v2")}
    if s.kind == "memory":
        return {"mark": "on disk" if s.count else "-", "why": f"~/.codex/memories; features.memories in {cfg}",
                "memories": feats.get("memories")}
    if s.kind == "hook_context":
        return {"mark": "on disk" if s.count else "-", "why": f"[hooks] in {cfg}", "hooks": settings["hooks"]}
    return {"mark": "-"}


def skill_on_disk(path: str) -> bool:
    """True when the SKILL.md is on disk, or the plugin cache holds the skill in another version folder
    (.../plugins/cache/<marketplace>/<plugin>/<version>/...): the plugin updated since the session."""
    if not path:
        return False
    p = Path(path).expanduser()
    if p.is_file():
        return True
    parts = p.parts
    if "cache" in parts and parts.index("cache") > 0 and parts[parts.index("cache") - 1] == "plugins":
        i = parts.index("cache")
        if len(parts) > i + 4:
            base, tail = Path(*parts[:i + 3]), Path(*parts[i + 4:])
            try:
                return any((v / tail).is_file() for v in base.iterdir() if v.is_dir())
            except OSError:
                return False
    return False


def _mark(n: int, total: int) -> str:
    if total == 0:
        return "-"
    if n == total:
        return "on disk"
    if n == 0:
        return "not on disk"
    return f"partly on disk ({n} of {total})"


def session_block(r: rollout.Rollout, fc: rollout.CodexFirstCall) -> dict:
    return {"id": fc.session, "path": fc.path, "cwd": fc.cwd, "start": fc.start, "model": fc.model,
            "originator": fc.originator, "codex_version": fc.version, "subagent": fc.subagent,
            "resumed_from": fc.resumed, "forked_from": fc.forked_from, "page": fc.page}


# ---------- loaded and used ----------

def load_rows(root: Path, cache_file: Path) -> tuple[dict[str, dict], dict]:
    """usage_row of every rollout, cached by size and mtime, and counts of the files walle could not read."""
    cache: dict = {}
    try:
        cache = json.loads(cache_file.read_text())
        if cache.get("_v") != CACHE_VERSION:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    out: dict[str, dict] = {}
    fresh: dict = {"_v": CACHE_VERSION}
    skipped = {"zstd_missing": 0, "unreadable": 0}
    for f in rollout.rollout_files(root):
        st = f.stat()
        stamp = f"{st.st_size}:{int(st.st_mtime)}"
        hit = cache.get(str(f))
        if hit and hit.get("stamp") == stamp:
            row = hit["row"]
        else:
            r = rollout.load(f)
            if r is None:
                key = "zstd_missing" if f.suffix == ".zst" and not rollout.zstd_program() else "unreadable"
                skipped[key] += 1
                continue
            row = rollout.usage_row(r)
        fresh[str(f)] = {"stamp": stamp, "row": row}
        out[str(f)] = row
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(fresh, ensure_ascii=False))
    return out, skipped


def _stats(xs: list[int]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {"n": 0}
    q = statistics.quantiles(xs, n=10, method="inclusive") if len(xs) > 1 else [xs[0]] * 9
    return {"n": len(xs), "median": statistics.median(xs), "p10": round(q[0]), "p90": round(q[8]),
            "min": xs[0], "max": xs[-1]}


def usage(rows: dict[str, dict], since: str | None = None, before: str | None = None,
          skipped: dict | None = None) -> dict:
    by_thread: dict[str, list[dict]] = defaultdict(list)
    for path, r in rows.items():
        by_thread[r["thread"]].append(dict(r, path=path))
    threads = []
    for tid, rs in by_thread.items():
        rs.sort(key=lambda r: (r["page"] is not None, r["path"]))
        head = rs[0]
        if (since and head["start"] < since) or (before and not (head["start"] and head["start"] < before)):
            continue
        threads.append((tid, head, rs))
    n = len(threads)

    first: dict[str, list[int]] = defaultdict(list)
    no_fork: dict[str, list[int]] = defaultdict(list)
    excluded = Counter()
    sources: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    skill = defaultdict(lambda: {"listed": 0, "used": 0, "calls": 0, "line_bytes": [], "group": ""})
    builtin = defaultdict(lambda: {"used": 0, "calls": 0})
    server = defaultdict(lambda: {"loaded": 0, "used": 0, "calls": 0, "tools": Counter(), "loaded_tools": []})
    listing_bytes: list[int] = []
    unlisted = Counter()
    group_used = Counter()  # threads that used a skill of the group
    for tid, head, rs in threads:
        orig = head["originator"] or "(unknown)"
        if head["first_input"] is None:
            excluded["no first call"] += 1
        elif head["resumed"]:
            excluded["resumed another thread"] += 1
        else:
            first[orig].append(head["first_input"])
            if not head["forked"]:
                no_fork[orig].append(head["first_input"])
            for k, b in head["sources"].items():
                sources[orig][k].append(b)
        # Skills: listed in the thread's first skill list; used anywhere in the thread.
        listed = next((r for r in rs if r["listed"]), None)
        names = set(listed["listed"]) if listed else set()
        if listed and listed["listing_bytes"]:
            listing_bytes.append(listed["listing_bytes"])
        for nm in names:
            s = skill[nm]
            s["listed"] += 1
            s["line_bytes"].append(listed["skill_lines"].get(nm, 0))
            s["group"] = plugin_of(nm, listed["skill_paths"].get(nm, ""))
        used: Counter = Counter()
        tools: Counter = Counter()
        srv_used: dict[str, Counter] = defaultdict(Counter)
        loaded: dict[str, int] = {}
        for r in rs:
            used.update(r["skills_used"])
            tools.update(r["tools"])
            unlisted.update(r["skill_reads_unlisted"])
            for k, ts in r["servers_used"].items():
                srv_used[k].update(ts)
            for k, v in r["servers_loaded"].items():
                loaded[k] = max(loaded.get(k, 0), v)
        for nm, c in used.items():
            s = skill[nm]
            s["used"] += 1
            s["calls"] += c
            if not s["group"]:
                s["group"] = plugin_of(nm, "")
        for g in {skill[nm]["group"] for nm in used}:
            group_used[g] += 1
        for t, c in tools.items():
            builtin[t]["used"] += 1
            builtin[t]["calls"] += c
        for k, v in loaded.items():
            server[k]["loaded"] += 1
            server[k]["loaded_tools"].append(v)
        for k, ts in srv_used.items():
            server[k]["used"] += 1
            server[k]["calls"] += sum(ts.values())
            server[k]["tools"].update(ts)

    def med(xs):
        return round(statistics.median(xs)) if xs else 0

    skill_rows = [{"skill": nm, "group": v["group"], "sessions_listed": v["listed"],
                   "share_listed": round(100 * v["listed"] / n, 1) if n else 0, "sessions_used": v["used"],
                   "calls": v["calls"], "line_bytes_median": med(v["line_bytes"]),
                   "tokens_per_session_est": round(med(v["line_bytes"]) * 0.25)}
                  for nm, v in skill.items()]
    skill_rows.sort(key=lambda x: (x["calls"] > 0, -x["sessions_listed"] * x["line_bytes_median"], x["skill"]))
    groups = defaultdict(lambda: {"skills": 0, "sessions_listed_max": 0, "line_bytes_median": 0, "calls": 0,
                                  "skills_used": 0})
    for r in skill_rows:
        g = groups[r["group"]]
        g["skills"] += 1
        g["sessions_listed_max"] = max(g["sessions_listed_max"], r["sessions_listed"])
        g["line_bytes_median"] += r["line_bytes_median"]
        g["calls"] += r["calls"]
        g["skills_used"] += 1 if r["calls"] else 0
    group_rows = sorted(({"group": k, **v, "sessions_used": group_used[k]} for k, v in groups.items()),
                        key=lambda g: (g["calls"] > 0, -g["sessions_listed_max"] * g["line_bytes_median"]))
    server_rows = [{"server": k, "sessions_loaded": v["loaded"], "sessions_used": v["used"], "calls": v["calls"],
                    "tools_called": len(v["tools"]), "loaded_tools_median": med(v["loaded_tools"]),
                    "top_called": v["tools"].most_common(5)} for k, v in server.items()]
    server_rows.sort(key=lambda x: (-x["calls"], x["server"]))
    tool_rows = sorted(({"tool": t, "sessions_used": v["used"], "calls": v["calls"]} for t, v in builtin.items()),
                       key=lambda x: (-x["calls"], x["tool"]))
    starts = sorted(h["start"] for _, h, _ in threads if h["start"])
    return {
        "since": since, "before": before, "sessions": n, "rollout_files": sum(len(rs) for _, _, rs in threads),
        "subagent_threads": sum(1 for _, h, _ in threads if h["subagent"]),
        "forked_threads": sum(1 for _, h, _ in threads if h["forked"]),
        "window": {"first": starts[0], "last": starts[-1]} if starts else {},
        "skipped_files": skipped or {"zstd_missing": 0, "unreadable": 0},
        "first_calls": {o: dict(_stats(xs), without_forked=_stats(no_fork[o]),
                                source_bytes_median={k: med(v) for k, v in sorted(sources[o].items())})
                        for o, xs in sorted(first.items(), key=lambda x: -len(x[1]))},
        "first_calls_excluded": dict(excluded),
        "skill_listing_bytes_median": med(listing_bytes),
        "skills": skill_rows, "skill_groups": group_rows, "skill_reads_unlisted": unlisted.most_common(20),
        "servers": server_rows, "tools": tool_rows,
        "notes": [
            "A session is a thread: later page files (<thread>_<page>) fold into it.",
            "First calls leave out threads that resume another thread (session_meta.history_base): their first call "
            "carries history the rollout does not hold. Forked threads (forked_from_id) carry their parent's history; "
            "without_forked leaves them out too.",
            "Skills used: a tool call that names the skill's SKILL.md, a <skill> block the prompt invoked, or "
            "$name in a user message.",
            "Servers loaded: the <tools> namespace block and session_meta.dynamic_tools; most rollouts record "
            "neither, so 'loaded' undercounts. Used: McpToolCall and DynamicToolCall items.",
            "Tools: the model's tool calls by name. Rollouts do not record the built-in tool schemas.",
            "tokens_per_session_est of a skill: its line bytes at bytes-v1's 4 ASCII characters per token.",
        ],
    }


# ---------- Markdown ----------

def _n(x) -> str:
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    return f"{x:,}" if isinstance(x, (int, float)) else ("-" if x is None else str(x))


def _table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---:" if i else "---" for i in range(len(headers))) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def render(data: dict) -> str:
    run = data["run"]
    md = []
    if "first_call" in data:
        s, fc = data["session"], data["first_call"]
        flags = [x for x in ("subagent" if s["subagent"] else "", f"forked from {s['forked_from']}" if s["forked_from"] else "",
                             f"resumes {s['resumed_from']}" if s["resumed_from"] else "", f"page {s['page']}" if s["page"] else "") if x]
        md.append(f"# walle context: Codex session {s['id'][:8]}\n")
        md.append(f"{s['start']} · {s['model']} · {s['originator']} {s['codex_version']} · `{s['cwd']}`"
                  + (f" · {', '.join(flags)}" if flags else "") + "  \n"
                  f"Estimator: {run['estimator']}. {data['note']}\n")
        rows = []
        for r in sorted(fc["sources"], key=lambda r: -r["tokens_est"]):
            if not r["bytes"] and r["recorded"] == "not_recorded":
                continue
            rows.append([r["label"], _n(r["bytes"]), _n(r["items"]), _n(r["tokens_est"]), f"{r['share']}%",
                         r["on_disk"]["mark"]])
        res = fc["residual"]
        rows.append(["Sum of estimates", "", "", _n(fc["estimated_total"]), "", ""])
        rows.append(["Residual (measured minus estimates)", "", "", _n(res["tokens"]), f"{res['share']}%", ""])
        rows.append([f"**Measured first call** (input {_n(fc['measured']['input'])}, cached {_n(fc['measured']['cached'])})",
                     "", "", f"**{_n(fc['measured']['total'])}**", "100%", ""])
        md.append(_table(["Source", "Bytes", "Items", "Est. tokens", "Share", "On disk"], rows))
        md.append(f"\nResidual: {res['note']}.")
        if fc.get("skill_plugins"):
            md.append("\n## Skill list by plugin\n")
            md.append(_table(["Plugin", "Skills", "Bytes", "Est. tokens", "Share"],
                             [[g["plugin"], g["skills"], _n(g["bytes"]), _n(g["tokens_est"]), f"{g['share']}%"]
                              for g in fc["skill_plugins"]]))
        notes = []
        for r in fc["sources"]:
            od = r["on_disk"]
            if r["recorded"] == "not_recorded" or od["mark"] == "-":
                continue
            why = od.get("why", "")
            if od.get("not_on_disk"):
                why += f"; not on disk: {', '.join(od['not_on_disk'][:8])}{' …' if len(od['not_on_disk']) > 8 else ''}"
            if od.get("files"):
                why += "; " + ", ".join(f"{f['path']} ({_n(f['bytes'])} bytes)" for f in od["files"])
            notes.append(f"- {r['label']}: {od['mark']}. {why}")
        if notes:
            md.append("\n## Where each source comes from\n\nOnly sources on disk (a file or a config.toml key) can "
                      "change with a file edit.\n\n" + "\n".join(notes))
    if "usage" in data:
        u = data["usage"]
        sk = u["skipped_files"]
        md.append(f"# walle context --agent codex --usage\n\n{u['sessions']} threads ({u['rollout_files']} rollout files, "
                  f"{u['subagent_threads']} subagent threads), {u['window'].get('first', '-')} to "
                  f"{u['window'].get('last', '-')}. {data['note']}\n")
        if sk.get("zstd_missing") or sk.get("unreadable"):
            md.append(f"Skipped {sk.get('zstd_missing', 0)} zstd-compressed files (install the zstd program to read "
                      f"them) and {sk.get('unreadable', 0)} files walle could not read.\n")
        md.append("## First-call input tokens, by app\n")
        md.append(_table(["App", "Threads", "Median", "p10", "p90", "Median without forked threads"],
                         [[o, _n(v["n"]), _n(v.get("median")), _n(v.get("p10")), _n(v.get("p90")),
                           f"{_n(v['without_forked'].get('median'))} ({_n(v['without_forked']['n'])})"]
                          for o, v in u["first_calls"].items()]))
        if u["first_calls_excluded"]:
            md.append("\nLeft out: " + ", ".join(f"{k} {v}" for k, v in u["first_calls_excluded"].items()) + ".")
        md.append("\n## Skills listed but never used (largest first)\n")
        unused = [s for s in u["skills"] if not s["calls"]]
        md.append(f"{len(unused)} of {len(u['skills'])} listed skills were never used. "
                  f"Median skill list: {_n(u['skill_listing_bytes_median'])} bytes.\n")
        md.append(_table(["Skill", "Plugin", "Listed", "Line bytes"],
                         [[s["skill"], s["group"], f"{_n(s['sessions_listed'])} ({s['share_listed']}%)",
                           _n(s["line_bytes_median"])] for s in unused[:25]]))
        md.append("\n## Skills used\n")
        used = sorted((s for s in u["skills"] if s["calls"]), key=lambda s: -s["calls"])
        md.append(_table(["Skill", "Listed", "Used", "Calls"],
                         [[s["skill"], _n(s["sessions_listed"]), _n(s["sessions_used"]), _n(s["calls"])] for s in used[:25]]))
        md.append("\n## Skills by plugin\n")
        md.append(_table(["Plugin", "Skills", "Used skills", "Listed (max)", "Used (threads)", "Line bytes (sum of medians)",
                          "Calls"],
                         [[g["group"], g["skills"], g["skills_used"], _n(g["sessions_listed_max"]), _n(g["sessions_used"]),
                           _n(g["line_bytes_median"]), _n(g["calls"])] for g in u["skill_groups"][:25]]))
        md.append("\n## App connectors and MCP servers\n")
        md.append(_table(["Server", "Loaded (recorded)", "Used", "Calls", "Tools called", "Top tools"],
                         [[s["server"], _n(s["sessions_loaded"]), _n(s["sessions_used"]), _n(s["calls"]),
                           _n(s["tools_called"]), ", ".join(f"{t} {c}" for t, c in s["top_called"][:3])]
                          for s in u["servers"][:30]]))
        md.append("\n## Built-in tools\n")
        md.append(_table(["Tool", "Used", "Calls"], [[t["tool"], _n(t["sessions_used"]), _n(t["calls"])]
                                                     for t in u["tools"][:20]]))
        md.append("\n" + "\n".join(f"- {x}" for x in u["notes"]))
    return "\n".join(md) + "\n"
