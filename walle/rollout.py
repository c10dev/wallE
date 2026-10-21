"""Read what a Codex rollout records about the context: the first call's sources, tool calls, skill use.

A rollout (~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<thread id>[_<page id>].jsonl, most of them
compressed to .jsonl.zst; older ones also in ~/.codex/archived_sessions) has one JSON object per
line, {"timestamp", "type", "payload"}. walle reads these lines:

| type, payload type        | what it records                                                          |
|---------------------------|--------------------------------------------------------------------------|
| session_meta              | thread id, originator, base_instructions, dynamic_tools, history_base    |
| response_item: message    | developer and user messages; content_item_kinds names each part          |
| response_item: *_call     | the model's tool calls (custom_tool_call, function_call, ...)            |
| event_msg: token_count    | last_token_usage of each model call                                      |
| event_msg: item_completed | finished items: McpToolCall, DynamicToolCall, CommandExecution, ...      |

Rules from the 463 rollouts on the author's machine (2026-10-21):
- Lines split on "\\n" only. str.splitlines() also splits on U+2028, which a JSON string can hold.
- 461 files are zstd-compressed. Python 3.11 has no zstd module: walle runs the zstd program when it
  is installed. Without it, walle skips those files and says how many it skipped.
- Files written before content_item_kinds existed (248) are sorted by the tag that starts each part,
  for example "<skills_instructions>" or "## Memory".
- item_completed can repeat an item id. walle counts each (type, id) once.
- A rollout whose session_meta has history_base resumes a thread. Its first call carries history that
  this file does not hold, so it is not a first call: walle leaves it out of first-call figures.
- A forked thread (forked_from_id) copies its parent's history into the file before its own first
  call. walle counts that part as "history".
- dynamic_tools marked deferLoading are not sent with the first call; their namespace line in
  "<tools>" is.
- The first call is the first token_count with last_token_usage. Its input is every item up to the last
  input item before that token_count (a developer, user or system message, or an agent message). The
  reasoning, text, tool calls and tool outputs after that item are the call's output and its results.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .transcript import Source, blen

SOURCES = ("base_instructions", "agents_md", "skill_listing", "app_context", "tool_namespaces", "plugins_apps",
           "permissions", "collaboration_mode", "multi_agent", "memory", "environment", "hook_context",
           "skill_bodies", "history", "first_prompt", "other")
FILE_RE = re.compile(r"^rollout-(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d)-([0-9a-f-]{36})(?:_([0-9a-f-]{36}))?\.jsonl(\.zst)?$")
# The tag that starts a part, for rollouts without content_item_kinds. Names are Codex's kinds where one
# exists; "app.thread_coordination" and "hook.additional_context" are walle's names.
TAGS = (
    ("<app-context>", "generic.developer_instructions"),
    ("<skills_instructions>", "host_skills.instructions"),
    ("<permissions instructions>", "permissions.instructions"),
    ("<collaboration_mode>", "collaboration_mode.instructions"),
    ("<apps_instructions>", "apps.instructions"),
    ("<plugins_instructions>", "plugins.usage_instructions"),
    ("<recommended_plugins>", "plugins.recommendations"),
    ("# AGENTS.md instructions", "agents_md.instructions"),
    ("<user_instructions>", "agents_md.instructions"),
    ("<environment_context>", "environments.environment_context"),
    ("<tools>", "tools.deferred_namespaces"),
    ("<multi_agent_mode>", "multi_agent.mode_instructions"),
    ("<multi_agent_role>", "multi_agent.role_instructions"),
    ("You are `/root`", "multi_agent.usage_hint"),
    ("## Memory", "memories.instructions"),
    ("<skill>", "skills.selected_skill_instructions"),
    ("<model_switch>", "model_switch.instructions"),
    ("<codex_internal_context", "goal.internal_context"),
    ("<turn_aborted>", "generic.turn_aborted"),
    ("<image_resize_notice", "images.resize_notice"),
    ("<codex_apps_", "additional_content.codex_apps"),
    ("<external_codex_apps_", "additional_content.codex_apps"),
    ("Thread coordination:", "app.thread_coordination"),
    ("PONYTAIL MODE", "hook.additional_context"),
    ("# Files pasted by the user", "user.text"),
)
SOURCE_OF = {
    "agents_md.instructions": "agents_md",
    "host_skills.instructions": "skill_listing",
    "app.thread_coordination": "app_context",
    "tools.deferred_namespaces": "tool_namespaces",
    "apps.instructions": "plugins_apps",
    "plugins.usage_instructions": "plugins_apps",
    "plugins.recommendations": "plugins_apps",
    "plugins.instructions": "plugins_apps",
    "permissions.instructions": "permissions",
    "collaboration_mode.instructions": "collaboration_mode",
    "memories.instructions": "memory",
    "environments.environment_context": "environment",
    "hook.additional_context": "hook_context",
    "skills.selected_skill_instructions": "skill_bodies",
}
OUTPUT_TYPES = {"reasoning", "function_call", "custom_tool_call", "local_shell_call", "web_search_call",
                "tool_search_call", "function_call_output", "custom_tool_call_output", "local_shell_call_output",
                "tool_search_output"}
CALL_TYPES = {"function_call", "custom_tool_call", "local_shell_call", "web_search_call", "tool_search_call"}
ROOT_RE = re.compile(r"^- `(r\d+)` = `([^`]+)`\s*$")
SKILL_RE = re.compile(r"^- ([\w.-]+(?::[\w.-]+)*): (.*?)(?: \(file: ([^()\n]+)\))?\s*$", re.S)
SKILL_PATH_RE = re.compile(r"[^\s\"'`()\[\]{}<>,;|]*SKILL\.md")
DOLLAR_RE = re.compile(r"\$([\w.-]+(?::[\w.-]+)?)")
SKILL_NAME_RE = re.compile(r"<name>\s*([^<]+?)\s*</name>")
AGENTS_FOR_RE = re.compile(r"^# AGENTS\.md instructions for (.+)$", re.M)


def zstd_program() -> str | None:
    return shutil.which("zstd")


def read(path: Path) -> bytes | None:
    """The bytes of a rollout; None when it is compressed and the zstd program is not installed or fails."""
    if path.suffix != ".zst":
        try:
            return path.read_bytes()
        except OSError:
            return None
    exe = zstd_program()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-dcq", "--", str(path)], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def records(data: bytes):
    """The JSON objects of a rollout, one per line. Lines split on \\n only."""
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict):
            yield d


def name_parts(path: Path) -> tuple[str, str | None] | None:
    """(thread id, page id) from a rollout file name; None when the name is not a rollout's."""
    m = FILE_RE.match(path.name)
    return (m.group(2), m.group(3)) if m else None


