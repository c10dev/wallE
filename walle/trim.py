"""walle trim --plan: what to take out of every session's context, what it saves, and how to undo it.

Inputs (read only):
- loaded against used over a period (context.usage, the data of `walle context --usage`): MCP servers,
  claude.ai connectors, plugins, skills and hooks.
- the newest scan in the runs folder: DELETE items (a line loaded twice) and MOVE items (a large layer).
- keep.json: kept items and kept sources leave the plan.
- the keys of ~/.claude.json, ~/.claude/settings.json and installed_plugins.json (key names only;
  values can hold secrets), and the claude.ai plugins synced to ~/.claude/plugins/synced/ (manifest.json).

Output: runs/<id>/plan.json and plan.md with one row per action. Each row has the change (a settings key,
a patch or a place in an app), who can make it (walle, a patch, or the user in an app), the expected
saving as a lower bound in bytes-v2 tokens per session, the sessions that loaded and used the source,
the risk, the undo and the check for a later session. An instruction-file change is a patch file in
runs/<id>/patches/; walle never edits an instruction file. Rows that need an answer to "does this
user's work need it?" go to judge batch files in runs/<id>/judge/ (judge.py format: no API key, no
network); `walle trim --import` reads the answers back into the plan.

Each row that walle can apply records its target file (`change.path`) and the sha256 of that file when
the plan read it (`change.sha256`; `targets` lists them once per file). walle trim changes nothing:
`walle apply <run> --only <id>` changes the rows the user picks and refuses a target whose hash is not the
plan's (walle/apply.py). Measured values stay out of the scan verdict, Start here and the alarm (brief 2.1).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, context, estimate, judge, keep, runs, skills
from .collect import HOME, find_checkout
from .triage import _id, norm_id

SCHEMA = 1
# A source is proposed when it loaded in at least MIN_SESSIONS sessions of the period, sessions used it in
# fewer than USED_SHARE of them, and it had fewer calls than sessions that loaded it (less than one call per
# session). The second gate keeps a source that few sessions use but use heavily, such as the Supabase
# connector (53 of 582 sessions, 788 calls).
MIN_SESSIONS = 10
USED_SHARE = 0.10
MIN_TOKENS = 20  # rows that save fewer tokens per session (lower bound) are left out
HOOK_MIN_SESSIONS = 3
HOOK_MIN_BYTES = 20_000
# Safety plugins: never proposed (plan 5.1, T4).
PROTECTED_PLUGINS = ("secret-redact", "compute-pool")
# Lower bound of the rate: the low end of the bytes-v2 range of each source kind. Prose rows (skill lines,
# hook text) use the ASCII rate, the lower of the two prose rates.
LOW = {k: v["range"][0] for k, v in estimate.BYTES_V2.items()}
SKILL_OVERRIDE = "user-invocable-only"  # hides the skill from the model; /name still works
# Host MCP servers that the user can switch off in the host app. Other host servers are the app's own
# plumbing (ccd_*, terminal, visualize): report only.
HOST_SWITCHES = {
    "computer-use": "the Claude desktop app's settings: turn computer use off",
    "claude-in-chrome": "the Claude in Chrome extension: disconnect it from Claude Code",
}
# walle cannot see app menus: a place in an app is from the docs and the app's tool names.
UNCONFIRMED = "walle has not confirmed the menu path"
SETTINGS = "~/.claude/settings.json"
CLAUDE_JSON = "~/.claude.json"
# Docs that the plugin and skill rows cite (read 2026-10-21).
DOC_PLUGIN_DISABLE = "https://code.claude.com/docs/en/plugins/loading#control-which-synced-plugins-load"
DOC_SKILL_OVERRIDES = "https://code.claude.com/docs/en/skills#override-skill-visibility-from-settings"
DOC_ORG_PLUGINS = "https://claude.com/docs/plugins/admin"
SYNCED_KEY = "synced"  # enabledPlugins key of a claude.ai plugin in Claude Code: "<name>@synced"
# Surfaces are the "entrypoint" of a session's transcript. For synced claude.ai skills and synced plugins, the
# saving floor counts only CONFIRMED surfaces; the other surfaces are a separate, unconfirmed number.
CONFIRMED = ("cli", "sdk-cli")
DESKTOP = "claude-desktop"
SURFACES = (*CONFIRMED, DESKTOP)
SKILL_DESKTOP = ("claude-desktop loads the synced claude.ai skills as a plugin. Measured on claude-desktop 2.1.299, "
                 "2026-10-21: a skill was still listed after its skillOverrides key was set (14 keys set at 03:10, "
                 "still listed in the 3 desktop sessions started after 03:11).")
SYNCED_SKILL_NOTE = ("cli and sdk-cli load the synced claude.ai skills as user skills, where skillOverrides applies. "
                     "This comes from reading the Claude Code 2.1.303 code; no cli or sdk-cli session has confirmed it "
                     "yet. " + SKILL_DESKTOP + " The saving counts only cli and sdk-cli sessions.")
PLUGIN_DESKTOP = ("the docs and the Claude Code code confirm enabledPlugins[\"<name>@synced\"] for cli and sdk-cli; "
                  "its effect on claude-desktop is not confirmed.")
JUDGE_CACHE = "trim-judge.json"
RUBRIC_VERSION = 1
RUBRIC = """You decide, for one user, whether a source that loads into their coding agent's sessions is worth what it costs.
Each item is one source: a claude.ai connector, an MCP server, a plugin, a skill, a hook, or a section of an instruction file.
"usage" says how many sessions of the period loaded the source and used it. "change" is the action walle proposes; "risk" is what can stop working.
Judge from this batch file and what you know of the user's work. You may read the user's instruction files. Do not open or change any settings file.

type "need":
- real: true when the user's work does not need the source at the start of every session, so the change is worth making. false when the work needs it.
- keep: "neither" to make the change, "a" to keep the source as it is, "ask" when only the user can tell (for example rare but important work).
- A source that sessions used is not needed only when its uses were one-off or another source does the same job.

fix: one line of at most 25 words: the change, or a smaller one (for example a project scope instead of every folder). Write "-" when real is false.
why: at most 15 words.
Give one verdict for each id.
Write only a JSON object of the form {"verdicts": [{"id", "real", "keep", "fix", "why"}, ...]} to the answer file. Do not edit any other file."""


class TrimError(Exception):
    pass


# ---------------------------------------------------------------------- numbers
def prose_min(text: str) -> int:
    """Lower bound of the bytes-v2 tokens of prose: each byte at the low end of its rate."""
    ascii_b = sum(1 for c in text if ord(c) < 128)
    other_b = len(text.encode()) - ascii_b
    return round(ascii_b * LOW["prose_ascii"] + other_b * LOW["prose_other"])


def server_min(s: dict) -> int:
    """Lower bound of the tokens a server adds to a session that loads it (median bytes x low rate)."""
    return round(s["name_bytes_median"] * LOW["deferred_tools"] + s["schema_bytes_median"] * LOW["mcp_tools"]
                 + s["instr_bytes_median"] * LOW["mcp_instructions"])


def gate(loaded: int, used: int, calls: int) -> bool:
    return loaded >= MIN_SESSIONS and used < USED_SHARE * loaded and calls < loaded


def row_id(kind: str, key: str) -> str:
    return _id(["trim", kind, key])


def file_sha256(path: Path) -> str | None:
    """The sha256 of a file's bytes, or None when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def target_path(file: str, home: Path) -> Path:
    """The absolute path of a change's file: "~/" is the home walle reads (not $HOME, so tests can move it)."""
    return home / file[2:] if file.startswith("~/") else Path(file)


_ABSENT = object()


def _at(doc, keys: list[str]):
    for k in keys:
        if not isinstance(doc, dict) or k not in doc:
            return _ABSENT
        doc = doc[k]
    return doc


def already_set(c: dict, doc: dict) -> bool:
    """The file already has the state that the change makes: walle apply will report it as "already"."""
    now = _at(doc, [str(k) for k in c["keys"]])
    if c["op"] == "set":
        return now is not _ABSENT and now == c["value"] and type(now) is type(c["value"])
    if c["op"] == "remove":
        return now is _ABSENT
    return now is _ABSENT and _at(doc, [str(k) for k in c["to"]]) is not _ABSENT


