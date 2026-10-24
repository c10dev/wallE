"""Read what a Claude Code transcript records about the context: attachments, tool calls, skill calls.

A transcript (~/.claude/projects/<slug>/<session>.jsonl) has one JSON object per line. Lines of
type "attachment" record parts of what the session sends to the model. walle reads these:

| attachment              | what it records                                  | recorded size (bytes)           |
|-------------------------|--------------------------------------------------|---------------------------------|
| prompt_snapshot         | systemPrompt parts; later also tools (name,      | parts joined with newlines;     |
|                         | description, schema)                             | each tool as JSON               |
| deferred_tools_delta    | names of tools the session loads on demand       | addedLines joined with newlines |
| mcp_instructions_delta  | the instructions of each MCP server              | addedBlocks joined              |
| skill_listing           | name and description of each listed skill        | content                         |
| instructions            | CLAUDE.md, MEMORY.md and other instruction files | file contents joined            |
| agent_listing_delta     | the agent types the session can start            | addedLines joined               |
| hook_additional_context | text that hooks add                              | content joined                  |

The first API call is the first assistant line. Its usage (input + cache creation + cache read
tokens) is the measured size of everything above plus the first prompt. Claude Code writes the
tools of prompt_snapshot after the first call; walle takes the first snapshot with tools that comes
before the second call and marks it "after first call".
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import cost

SOURCES = ("system_prompt", "builtin_tools", "mcp_tools", "deferred_tools", "mcp_instructions",
           "skill_listing", "instructions", "agent_listing", "hook_context", "first_prompt", "other_attachments")
CMD_RE = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")
HOOK_TAG_RE = re.compile(r"^\s*(\[[^\]]{1,40}\]|[A-Za-z][\w .-]{0,30}:)")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Lines worth parsing in a full pass. The rest (tool results, assistant text) is most of the bytes.
USAGE_MARKERS = (b'"tool_use"', b"deferred_tools_delta", b"mcp_instructions_delta", b"skill_listing",
                 b"hook_additional_context", b'"hook_success"', b"stop_hook_summary", b"<command-name>",
                 b"prompt_snapshot")


def blen(text: str) -> int:
    return len(text.encode())


def mcp_server(tool: str) -> str | None:
    """The raw server part of an MCP tool name mcp__<server>__<tool>."""
    if not tool.startswith("mcp__"):
        return None
    rest = tool[5:]
    i = rest.find("__")
    return rest[:i] if i > 0 else rest


def server_key(raw: str) -> str:
    """One key per MCP server across the names Claude Code uses for it.

    Tool names use claude_ai_<Name> and plugin_<plugin>_<server>; instruction blocks and server
    lists use "claude.ai <Name>" and plugin:<plugin>:<server>. Connector UUIDs stay as they are
    (context.py maps them to names).
    """
    s = raw.strip()
    if UUID_RE.match(s):
        return s
    for p in ("claude_ai_", "claude.ai "):
        if s.startswith(p):
            return "claude.ai:" + re.sub(r"[^a-z0-9]", "", s[len(p):].lower())
    if s.startswith("plugin_"):
        parts = s.split("_", 2)
        if len(parts) == 3:
            return f"plugin:{parts[1]}:{parts[2]}"
    return s


def hook_source(text: str) -> str:
    """A label for hook output: its leading [tag] or Name: prefix, else its first 40 characters."""
    m = HOOK_TAG_RE.match(text or "")
    if m:
        return m.group(1).strip()
    return (text or "").strip().split("\n", 1)[0][:40]


def label_digest(label: str) -> str:
    """A stand-in for a hook output label once its transcript is deleted: it matches the same label in another
    session without keeping the text."""
    return hashlib.sha256(label.encode()).hexdigest()[:12]


def _user_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text") or "")
        return "\n".join(out)
    return ""


def _strings(obj) -> list[str]:
    """String values of an attachment payload, without its type."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() if k != "type" for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