def rollout_files(root: Path) -> list[Path]:
    """Rollout files under a Codex home (sessions/, archived_sessions/) or any folder that holds them."""
    bases = [root / d for d in ("sessions", "archived_sessions") if (root / d).is_dir()] or [root]
    return sorted(p for b in bases for p in b.rglob("rollout-*") if p.is_file() and name_parts(p))


def part_kind(kinds, i: int, text: str, role: str) -> str:
    """The kind of the i-th part of a message: content_item_kinds when the rollout has it, else its tag."""
    k = kinds[i] if isinstance(kinds, list) and i < len(kinds) else None
    if isinstance(k, str) and k and k != "unknown":
        return k
    s = text.lstrip()
    for tag, name in TAGS:
        if s.startswith(tag):
            return name
    return "user.text" if role == "user" else "developer.untagged"


def source_of(kind: str, text: str) -> str:
    if kind in SOURCE_OF:
        return SOURCE_OF[kind]
    if kind == "generic.developer_instructions":
        # The Desktop app context, or developer instructions of a subagent or a config. The app's page and
        # time notes (additional_content.*) go to "other".
        return "app_context" if text.lstrip().startswith("<app-context>") else "other"
    if kind.startswith("multi_agent."):
        return "multi_agent"
    if kind.startswith("user."):
        return "first_prompt"
    return "other"