def record_targets(rows: list[dict], home: Path) -> dict[str, str | None]:
    """Write the path and the sha256 of each settings file that a row changes, as the plan reads it now.

    walle apply refuses a target whose hash differs: the plan was made for that file as it was.
    A row whose key already has the planned state gets "already": true. The sessions of the period
    can be older than that change, so the row stays in the plan; mark_set_keys gives its status per
    entrypoint from the sessions after the change.
    """
    targets: dict[str, str | None] = {}
    docs: dict[str, dict] = {}
    for r in rows:
        c = r["change"]
        if c.get("kind") != "settings_key":
            continue
        path = str(target_path(c["file"], home))
        if path not in targets:
            targets[path] = file_sha256(Path(path))
            docs[path] = _json(Path(path))
        c["path"], c["sha256"] = path, targets[path]
        c["already"] = already_set(c, docs[path])
    return targets


# ---------------------------------------------------------------------- what is on disk (key names only)
def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def configured_servers(home: Path, folders: set[str]) -> dict[str, dict]:
    """Server key -> where a file configures it: {file, keys (the JSON key path), scope}.

    Reads only key names of mcpServers: the values hold commands, env and headers (secrets).
    """
    out: dict[str, dict] = {}
    cj = _json(home / ".claude.json")
    for name in (cj.get("mcpServers") or {}) if isinstance(cj.get("mcpServers"), dict) else []:
        out.setdefault(context.server_key(str(name)), {"file": CLAUDE_JSON, "keys": ["mcpServers", str(name)],
                                                        "scope": "every folder"})
    projects = cj.get("projects") if isinstance(cj.get("projects"), dict) else {}
    for path, p in sorted(projects.items()):
        if isinstance(p, dict) and isinstance(p.get("mcpServers"), dict):
            for name in p["mcpServers"]:
                out.setdefault(context.server_key(str(name)),
                               {"file": CLAUDE_JSON, "keys": ["projects", path, "mcpServers", str(name)], "scope": path})
    seen: set[Path] = set()
    for f in sorted(folders):
        d = Path(f)
        for x in [d, *d.parents]:
            if x in seen:
                continue
            seen.add(x)
            mcp = _json(x / ".mcp.json").get("mcpServers")
            for name in mcp if isinstance(mcp, dict) else []:
                out.setdefault(context.server_key(str(name)), {"file": str(x / ".mcp.json"),
                                                                "keys": ["mcpServers", str(name)], "scope": str(x)})
    return out