@dataclass
class Source:
    kind: str  # one of SOURCES
    bytes: int
    count: int | None
    recorded: str  # before_first_call | after_first_call | not_recorded
    text: str = field(default="", repr=False)  # for the estimator; not written out
    detail: dict = field(default_factory=dict)


@dataclass
class FirstCall:
    session: str
    path: str
    cwd: str
    start: str
    model: str
    entrypoint: str
    version: str
    org: str | None  # organization UUID of the signed-in account, used to pick a skill sync bucket
    measured: dict  # input, cache_creation (cache_write_1h of it), cache_read, total tokens of the first call; cost
    sources: list[Source]


def first_call(path: Path) -> FirstCall | None:
    """Break the first API call of a main-session transcript into its recorded sources."""
    cwd = start = model = entrypoint = version = ""
    org = None
    atts: list[tuple[str, dict]] = []
    prompt = None
    snapshot = tools_snapshot = None
    tools_when = "not_recorded"
    measured = None
    first_id = None
    with path.open("rb") as fh:
        for raw in fh:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("isSidechain"):
                continue
            cwd = cwd or d.get("cwd") or ""
            start = start or d.get("timestamp") or ""
            entrypoint = entrypoint or d.get("entrypoint") or ""
            version = version or d.get("version") or ""
            t = d.get("type")
            if t == "assistant":
                msg = d.get("message") or {}
                mid = msg.get("id") or d.get("uuid")
                u = msg.get("usage") or {}
                total = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                         + u.get("cache_read_input_tokens", 0))
                if measured is None and total:
                    first_id, model = mid, msg.get("model", "")
                    split = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
                    measured = {"input": u.get("input_tokens", 0),
                                "cache_creation": u.get("cache_creation_input_tokens", 0),
                                "cache_write_1h": split.get("ephemeral_1h_input_tokens", 0),
                                "cache_read": u.get("cache_read_input_tokens", 0), "total": total}
                    measured["cost"] = cost.claude(measured, model)
                    if tools_snapshot is not None:
                        break
                elif measured is not None and mid != first_id:
                    break  # the second call: what comes later is not about the first one
                continue
            if t == "user" and measured is None and prompt is None and not d.get("isMeta"):
                text = _user_text((d.get("message") or {}).get("content"))
                if text:
                    prompt = text
                continue
            if t != "attachment":
                continue
            a = d.get("attachment") or {}
            at = a.get("type")
            if at == "prompt_snapshot":
                if snapshot is None and measured is None:
                    snapshot = a
                if tools_snapshot is None and isinstance(a.get("tools"), list):
                    tools_snapshot = a
                    tools_when = "before_first_call" if measured is None else "after_first_call"
                    if measured is not None:
                        break
                continue
            if measured is not None:
                continue
            if at == "credential_org":
                org = a.get("organizationUuid") or None
                continue
            atts.append((at, a))
    if measured is None:
        return None
    snapshot = snapshot or tools_snapshot
    return FirstCall(path.stem, str(path), cwd, start, model, entrypoint, version, org, measured,
                     _sources(atts, prompt, snapshot, tools_snapshot, tools_when))