def parse_skill_listing(text: str) -> tuple[dict[str, str], list[dict]]:
    """Roots table and skill lines of a <skills_instructions> block.

    A line is "- name: description (file: rN/dir/SKILL.md)"; rN is a root of the table above it. Older
    blocks give the absolute path instead. Returns ({rN: root}, [{name, path, bytes}]).
    """
    roots: dict[str, str] = {}
    entries: list[str] = []
    for line in text.split("\n"):
        m = ROOT_RE.match(line)
        if m:
            roots[m.group(1)] = m.group(2)
            continue
        if line.startswith("- "):
            entries.append(line)
        elif entries and line and not line.startswith(("#", "<")):
            entries[-1] += "\n" + line
        elif entries and not line:
            entries.append("")  # a blank line ends an entry
    out = []
    for e in entries:
        m = SKILL_RE.match(e)
        if not e or not m:
            continue
        path = (m.group(3) or "").strip()
        head, _, rest = path.partition("/")
        if head in roots:
            path = roots[head].rstrip("/") + "/" + rest
        out.append({"name": m.group(1), "path": path, "bytes": blen(e) + 1})
    return roots, out


def _text(content) -> list[tuple[str, str]]:
    """(type, text) of the parts of a message content list."""
    out = []
    for c in content if isinstance(content, list) else []:
        if isinstance(c, dict):
            out.append((str(c.get("type") or ""), c.get("text") if isinstance(c.get("text"), str) else ""))
    return out


def _call_text(p: dict) -> str:
    """The input text of a tool call, for finding SKILL.md paths and for the history size."""
    for k in ("arguments", "input"):
        v = p.get(k)
        if isinstance(v, str):
            return v
        if v is not None:
            return json.dumps(v, ensure_ascii=False)
    a = p.get("action")
    return json.dumps(a, ensure_ascii=False) if a is not None else ""


def _output_text(p: dict) -> str:
    v = p.get("output")
    return v if isinstance(v, str) else (json.dumps(v, ensure_ascii=False) if v is not None else "")


def call_name(p: dict) -> str:
    t = p.get("type")
    if t == "web_search_call":
        return "web_search"
    if t == "tool_search_call":
        return "tool_search"
    name = str(p.get("name") or t)
    return f"{p['namespace']}.{name}" if p.get("namespace") else name


@dataclass
class Rollout:
    """One rollout file, read once."""
    path: str
    thread: str  # session_meta id (the thread id), else the id in the file name
    page: str | None  # the page id of a later page file (<thread>_<page>)
    meta: dict  # the first session_meta payload
    metas: int
    first_usage: dict | None  # last_token_usage of the first call
    inputs: list[dict]  # payloads of the response_items the first call sent
    model: str = ""
    cwd: str = ""
    items: list[dict] = field(default_factory=list, repr=False)  # every response_item payload
    completed: list[dict] = field(default_factory=list, repr=False)  # item_completed items, each (type, id) once

    @property
    def resumed(self) -> str | None:
        hb = self.meta.get("history_base")
        if isinstance(hb, dict):
            return str(hb.get("thread_id") or "?")
        return "?" if hb else None

    @property
    def forked_from(self) -> str | None:
        v = self.meta.get("forked_from_id")
        return str(v) if v else None

    @property
    def originator(self) -> str:
        return str(self.meta.get("originator") or "")

    @property
    def subagent(self) -> bool:
        src = self.meta.get("source")
        return isinstance(src, dict) and "subagent" in src

    @property
    def start(self) -> str:
        return str(self.meta.get("timestamp") or "")