def synced_plugins(home: Path) -> dict[str, dict]:
    """claude.ai plugins that claude.ai syncs to Claude Code: plugin name -> {preference}.

    `claude plugin list` shows them as "Synced from claude.ai", with the key <name>@synced. walle reads them
    from disk: the sync bucket in use (~/.claude/plugins/synced/<org>_<account>/, the one that skills.py
    reads), its manifest.json and one folder per plugin (<name> or <name>~gN). preference is the
    installationPreference that the organization set ("available" was seen), or None.
    """
    out: dict[str, dict] = {}
    for bucket in skills._buckets(home / ".claude" / "plugins" / "synced", None):
        listed = _json(bucket / "manifest.json").get("plugins")
        prefs = {str(p["name"]): p.get("installationPreference") for p in listed if isinstance(p, dict)
                 and p.get("name")} if isinstance(listed, list) else {}
        for d in sorted(bucket.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            base = re.sub(r"~g\d+$", "", d.name)
            pref = prefs.get(base) or _json(d.with_name(d.name + ".meta.json")).get("installation_preference")
            e = out.setdefault(skills._plugin_name(d, base), {"preference": None})
            e["preference"] = e["preference"] or pref
        for name, pref in prefs.items():
            if not any(d.name == name or d.name.startswith(name + "~g") for d in bucket.iterdir()):
                out.setdefault(name, {"preference": pref})
    return out


def required(pref) -> bool:
    """The organization requires the plugin: members cannot turn it off (the admin doc's "Required")."""
    return isinstance(pref, str) and "required" in pref.lower()


def plugin_catalog(home: Path) -> dict[str, dict]:
    """Plugin name -> {sources, key (the enabledPlugins key), synced, preference}.

    key is name@marketplace for an installed plugin, and name@synced for a claude.ai plugin that is synced
    to Claude Code (not to an installed plugin of the same name). A plugin that only the desktop host keeps
    (~/.claude/remote/plugins) has no key.
    """
    cat: dict[str, dict] = {}

    def entry(name: str) -> dict:
        return cat.setdefault(name, {"sources": set(), "key": None, "synced": False, "preference": None})

    for _root, source, name in skills.plugin_roots(home):
        entry(name)["sources"].add(source)
    installed = _json(home / ".claude" / "plugins" / "installed_plugins.json").get("plugins") or {}
    for key in installed if isinstance(installed, dict) else []:
        e = entry(str(key).split("@", 1)[0])
        e["key"] = str(key)
        e["sources"].add("plugin")
    for name, info in synced_plugins(home).items():
        e = entry(name)
        e["sources"].add("synced_plugin")
        e["preference"] = info["preference"]
        if e["key"] is None:
            e["key"], e["synced"] = f"{name}@{SYNCED_KEY}", True
    return cat


def plugin_skill(name: str, plugins: dict[str, dict]) -> bool:
    """name is a skill of a real plugin: <plugin>:* of a plugin on disk that is not the claude.ai skill sync.

    A synced claude.ai skill (anthropic-skills:*) is not one. A subfolder skill ("atc:status") also has a
    prefix, but no plugin of that name.
    """
    if ":" not in name:
        return False
    prefix = name.split(":", 1)[0]
    return prefix != skills.SYNCED_SKILL_PREFIX and prefix in plugins


def _user_skill(name: str, home: Path, folders: set[str]) -> str | None:
    """The SKILL.md of a skill without a prefix: the user's (~/.claude/skills) or a project's, else None."""
    p = home / ".claude" / "skills" / name / "SKILL.md"
    if p.is_file():
        return str(p)
    for f in sorted(folders):
        for x in [Path(f), *Path(f).parents]:
            q = x / ".claude" / "skills" / name / "SKILL.md"
            if q.is_file():
                return str(q)
    return None


# ---------------------------------------------------------------------- rows from usage
def _row(kind: str, key: str, name: str, action: str | None, title: str, change: dict, applies: str,
         where: str, per_session: int, sessions: dict, unused_sessions: int, risk: tuple[str, str],
         undo: str, check: str, need: dict | None, extra: dict | None = None) -> dict:
    """One plan row. The id comes from kind and key, so a kept row matches the next plan's row."""
    row = {
        "id": row_id(kind, key), "group": "source", "action": action,
        "source": {"type": (need or {}).get("source") or kind, "key": key, "name": name},
        "title": title, "change": change, "applies": applies, "where": where,
        "saving": {"tokens_per_session_min": per_session, "unused_sessions": unused_sessions,
                   "unused_tokens_min": per_session * unused_sessions,
                   "per": "session that loads it", "estimator": "bytes-v2, low end of the rate range"},
        "sessions": sessions, "risk": {"level": risk[0], "why": risk[1]}, "undo": undo, "check": check,
        "judge": {"needed": need is not None, "verdict": None},
    }
    if need is not None:
        row["_need"] = need
    if extra:
        row.update(extra)
    return row


def _count(n: int, noun: str) -> str:
    return f"{n:,} {noun}" + ("" if n == 1 else "s")


def _usage_text(loaded: int, used: int, calls: int, n: int, what: str = "sessions") -> str:
    return f"loaded in {loaded:,} of {n:,} {what}, used in {used:,} ({calls:,} calls)"


def server_rows(u: dict, home: Path, configured: dict[str, dict], plugins: dict[str, dict]) -> tuple[list, list]:
    rows, report_only = [], []
    n = u["sessions"]
    for s in u["servers"]:
        key = s["key"]
        if key.startswith("plugin:"):
            continue  # folded into the plugin's row
        loaded, used, calls = s["sessions_loaded"], s["sessions_used"], s["calls"]
        if not gate(loaded, used, calls):
            continue
        per = server_min(s)
        if per < MIN_TOKENS:
            continue
        unused = loaded - s["sessions_loaded_and_used"]
        name = s["server"]
        sess = {"period": n, "loaded": loaded, "used": used, "calls": calls,
                "tools_called": s["tools_called"], "tools_offered": s["tools_offered"]}
        usage_line = _usage_text(loaded, used, calls, n)
        check = (f"In a session started after the change, `walle context --last` lists no deferred tool names "
                 f"and no instruction block of {name}.")
        need = {"source": None, "name": name, "used": bool(calls),
                "context": {"usage": usage_line, "tools_called": [t for t, _c in s.get("top_called") or []]}}
        kind = context.server_kind(key, configured)
        if kind == "local":
            conf = configured[key]
            used_in = [c for c, _n in s.get("used_in") or [] if c]
            narrower = _common_folder(used_in) if used_in else None
            keys = conf["keys"]
            if conf["file"] == CLAUDE_JSON and narrower and narrower != conf["scope"] and keys[0] == "projects":
                title = f"Move the MCP server {name} to the folder that uses it"
                change = {"kind": "settings_key", "file": CLAUDE_JSON, "op": "move", "keys": keys,
                          "to": ["projects", narrower, "mcpServers", keys[-1]]}
                undo = f"Move the key back to {_keys(keys)} (walle apply keeps a backup)."
                risk = ("medium", f"sessions outside {narrower} lose {name}. The desktop app rewrites "
                                  f"{CLAUDE_JSON} while it runs: change it with the app closed.")
                where = f"{CLAUDE_JSON} {_keys(keys)} -> {_keys(change['to'])}"
            elif conf["file"] == CLAUDE_JSON:
                title = f"Remove the MCP server {name} from {conf['scope']}"
                change = {"kind": "settings_key", "file": CLAUDE_JSON, "op": "remove", "keys": keys}
                undo = f"Put the key back at {_keys(keys)} (walle apply keeps a backup)."
                risk = ("medium" if calls else "low",
                        f"tasks that call {name} fail until the key is back. The desktop app rewrites "
                        f"{CLAUDE_JSON} while it runs: change it with the app closed.")
                where = f"{CLAUDE_JSON} {_keys(keys)}"
            else:  # a project's .mcp.json: a file in the project, so a patch, written by the agent
                title = f"Remove the MCP server {name} from {conf['file']}"
                change = {"kind": "patch", "patch": None, "file": conf["file"],
                          "how": f"remove {_keys(keys)} from {conf['file']} in the repository"}
                undo = "git revert of the commit that removed it."
                risk = ("medium" if calls else "low", f"tasks in {conf['scope']} that call {name} fail.")
                where = f"{conf['file']} {_keys(keys)}"
                need["source"] = "project MCP server"
                rows.append(_row("server", key, name, "T2", title, change, "patch", where, per, sess, unused,
                                 risk, undo, check, need))
                continue
            need["source"] = "local MCP server"
            rows.append(_row("server", key, name, "T2", title, change, "walle", where, per, sess, unused,
                             risk, undo, check, need, {"used_in": used_in}))
        elif kind == "connector":
            title = f"Disconnect the claude.ai connector {name}"
            change = {"kind": "app", "app": "claude.ai", "what": f"disconnect {name}"}
            where = (f"claude.ai Settings > Connectors > {name.replace('claude.ai ', '')} ({UNCONFIRMED}). It loads in "
                     "every surface signed in to the account; the Claude desktop app also has a per-session connector toggle.")
            undo = "Connect it again in the same place and sign in to the service again."
            risk = ("medium" if calls else "low",
                    f"tasks that need {name} fail until it is connected again; {used:,} sessions used it.")
            need["source"] = "claude.ai connector"
            rows.append(_row("server", key, name, "T1", title, change, "manual", where, per, sess, unused,
                             risk, undo, check, need))
        elif key in HOST_SWITCHES:
            title = f"Turn off {name} in the host app"
            change = {"kind": "app", "app": "Claude desktop app", "what": f"turn off {name}"}
            where = f"{HOST_SWITCHES[key][0].upper()}{HOST_SWITCHES[key][1:]} ({UNCONFIRMED})."
            need["source"] = "host app MCP server"
            rows.append(_row("server", key, name, "T1", title, change, "manual", where, per, sess, unused,
                             ("medium" if calls else "low", f"tasks that use {name} stop working until it is on again."),
                             "Turn it on again in the same place.", check, need))
        else:  # a server of the host app: no file key walle knows
            report_only.append({"type": "host MCP server", "name": name, "sessions": sess,
                                "tokens_per_session_min": per,
                                "why": "the host app adds it; walle knows no settings key or file for it. "
                                       "Look for a switch in the app's settings."})
    return rows, report_only


def _keys(keys: list[str]) -> str:
    return "".join(f'["{k}"]' if i else k for i, k in enumerate(keys))


def _common_folder(folders: list[str]) -> str | None:
    """The deepest folder that holds every one of folders, else None."""
    parts = [Path(f).parts for f in folders]
    common = []
    for xs in zip(*parts):
        if len(set(xs)) != 1:
            break
        common.append(xs[0])
    return str(Path(*common)) if len(common) > 1 else None


def _plugin_parts(u: dict) -> dict[str, dict]:
    """Plugin name -> its listed skills and loaded MCP servers in a usage block."""
    by_name: dict[str, dict] = defaultdict(lambda: {"skills": [], "servers": []})
    for s in u["skills"]:
        if ":" in s["skill"]:
            by_name[s["skill"].split(":", 1)[0]]["skills"].append(s)
    for s in u["servers"]:
        if s["key"].startswith("plugin:"):
            by_name[s["key"].split(":")[1]]["servers"].append(s)
    return by_name


def _plugin_counts(parts: dict | None) -> tuple[int, int, int]:
    """(sessions that loaded the plugin, sessions that used it, calls) of one plugin's parts."""
    sks, svs = (parts or {}).get("skills") or [], (parts or {}).get("servers") or []
    sessions = max([s["sessions_listed"] for s in sks] + [s["sessions_loaded"] for s in svs] + [0])
    calls = sum(s["calls"] for s in sks) + sum(s["calls"] for s in svs)
    used = min(sessions, sum(s["sessions_used"] for s in sks) + sum(s["sessions_used"] for s in svs))
    return sessions, used, calls


def split_saving(row: dict, per: int, counts: dict[str, tuple[int, int]], why: str) -> None:
    """Count the saving floor only on the confirmed surfaces (cli, sdk-cli).

    counts is entrypoint -> (sessions that loaded the source, sessions that used it). The sessions of the other
    surfaces (claude-desktop and any other entrypoint) are a separate, unconfirmed number with its reason.
    """
    conf = sum(max(0, n - k) for ep, (n, k) in counts.items() if ep in CONFIRMED)
    other = {ep: n - k for ep, (n, k) in sorted(counts.items()) if ep not in CONFIRMED and n - k > 0}
    sv = row["saving"]
    sv["unused_sessions"], sv["unused_tokens_min"] = conf, per * conf
    sv["confirmed_surfaces"] = list(CONFIRMED)
    total = sum(other.values())
    sv["unconfirmed"] = {"sessions": other, "unused_sessions": total, "unused_tokens_min": per * total, "why": why}
    row["sessions"]["by_entrypoint"] = {ep: {"loaded": n, "used": k} for ep, (n, k) in sorted(counts.items())}


def plugin_rows(u: dict, plugins: dict[str, dict], by_ep: dict[str, dict] | None = None) -> list[dict]:
    """One row per plugin: its skills and MCP servers go together.

    An installed plugin and a claude.ai plugin synced to Claude Code are one enabledPlugins key (walle).
    A plugin that only the desktop app keeps and a plugin that the organization requires are manual.
    walle never offers skillOverrides for a plugin skill: Claude Code does not apply it to plugin skills
    (DOC_SKILL_OVERRIDES). The synced claude.ai skills (anthropic-skills:*) are not a plugin here: each one
    has its own skill row (skill_rows).
    """
    n = u["sessions"]
    by_name = _plugin_parts(u)
    ep_parts = {ep: _plugin_parts(ub) for ep, ub in (by_ep or {}).items()}
    rows = []
    for name, parts in sorted(by_name.items()):
        if name == skills.SYNCED_SKILL_PREFIX or name in PROTECTED_PLUGINS:
            continue  # claude.ai skills get one row each; safety plugins stay
        if name not in plugins and not parts["servers"]:
            continue  # a subfolder skill ("atc/controller:tick"): it loads on demand
        sks, svs = parts["skills"], parts["servers"]
        sessions, used, calls = _plugin_counts(parts)
        if not gate(sessions, used, calls):
            continue
        skill_tok = sum(s["line_bytes_median"] * LOW["prose_ascii"] * s["sessions_listed"] for s in sks)
        server_tok = sum(server_min(s) * s["sessions_loaded"] for s in svs)
        per = round((skill_tok + server_tok) / sessions) if sessions else 0
        if per < MIN_TOKENS:
            continue
        cat = plugins.get(name) or {"sources": set(), "key": None, "synced": False, "preference": None}
        sess = {"period": n, "loaded": sessions, "used": used, "calls": calls, "skills": len(sks),
                "mcp_servers": len(svs), "sessions_failed": max([s["sessions_failed"] for s in svs] + [0])}
        check = (f"In a session started after the change, `walle context --last` lists no skill named {name}:... "
                 f"and no MCP server plugin:{name}:...")
        used_skills = [f"{s['skill'].split(':', 1)[1]} ({s['calls']:,} calls)"
                       for s in sorted(sks, key=lambda s: (-s["calls"], s["skill"])) if s["calls"]]
        need = {"source": "plugin", "name": name, "used": bool(calls),
                "context": {"usage": _usage_text(sessions, used, calls, n),
                            "skills": [s["skill"].split(":", 1)[1] for s in sks][:40],
                            "skills_used": used_skills[:10],
                            "mcp_servers": [s["server"] for s in svs]}}
        with_used = f", with the skills that sessions used ({', '.join(used_skills[:5])})" if used_skills else ""
        whole = (f"the whole plugin goes: its {_count(len(sks), 'skill')}, {_count(len(svs), 'MCP server')} and any "
                 f"hooks{with_used}. walle cannot turn off one skill of a plugin: Claude Code does not apply "
                 f"skillOverrides to plugin skills ({DOC_SKILL_OVERRIDES}).")
        level = "high" if used_skills and calls >= 10 else "medium"
        key = cat["key"]
        if key and ("plugin" in cat["sources"] or (cat["synced"] and not required(cat["preference"]))):
            change = {"kind": "settings_key", "file": SETTINGS, "op": "set", "keys": ["enabledPlugins", key],
                      "value": False}
            where = f'{SETTINGS} enabledPlugins["{key}"] = false'
            title = f"Turn off the plugin {name}"
            if cat["synced"]:
                title = f"Turn off the claude.ai plugin {name} in Claude Code"
                need["source"] = "claude.ai plugin"
                where += (f": the key that `claude plugin disable {key}` and the /plugin Installed tab write "
                          f"({DOC_PLUGIN_DISABLE}).")
                whole += (" An organization-required plugin ignores the key. The docs do not confirm that the key "
                          "survives the next sync. The docs and the Claude Code code confirm the key for cli and "
                          "sdk-cli; its effect on claude-desktop is not confirmed: check with walle context --last.")
                check += " Check a claude-desktop session too: its effect there is not confirmed."
            row = _row("plugin", name, name, "T4", title, change, "walle", where, per, sess, sessions - used,
                       (level, whole), f'Set enabledPlugins["{key}"] back to true, or walle undo.', check, need)
            row["_listed"] = ("plugin", name)
            if cat["synced"]:
                counts = {ep: _plugin_counts(p.get(name))[:2] for ep, p in ep_parts.items()}
                split_saving(row, per, counts, PLUGIN_DESKTOP)
            rows.append(row)
        else:
            if cat["synced"]:  # required by the organization
                where = (f"Your organization requires {name}: Claude Code refuses to turn it off, and only the "
                         f"organization Owner can change it on claude.ai ({DOC_ORG_PLUGINS}).")
            elif cat["sources"]:
                where = (f"walle finds {name} only in the desktop app's copies (~/.claude/remote/plugins), not among "
                         "the plugins that claude.ai syncs to Claude Code, so no settings key turns it off. On claude.ai "
                         "a member can turn off a plugin that the Owner set to \"Installed by default\" and remove one "
                         "that is \"Available to install\"; a \"Required\" plugin needs the organization Owner "
                         f"({DOC_ORG_PLUGINS}).")
            else:
                where = "walle did not find this plugin on disk. Find where it is installed: /plugin in Claude Code."
            change = {"kind": "app", "app": "claude.ai", "what": f"turn off the plugin {name}"}
            rows.append(_row("plugin", name, name, "T3", f"Turn off the plugin {name}", change, "manual", where,
                             per, sess, sessions - used, (level, whole), "Turn it on again in the same place.", check,
                             need))
    return rows


def skill_rows(u: dict, plugins: dict[str, dict], home: Path, folders: set[str],
               by_ep: dict[str, dict] | None = None) -> list[dict]:
    """A skill that no session called: skillOverrides hides it from the model.

    A user skill, a built-in skill and a synced claude.ai skill (anthropic-skills:*) get a skillOverrides row.
    A skill of a real plugin (<plugin>:* of a plugin that is not the claude.ai skill sync) gets none: Claude
    Code does not apply skillOverrides to plugin skills (DOC_SKILL_OVERRIDES); the plugin's row takes it out.
    The saving of a synced claude.ai skill counts only cli and sdk-cli sessions (SYNCED_SKILL_NOTE).
    A duplicate claude.ai skill ("<name>-2") is a manual row.
    """
    n = u["sessions"]
    listed = {s["skill"] for s in u["skills"]}
    found = {sk.name: sk.description for sk in skills.discover(None, home)}
    bucket = {sk.name for sk in skills.discover(None, home, sources=("synced",))}
    ep_skills = {ep: {s["skill"]: s for s in ub["skills"]} for ep, ub in (by_ep or {}).items()}
    rows = []
    for s in u["skills"]:
        nm = s["skill"]
        prefix = nm.split(":", 1)[0] if ":" in nm else None
        if prefix and prefix != skills.SYNCED_SKILL_PREFIX:
            continue  # a plugin skill (the plugin's row) or a subfolder skill (on demand)
        if s["calls"] or s["sessions_listed"] < MIN_SESSIONS:
            continue
        per = round(s["line_bytes_median"] * LOW["prose_ascii"])
        if per < MIN_TOKENS:
            continue
        short = nm.split(":", 1)[1] if prefix else nm
        sess = {"period": n, "loaded": s["sessions_listed"], "used": 0, "calls": 0}
        check = (f"In a session started after the change, `walle context --last` shows a skill list without "
                 f"{nm}, and /{short} still works.")
        need = {"source": "skill", "name": nm, "used": False,
                "context": {"usage": _usage_text(s["sessions_listed"], 0, 0, n, "sessions (listed)"),
                            "description": _description(nm, found, home, folders)}}
        base = re.match(r"^(.*)-\d+$", nm)
        if prefix and base and base.group(1) in listed:
            change = {"kind": "app", "app": "claude.ai", "what": f"delete {short}, a copy of {base.group(1).split(':', 1)[1]}"}
            rows.append(_row("skill", nm, nm, "T6", f"Delete the duplicate skill {short}", change, "manual",
                             f"claude.ai skills settings: delete the copy, keep the first one ({UNCONFIRMED}).", per, sess,
                             s["sessions_listed"], ("low", "the first copy stays."),
                             "Add the skill again in claude.ai.", check, need))
            continue
        need["source"] = "claude.ai skill" if prefix else ("user skill" if _user_skill(nm, home, folders) else "built-in skill")
        change = {"kind": "settings_key", "file": SETTINGS, "op": "set", "keys": ["skillOverrides", nm],
                  "value": SKILL_OVERRIDE}
        extra = {}
        if prefix:
            check = (f"In a cli or sdk-cli session started after the change, `walle context --last` shows a skill "
                     f"list without {nm}, and /{short} still works. A claude-desktop session may still list it.")
            extra["note"] = SYNCED_SKILL_NOTE
            if nm not in bucket:
                extra["past"] = True
                extra["note"] = (f"{nm} is not in the current bucket (~/.claude/skills/synced): a new cli or sdk-cli "
                                 "session does not get it from the sync, so its loaded count is from past sessions. "
                                 "claude-desktop gets the synced skills from its own plugin copies; the status per "
                                 "entrypoint tells whether it still lists the skill. " + SYNCED_SKILL_NOTE)
        row = _row("skill", nm, nm, "T5", f"Hide the skill {nm} from the model", change, "walle",
                   f'{SETTINGS} skillOverrides["{nm}"] = "{SKILL_OVERRIDE}"', per, sess, s["sessions_listed"],
                   ("low", f"the model no longer calls {nm} by itself; /{short} still works."),
                   f'Delete skillOverrides["{nm}"], or walle undo.', check, need, extra)
        row["_listed"] = ("skill", nm)
        if prefix:
            counts = {ep: ((sk := ep_skills[ep].get(nm)) and (sk["sessions_listed"], sk["sessions_used"])) or (0, 0)
                      for ep in ep_skills}
            split_saving(row, per, counts, SKILL_DESKTOP)
            if extra.get("past"):
                _not_counted(row, "not in the current bucket")
        rows.append(row)
    return rows


def _not_counted(row: dict, why: str) -> None:
    """The row saves nothing that walle can count: the saving stays as a size, not a saving."""
    sv = row["saving"]
    sv.update(counted=False, not_counted=why, unused_sessions=0, unused_tokens_min=0)
    if sv.get("unconfirmed"):
        sv["unconfirmed"].update(unused_sessions=0, unused_tokens_min=0)


def usage_by_surface(rows_u: dict[str, dict], since: str | None, before: str | None) -> dict[str, dict]:
    """entrypoint -> context.usage of the sessions of that surface. A session without one is "unknown"."""
    eps = {f"{r['slug']}/{r['session']}": r.get("entrypoint") or "unknown" for r in rows_u.values() if r["main"]}
    groups: dict[str, dict] = defaultdict(dict)
    for k, r in rows_u.items():
        ep = eps.get(f"{r['slug']}/{r['session']}")
        if ep:
            groups[ep][k] = r
    return {ep: context.usage(g, since, before) for ep, g in sorted(groups.items())}


# ---------------------------------------------------------------------- keys that are already set
def _utc(ts: str) -> str:
    """A time as YYYY-MM-DDTHH:MM:SS in UTC. A time without a zone is local (walle apply writes those)."""
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if t.tzinfo is None:
        t = t.astimezone()
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def apply_set_times(out: Path) -> dict[tuple, tuple[str, str]]:
    """(file, keys) -> (UTC time, the apply id) of the earliest walle apply record that changed that key."""
    found: dict[tuple, tuple[str, str]] = {}
    for line in runs.read_index(out):
        if line.get("kind") != "apply" or not line.get("path"):
            continue
        try:
            rec = json.loads(Path(line["path"]).read_text())
        except (OSError, ValueError):
            continue
        when = _utc(rec.get("generated") or line.get("generated") or "")
        if not when or not isinstance(rec, dict):
            continue
        for x in rec.get("rows") or []:
            if isinstance(x, dict) and x.get("changed"):
                k = (x.get("file"), tuple(str(y) for y in x.get("keys") or []))
                if k not in found or when < found[k][0]:
                    found[k] = (when, str(line.get("id")))
    return found


def _session_marks(rows_u: dict[str, dict]) -> list[tuple[str, str, set, list]]:
    """(UTC start, entrypoint, listed skills, MCP server keys) of every main session."""
    out = []
    for r in rows_u.values():
        if r.get("main") and r.get("start"):
            out.append((_utc(r["start"]), r.get("entrypoint") or "unknown", set(r.get("listed") or []),
                        list(r.get("servers") or [])))
    return out


def set_status(marks: list[tuple], since: str, spec: tuple[str, str]) -> list[dict]:
    """Per entrypoint: does a session that started after `since` still list the source of a key that is set?"""
    kind, name = spec

    def listed(m) -> bool:
        if kind == "skill":
            return name in m[2]
        return any(x.startswith(name + ":") for x in m[2]) or any(k.startswith(f"plugin:{name}:") for k in m[3])

    by: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for m in marks:
        if m[0] and m[0] >= since:
            by[m[1]][0] += 1
            by[m[1]][1] += listed(m)
    out = []
    for ep in [*SURFACES, *sorted(set(by) - set(SURFACES))]:
        n, k = by.get(ep, (0, 0))
        if not n:
            state, text = "not measured", f"set, not measured on {ep}"
        elif k:
            state, text = "still listed", f"set, still listed on {ep} ({k} sessions since {since} UTC)"
        else:
            state, text = "acts", f"already set on {ep}: none of {n} sessions since {since} UTC lists it"
        out.append({"entrypoint": ep, "state": state, "sessions": n, "listed": k, "text": text})
    return out


def mark_set_keys(rows: list[dict], rows_u: dict[str, dict], out: Path) -> None:
    """For a row whose key is already set: its status per entrypoint, from the sessions after the key was set.

    The set time is the earliest walle apply record that changed the key, else the modification time of the
    file (every session after it saw the file as it is). The row never deletes the key, and its saving is not
    counted.
    """
    times = apply_set_times(out)
    marks = _session_marks(rows_u)
    for r in rows:
        spec = r.pop("_listed", None)
        c = r["change"]
        if spec is None or not c.get("already"):
            continue
        hit = times.get((c["file"], tuple(str(k) for k in c["keys"])))
        if hit:
            at = {"time": hit[0], "from": f"walle apply {hit[1]}"}
        else:
            try:
                mtime = Path(c["path"]).stat().st_mtime
            except (OSError, KeyError):
                continue
            at = {"time": datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                  "from": f"the modification time of {c['file']}"}
        c["set_at"] = at
        r["set_status"] = set_status(marks, at["time"], spec)
        _not_counted(r, "the key is already set")


def _description(name: str, found: dict[str, str], home: Path, folders: set[str]) -> str:
    """The description of a listed skill from its SKILL.md, or "" for a built-in skill."""
    if name in found:
        return found[name][:400]
    p = _user_skill(name, home, folders)
    if p:
        sk = skills.read_skill(Path(p), name, "user")
        return sk.description[:400] if sk else ""
    return ""


def hook_rows(u: dict) -> list[dict]:
    n = u["sessions"]
    rows = []
    for h in u["hooks"]:
        if h["by"] != "source" or h["sessions"] < HOOK_MIN_SESSIONS or h["bytes"] < HOOK_MIN_BYTES:
            continue
        label = h["hook"]
        per = round(h["bytes"] / h["sessions"] * LOW["prose_ascii"])
        sess = {"period": n, "loaded": h["sessions"], "used": None, "calls": None, "injections": h["injections"]}
        change = {"kind": "patch", "patch": None,
                  "how": "shorten the hook's output to one line, or stop it where it does not help"}
        where = (f"The hook whose output starts with \"{label}\": look in {SETTINGS} hooks, the project's "
                 ".claude/settings.json hooks and the plugins' hooks/hooks.json.")
        check = (f"`walle context --usage --since <the change date>` shows fewer bytes per injection for "
                 f"\"{label}\".")
        need = {"source": "hook output", "name": label, "used": True,
                "context": {"usage": f"added to {h['sessions']:,} of {n:,} sessions, {h['injections']:,} times, "
                                     f"{h['bytes']:,} bytes in all"}}
        row = _row("hook", label, label, "T9", f"Shorten the hook output \"{label}\"", change, "patch", where,
                   per, sess, h["sessions"], ("medium", "a check that the hook makes can stop."),
                   "git revert of the hook change, or restore the script from its backup.", check, need)
        row["saving"]["per"] = "session that gets the hook output"
        rows.append(row)
    return rows


# ---------------------------------------------------------------------- rows from the scan
def _patch(file: str, ranges: list[tuple[int, int]], texts: list[str]) -> tuple[str, Path] | None:
    """A unified diff that deletes the line ranges from file, or None when the file changed since the scan."""
    path = Path(file)
    try:
        old = path.read_text().splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None
    drop = set()
    for (a, b), text in zip(ranges, texts):
        live = "".join(old[a - 1:b])
        if norm_id(live) != norm_id(text):
            return None
        drop.update(range(a, b + 1))
    new = [line for i, line in enumerate(old, 1) if i not in drop]
    base = find_checkout(path.parent)[0] or path.parent
    rel = path.relative_to(base)
    out = []
    for line in difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}", n=1):
        out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out), base