def _sources(atts, prompt, snapshot, tools_snapshot, tools_when) -> list[Source]:
    by: dict[str, list[dict]] = {}
    for at, a in atts:
        by.setdefault(at, []).append(a)
    out: list[Source] = []

    parts = (snapshot or {}).get("systemPrompt") or []
    text = "\n".join(p for p in parts if isinstance(p, str))
    out.append(Source("system_prompt", blen(text), len(parts), "before_first_call" if snapshot else "not_recorded",
                      text, {"parts": len(parts)}))

    tools = (tools_snapshot or {}).get("tools") or []
    for kind, keep in (("builtin_tools", False), ("mcp_tools", True)):
        mine = [x for x in tools if isinstance(x, dict) and str(x.get("name", "")).startswith("mcp__") == keep]
        texts = [json.dumps(x, ensure_ascii=False) for x in mine]
        sizes = {x.get("name"): blen(s) for x, s in zip(mine, texts)}
        detail = {"tools": sizes}
        if keep:
            servers: dict[str, int] = {}
            for x in mine:
                k = server_key(mcp_server(x["name"]) or "")
                servers[k] = servers.get(k, 0) + 1
            detail["servers"] = servers
        out.append(Source(kind, sum(sizes.values()), len(mine), tools_when if tools_snapshot else "not_recorded",
                          "\n".join(texts), detail))

    names: list[str] = []
    lines: list[str] = []
    pending: list[str] = []
    needs_auth: list[str] = []
    for a in by.get("deferred_tools_delta", []):
        removed = set(a.get("removedNames") or [])
        keep = [(n, l) for n, l in zip(names, lines) if n not in removed]
        names, lines = [n for n, _ in keep], [l for _, l in keep]
        names += [str(x) for x in a.get("addedNames") or []]
        lines += [str(x) for x in a.get("addedLines") or []]
        pending += [_name(x) for x in a.get("pendingMcpServers") or []]
        needs_auth += [_name(x) for x in a.get("needsAuthMcpServers") or []]
    text = "\n".join(lines)
    servers: dict[str, list[int]] = {}
    for n, l in zip(names, lines):
        k = server_key(mcp_server(n)) if n.startswith("mcp__") else "(built-in)"
        e = servers.setdefault(k, [0, 0])
        e[0] += 1
        e[1] += blen(l) + 1
    out.append(Source("deferred_tools", blen(text), len(names), _when(by, "deferred_tools_delta"), text,
                      {"mcp_names": sum(1 for n in names if n.startswith("mcp__")),
                       "servers": {k: {"names": v[0], "bytes": v[1]} for k, v in servers.items()},
                       "pending_servers": sorted(set(pending)), "needs_auth_servers": sorted(set(needs_auth))}))

    blocks: list[str] = []
    snames: list[str] = []
    for a in by.get("mcp_instructions_delta", []):
        snames += [str(x) for x in a.get("addedNames") or []]
        blocks += [str(x) for x in a.get("addedBlocks") or []]
    text = "\n".join(blocks)
    out.append(Source("mcp_instructions", blen(text), len(blocks), _when(by, "mcp_instructions_delta"), text,
                      {"servers": {server_key(n): blen(b) for n, b in zip(snames, blocks)}}))

    listing = by.get("skill_listing", [])
    text = "\n".join(str(a.get("content") or "") for a in listing)
    skill_names = [str(n) for a in listing for n in a.get("names") or []]
    out.append(Source("skill_listing", blen(text), len(skill_names) if listing else 0,
                      _when(by, "skill_listing"), text, {"names": skill_names}))

    files = [f for a in by.get("instructions", []) for f in a.get("files") or [] if isinstance(f, dict)]
    text = "\n".join(str(f.get("content") or "") for f in files)
    out.append(Source("instructions", blen(text), len(files), _when(by, "instructions"), text,
                      {"files": [{"path": f.get("path"), "type": f.get("type"), "bytes": blen(str(f.get("content") or ""))}
                                 for f in files]}))

    agent_lines = [str(x) for a in by.get("agent_listing_delta", []) for x in a.get("addedLines") or []]
    types = [str(x) for a in by.get("agent_listing_delta", []) for x in a.get("addedTypes") or []]
    text = "\n".join(agent_lines)
    out.append(Source("agent_listing", blen(text), len(types), _when(by, "agent_listing_delta"), text,
                      {"types": types}))

    hooks = []
    for a in by.get("hook_additional_context", []):
        hooks.append((a.get("hookName") or "?", "\n".join(x for x in a.get("content") or [] if isinstance(x, str))))
    for a in by.get("hook_success", []):
        if a.get("hookEvent") in ("UserPromptSubmit", "SessionStart") and str(a.get("content") or "").strip():
            hooks.append((a.get("hookName") or "?", str(a.get("content"))))
    text = "\n".join(h for _, h in hooks)
    out.append(Source("hook_context", blen(text), len(hooks),
                      "before_first_call" if hooks else "not_recorded", text,
                      {"hooks": [{"name": n, "source": hook_source(h), "bytes": blen(h)} for n, h in hooks]}))

    text = prompt or ""
    out.append(Source("first_prompt", blen(text), 1 if prompt is not None else 0,
                      "before_first_call" if prompt is not None else "not_recorded", text))

    known = {"deferred_tools_delta", "mcp_instructions_delta", "skill_listing", "instructions",
             "agent_listing_delta", "hook_additional_context", "hook_success"}
    other = [(at, a) for at, a in atts if at not in known]
    strings = [s for _, a in other for s in _strings(a)]
    text = "\n".join(strings)
    counts: dict[str, int] = {}
    for at, _ in other:
        counts[str(at)] = counts.get(str(at), 0) + 1
    out.append(Source("other_attachments", blen(text), len(other), "before_first_call" if other else "not_recorded",
                      text, {"types": counts}))
    return out