def load(path: Path, data: bytes | None = None) -> Rollout | None:
    """Read a rollout. None when it cannot be read (see read())."""
    data = read(path) if data is None else data
    if data is None:
        return None
    ids = name_parts(path) or ("", None)
    meta: dict = {}
    metas = 0
    first_usage = None
    model = cwd = ""
    items: list[dict] = []
    before: list[dict] = []
    completed: list[dict] = []
    seen: set = set()
    for d in records(data):
        t = d.get("type")
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        if t == "session_meta":
            metas += 1
            if not meta:
                meta = p
        elif t == "turn_context":
            model = model or str(p.get("model") or "")
            cwd = cwd or str(p.get("cwd") or "")
        elif t == "response_item":
            items.append(p)
            if first_usage is None:
                before.append(p)
        elif t == "event_msg" and p.get("type") == "token_count" and first_usage is None:
            last = (p.get("info") or {}).get("last_token_usage") if isinstance(p.get("info"), dict) else None
            if isinstance(last, dict) and last.get("input_tokens") is not None:
                first_usage = last
        elif t == "event_msg" and p.get("type") == "item_completed":
            it = p.get("item") if isinstance(p.get("item"), dict) else {}
            key = (it.get("type"), it.get("id"))
            if it.get("id") is not None and key in seen:
                continue
            seen.add(key)
            completed.append(it)
    # The first call's input ends at its last input item; what follows is its output and the tool results.
    last_in = max((i for i, p in enumerate(before) if _is_input(p)), default=-1)
    inputs = before[:last_in + 1] if first_usage is not None else []
    return Rollout(str(path), str(meta.get("id") or ids[0]), ids[1], meta, metas, first_usage, inputs,
                   model, cwd or str(meta.get("cwd") or ""), items, completed)


def _is_input(p: dict) -> bool:
    t = p.get("type")
    if t == "message":
        return p.get("role") in ("developer", "user", "system")
    return t == "agent_message"


# ---------- the first call ----------

@dataclass
class CodexFirstCall:
    session: str
    path: str
    cwd: str
    start: str
    model: str
    originator: str
    version: str
    subagent: bool
    resumed: str | None  # the thread whose history this rollout resumes
    forked_from: str | None
    page: str | None
    measured: dict  # input, cached, cache_write, total tokens of the first call
    sources: list[Source]


def first_call(r: Rollout) -> CodexFirstCall | None:
    """Break the first call of a rollout into its recorded sources."""
    if r.first_usage is None:
        return None
    u = r.first_usage
    measured = {"input": int(u.get("input_tokens") or 0), "cached": int(u.get("cached_input_tokens") or 0),
                "cache_write": int(u.get("cache_write_input_tokens") or 0)}
    measured["total"] = measured["input"]  # input_tokens includes the cached part
    return CodexFirstCall(r.thread, r.path, r.cwd, r.start, r.model, r.originator, str(r.meta.get("cli_version") or ""),
                          r.subagent, r.resumed, r.forked_from, r.page, measured, _sources(r))