def instruction_rows(scan: dict, run_dir: Path, home: Path) -> list[dict]:
    rows = []
    patches = run_dir / "patches"
    for it in scan.get("items", []):
        if it.get("state") != "open" or it.get("scope") != "session_start":
            continue
        first = it["locations"][0] if it["locations"] else {}
        stacks = it.get("stacks") or []
        item = {k: it.get(k) for k in ("id", "rule", "label", "locations", "team_key")}
        loc = f"{_short(first.get('file'), home)}:{first.get('line')}"
        if it.get("verb") == "DELETE":
            dels = [l for l in it["locations"] if l.get("role") == "delete"] or it["locations"][:1]
            got = _patch(dels[0]["file"], [(l["line"], l.get("end_line") or l["line"]) for l in dels],
                         [l.get("text") or "" for l in dels]) if all(l["file"] == dels[0]["file"] for l in dels) else None
            per = sum(prose_min(l.get("text") or "") for l in dels)
            change = {"kind": "patch", "patch": None, "file": dels[0]["file"],
                      "lines": [[l["line"], l.get("end_line") or l["line"]] for l in dels]}
            stale = got is None
            if got:
                text, base = got
                patches.mkdir(parents=True, exist_ok=True)
                pf = patches / f"{it['id']}-delete.patch"
                pf.write_text(text)
                change.update(patch=str(pf), base=str(base), apply=f"git -C {base} apply {pf}")
            keep_side = next((l for l in it["locations"] if l.get("role") == "keep"), None)
            why = (f"the same rule also loads from {_short(keep_side['file'], home)}:{keep_side['line']}"
                   if keep_side and keep_side["file"] != dels[0]["file"] else "the same rule is in the file twice")
            rows.append({
                "id": it["id"], "group": "instruction", "action": "T10",
                "source": {"type": "instruction", "key": it["id"], "name": loc},
                "title": f"Delete the line loaded twice at {loc}", "change": change, "applies": "patch",
                "where": change.get("apply") or "the file changed since the scan: run walle scan, then walle trim --plan",
                "saving": {"tokens_per_session_min": per, "per": "session started in a folder whose stack loads it",
                           "stacks": len(stacks), "estimator": "bytes-v2, low end of the rate range"},
                "sessions": None, "stacks": stacks,
                "risk": {"level": "low", "why": why + "."},
                "undo": f"git -C {change.get('base', '<checkout>')} apply -R {change.get('patch') or '<patch>'}",
                "check": f"`walle scan` again: item {it['id']} is gone and the stack is at least {_approx(per)} "
                         "est. tokens smaller.",
                "judge": {"needed": False, "verdict": None}, "item": item, "stale": stale,
            })
        elif it.get("label") == "large_layer":
            move = it.get("move") or {}
            head = move.get("heading") or "-"
            rows.append({
                "id": it["id"], "group": "instruction", "action": "T7",
                "source": {"type": "instruction", "key": it["id"], "name": loc},
                "title": f"Move a section of {_short(first.get('file'), home)} to a file that loads on demand",
                "change": {"kind": "patch", "patch": None, "file": first.get("file"),
                           "how": f"move \"## {head}\" (lines {first.get('line')}-{first.get('end_line')}) to a "
                                  "skill, a .claude/rules file with paths:, or a CLAUDE.md of the subfolder it is about"},
                "applies": "patch", "where": f"{first.get('file')}:{first.get('line')}",
                "saving": None,
                "size": {"tokens_est": move.get("tokens_est") or 0, "layer_tokens_est": it.get("tokens_est"),
                         "section": head, "lines": [first.get("line"), first.get("end_line")]},
                "sessions": None, "stacks": stacks,
                "risk": {"level": "medium", "why": "a rule that every task needs can stop loading."},
                "undo": "git revert of the commit that moved it.",
                "check": "`walle scan` again: the layer is smaller; sessions that need the section still read it.",
                "judge": {"needed": True, "verdict": None}, "item": item,
                "_need": {"source": "instruction section", "name": f"{_short(first.get('file'), home)} ## {head}",
                          "used": True, "text": (first.get("text") or "")[:judge.MAX_TEXT],
                          "context": {"usage": f"loads at the start of every session in {len(stacks)} stack(s)"}},
            })
    return rows


