"""walle context: what one session sent to the model, and what the sessions of a period loaded and used.

`walle context` breaks the first API call of one Claude Code session into its recorded sources
(transcript.py). Each row has the recorded size, a token estimate, and whether the source is on
disk. The residual row is the measured first-call tokens minus the sum of the estimates.

`walle context --usage` reads every main session of a period. For each MCP server, MCP tool,
built-in tool, skill, plugin and hook it counts the sessions that loaded it, the sessions that used
it, the calls, and the bytes it adds to a session.

These are measured values. They never enter the instruction-stack verdict, Start here or the alarm:
those stay on the estimated instruction stack (brief 2.1, rule L4). walle reads transcripts and
settings only; it writes only to the runs folder and its cache folder.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import __version__, estimate, skills
from .collect import HOME
from .stack import memory_folder, memory_slug
from .transcript import UUID_RE, FirstCall, first_call, server_key, usage_row

SCHEMA = 1
CACHE_VERSION = 2
NOTE = ("Measured values. They do not enter the instruction-stack verdict, Start here or the alarm, "
        "which use the estimated instruction stack only.")
LABELS = {
    "system_prompt": "System prompt and host blocks",
    "builtin_tools": "Built-in tool schemas",
    "mcp_tools": "MCP tool schemas loaded at start",
    "deferred_tools": "Deferred tool names (ToolSearch)",
    "mcp_instructions": "MCP server instructions",
    "skill_listing": "Skill list",
    "instructions": "CLAUDE.md, MEMORY.md and rules",
    "agent_listing": "Agent list",
    "hook_context": "Hook output",
    "first_prompt": "First prompt",
    "other_attachments": "Other attachments",
}


class ContextError(Exception):
    pass


# ---------- finding transcripts ----------

def main_transcripts(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("*/*.jsonl") if p.is_file())


def find_session(root: Path, session: str | None) -> Path:
    files = main_transcripts(root)
    if not files:
        raise ContextError(f"no transcripts under {root}")
    if not session:
        return max(files, key=lambda p: p.stat().st_mtime)
    hits = [p for p in files if p.stem == session] or [p for p in files if p.stem.startswith(session)]
    if not hits:
        raise ContextError(f"no session {session!r} under {root}")
    if len({p.stem for p in hits}) > 1:
        raise ContextError(f"{session!r} matches {len(hits)} sessions; give more of the id")
    return hits[0]


# ---------- what is on disk ----------

def local_mcp_servers(folder: Path | None, home: Path = HOME) -> dict[str, str]:
    """server key -> where it is configured, for MCP servers configured in files.

    Reads only the keys of mcpServers: their values hold commands, env and headers (secrets).
    """
    out: dict[str, str] = {}

    def keys(path: Path, *dig: str) -> list[str]:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return []
        for k in dig:
            data = data.get(k) if isinstance(data, dict) else None
        return [str(k) for k in data] if isinstance(data, dict) else []

    claude_json = home / ".claude.json"
    for k in keys(claude_json, "mcpServers"):
        out.setdefault(server_key(k), "~/.claude.json mcpServers")
    if folder is not None:
        try:
            projects = json.loads(claude_json.read_text()).get("projects") or {}
        except (OSError, ValueError, AttributeError):
            projects = {}
        for d in [folder, *folder.parents]:
            p = projects.get(str(d)) if isinstance(projects, dict) else None
            if isinstance(p, dict) and isinstance(p.get("mcpServers"), dict):
                for k in p["mcpServers"]:
                    out.setdefault(server_key(str(k)), f"~/.claude.json projects[{d}]")
            for k in keys(d / ".mcp.json", "mcpServers"):
                out.setdefault(server_key(k), str(d / ".mcp.json"))
    for root, _source, name in skills.plugin_roots(home):
        mcp = root / ".mcp.json"
        names = keys(mcp, "mcpServers") or [k for k in keys(mcp) if k != "mcpServers"]
        for k in names:
            out.setdefault(f"plugin:{name}:{k}", str(mcp))
    return out


def server_kind(key: str, local: dict[str, str]) -> str:
    if key in local:
        return "local"
    if UUID_RE.match(key) or key.startswith("claude.ai:"):
        return "connector"
    if key.startswith("plugin:"):
        return "plugin"
    return "host"


# ---------- C1: one session ----------

def session_context(fc: FirstCall, estimator: str = estimate.DEFAULT, home: Path = HOME,
                    names: dict[str, str] | None = None) -> dict:
    names = names or {}
    cwd = Path(fc.cwd) if fc.cwd else None
    local = local_mcp_servers(cwd, home)
    rows = []
    for s in fc.sources:
        tokens = estimate.estimate(s.text, s.kind, estimator)
        rows.append({"source": s.kind, "label": LABELS[s.kind], "bytes": s.bytes, "items": s.count,
                     "recorded": s.recorded, "tokens_est": tokens, "basis": estimate.basis(s.kind, estimator),
                     "on_disk": _on_disk(s, cwd, fc.org, local, home, names), "detail": _detail(s, names)})
    est_total = sum(r["tokens_est"] for r in rows)
    measured = fc.measured["total"]
    for r in rows:
        r["share"] = round(100 * r["tokens_est"] / measured, 1) if measured else None
    return {
        "measured": fc.measured,
        "sources": rows,
        "estimated_total": est_total,
        "residual": {"tokens": measured - est_total,
                     "share": round(100 * (measured - est_total) / measured, 1) if measured else None,
                     "note": "measured first-call tokens minus the sum of the estimates: the parts no "
                             "attachment records (message framing, tool-use scaffolding) and estimate error"},
    }


def _detail(s, names: dict[str, str]) -> dict:
    d = dict(s.detail)
    if s.kind == "deferred_tools":
        d["servers"] = {display(k, names): v for k, v in sorted(d["servers"].items(), key=lambda x: -x[1]["bytes"])}
    elif s.kind in ("mcp_instructions", "mcp_tools"):
        d["servers"] = {display(k, names): v for k, v in d.get("servers", {}).items()}
    return d


def _on_disk(s, cwd: Path | None, org: str | None, local: dict[str, str], home: Path, names) -> dict:
    """Which part of a source walle can find on disk, and so could trim with a file change."""
    if s.kind in ("system_prompt", "builtin_tools", "other_attachments"):
        return {"mark": "not on disk", "why": "built into Claude Code or the host app"}
    if s.kind == "first_prompt":
        return {"mark": "-", "why": "typed or sent by the user"}
    if s.kind == "hook_context":
        return {"mark": "on disk" if s.count else "-", "why": "hook scripts in settings or plugins"}
    if s.kind == "skill_listing":
        found = {sk.name for sk in skills.discover(cwd, home, org)} if cwd else set()
        listed = s.detail.get("names", [])
        missing = [n for n in listed if n not in found]
        return {"mark": _mark(len(listed) - len(missing), len(listed)), "on_disk": len(listed) - len(missing),
                "not_on_disk": missing, "why": "skills not on disk are built into Claude Code"}
    if s.kind == "instructions":
        files = s.detail.get("files", [])
        here = [f for f in files if f.get("path") and Path(f["path"]).is_file()]
        return {"mark": _mark(len(here), len(files)), "on_disk": len(here),
                "not_on_disk": [f.get("path") for f in files if f not in here]}
    if s.kind == "agent_listing":
        mine = set()
        for d in [home / ".claude" / "agents"] + ([c / ".claude" / "agents" for c in [cwd, *cwd.parents]] if cwd else []):
            if d.is_dir():
                mine |= {p.stem for p in d.glob("*.md")}
        types = s.detail.get("types", [])
        return {"mark": _mark(sum(1 for t in types if t in mine), len(types)),
                "not_on_disk": [t for t in types if t not in mine], "why": "built-in agent types"}
    if s.kind in ("deferred_tools", "mcp_tools", "mcp_instructions"):
        servers = s.detail.get("servers", {})
        by_kind: dict[str, list[str]] = defaultdict(list)
        n_disk = n_all = 0
        for k, v in servers.items():
            kind = "built-in" if k == "(built-in)" else server_kind(k, local)
            by_kind[kind].append(display(k, names))
            n = v["names"] if isinstance(v, dict) else 1
            n_all += n
            if kind in ("local", "plugin"):
                n_disk += n
        return {"mark": _mark(n_disk, n_all), "servers_by_kind": dict(by_kind),
                "why": "claude.ai connectors and host servers are not on disk; local and plugin servers are"}
    return {"mark": "-"}


def _mark(n: int, total: int) -> str:
    if total == 0:
        return "-"
    if n == total:
        return "on disk"
    if n == 0:
        return "not on disk"
    return f"partly on disk ({n} of {total})"


# ---------- C2: loaded and used across sessions ----------

def load_rows(root: Path, cache_file: Path) -> dict[str, dict]:
    """usage_row of every main transcript and subagent file, cached by size and mtime."""
    cache: dict = {}
    try:
        cache = json.loads(cache_file.read_text())
        if cache.get("_v") != CACHE_VERSION:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    out: dict[str, dict] = {}
    fresh: dict = {"_v": CACHE_VERSION}
    for p in main_transcripts(root):
        for f, main in [(p, True)] + [(s, False) for s in sorted((p.parent / p.stem / "subagents").rglob("agent-*.jsonl"))]:
            st = f.stat()
            stamp = f"{st.st_size}:{int(st.st_mtime)}"
            hit = cache.get(str(f))
            if hit and hit.get("stamp") == stamp:
                row = hit["row"]
            else:
                row = usage_row(f, main)
            fresh[str(f)] = {"stamp": stamp, "row": row}
            row = dict(row, main=main, session=p.stem, slug=p.parent.name)
            out[str(f)] = row
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(fresh, ensure_ascii=False))
    return out


def connector_names(rows) -> dict[str, str]:
    """Server key -> display name.

    The desktop app names claude.ai connectors by UUID; the CLI names them claude_ai_<Name>. A UUID
    server gets the name of the claude.ai server whose tool names it shares most (Jaccard >= 0.5).
    """
    tools: dict[str, set] = defaultdict(set)
    shown: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        for k, ts in r.get("server_tools", {}).items():
            tools[k].update(ts)
        for k, raw in r.get("display", {}).items():
            shown[k][raw] += 1
    names: dict[str, str] = {}
    for k in tools.keys() | shown.keys():
        if k.startswith("claude.ai:"):
            forms = [f for f, _ in shown[k].most_common() if f.startswith("claude.ai ")]
            raw = forms[0] if forms else next(iter(shown[k]), k)
            names[k] = raw if raw.startswith("claude.ai ") else "claude.ai " + raw.replace("claude_ai_", "").replace("_", " ")
    named = [k for k in tools if k.startswith("claude.ai:")]
    for k in tools:
        if not UUID_RE.match(k):
            continue
        best, score = None, 0.0
        for n in named:
            a, b = tools[k], tools[n]
            j = len(a & b) / len(a | b) if a | b else 0
            if j > score:
                best, score = n, j
        if best and score >= 0.5:
            names[k] = best  # an alias: the key of the named server
    return names


def canonical(key: str, names: dict[str, str]) -> str:
    """The key a server is counted under: UUID servers fold into the named server they match."""
    v = names.get(key)
    return v if v and v.startswith("claude.ai:") else key


def display(key: str, names: dict[str, str]) -> str:
    c = canonical(key, names)
    if c in names and not names[c].startswith("claude.ai:"):
        return names[c]
    if UUID_RE.match(c):
        return f"claude.ai {c[:8]} (connector id)"
    return c


def _short(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def usage(rows: dict[str, dict], since: str | None = None, before: str | None = None) -> dict:
    by_session: dict[str, dict] = {}
    subs: dict[str, list[dict]] = defaultdict(list)
    for r in rows.values():
        key = f"{r['slug']}/{r['session']}"
        if r["main"]:
            by_session[key] = r
        else:
            subs[key].append(r)
    keep = {k: r for k, r in by_session.items()
            if (not since or r["start"] >= since) and (not before or (r["start"] and r["start"] < before))}
    names = connector_names(list(rows.values()))
    n = len(keep)

    srv = defaultdict(lambda: {"loaded": 0, "used": 0, "loaded_used": 0, "calls": 0, "names": [], "name_bytes": [],
                               "schema_bytes": [], "instr_bytes": [], "offered": set(), "called": Counter(),
                               "failed": 0, "needs_auth": 0})
    tool = defaultdict(lambda: {"loaded": 0, "used": 0, "calls": 0})
    builtin = defaultdict(lambda: {"loaded": 0, "used": 0, "calls": 0, "schema_bytes": []})
    skill = defaultdict(lambda: {"listed": 0, "used": 0, "calls": 0, "line_bytes": []})
    hooks = defaultdict(lambda: {"sessions": 0, "injections": 0, "bytes": 0})
    hook_total = {"sessions": 0, "injections": 0, "bytes": 0}
    listing_bytes = []
    for key, m in keep.items():
        files = [m] + subs.get(key, [])
        calls = Counter()
        for f in files:
            calls.update(f["tools"])
        # MCP servers: loaded = offered as deferred names, loaded schemas or instruction blocks.
        loaded: dict[str, dict] = {}
        for k, v in m["servers"].items():
            c = canonical(k, names)
            e = loaded.setdefault(c, {"names": 0, "bytes": 0, "schema": 0, "instr": 0, "tools": set()})
            e["names"] += v["deferred"] + v["loaded"]
            e["bytes"] += v["deferred_bytes"]
            e["schema"] += v.get("loaded_bytes", 0)
            e["tools"].update(m["server_tools"].get(k, []))
        for k, b in m["instr"].items():
            c = canonical(k, names)
            e = loaded.setdefault(c, {"names": 0, "bytes": 0, "schema": 0, "instr": 0, "tools": set()})
            e["instr"] = max(e["instr"], b)
        for k in set(canonical(x, names) for x in m.get("failed", [])):
            srv[k]["failed"] += 1
        for k in set(canonical(x, names) for x in m.get("needs_auth", [])):
            srv[k]["needs_auth"] += 1
        used: Counter = Counter()
        used_tools: Counter = Counter()
        for t, c in calls.items():
            if t.startswith("mcp__"):
                raw = t[5:].split("__", 1)[0]
                k = canonical(server_key(raw), names)
                used[k] += c
                used_tools[(k, t[5 + len(raw) + 2:])] += c
        for k, e in loaded.items():
            s = srv[k]
            s["loaded"] += 1
            s["loaded_used"] += 1 if used.get(k) else 0
            if e["names"]:
                s["names"].append(e["names"])
                s["name_bytes"].append(e["bytes"])
                s["schema_bytes"].append(e["schema"])
            if e["instr"]:
                s["instr_bytes"].append(e["instr"])
            s["offered"].update(e["tools"])
            for t in e["tools"]:
                tool[(k, t)]["loaded"] += 1
        for k, c in used.items():
            srv[k]["used"] += 1
            srv[k]["calls"] += c
        for (k, t), c in used_tools.items():
            tool[(k, t)]["used"] += 1
            tool[(k, t)]["calls"] += c
            srv[k]["called"][t] += c
        # Built-in tools: loaded = in the tools snapshot.
        for t, b in m["builtin_loaded"].items():
            builtin[t]["loaded"] += 1
            builtin[t]["schema_bytes"].append(b)
        for t, c in calls.items():
            if not t.startswith("mcp__"):
                builtin[t]["used"] += 1
                builtin[t]["calls"] += c
        # Skills: listed in skill_listing; used by the Skill tool or a slash command that names a listed skill.
        listed = set(m["listed"])
        if m["listing_bytes"]:
            listing_bytes.append(m["listing_bytes"])
        for nm in listed:
            skill[nm]["listed"] += 1
            if nm in m["skill_lines"]:
                skill[nm]["line_bytes"].append(m["skill_lines"][nm])
        hits: Counter = Counter()
        invoked = Counter()
        for f in files:
            invoked.update({("Skill", k): c for k, c in f["skills"].items()})
            invoked.update({("slash", k): c for k, c in f["slash"].items()})
        for (how, k), c in invoked.items():
            match = [x for x in listed if x == k] or [x for x in listed if _short(x) == _short(k)]
            if not match:
                if how == "slash":
                    continue  # a built-in command such as /clear, not a skill
                match = [k]
            hits[match[0]] += c
        for nm, c in hits.items():
            skill[nm]["used"] += 1
            skill[nm]["calls"] += c
        # Hooks: text hooks added, in the main session and its subagents.
        seen = set()
        any_hook = False
        for f in files:
            for hk, (cnt, b) in f["hooks"].items():
                if hk.startswith("name:"):
                    any_hook = True
                    hook_total["injections"] += cnt
                    hook_total["bytes"] += b
                h = hooks[hk]
                h["injections"] += cnt
                h["bytes"] += b
                if hk not in seen:
                    seen.add(hk)
                    h["sessions"] += 1
        hook_total["sessions"] += any_hook

    def med(xs):
        return round(statistics.median(xs)) if xs else 0

    v2 = estimate.BYTES_V2
    servers = []
    for k, s in srv.items():
        nb, sb, ib = med(s["name_bytes"]), med(s["schema_bytes"]), med(s["instr_bytes"])
        servers.append({
            "server": display(k, names), "key": k,
            "aliases": sorted(a for a, v in names.items() if v == k and a != k),
            "sessions_loaded": s["loaded"], "share_loaded": round(100 * s["loaded"] / n, 1) if n else 0,
            "sessions_used": s["used"], "sessions_loaded_and_used": s["loaded_used"], "calls": s["calls"],
            "sessions_failed": s["failed"], "sessions_needs_auth": s["needs_auth"],
            "tool_names_median": med(s["names"]), "name_bytes_median": nb, "schema_bytes_median": sb,
            "instr_bytes_median": ib, "bytes_per_session": nb + sb + ib,
            "tokens_per_session_est": round(nb * v2["deferred_tools"]["tok_per_byte"]
                                            + sb * v2["mcp_tools"]["tok_per_byte"]
                                            + ib * v2["mcp_instructions"]["tok_per_byte"]),
            "tools_offered": len(s["offered"]), "tools_called": len([t for t in s["offered"] if s["called"].get(t)]),
            "top_called": s["called"].most_common(5),
        })
    for s in servers:
        s["tokens_unused_est"] = s["tokens_per_session_est"] * (s["sessions_loaded"] - s["sessions_loaded_and_used"])
    servers.sort(key=lambda s: (-s["tokens_unused_est"], s["server"]))

    tools = [{"server": display(k, names), "tool": t, "sessions_loaded": v["loaded"], "sessions_used": v["used"],
              "calls": v["calls"]} for (k, t), v in tool.items()]
    tools.sort(key=lambda x: (x["calls"] > 0, -x["sessions_loaded"], x["server"], x["tool"]))
    built = [{"tool": t, "sessions_loaded": v["loaded"], "sessions_used": v["used"], "calls": v["calls"],
              "schema_bytes_median": med(v["schema_bytes"])} for t, v in builtin.items() if v["loaded"]]
    built.sort(key=lambda x: (x["calls"], -x["schema_bytes_median"]))
    skill_rows = [{"skill": nm, "sessions_listed": v["listed"], "share_listed": round(100 * v["listed"] / n, 1) if n else 0,
                   "sessions_used": v["used"], "calls": v["calls"], "line_bytes_median": med(v["line_bytes"])}
                  for nm, v in skill.items()]
    skill_rows.sort(key=lambda x: (x["calls"] > 0, -x["sessions_listed"] * x["line_bytes_median"], x["skill"]))

    plugins = defaultdict(lambda: {"skills": 0, "sessions_listed_max": 0, "skill_calls": 0, "listing_bytes_median": 0,
                                   "mcp_servers": [], "mcp_sessions_loaded_max": 0, "mcp_calls": 0})
    for r in skill_rows:
        if ":" in r["skill"]:
            p = plugins[r["skill"].split(":", 1)[0]]
            p["skills"] += 1
            p["sessions_listed_max"] = max(p["sessions_listed_max"], r["sessions_listed"])
            p["skill_calls"] += r["calls"]
            p["listing_bytes_median"] += r["line_bytes_median"]
    for s in servers:
        if s["key"].startswith("plugin:"):
            p = plugins[s["key"].split(":")[1]]
            p["mcp_servers"].append(s["server"])
            p["mcp_sessions_loaded_max"] = max(p["mcp_sessions_loaded_max"], s["sessions_loaded"])
            p["mcp_calls"] += s["calls"]
    plugin_rows = [dict(plugin=k, **v) for k, v in plugins.items()]
    plugin_rows.sort(key=lambda x: (x["skill_calls"] + x["mcp_calls"] > 0, -x["sessions_listed_max"], x["plugin"]))

    hook_rows = [{"by": k.split(":", 1)[0], "hook": k.split(":", 1)[1], **v} for k, v in hooks.items()]
    hook_rows.sort(key=lambda x: -x["bytes"])
    starts = sorted(r["start"] for r in keep.values() if r["start"])
    return {
        "since": since, "before": before, "sessions": n,
        "subagent_files": sum(len(subs.get(k, [])) for k in keep),
        "window": {"first": starts[0], "last": starts[-1]} if starts else {},
        "skill_listing_bytes_median": med(listing_bytes),
        "servers": servers, "mcp_tools": tools, "builtin_tools": built, "skills": skill_rows,
        "plugins": plugin_rows, "hooks": hook_rows, "hooks_total": hook_total,
        "sessions_with_failed_server": sum(1 for r in keep.values() if r.get("failed")),
        "sessions_with_needs_auth": sum(1 for r in keep.values() if r.get("needs_auth")),
        "needs_auth_servers_median": med([len(r["needs_auth"]) for r in keep.values() if r.get("needs_auth")]),
        "notes": [
            "Loaded MCP servers: deferred tool names, tool schemas loaded at start, or instruction blocks in the main "
            "transcript. Bytes per session: median deferred-name bytes + median schema bytes + median instruction bytes.",
            "Used: tool calls in the main transcript and its subagent files.",
            "A claude.ai connector named by UUID (desktop) counts under the claude_ai_<Name> server (CLI) whose "
            "tool names it shares; see aliases.",
            "Skills used: Skill tool calls, and slash commands that name a listed skill.",
            "tokens_per_session_est uses bytes-v2 rates for deferred names and instructions.",
        ],
    }


# ---------- run, files, Markdown ----------

def run_block(estimator: str, label: str | None, kind_args: dict) -> dict:
    return {"id": None, "kind": "context", "parent_id": None, "label": label, "walle_version": __version__,
            "estimator": estimator, "estimator_detail": estimate.describe(estimator), **kind_args}


def build(args, root: Path, out: Path) -> dict:
    if getattr(args, "agent", "claude") != "claude":
        raise ContextError("only --agent claude is supported; the Codex rollout reader is plan step C3")
    data = {"schema": SCHEMA, "generated": datetime.now().isoformat(timespec="seconds"), "note": NOTE}
    cache = out.parent / "cache" / "context-usage.json"
    names: dict[str, str] = {}
    if args.usage:
        rows = load_rows(root, cache)
        data["usage"] = usage(rows, args.since, args.before)
        names = connector_names(list(rows.values()))
        data["run"] = run_block(args.estimator, args.label, {"agent": "claude", "transcripts": str(root)})
        return data
    path = find_session(root, args.session)
    fc = first_call(path)
    if fc is None:
        raise ContextError(f"{path.name} has no API call yet")
    if cache.is_file():
        try:
            names = connector_names([v["row"] for k, v in json.loads(cache.read_text()).items() if k != "_v"])
        except (OSError, ValueError, KeyError, TypeError):
            names = {}
    data["run"] = run_block(args.estimator, args.label, {"agent": "claude", "transcripts": str(root)})
    data["session"] = {"id": fc.session, "path": fc.path, "cwd": fc.cwd, "start": fc.start, "model": fc.model,
                       "entrypoint": fc.entrypoint, "claude_code_version": fc.version,
                       "memory_slug": memory_slug(memory_folder(Path(fc.cwd))) if fc.cwd else None}
    data["first_call"] = session_context(fc, args.estimator, names=names)
    return data


def write(data: dict, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    data["run"]["id"] = run_dir.name
    (run_dir / "context.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=sorted))
    (run_dir / "context.md").write_text(render(data))
    return run_dir / "context.md"


def _n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else ("-" if x is None else str(x))


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
        md.append(f"# walle context: session {s['id'][:8]}\n")
        md.append(f"{s['start']} · {s['model']} · {s['entrypoint']} · `{s['cwd']}`  \n"
                  f"Estimator: {run['estimator']}. {data['note']}\n")
        rows = []
        for r in sorted(fc["sources"], key=lambda r: -r["tokens_est"]):
            if not r["bytes"] and r["recorded"] == "not_recorded":
                continue
            when = {"after_first_call": " (recorded after the first call)", "not_recorded": " (not recorded)"}.get(r["recorded"], "")
            rows.append([r["label"] + when, _n(r["bytes"]), _n(r["items"]), _n(r["tokens_est"]),
                         f"{r['share']}%", r["on_disk"]["mark"]])
        res = fc["residual"]
        rows.append(["Sum of estimates", "", "", _n(fc["estimated_total"]), "", ""])
        rows.append(["Residual (measured minus estimates)", "", "", _n(res["tokens"]), f"{res['share']}%", ""])
        rows.append([f"**Measured first call** (input {_n(fc['measured']['input'])}, cache write "
                     f"{_n(fc['measured']['cache_creation'])}, cache read {_n(fc['measured']['cache_read'])})",
                     "", "", f"**{_n(fc['measured']['total'])}**", "100%", ""])
        md.append(_table(["Source", "Bytes", "Items", "Est. tokens", "Share", "On disk"], rows))
        notes = []
        for r in fc["sources"]:
            od = r["on_disk"]
            if od.get("not_on_disk"):
                notes.append(f"- {r['label']}, not on disk: {', '.join(map(str, od['not_on_disk']))}")
            if od.get("servers_by_kind"):
                notes.append(f"- {r['label']}: " + "; ".join(f"{k} {len(v)}: {', '.join(v[:8])}{' …' if len(v) > 8 else ''}"
                                                             for k, v in od["servers_by_kind"].items()))
        if notes:
            md.append("\n## Not on disk\n\nOnly sources on disk can change with a file edit. Connectors, host servers and "
                      "built-ins change in the app or not at all.\n\n" + "\n".join(notes))
            if any("(connector id)" in x for x in notes):
                md.append("\nThe desktop app names claude.ai connectors by id. walle names them from the tool names "
                          "they share with CLI sessions, after `walle context --usage` has run once. An id that stays "
                          "has no such CLI session.")
        dt = next((r for r in fc["sources"] if r["source"] == "deferred_tools"), None)
        if dt and dt["detail"].get("servers"):
            top = list(dt["detail"]["servers"].items())[:12]
            md.append("\n## Deferred tool names by server\n\n" + _table(
                ["Server", "Names", "Bytes"], [[k, _n(v["names"]), _n(v["bytes"])] for k, v in top]))
    if "usage" in data:
        u = data["usage"]
        md.append(f"# walle context --usage\n\n{u['sessions']} main sessions ({u['subagent_files']} subagent files), "
                  f"{u['window'].get('first', '-')} to {u['window'].get('last', '-')}. {data['note']}\n")
        md.append("## MCP servers, by estimated tokens loaded but not used\n")
        md.append(_table(["Server", "Loaded", "Used", "Calls", "Tools called / offered", "Bytes per session", "Est. tok per session"],
                         [[s["server"], f"{_n(s['sessions_loaded'])} ({s['share_loaded']}%)", _n(s["sessions_used"]),
                           _n(s["calls"]), f"{s['tools_called']} / {s['tools_offered']}", _n(s["bytes_per_session"]),
                           _n(s["tokens_per_session_est"])] for s in u["servers"][:30]]))
        failed = sorted((s for s in u["servers"] if s["sessions_failed"]), key=lambda s: -s["sessions_failed"])
        if failed:
            md.append(f"\nServers that failed to connect: " + ", ".join(f"{s['server']} ({_n(s['sessions_failed'])} sessions)"
                                                                     for s in failed[:6])
                      + f". {_n(u['sessions_with_needs_auth'])} sessions listed servers that wait for sign-in "
                        f"(median {u['needs_auth_servers_median']} servers).\n")
        md.append("\n## Skills listed but never used (largest first)\n")
        unused = [s for s in u["skills"] if not s["calls"]]
        md.append(f"{len(unused)} of {len(u['skills'])} listed skills were never called. "
                  f"Median skill list: {_n(u['skill_listing_bytes_median'])} bytes.\n")
        md.append(_table(["Skill", "Listed", "Line bytes"],
                         [[s["skill"], f"{_n(s['sessions_listed'])} ({s['share_listed']}%)", _n(s["line_bytes_median"])]
                          for s in unused[:25]]))
        md.append("\n## Skills used\n")
        used = sorted((s for s in u["skills"] if s["calls"]), key=lambda s: -s["calls"])
        md.append(_table(["Skill", "Listed", "Used", "Calls"],
                         [[s["skill"], _n(s["sessions_listed"]), _n(s["sessions_used"]), _n(s["calls"])] for s in used[:25]]))
        md.append("\n## Plugins\n")
        md.append(_table(["Plugin", "Skills", "Listed (max)", "Skill calls", "MCP servers", "MCP calls"],
                         [[p["plugin"], p["skills"], _n(p["sessions_listed_max"]), _n(p["skill_calls"]),
                           len(p["mcp_servers"]), _n(p["mcp_calls"])] for p in u["plugins"][:25]]))
        md.append("\n## Built-in tools, least used first\n")
        md.append(_table(["Tool", "Loaded", "Used", "Calls", "Schema bytes"],
                         [[b["tool"], _n(b["sessions_loaded"]), _n(b["sessions_used"]), _n(b["calls"]),
                           _n(b["schema_bytes_median"])] for b in u["builtin_tools"][:20]]))
        ht = u["hooks_total"]
        md.append(f"\n## Hook output, by source\n\n{_n(ht['sessions'])} sessions got hook output: "
                  f"{_n(ht['injections'])} injections, {_n(ht['bytes'])} bytes.\n")
        md.append(_table(["Source", "Sessions", "Injections", "Bytes"],
                         [[h["hook"][:60], _n(h["sessions"]), _n(h["injections"]), _n(h["bytes"])]
                          for h in u["hooks"] if h["by"] == "source"][:15]))
        md.append("\n" + "\n".join(f"- {x}" for x in u["notes"]))
    return "\n".join(md) + "\n"


def add_parser(sub, default_transcripts: Path):
    x = sub.add_parser("context", help="break one session's first API call into sources; with --usage, "
                                       "count what sessions loaded against what they used")
    x.add_argument("--last", action="store_true", help="the newest session (the default)")
    x.add_argument("--session", help="session id, or the start of one")
    x.add_argument("--agent", choices=["claude", "codex"], default="claude")
    x.add_argument("--usage", action="store_true", help="loaded against used, for every main session of the period")
    x.add_argument("--since", help="with --usage: ISO date, sessions that start on or after it")
    x.add_argument("--before", help="with --usage: ISO date, sessions that start before it")
    x.add_argument("--estimator", choices=estimate.ESTIMATORS, default=estimate.DEFAULT,
                   help=f"token estimator (default {estimate.DEFAULT})")
    x.add_argument("--transcripts", type=Path, default=default_transcripts)
    x.add_argument("--label", help="a name for this run")
    return x


def run_cli(args) -> int:
    import sys
    from .runs import new_run_dir
    try:
        data = build(args, args.transcripts.expanduser(), args.out)
    except ContextError as e:
        print(f"walle context: {e}", file=sys.stderr)
        return 2
    # The run folder sits with the other runs. `latest` and the run index stay on scan runs.
    print(write(data, new_run_dir(args.out)))
    return 0