def _sources(r: Rollout) -> list[Source]:
    parts: dict[str, list[tuple[str, str]]] = {k: [] for k in SOURCES}  # source -> [(kind, text)]
    images = 0
    # The current turn starts after the last output item of the inputs: user text before it is history.
    turn = max((i for i, p in enumerate(r.inputs) if not _is_input(p)), default=-1)
    history: list[tuple[str, str]] = []
    for i, p in enumerate(r.inputs):
        t = p.get("type")
        if t == "message" and p.get("role") in ("developer", "user", "system"):
            kinds = (p.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds")
            for j, (ctype, text) in enumerate(_text(p.get("content"))):
                if ctype == "input_image":
                    images += 1
                    continue
                kind = part_kind(kinds, j, text, str(p.get("role")))
                src = source_of(kind, text)
                if src == "first_prompt" and i < turn:
                    history.append((kind, text))
                else:
                    parts[src].append((kind, text))
        elif t == "agent_message":
            text = "\n".join(x for _, x in _text(p.get("content"))) or _flat(p.get("content"))
            (history if i < turn else parts["first_prompt"]).append(("agent_message", text))
        elif t == "message":  # assistant text in a forked thread's history
            history.append(("assistant", "\n".join(x for _, x in _text(p.get("content")))))
        elif t in CALL_TYPES:
            history.append((t, _call_text(p)))
        elif t and t.endswith("_output"):
            history.append((t, _output_text(p)))
        elif t == "reasoning":
            summary = p.get("summary") if isinstance(p.get("summary"), list) else []
            history.append((t, "\n".join(str(s.get("text") or "") for s in summary if isinstance(s, dict))))
    parts["history"] = history

    out: list[Source] = []
    base = r.meta.get("base_instructions")
    base = base.get("text") if isinstance(base, dict) else base
    base = base if isinstance(base, str) else ""
    out.append(Source("base_instructions", blen(base), 1 if base else 0,
                      "before_first_call" if base else "not_recorded", base))
    # Dynamic tools: a tool marked deferLoading is sent as its namespace's line in <tools>. walle counts the
    # schema of every other dynamic tool as sent with the first call; the rollout does not show the request.
    sent, deferred = [], []
    for x in r.meta.get("dynamic_tools") or []:
        if not isinstance(x, dict):
            continue
        subs = x.get("tools") if isinstance(x.get("tools"), list) else [x]
        late = [y for y in subs if isinstance(y, dict) and y.get("deferLoading")]
        sent += [y for y in subs if isinstance(y, dict) and not y.get("deferLoading")]
        if late:
            deferred.append({"name": x.get("name"), "tools": len(late),
                             "bytes": sum(blen(json.dumps(y, ensure_ascii=False)) for y in late)})
    for kind in SOURCES[1:]:
        ps = parts[kind]
        texts = [x for _, x in ps] + ([json.dumps(x, ensure_ascii=False) for x in sent] if kind == "tool_namespaces" else [])
        text = "\n".join(texts)
        n = len(texts)
        detail: dict = {}
        if kind == "skill_listing":
            roots, skills = parse_skill_listing(text)
            detail = {"roots": roots, "skills": skills}
            n = len(skills)
        elif kind == "agents_md":
            detail = {"for": AGENTS_FOR_RE.findall(text), "project_doc": "--- project-doc ---" in text}
        elif kind == "tool_namespaces":
            detail = {"namespaces": namespace_names(text), "dynamic_deferred": deferred,
                      "dynamic_sent": [x.get("name") for x in sent]}
        elif kind == "first_prompt":
            detail = {"images": images}
        elif kind == "skill_bodies":
            detail = {"skills": [m for _, x in ps for m in SKILL_NAME_RE.findall(x)[:1]]}
        if kind in ("other", "history", "app_context", "plugins_apps", "multi_agent", "first_prompt"):
            counts: dict[str, int] = {}
            for k, _ in ps:
                counts[k] = counts.get(k, 0) + 1
            detail["kinds"] = counts
        recorded = "before_first_call" if n or (kind == "first_prompt" and images) else "not_recorded"
        # Bytes of the parts themselves: the newlines that join them for the estimator are not sent.
        out.append(Source(kind, sum(blen(x) for x in texts), n, recorded, text, detail))
    return out


def _flat(v) -> str:
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False) if v is not None else ""


def namespace_names(text: str) -> list[str]:
    """Names in a <tools> block: lines "- name: description"."""
    return [m.group(1) for m in re.finditer(r"^- ([^\s:]+):", text, re.M)]


def server_of_namespace(name: str) -> str:
    """A tool namespace as a server key: mcp__codex_apps__linear -> codex_apps:linear, codex_app -> dynamic:codex_app."""
    if name.startswith("mcp__"):
        rest = name[5:]
        server, _, tool = rest.partition("__")
        return f"{server}:{tool}" if server == "codex_apps" and tool else server
    return f"dynamic:{name}"


# ---------- loaded and used ----------