def _short(path: str | None, home: Path) -> str:
    h = str(home).rstrip("/")
    p = path or "-"
    return "~" + p[len(h):] if p.startswith(h + "/") else p


# ---------------------------------------------------------------------- judge
def _need_items(rows: list[dict]) -> list[dict]:
    items = []
    for r in rows:
        need = r.pop("_need", None)
        if need is None:
            continue
        ask = {"type": "need", "source": need["source"], "name": need["name"], "used": need["used"],
               "change": r["title"]}
        if need.get("text"):
            ask["text"] = need["text"]
        ctx = dict(need.get("context") or {})
        ctx["risk"] = r["risk"]["why"]
        items.append({"row": r, "ask": ask, "context": ctx, "refs": [], "key": judge.item_key(ask, RUBRIC_VERSION)})
    return items


def attach_verdicts(rows: list[dict], cache: dict, now: set | None = None) -> None:
    now = now or set()
    for r in rows:
        k = (r.get("judge") or {}).get("key")
        v = cache.get(k) if k else None
        if v:
            r["judge"]["verdict"] = {x: v[x] for x in ("real", "keep", "fix", "why")}
            r["judge"]["judge"] = v.get("judge")
            r["judge"]["from_cache"] = k not in now


# ---------------------------------------------------------------------- the plan
def build(args, run_dir: Path) -> dict:
    out = args.out
    home = HOME
    root = args.transcripts.expanduser()
    if not context.main_transcripts(root):
        raise TrimError(f"no transcripts under {root}")
    rows_u = context.load_rows(root, out.parent / "cache" / "context-usage.json")
    u = context.usage(rows_u, args.since, args.before)
    folders = {r["cwd"] for r in rows_u.values() if r.get("cwd")}
    configured = configured_servers(home, folders)
    plugins = plugin_catalog(home)
    by_ep = usage_by_surface(rows_u, args.since, args.before)
    srv, report_only = server_rows(u, home, configured, plugins)
    rows = srv + plugin_rows(u, plugins, by_ep) + skill_rows(u, plugins, home, folders, by_ep) + hook_rows(u)

    scan_path = args.scan.expanduser().resolve() if args.scan else judge.latest_scan(out)
    scan = None
    if scan_path and scan_path.is_file():
        try:
            scan = json.loads(scan_path.read_text())
        except (OSError, ValueError):
            scan = None
    if scan is not None:
        ins = instruction_rows(scan, run_dir, home)
        ins.sort(key=lambda r: (r["action"] != "T10", -((r["saving"] or {}).get("tokens_per_session_min") or 0),
                                r["id"]))
        rows += ins

    kept_ids = keep.source_ids(keep.path_for(out))
    kept = [{"id": r["id"], "name": r["source"]["name"], "type": r["source"]["type"]} for r in rows if r["id"] in kept_ids]
    rows = [r for r in rows if r["id"] not in kept_ids]
    targets = record_targets(rows, home)
    mark_set_keys(rows, rows_u, out)
    src = [r for r in rows if r["group"] == "source"]
    src.sort(key=lambda r: (-r["saving"]["unused_tokens_min"], r["id"]))
    rows = src + [r for r in rows if r["group"] != "source"]

    items = _need_items(rows)
    for it in items:
        it["row"]["judge"]["key"] = it["key"]
    cache_file = out.parent / "cache" / JUDGE_CACHE
    cache = judge.load_cache(cache_file)
    todo = [it for it in items if it["key"] not in cache]
    size = max(1, args.batch_size)
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    head = {"generated": datetime.now().isoformat(timespec="seconds"), "plan": str(run_dir / "plan.json")}
    files = judge.write_export(run_dir / judge.JUDGE_DIR, batches, "quick", head, RUBRIC)
    attach_verdicts(rows, cache)

    data = {
        "schema": SCHEMA, "generated": head["generated"],
        "run": {"id": run_dir.name, "kind": "trim", "label": args.label, "walle_version": __version__,
                "estimator": "bytes-v2", "since": args.since, "before": args.before, "transcripts": str(root),
                "scan": str(scan_path) if scan is not None else None,
                "scan_generated": (scan or {}).get("generated")},
        "period": {"sessions": u["sessions"], **u["window"]},
        "rows": rows, "kept": kept, "report_only": report_only, "targets": targets,
        "judge": {"rows": len(items), "cached": len(items) - len(todo), "to_judge": len(todo), "files": files,
                  "rubric_version": RUBRIC_VERSION},
        "method": {"min_sessions": MIN_SESSIONS, "used_share": USED_SHARE, "min_tokens": MIN_TOKENS,
                   "hook_min_sessions": HOOK_MIN_SESSIONS, "hook_min_bytes": HOOK_MIN_BYTES,
                   "protected_plugins": list(PROTECTED_PLUGINS), "low_rates": LOW},
        "notes": NOTES,
    }
    if scan is None:
        data["notes"] = NOTES + ["No scan.json in the runs folder: the plan has no instruction-file rows. "
                                 "Run `walle scan <folder>` with the same --out, then `walle trim --plan`."]
    return data