def _name(x) -> str:
    return str(x.get("name")) if isinstance(x, dict) else str(x)


def _when(by: dict, at: str) -> str:
    return "before_first_call" if by.get(at) else "not_recorded"


def usage_row(path: Path, main: bool = True) -> dict:
    """What one transcript file loaded and used, for `walle context --usage`.

    Loaded: MCP servers (deferred tool names and instruction blocks), listed skills, built-in tools
    of the tools snapshot. Used: tool calls by name, Skill calls and slash commands, hook text.
    """
    r = {"start": "", "cwd": "", "entrypoint": "", "first_total": None,
         "tools": {}, "skills": {}, "slash": {}, "servers": {}, "server_tools": {}, "instr": {}, "display": {},
         "listed": [], "listing_bytes": None, "skill_lines": {}, "builtin_loaded": {}, "hooks": {},
         "failed": [], "needs_auth": []}
    tool_ids: set = set()
    hook_seen: dict = {}
    head = 0
    with path.open("rb") as fh:
        for raw in fh:
            head += 1
            if head > 40 and not any(m in raw for m in USAGE_MARKERS):
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict) or (main and d.get("isSidechain")):
                continue
            r["cwd"] = r["cwd"] or d.get("cwd") or ""
            r["start"] = r["start"] or d.get("timestamp") or ""
            r["entrypoint"] = r["entrypoint"] or d.get("entrypoint") or ""
            t = d.get("type")
            msg = d.get("message") or {}
            if t == "assistant":
                u = msg.get("usage") or {}
                if r["first_total"] is None and u:
                    tot = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                           + u.get("cache_read_input_tokens", 0))
                    r["first_total"] = tot or None
                for b in msg.get("content") or []:
                    if not isinstance(b, dict) or b.get("type") != "tool_use" or b.get("id") in tool_ids:
                        continue
                    tool_ids.add(b.get("id"))
                    name = str(b.get("name", ""))
                    r["tools"][name] = r["tools"].get(name, 0) + 1
                    if name == "Skill":
                        inp = b.get("input") or {}
                        k = str(inp.get("skill") or inp.get("command") or "?")
                        r["skills"][k] = r["skills"].get(k, 0) + 1
            elif t == "user":
                c = msg.get("content")
                texts = [c] if isinstance(c, str) else []
                if isinstance(c, list):
                    for b in c:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "text":
                            texts.append(b.get("text") or "")
                        elif b.get("type") == "tool_result":
                            cc = b.get("content")
                            texts += [cc] if isinstance(cc, str) else [
                                x.get("text") or "" for x in cc or [] if isinstance(x, dict) and x.get("type") == "text"]
                for s in texts:
                    if "<command-name>" in s:
                        for m in CMD_RE.findall(s):
                            r["slash"][m] = r["slash"].get(m, 0) + 1
            elif t == "attachment":
                a = d.get("attachment") or {}
                at = a.get("type")
                if at == "deferred_tools_delta":
                    for n in list(a.get("addedNames") or []) + list(a.get("readdedNames") or []):
                        _offer(r, str(n), "deferred")
                    for x in list(a.get("pendingMcpServers") or []) + list(a.get("needsAuthMcpServers") or []):
                        r["display"].setdefault(server_key(_name(x)), _name(x))
                    for field_, key in (("failedMcpServers", "failed"), ("needsAuthMcpServers", "needs_auth")):
                        for x in a.get(field_) or []:
                            k = server_key(_name(x))
                            if k not in r[key]:
                                r[key].append(k)
                elif at == "mcp_instructions_delta":
                    for n, blk in zip(a.get("addedNames") or [], a.get("addedBlocks") or []):
                        k = server_key(str(n))
                        r["display"].setdefault(k, str(n))
                        r["instr"][k] = max(r["instr"].get(k, 0), blen(str(blk)))
                elif at == "skill_listing":
                    names = [str(n) for n in a.get("names") or []]
                    r["listed"] = sorted(set(r["listed"]) | set(names))
                    if r["listing_bytes"] is None:
                        r["listing_bytes"] = blen(str(a.get("content") or ""))
                    for line in _listing_lines(str(a.get("content") or "")):
                        nm = line[2:].split(": ", 1)[0]
                        if nm in names:
                            r["skill_lines"][nm] = blen(line) + 1
                elif at == "prompt_snapshot" and isinstance(a.get("tools"), list) and not r["builtin_loaded"]:
                    for x in a["tools"]:
                        if not isinstance(x, dict):
                            continue
                        name = str(x.get("name", ""))
                        if name.startswith("mcp__"):
                            _offer(r, name, "loaded", blen(json.dumps(x, ensure_ascii=False)))
                        else:
                            r["builtin_loaded"][name] = blen(json.dumps(x, ensure_ascii=False))
                elif at == "hook_additional_context":
                    text = "\n".join(x for x in a.get("content") or [] if isinstance(x, str))
                    hook_seen[(a.get("toolUseID"), a.get("hookName"))] = (a.get("hookName") or "?", blen(text),
                                                                          hook_source(text))
                elif at == "hook_success":
                    content = str(a.get("content") or "")
                    if content.strip() and a.get("hookEvent") in ("UserPromptSubmit", "SessionStart"):
                        hook_seen.setdefault((a.get("toolUseID"), a.get("hookName")),
                                             (a.get("hookName") or "?", blen(content), hook_source(content)))
            elif t == "system" and d.get("subtype") == "stop_hook_summary":
                hac = d.get("hookAdditionalContext") or []
                if hac:
                    text = json.dumps(hac, ensure_ascii=False) if not isinstance(hac, str) else hac
                    hook_seen[(d.get("uuid"), "Stop")] = ("Stop", blen(text), hook_source(_strings(hac)[0] if _strings(hac) else ""))
    hooks: dict[str, list[int]] = {}
    for name, b, src in hook_seen.values():
        for key in (f"name:{name}", f"source:{src}"):
            e = hooks.setdefault(key, [0, 0])
            e[0] += 1
            e[1] += b
    r["hooks"] = hooks
    r["server_tools"] = {k: sorted(v) for k, v in r["server_tools"].items()}
    return r


def _offer(r: dict, name: str, how: str, size: int = 0):
    """Record one MCP tool the session was offered: deferred (a name for ToolSearch) or loaded (full schema)."""
    raw = mcp_server(name)
    if raw is None:
        return
    k = server_key(raw)
    r["display"].setdefault(k, raw)
    tools = r["server_tools"].setdefault(k, set())
    tool = name[len("mcp__") + len(raw) + 2:]
    if tool in tools:
        return
    tools.add(tool)
    e = r["servers"].setdefault(k, {"deferred": 0, "deferred_bytes": 0, "loaded": 0, "loaded_bytes": 0})
    if how == "deferred":
        e["deferred"] += 1
        e["deferred_bytes"] += blen(name) + 1
    else:
        e["loaded"] += 1
        e["loaded_bytes"] += size


def _listing_lines(content: str) -> list[str]:
    """The lines of a skill listing, one per skill (a description may go on over several lines)."""
    out: list[str] = []
    for line in content.split("\n"):
        if line.startswith("- ") and ": " in line:
            out.append(line)
        elif out:
            out[-1] += "\n" + line
    return out