def usage_row(r: Rollout) -> dict:
    """What one rollout loaded and used, for `walle context --agent codex --usage`."""
    fc = first_call(r)
    src = {s.kind: s for s in fc.sources} if fc else {}
    listing = None
    # The skill list: from the first call, else the first one the rollout records (a resumed page).
    if fc and src["skill_listing"].count:
        listing = src["skill_listing"].detail
    else:
        for p in r.items:
            if p.get("type") == "message" and p.get("role") in ("developer", "user"):
                kinds = (p.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds")
                for j, (_, text) in enumerate(_text(p.get("content"))):
                    if part_kind(kinds, j, text, str(p.get("role"))) == "host_skills.instructions":
                        roots, skills = parse_skill_listing(text)
                        listing = {"roots": roots, "skills": skills}
                        break
            if listing:
                break
    skills = (listing or {}).get("skills", [])
    by_path = {s["path"]: s["name"] for s in skills if s["path"]}
    names = {s["name"] for s in skills}
    used: dict[str, int] = {}
    unlisted: dict[str, int] = {}
    tools: dict[str, int] = {}
    for p in r.items:
        t = p.get("type")
        if t in CALL_TYPES:
            name = call_name(p)
            tools[name] = tools.get(name, 0) + 1
            hits = set()
            for tok in SKILL_PATH_RE.findall(_call_text(p)):
                n = match_skill(tok, by_path)
                if n:
                    hits.add(n)
                else:
                    key = "/".join(tok.split("/")[-2:])
                    unlisted[key] = unlisted.get(key, 0) + 1
            for n in hits:
                used[n] = used.get(n, 0) + 1
        elif t == "message" and p.get("role") in ("developer", "user"):
            kinds = (p.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds")
            for j, (_, text) in enumerate(_text(p.get("content"))):
                kind = part_kind(kinds, j, text, str(p.get("role")))
                if kind == "skills.selected_skill_instructions":
                    m = SKILL_NAME_RE.search(text)
                    n = _listed(m.group(1), names) if m else None
                    if n:
                        used[n] = used.get(n, 0) + 1
                elif kind == "user.text" and "$" in text:
                    for m in set(DOLLAR_RE.findall(text)):
                        n = _listed(m, names)
                        if n:
                            used[n] = used.get(n, 0) + 1
    servers: dict[str, dict[str, int]] = {}
    for it in r.completed:
        if it.get("type") == "McpToolCall":
            server, tool = str(it.get("server") or "?"), str(it.get("tool") or "?")
            if server == "codex_apps" and "." in tool:
                app, _, tool = tool.partition(".")
                server = f"codex_apps:{app}"
        elif it.get("type") == "DynamicToolCall":
            server, tool = f"dynamic:{it.get('namespace') or it.get('tool')}", str(it.get("tool") or "?")
        else:
            continue
        e = servers.setdefault(server, {})
        e[tool] = e.get(tool, 0) + 1
    loaded: dict[str, int] = {}
    if fc:
        tn = src["tool_namespaces"].detail
        for n in tn.get("namespaces", []):
            loaded[server_of_namespace(n)] = 0
        for x in tn.get("dynamic_deferred", []):
            loaded[f"dynamic:{x['name']}"] = x["tools"]
        for n in tn.get("dynamic_sent", []):
            loaded[f"dynamic:{n}"] = 1
    return {
        "thread": r.thread, "page": r.page, "start": r.start, "originator": r.originator, "subagent": r.subagent,
        "resumed": r.resumed, "forked": r.forked_from is not None, "cwd": r.cwd, "model": r.model,
        "first_input": fc.measured["input"] if fc else None,
        "sources": {s.kind: s.bytes for s in fc.sources} if fc else {},
        "listed": sorted(names), "skill_lines": {s["name"]: s["bytes"] for s in skills},
        "skill_paths": {s["name"]: s["path"] for s in skills},
        "listing_bytes": sum(s["bytes"] for s in skills) if listing else None,
        "skills_used": used, "skill_reads_unlisted": unlisted, "tools": tools,
        "servers_used": servers, "servers_loaded": loaded,
    }


def _listed(name: str, names: set[str]) -> str | None:
    name = name.strip()
    if name in names:
        return name
    short = [n for n in names if n.rsplit(":", 1)[-1] == name]
    return short[0] if len(short) == 1 else None


def match_skill(token: str, by_path: dict[str, str]) -> str | None:
    """The listed skill whose SKILL.md a path in a tool call names: the same path, or one path ends with the
    other; else the only listed skill with the same <folder>/SKILL.md."""
    tok = token.replace("\\", "/")
    tok = tok[2:] if tok.startswith("./") else tok
    exact = [n for p, n in by_path.items() if p == tok or p.endswith("/" + tok.lstrip("/")) or tok.endswith(p)]
    if len(set(exact)) == 1:
        return exact[0]
    tail = "/" + "/".join(tok.split("/")[-2:])
    near = {n for p, n in by_path.items() if p.endswith(tail)}
    return near.pop() if len(near) == 1 else None