NOTES = [
    "walle trim --plan changes nothing. Pick row ids, then `walle apply <run> --only <id>[,<id>]`: it backs up "
    "and changes only rows that walle can apply, and prints a checklist for manual rows. `walle undo <apply-id>` "
    "puts the files back.",
    "walle apply refuses a settings file that changed since this plan (sha256 in targets). The Claude app rewrites "
    "~/.claude.json while it runs: close every Claude app and session, run walle trim --plan again, then apply "
    "with --app-closed.",
    "Saving: the median bytes a source adds to a session that loads it, times the low end of the bytes-v2 rate "
    "of that kind of bytes. Estimates, not measured values.",
    "A source is proposed when it loaded in at least 10 sessions, sessions used it in fewer than 10% of them, "
    "and it had fewer calls than sessions that loaded it. A skill is proposed when no session called it.",
    "A plugin's skills and MCP servers are one row. Subfolder skills load on demand and are not proposed.",
    "walle offers no skillOverrides key for a skill of a real plugin (<plugin>:* of a plugin that is not the "
    f"claude.ai skill sync): Claude Code does not apply it to plugin skills ({DOC_SKILL_OVERRIDES}). The plugin's "
    "row takes the skill out with the whole plugin.",
    "A synced claude.ai skill (anthropic-skills:*) gets a skillOverrides row. " + SYNCED_SKILL_NOTE,
    "Surfaces are the entrypoint of each session (cli, sdk-cli, claude-desktop). For synced claude.ai skills and "
    "synced plugins, the saving counts cli and sdk-cli sessions; the other surfaces are a separate, unconfirmed "
    "number.",
    "A key that is already set gets a status per entrypoint from the sessions that started after it was set (the "
    "earliest walle apply record that set it, else the modification time of the settings file): already set, "
    "still listed, or not measured. Its saving is not counted. No row deletes a key.",
    "A claude.ai plugin synced to Claude Code is the key enabledPlugins[\"<name>@synced\"] = false, the key that "
    f"`claude plugin disable` writes ({DOC_PLUGIN_DISABLE}); " + PLUGIN_DESKTOP + " A plugin that only the desktop "
    "app keeps is manual: "
    f"on claude.ai, members turn off \"Installed by default\" plugins; \"Required\" needs the Owner ({DOC_ORG_PLUGINS}).",
    "Rows that walle can apply change one settings key. Manual rows change a place in an app. Patch rows are "
    "patch files in patches/ (or a change the agent writes); walle never edits an instruction file.",
    "Kept rows (walle keep <id>) leave the plan; walle keep --list lists them.",
]


# ---------------------------------------------------------------------- import
def import_answers(args) -> Path:
    run_dir = _plan_dir(args.out, args.run)
    data = json.loads((run_dir / "plan.json").read_text())
    root = run_dir / judge.JUDGE_DIR
    cache_file = args.out.parent / "cache" / JUDGE_CACHE
    cache = judge.load_cache(cache_file)
    errors: list[str] = []
    now: set = set()
    if (root / "manifest.json").is_file():
        _cur, now, _answered, errors = judge.read_answers(root, cache, cache_file, args.judge_name)
    attach_verdicts(data["rows"], cache, now)
    data["judge"]["to_judge"] = sum(1 for r in data["rows"] if r["judge"]["needed"] and not r["judge"]["verdict"])
    data["judge"]["errors"] = errors
    (run_dir / "plan.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (run_dir / "plan.md").write_text(render(data))
    for e in errors:
        print(f"walle trim: {e}", file=sys.stderr)
    return run_dir / "plan.md"


def _plan_dir(out: Path, run: str | None) -> Path:
    if run:
        d = out / run if not Path(run).is_absolute() else Path(run)
        d = d.parent if d.name == "plan.json" else d
        if (d / "plan.json").is_file():
            return d
        raise TrimError(f"no plan.json in {d}")
    plans = keep.newest_plans(out, 1)
    if not plans:
        raise TrimError(f"no trim plan under {out}. Run `walle trim --plan` first.")
    return plans[0].parent


# ---------------------------------------------------------------------- Markdown
def _n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else ("-" if x is None else str(x))


def _approx(n: int) -> str:
    if n <= 0:
        return "0"
    step = 10 if n < 1000 else 100
    return f"~{max(step, int(n / step + 0.5) * step):,}"


def _cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _verdict(r: dict) -> str:
    j = r.get("judge") or {}
    if not j.get("needed"):
        return "-"
    v = j.get("verdict")
    if not v:
        return "waiting"
    if v["keep"] == "ask":
        return "ask the user"
    return "not needed" if v["real"] else "needed"


def render(data: dict) -> str:
    rows = data["rows"]
    p = data["period"]
    md = ["# walle trim --plan", ""]
    md.append(f"{_n(p.get('sessions'))} main sessions, {(p.get('first') or '-')[:10]} to {(p.get('last') or '-')[:10]}. "
              f"Scan: {data['run']['scan'] or 'none'}. Estimator: bytes-v2; savings use the low end of each rate.")
    md.append("")
    by = defaultdict(int)
    for r in rows:
        by[r["applies"]] += 1
    done = sum(1 for r in rows if r["change"].get("already"))
    md.append(f"{len(rows)} rows: walle can apply {by['walle']}"
              + (f" ({done} already set in the file)" if done else "")
              + f", manual {by['manual']}, patch {by['patch']}. "
              f"Kept and left out: {len(data['kept'])} (walle keep --list). Nothing here is applied.")
    md.append("")
    conf, other = totals(rows)
    md.append(f"Saving over the period, rows not yet set (min est. tokens): {_approx(conf)} on confirmed surfaces "
              f"({', '.join(CONFIRMED)} for synced skills and plugins; every surface for the other rows), and "
              f"{_approx(other)} more on unconfirmed surfaces (claude-desktop and others; see each row).")
    md.append("")
    src = [r for r in rows if r["group"] == "source"]
    ins = [r for r in rows if r["group"] == "instruction"]
    if src:
        md += ["## Sources, by unused est. tokens over the period", "",
               "| id | source | change | applies | saving (min est. tokens / session) | loaded | used (calls) | risk | judge |",
               "|---|---|---|---|---:|---:|---:|---|---|"]
        for r in src:
            s = r["sessions"]
            used = "-" if s.get("used") is None else f"{_n(s['used'])} ({_n(s['calls'])})"
            md.append("| " + " | ".join(_cell(x) for x in [
                r["id"], f"{r['source']['type']}: {r['source']['name']}", r["title"], _applies(r),
                _saving_cell(r), _n(s["loaded"]) + (" (past)" if r.get("past") else ""), used, r["risk"]["level"],
                _verdict(r)]) + " |")
        md.append("")
    if ins:
        md += ["## Instruction files", "",
               "| id | where | change | applies | saving (min est. tokens / session) | stacks | risk | judge |",
               "|---|---|---|---|---:|---:|---|---|"]
        for r in ins:
            sav = _approx(r["saving"]["tokens_per_session_min"]) if r["saving"] else f"moves {_approx(r['size']['tokens_est'])}"
            md.append("| " + " | ".join(_cell(x) for x in [
                r["id"], r["source"]["name"], r["title"], r["applies"], sav, len(r.get("stacks") or []),
                r["risk"]["level"], _verdict(r)]) + " |")
        md.append("")
    if rows:
        md += ["## Rows", ""]
        for r in rows:
            md.append(f"### {r['id']} · {r['title']}")
            md.append("")
            md.append(f"- Change: {_change_text(r)}")
            md.append(f"- Where: {r['where']}")
            md.append(f"- Who: {WHO[r['applies']]}")
            c = r["change"]
            if c.get("already"):
                at = c.get("set_at")
                md.append("- In the file: the key already has this state"
                          + (f" (set {at['time']} UTC, from {at['from']})" if at else "")
                          + ". The row never deletes it.")
                for st in r.get("set_status") or []:
                    md.append(f"  - {st['text']}")
            if r["saving"] and r["saving"].get("counted") is False:
                md.append(f"- Saving: not counted: {r['saving']['not_counted']}. Size: at least "
                          f"{_approx(r['saving']['tokens_per_session_min'])} est. tokens per {r['saving']['per']}.")
            elif r["saving"]:
                sv = r["saving"]
                line = f"- Saving: at least {_approx(sv['tokens_per_session_min'])} est. tokens per {sv['per']}"
                if sv.get("unused_sessions") is not None:
                    on = f" on {' and '.join(sv['confirmed_surfaces'])}" if sv.get("confirmed_surfaces") else ""
                    line += (f"; {_n(sv['unused_sessions'])} sessions{on} loaded it without using it "
                             f"(at least {_approx(sv['unused_tokens_min'])} est. tokens in all)")
                md.append(line + ".")
                un = sv.get("unconfirmed")
                if un and un["unused_sessions"]:
                    parts = ", ".join(f"{ep} {_n(k)}" for ep, k in un["sessions"].items())
                    md.append(f"- Unconfirmed, not in the saving: {_n(un['unused_sessions'])} sessions ({parts}), "
                              f"at least {_approx(un['unused_tokens_min'])} est. tokens: {un['why']}")
            else:
                md.append(f"- Size: the section is {_approx(r['size']['tokens_est'])} est. tokens "
                          f"(the layer {_approx(r['size']['layer_tokens_est'] or 0)}). A move is not a saving.")
            s = r.get("sessions")
            if s:
                used = "" if s.get("used") is None else f", used in {_n(s['used'])} ({_n(s['calls'])} calls)"
                past = " (past sessions: the skill is not in the current bucket)" if r.get("past") else ""
                md.append(f"- Sessions: loaded in {_n(s['loaded'])} of {_n(s['period'])}{past}{used}.")
                if s.get("by_entrypoint"):
                    md.append("- By entrypoint (loaded, used): " + ", ".join(
                        f"{ep} {_n(v['loaded'])}, {_n(v['used'])}" for ep, v in s["by_entrypoint"].items()) + ".")
            elif r.get("stacks"):
                md.append(f"- Stacks: {', '.join(r['stacks'])}.")
            md.append(f"- Risk: {r['risk']['level']}: {r['risk']['why']}")
            md.append(f"- Undo: {r['undo']}")
            md.append(f"- Check: {r['check']}")
            if r.get("note"):
                md.append(f"- Note: {r['note']}")
            if r.get("stale"):
                md.append("- No patch: the file changed since the scan. Run walle scan, then walle trim --plan.")
            j = r["judge"]
            if j["needed"]:
                v = j.get("verdict")
                line = f"- Judge: {_verdict(r)}"
                if v:
                    line += f": {v['why']}" + (f" Fix: {v['fix']}" if v["fix"] not in ("", "-") else "")
                md.append(line)
            md.append(f"- Keep it instead: `walle keep {r['id']}`")
            md.append("")
    if data.get("report_only"):
        md += ["## Report only", "", "No file or settings key that walle knows turns these off.", ""]
        for x in data["report_only"]:
            s = x["sessions"]
            md.append(f"- {x['type']} {x['name']}: loaded in {_n(s['loaded'])} sessions, used in {_n(s['used'])} "
                      f"({_n(s['calls'])} calls), at least {_approx(x['tokens_per_session_min'])} est. tokens per session.")
        md.append("")
    j = data["judge"]
    md += ["## Agent judgment", ""]
    if j["files"]:
        md.append(f"{j['to_judge']} rows wait for a judge ({j['cached']} answered before). For each batch file, follow "
                  "its rubric and write the verdict JSON to its answer file. Then run `walle trim --import`.")
        md += [""] + [f"- {f['batch']} -> {f['answer']}" for f in j["files"]]
    elif j.get("to_judge"):
        md.append(f"{j['to_judge']} rows have no verdict yet. Run `walle trim --import` after the answers are written.")
    else:
        md.append(f"All {j['rows']} rows that need a judge have a verdict.")
    for e in j.get("errors") or []:
        md.append(f"- {e}")
    if data["kept"]:
        md += ["", "## Kept", ""] + [f"- {k['id']} {k['type']}: {k['name']}" for k in data["kept"]]
    md += ["", "## Method", ""] + [f"- {x}" for x in data["notes"]]
    return "\n".join(md) + "\n"


WHO = {"walle": "`walle apply <run> --only <id>` changes this settings key after a backup; `walle undo` puts it back.",
       "manual": "you, in the app or on the web: walle cannot reach it.",
       "patch": "your agent applies the patch in the repository (or writes it, when there is no patch file)."}


def totals(rows: list[dict]) -> tuple[int, int]:
    """(saving on confirmed surfaces, saving on unconfirmed surfaces) of the source rows, min est. tokens."""
    conf = other = 0
    for r in rows:
        sv = r.get("saving") or {}
        if r["group"] != "source" or sv.get("counted") is False:
            continue
        conf += sv.get("unused_tokens_min") or 0
        other += (sv.get("unconfirmed") or {}).get("unused_tokens_min") or 0
    return conf, other


def _saving_cell(r: dict) -> str:
    if r["saving"].get("counted") is False:
        return "not counted"
    return _approx(r["saving"]["tokens_per_session_min"])


def _applies(r: dict) -> str:
    return f"{r['applies']} (already)" if r["change"].get("already") else r["applies"]


def _change_text(r: dict) -> str:
    c = r["change"]
    if c["kind"] == "settings_key":
        if c["op"] == "set":
            return f"set {_keys(c['keys'])} to {json.dumps(c['value'])} in {c['file']}"
        if c["op"] == "remove":
            return f"remove {_keys(c['keys'])} from {c['file']}"
        return f"move {_keys(c['keys'])} to {_keys(c['to'])} in {c['file']}"
    if c["kind"] == "app":
        return f"{c['what']} ({c['app']})"
    if c.get("patch"):
        return f"apply {c['patch']} (`{c['apply']}`)"
    return c.get("how") or "a patch"


# ---------------------------------------------------------------------- CLI
def add_parser(sub, default_transcripts: Path):
    x = sub.add_parser("trim", help="propose what to take out of every session's context (--plan), "
                                    "with savings, risk, undo and checks; changes nothing")
    x.add_argument("--plan", action="store_true", help="write plan.json, plan.md, patches/ and judge/ in a new run")
    x.add_argument("--import", dest="do_import", action="store_true",
                   help="read the judge answers of a plan (the newest, or RUN) back into it")
    x.add_argument("run", nargs="?", help="with --import: a run id or folder with plan.json")
    x.add_argument("--since", help="ISO date, sessions that start on or after it")
    x.add_argument("--before", help="ISO date, sessions that start before it")
    x.add_argument("--scan", type=Path, help="scan.json for instruction-file rows (default: the newest in the runs folder)")
    x.add_argument("--transcripts", type=Path, default=default_transcripts)
    x.add_argument("--batch-size", type=int, default=judge.BATCH, help="rows per judge batch file")
    x.add_argument("--judge-name", default="agent", help="with --import: who judged, kept with each verdict")
    x.add_argument("--label", help="a name for this run")
    return x


def run_cli(args) -> int:
    from .runs import append_row, new_run_dir
    try:
        if args.do_import:
            print(import_answers(args))
            return 0
        if not args.plan:
            print("walle trim: give --plan (write a plan) or --import (read judge answers into it)", file=sys.stderr)
            return 2
        run_dir = new_run_dir(args.out)
        data = build(args, run_dir)
    except (TrimError, context.ContextError, judge.JudgeError) as e:
        print(f"walle trim: {e}", file=sys.stderr)
        return 2
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (run_dir / "plan.md").write_text(render(data))
    by = defaultdict(int)
    for r in data["rows"]:
        by[r["applies"]] += 1
    append_row(args.out, {"id": run_dir.name, "kind": "trim", "label": args.label, "parent_id": None,
                          "generated": data["generated"], "roots": [], "walle_version": __version__,
                          "estimator": "bytes-v2", "rows": len(data["rows"]), "applies": dict(by),
                          "path": str(run_dir / "plan.json")})
    print(run_dir / "plan.md")
    return 0
