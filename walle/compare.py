"""walle context --compare: did the context of a session really shrink (plan C6, step 6 of the agent loop).

walle context --compare A B
  A and B are each a context run (its id, its folder or its context.json) or a session id (or the start of
  one) of --agent claude or codex. Per source: the recorded bytes and the estimated tokens of A and of B. Then
  the measured first-call tokens of each, and the MCP servers and skills that one side loaded and the other
  did not, with their calls.

walle context --compare <apply-id|date>
  The newest session since the split and the newest one before it: in the folder of --root (default: the
  current folder) when that folder has a session on both sides, and of the same model family when it can.
  For an apply, every row it changed with the sessions that loaded and used it, before and since (all
  folders: a settings key acts on every folder). Then the measured first-call medians of the folder, one model
  family per row (the same block as walle eve --around).

Recorded bytes compare when both sides split the first call the same way (the counting revision of
context.json). Estimated tokens compare only when, in addition, both sides used the same estimator. The
measured first call always compares when the model family is the same. The first prompt and hook output
change with every session: they are not settings, and the text marks them.

These are measured values. They never enter the instruction-stack verdict, Start here or the alarm (brief
2.1, L4). The result goes to a new run folder (kind compare) as compare.json and compare.md.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, brief, codex_context, context, estimate, eve, measure, rollout, runs, transcript
from .collect import HOME
from .strings import resolve_lang, t

PER_SESSION = ("first_prompt", "hook_context", "history")
NAMES_SHOWN = 6
INSIDE_SHOWN = 4
LABEL_COLS = 34


class CompareError(Exception):
    def __init__(self, key: str, **fields):
        super().__init__(key)
        self.key, self.fields = key, fields


# ---------------------------------------------------------------------- one side
def _record(estimator: str, revision: int) -> dict:
    return {"estimator": estimator, "counting_revision": revision}


def _claude_loaded(path: Path, known: dict | None = None) -> dict | None:
    """MCP servers and skills one Claude session loaded, and its calls of them (main transcript and subagents)."""
    if not path.is_file():
        return None
    main = transcript.usage_row(path, True)
    subs = [transcript.usage_row(p, False) for p in sorted((path.parent / path.stem / "subagents").rglob("agent-*.jsonl"))]
    names = {**context.connector_names([main]), **(known or {})}
    calls: dict[str, int] = {}
    skill_calls: dict[str, int] = {}
    for r in [main, *subs]:
        for tool, n in r["tools"].items():
            if tool.startswith("mcp__"):
                k = context.canonical(transcript.server_key(tool[5:].split("__", 1)[0]), names)
                calls[k] = calls.get(k, 0) + n
        for k, n in list(r["skills"].items()) + list(r["slash"].items()):
            skill_calls[k] = skill_calls.get(k, 0) + n
    servers = {}
    for k in {*main["servers"], *main["instr"]}:
        c = context.canonical(k, names)
        servers[context.display(c, names)] = {"loaded": True, "calls": calls.get(c, 0)}
    for c, n in calls.items():
        servers.setdefault(context.display(c, names), {"loaded": False, "calls": n})
    listed = set(main["listed"])
    skills = {}
    for nm in listed:
        n = skill_calls.get(nm, 0) + (skill_calls.get(nm.rsplit(":", 1)[-1], 0) if ":" in nm else 0)
        skills[nm] = {"loaded": True, "calls": n}
    return {"servers": dict(sorted(servers.items())), "skills": dict(sorted(skills.items()))}


def _codex_loaded(r: rollout.Rollout) -> dict:
    row = rollout.usage_row(r)
    servers = {k: {"loaded": True, "calls": sum(row["servers_used"].get(k, {}).values())} for k in row["servers_loaded"]}
    for k, ts in row["servers_used"].items():
        servers.setdefault(k, {"loaded": False, "calls": sum(ts.values())})
    skills = {nm: {"loaded": True, "calls": row["skills_used"].get(nm, 0)} for nm in row["listed"]}
    return {"servers": dict(sorted(servers.items())), "skills": dict(sorted(skills.items()))}


def cached_names(cache: Path) -> dict[str, str]:
    """Connector display names from the walle context --usage cache, as walle context uses them."""
    try:
        return context.connector_names([v["row"] for k, v in json.loads((cache / "context-usage.json").read_text()).items()
                                        if k != "_v"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def from_session(agent: str, root: Path, ref: str, estimator: str, names: dict | None = None) -> dict:
    """A side built now from a session id: the first call split into sources, and what the session loaded."""
    try:
        if agent == "codex":
            r, _ = codex_context.find_rollout(root, ref)
            fc = rollout.first_call(r)
            sc = codex_context.session_context(fc, estimator)
            session = codex_context.session_block(r, fc)
            session["entrypoint"] = fc.originator
            loaded = _codex_loaded(r)
            revision = codex_context.COUNTING_REVISION
        else:
            path = context.find_session(root, ref)
            fc = transcript.first_call(path)
            if fc is None:
                raise CompareError("compare.no_call", ref=ref)
            sc = context.session_context(fc, estimator, names=names)
            session = {"id": fc.session, "path": fc.path, "cwd": fc.cwd, "start": fc.start, "model": fc.model,
                       "entrypoint": fc.entrypoint}
            loaded = _claude_loaded(path, names)
            revision = context.COUNTING_REVISION
    except (context.ContextError, codex_context.CodexError) as e:
        raise CompareError("compare.not_found", ref=ref, why=str(e)) from None
    return {"ref": ref, "how": "session", "agent": agent, **_record(estimator, revision), "session": session,
            "first_call": {"measured": sc["measured"], "sources": [_source(s) for s in sc["sources"]]},
            "loaded": loaded}


def _source(s: dict) -> dict:
    return {"source": s["source"], "label": s["label"], "bytes": s["bytes"], "items": s["items"],
            "tokens_est": s["tokens_est"], "recorded": s.get("recorded"), "parts": parts(s)}


def parts(s: dict) -> dict[str, int] | None:
    """Bytes of each part of a source that has named parts: MCP servers, tools, instruction files."""
    d = s.get("detail") or {}
    if s["source"] == "deferred_tools":
        return {k: v.get("bytes", 0) for k, v in (d.get("servers") or {}).items() if isinstance(v, dict)}
    if s["source"] == "mcp_instructions":
        return {k: v for k, v in (d.get("servers") or {}).items() if isinstance(v, int)}
    if s["source"] in ("builtin_tools", "mcp_tools"):
        return {k: v for k, v in (d.get("tools") or {}).items() if isinstance(v, int)}
    if s["source"] in ("instructions", "agents_md"):
        return {str(f.get("path")): f.get("bytes", 0) for f in d.get("files") or [] if isinstance(f, dict)}
    return None


def context_run(out: Path, ref: str) -> Path | None:
    """The context.json of a context run: its id (index line or folder), its folder, or the file itself."""
    row = next((r for r in runs.read_index(out) if r.get("kind") == "context" and r.get("id") == ref), None)
    for p in ([Path(row["path"])] if row and row.get("path") else []) + [out / ref / "context.json",
                                                                          Path(ref).expanduser() / "context.json",
                                                                          Path(ref).expanduser()]:
        if p.name == "context.json" and p.is_file():
            return p
    return None


def from_run(path: Path, names: dict | None = None) -> dict:
    """A side read from a context run. A run made with --usage has no first call: compare refuses it."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise CompareError("compare.not_found", ref=str(path), why="not a context.json") from None
    run = data.get("run") or {}
    if "first_call" not in data:
        raise CompareError("compare.usage_run", ref=run.get("id") or str(path))
    agent = run.get("agent") or "claude"
    session = dict(data.get("session") or {})
    session.setdefault("entrypoint", session.get("originator") or "")
    loaded = None
    p = Path(session.get("path") or "")
    if agent == "claude" and session.get("path"):
        loaded = _claude_loaded(p, names)
    elif agent == "codex" and session.get("path") and p.is_file():
        r = rollout.load(p)
        loaded = _codex_loaded(r) if r else None
    return {"ref": run.get("id") or str(path), "how": "context run", "agent": agent,
            **_record(runs.estimator_of(run), runs.counting_of(run)), "session": session,
            "first_call": {"measured": data["first_call"]["measured"],
                           "sources": [_source(s) for s in data["first_call"]["sources"]]},
            "loaded": loaded, "path": str(path)}


def side(out: Path, ref: str, agent: str, root: Path, estimator: str, names: dict | None = None) -> dict:
    path = context_run(out, ref)
    return from_run(path, names) if path else from_session(agent, root, ref, estimator, names)


# ---------------------------------------------------------------------- comparing two sides
def compare_pair(a: dict, b: dict, lang: str = "en") -> dict:
    if a["agent"] != b["agent"]:
        raise CompareError("compare.agents_differ", a=a["agent"], b=b["agent"])
    notes = runs.token_notes([a], b, lang)
    same_split = runs.counting_of(a) == runs.counting_of(b)
    same_tokens = not notes
    sa = {s["source"]: s for s in a["first_call"]["sources"]}
    sb = {s["source"]: s for s in b["first_call"]["sources"]}
    rows = []
    for k in [*sa, *[k for k in sb if k not in sa]]:
        x, y = sa.get(k) or {}, sb.get(k) or {}
        if not (x.get("bytes") or y.get("bytes")):
            continue
        inside = []
        if same_split and x.get("parts") is not None and y.get("parts") is not None:
            pa, pb = x["parts"], y["parts"]
            inside = sorted(([n, pa.get(n, 0), pb.get(n, 0)] for n in {*pa, *pb} if pa.get(n, 0) != pb.get(n, 0)),
                            key=lambda e: (-abs(e[2] - e[1]), e[0]))
        rows.append({"source": k, "label": x.get("label") or y.get("label") or k, "inside": inside,
                     "a": {f: x.get(f) for f in ("bytes", "items", "tokens_est")},
                     "b": {f: y.get(f) for f in ("bytes", "items", "tokens_est")},
                     "delta_bytes": (y.get("bytes") or 0) - (x.get("bytes") or 0) if same_split else None,
                     "delta_tokens": ((y.get("tokens_est") or 0) - (x.get("tokens_est") or 0)) if same_tokens else None,
                     "per_session": k in PER_SESSION})
    rows.sort(key=lambda r: (r["per_session"], -max(r["a"]["bytes"] or 0, r["b"]["bytes"] or 0), r["source"]))
    ma, mb = a["first_call"]["measured"]["total"], b["first_call"]["measured"]["total"]
    fa, fb = measure.family(a["session"].get("model") or ""), measure.family(b["session"].get("model") or "")
    measured = {"a": ma, "b": mb, "family": [fa, fb], "same_family": fa == fb,
                "delta": mb - ma if fa == fb else None}
    loaded = None
    if a["loaded"] is not None and b["loaded"] is not None:
        loaded = {}
        for what in ("servers", "skills"):
            xa, xb = a["loaded"][what], b["loaded"][what]
            la = {k for k, v in xa.items() if v["loaded"]}
            lb = {k for k, v in xb.items() if v["loaded"]}
            loaded[what] = {"a_only": sorted(la - lb), "b_only": sorted(lb - la), "both": len(la & lb),
                            "calls": {k: [xa.get(k, {}).get("calls", 0), xb.get(k, {}).get("calls", 0)]
                                      for k in sorted({*xa, *xb}) if xa.get(k, {}).get("calls") or xb.get(k, {}).get("calls")}}
    return {"comparable": {"bytes": same_split, "tokens": same_tokens}, "token_notes": notes,
            "estimator": [a["estimator"], b["estimator"]],
            "counting_revision": [runs.counting_of(a), runs.counting_of(b)],
            "sources": rows if same_split else [], "measured": measured, "loaded": loaded}


# ---------------------------------------------------------------------- around a split point
def _sessions(agent: str, root: Path, cache: Path) -> list[eve.Session]:
    if agent == "codex":
        return eve.codex_sessions(root, cache / "context-usage-codex.json")[0]
    return eve.load_sessions(root, cache / "eve.json")


def pick_pair(sessions: list[eve.Session], split: dict, folder: Path) -> tuple[eve.Session | None, eve.Session | None, bool]:
    """(newest before, newest since, in this folder) around the split. Same folder when the folder has a session on
    both sides, and the same model family when the folder has one before."""
    memo: dict = {}
    key = eve.folder_key(str(folder), memo)
    by_side: dict[str, list[eve.Session]] = {"before": [], "after": []}
    for s in sessions:
        sd = measure.side_of(s.start, split)
        if sd and not s.resumed and s.cwd:
            by_side[sd].append(s)
    for sd in by_side:
        by_side[sd].sort(key=lambda s: (measure.session_time(s.start), s.path))
    mine = {sd: [s for s in ss if eve.folder_key(s.cwd, memo) == key] for sd, ss in by_side.items()}
    here = bool(mine["before"] and mine["after"])
    pool = mine if here else by_side
    if not pool["after"]:
        return (pool["before"][-1] if pool["before"] else None), None, here
    after = pool["after"][-1]
    fam = measure.family(after.model)
    same = [s for s in pool["before"] if measure.family(s.model) == fam]
    before = (same or pool["before"] or [None])[-1]
    return before, after, here


def _kind(row: dict) -> str | None:
    """How the sessions of a plan row are counted: skill, plugin or server. None for a row they do not show."""
    typ = (row.get("source") or {}).get("type") or ""
    if "skill" in typ:
        return "skill"
    if typ == "plugin":
        return "plugin"
    if "MCP server" in typ or "connector" in typ:
        return "server"
    return None


def applied_rows(out: Path, split: dict, root: Path, cache: Path) -> list[dict]:
    """Each row the apply changed: the sessions that loaded it and used it, before and since (all folders)."""
    a = split.get("apply") or {}
    try:
        rec = json.loads((out / a["id"] / "apply.json").read_text())
    except (OSError, ValueError, KeyError, TypeError):
        rec = {}
    changed = [r["id"] for r in rec.get("rows") or [] if r.get("changed")] or list(a.get("rows") or [])
    plan_path = (rec.get("plan") or {}).get("path") or str(out / (a.get("parent_id") or "-") / "plan.json")
    try:
        plan = {r["id"]: r for r in json.loads(Path(plan_path).read_text()).get("rows") or []}
    except (OSError, ValueError):
        plan = {}
    want = [(i, plan.get(i)) for i in changed]
    if not want:
        return []
    rows = context.load_rows(root, cache / "context-usage.json")
    names = context.connector_names(list(rows.values()))
    sessions: dict[str, dict] = {}
    for r in rows.values():
        key = f"{r['slug']}/{r['session']}"
        e = sessions.setdefault(key, {"main": None, "files": []})
        e["files"].append(r)
        if r["main"]:
            e["main"] = r
    out_rows = []
    for rid, prow in want:
        kind = _kind(prow) if prow else None
        name = ((prow or {}).get("source") or {}).get("key") or ((prow or {}).get("source") or {}).get("name")
        res = {"id": rid, "kind": kind, "name": name, "title": (prow or {}).get("title"),
               "before": {"sessions": 0, "loaded": 0, "used": 0}, "after": {"sessions": 0, "loaded": 0, "used": 0}}
        for e in sessions.values():
            m = e["main"]
            sd = measure.side_of(m["start"], split) if m else None
            if sd is None:
                continue
            res[sd]["sessions"] += 1
            if kind is None:
                continue
            loaded, used = _loaded_used(kind, name, m, e["files"], names)
            res[sd]["loaded"] += loaded
            res[sd]["used"] += used
        out_rows.append(res)
    return out_rows


def _loaded_used(kind: str, name: str, m: dict, files: list[dict], names: dict) -> tuple[bool, bool]:
    calls: dict[str, int] = {}
    for f in files:
        for tool, n in f["tools"].items():
            calls[tool] = calls.get(tool, 0) + n
    invoked = {k for f in files for k in [*f["skills"], *f["slash"]]}
    if kind == "skill":
        short = name.rsplit(":", 1)[-1]
        return name in m["listed"], name in invoked or (short in invoked and ":" in name)
    if kind == "plugin":
        servers = {context.canonical(k, names) for k in [*m["servers"], *m["instr"]]}
        loaded = any(x.startswith(name + ":") for x in m["listed"]) or any(k.startswith(f"plugin:{name}:") for k in servers)
        used = any(k.startswith(name + ":") for k in invoked) or any(
            t.startswith("mcp__") and context.canonical(transcript.server_key(t[5:].split("__", 1)[0]), names).startswith(f"plugin:{name}:")
            for t in calls)
        return loaded, used
    servers = {context.canonical(k, names) for k in [*m["servers"], *m["instr"]]}
    used = any(t.startswith("mcp__") and context.canonical(transcript.server_key(t[5:].split("__", 1)[0]), names) == name
               for t in calls)
    return name in servers, used


# ---------------------------------------------------------------------- text
def _num(x) -> str:
    return "-" if x is None else f"{x:,}"


def _signed(x) -> str:
    if x is None:
        return "-"
    return "0" if x == 0 else ("-" if x < 0 else "+") + f"{abs(x):,}"


def _who(s: dict, home: Path) -> str:
    sess = s["session"]
    return (f"{(sess.get('id') or '-')[:8]}  {measure.local(sess.get('start'))}  {measure.family(sess.get('model') or '')}"
            f"  {sess.get('entrypoint') or '-'}  {brief.shown(sess.get('cwd'), None, home)}")


def lines(res: dict, lang: str = "en", home: Path = HOME) -> list[str]:
    a, b, c = res["a"], res["b"], res["compare"]
    tags = res["tags"]
    out = [res["head"]]
    if res.get("split"):
        sp = res["split"]
        if sp["apply"]:
            out.append(t("measure.apply_line", lang, id=sp["apply"]["id"], plan=sp["apply"].get("parent_id") or "-",
                         n=len(sp["apply"].get("rows") or [])))
            out.append(t("compare.newest", lang))
        else:
            out.append(t("compare.split_at", lang, at=measure.local(sp["at"])))
        if not res["same_folder"]:
            out.append(t("compare.other_folders", lang))
    out.append(f"{brief.pad(tags[0], 7)}{_who(a, home)}")
    out.append(f"{brief.pad(tags[1], 7)}{_who(b, home)}")
    est, rev = c["estimator"], c["counting_revision"]
    out.append(t("diff.estimator_same", lang, a=est[0], b=est[1]) + "  " + t("compare.revision", lang, a=rev[0], b=rev[1]))
    out += c["token_notes"]
    if not c["comparable"]["bytes"]:
        out.append(t("compare.split_changed", lang))
    out.append("")
    if c["sources"]:
        head = [t("compare.col.source", lang), tags[0], tags[1], "+/-", t("compare.col.tokens", lang)]
        table = [head]
        for r in c["sources"]:
            label = r["label"] + (" *" if r["per_session"] else "")
            table.append([brief.clip(label, LABEL_COLS), _num(r["a"]["bytes"]), _num(r["b"]["bytes"]),
                          _signed(r["delta_bytes"]), _signed(r["delta_tokens"]) if c["comparable"]["tokens"] else "-"])
        cols = [max(brief.width(row[i]) for row in table) for i in range(5)]
        for row in table:
            out.append("  ".join([brief.pad(row[0], cols[0])] + [" " * (cols[i] - brief.width(row[i])) + row[i]
                                                                 for i in range(1, 5)]).rstrip())
        if any(r["per_session"] for r in c["sources"]):
            out.append(t("compare.per_session", lang))
        changed = [r for r in c["sources"] if r["inside"] and not r["per_session"]]
        if changed:
            out.append(t("compare.inside", lang))
            for r in changed:
                items = [f"{brief.shown(n, None, home) if r['source'] in ('instructions', 'agents_md') else n} "
                         f"{_signed(b - a)}" for n, a, b in r["inside"][:INSIDE_SHOWN]]
                if len(r["inside"]) > INSIDE_SHOWN:
                    items.append(t("compare.more", lang, n=len(r["inside"]) - INSIDE_SHOWN))
                out += brief.wrap_list(f"  {r['label']}: ", items, "    ")
    m = c["measured"]
    if m["same_family"]:
        text = t("compare.measured", lang, a=_num(m["a"]), b=_num(m["b"]), d=_signed(m["delta"]))
    else:
        text = t("compare.measured_family", lang, a=_num(m["a"]), b=_num(m["b"]), fa=m["family"][0], fb=m["family"][1])
    out.append(text)
    out.append(t("compare.one_each", lang) if m["same_family"] else t("compare.family_differs", lang))
    if c["loaded"] is None:
        out += brief.wrap(t("compare.no_loaded", lang), brief.WIDTH)
    else:
        for what in ("servers", "skills"):
            x = c["loaded"][what]
            for tag, names in ((tags[0], x["a_only"]), (tags[1], x["b_only"])):
                items = names[:NAMES_SHOWN] + ([t("compare.more", lang, n=len(names) - NAMES_SHOWN)]
                                               if len(names) > NAMES_SHOWN else []) or ["-"]
                out += brief.wrap_list(t(f"compare.only.{what}", lang, side=tag) + " ", items, "  ")
    if res.get("applied"):
        out += ["", t("compare.applied_head", lang)]
        for r in res["applied"]:
            x, y = r["before"], r["after"]
            what = f"{r['kind'] or '-'} {r['name'] or '-'}"
            if r["kind"] is None:
                out += brief.wrap(t("compare.applied_none", lang, id=r["id"], what=what), brief.WIDTH, "  ", "    ")
                continue
            out += brief.wrap(t("compare.applied_line", lang, id=r["id"], what=what, la=x["loaded"], na=x["sessions"],
                                lb=y["loaded"], nb=y["sessions"], ua=x["used"], ub=y["used"]), brief.WIDTH, "  ", "    ")
    if res.get("measure"):
        blk = res["measure"]
        out += ["", t("compare.measure_head", lang, folder=brief.shown(blk["folder"]["path"], None, home))]
        if blk["rows"]:
            shown, few = eve._shown_rows(blk, blk["rows"])
            if shown:
                out += eve._table(*eve._rows(blk, shown, ["first_call"], lang, False))
            out += eve._too_few_line(blk, few, lang)
            c1 = (blk["rows"][0].get("change") or {}).get("first_call")
            if c1 and c1["state"] == "too_few":
                out.append(t("measure.need", lang, n=measure.MIN_COMPARE, more=c1["need"]))
        else:
            out.append(t("measure.no_sessions", lang, folder=brief.shown(blk["folder"]["path"], None, home)))
        out.append(t("measure.not_proof", lang))
    return out


def markdown(res: dict, text: list[str]) -> str:
    md = ["# walle context --compare", "", "```text", *text, "```", ""]
    c, tags = res["compare"], res["tags"]
    if c["sources"]:
        md += ["## Sources", "", f"| Source | Bytes {tags[0]} | Bytes {tags[1]} | +/- | Est. tokens {tags[0]} | "
                                 f"Est. tokens {tags[1]} | +/- |", "|---|---:|---:|---:|---:|---:|---:|"]
        for r in c["sources"]:
            tok = c["comparable"]["tokens"]
            md.append(f"| {r['label']}{' (per session)' if r['per_session'] else ''} | {_num(r['a']['bytes'])} | "
                      f"{_num(r['b']['bytes'])} | {_signed(r['delta_bytes'])} | {_num(r['a']['tokens_est'])} | "
                      f"{_num(r['b']['tokens_est'])} | {_signed(r['delta_tokens']) if tok else '-'} |")
        md.append("")
    if c["loaded"]:
        for what in ("servers", "skills"):
            x = c["loaded"][what]
            md += [f"## {what.capitalize()}", "", f"- Loaded in {tags[0]} only: {', '.join(x['a_only']) or '-'}",
                   f"- Loaded in {tags[1]} only: {', '.join(x['b_only']) or '-'}", f"- Loaded in both: {x['both']}"]
            if x["calls"]:
                md.append(f"- Calls ({tags[0]} -> {tags[1]}): " + ", ".join(f"{k} {v[0]} -> {v[1]}" for k, v in x["calls"].items()))
            md.append("")
    return "\n".join(md) + "\n"


# ---------------------------------------------------------------------- CLI
def run_cli(args, home: Path = HOME) -> int:
    from .cli import out_in_scan
    lang = resolve_lang(getattr(args, "lang", None))
    refs = list(args.compare)
    agent = args.agent
    root = (args.transcripts or (home / ".codex" if agent == "codex" else args.claude_transcripts)).expanduser()
    estimator = estimate.name(args.estimator or (estimate.CODEX_DEFAULT if agent == "codex" else estimate.DEFAULT))
    folder = (args.root or Path.cwd()).expanduser().resolve()
    cache = args.out.parent / "cache"
    # With a split point, the command measures the sessions of a folder: it writes nothing inside it.
    hit = out_in_scan(args.out, [folder], home) if len(refs) == 1 else None
    if hit:
        print(t("out.in_folder", lang, path=hit[0], root=hit[1]), file=sys.stderr)
        return 2
    res: dict = {"split": None, "applied": [], "measure": None, "same_folder": True}
    names = cached_names(cache) if agent == "claude" else {}
    try:
        if len(refs) == 1:
            try:
                split = measure.split_point(args.out, refs[0], args.days)
            except measure.SplitError as e:
                raise CompareError("compare.bad_ref", ref=refs[0]) from None
            sessions = _sessions(agent, root, cache)
            before, after, here = pick_pair(sessions, split, folder)
            if after is None or before is None:
                print(t("compare.no_pair", lang, side=t("measure.side." + ("after" if after is None else "before"), lang),
                        at=measure.local(split["at"])))
                return 0
            a = from_session(agent, root, Path(before.path).stem if agent == "claude" else _thread(before), estimator, names)
            b = from_session(agent, root, Path(after.path).stem if agent == "claude" else _thread(after), estimator, names)
            res.update(split=split, same_folder=here, tags=[t("measure.side.before", lang), t("measure.side.after", lang)])
            if split["apply"] and agent == "claude":
                res["applied"] = applied_rows(args.out, split, root, cache)
            res["measure"] = eve.measure_block(sessions, agent, folder, split)
            res["head"] = f"walle context --compare {refs[0]}  {agent}"
        elif len(refs) == 2:
            a = side(args.out, refs[0], agent, root, estimator, names)
            b = side(args.out, refs[1], agent, root, estimator, names)
            res.update(tags=["A", "B"], head=f"walle context --compare {refs[0]} {refs[1]}  {a['agent']}")
        else:
            raise CompareError("compare.args")
        res["compare"] = compare_pair(a, b, lang)
    except CompareError as e:
        print(t(e.key, lang, **e.fields), file=sys.stderr)
        return 2
    res.update(a=a, b=b)
    text = lines(res, lang, home)
    run_dir = runs.new_run_dir(args.out)
    now = datetime.now().isoformat(timespec="seconds")
    parent = ((res["split"] or {}).get("apply") or {}).get("id")
    data = {"schema": 1, "generated": now, "note": context.NOTE,
            "run": {"id": run_dir.name, "kind": "compare", "parent_id": parent, "label": args.label,
                    "walle_version": __version__, "agent": a["agent"], "estimator": b["estimator"],
                    "counting_revision": runs.counting_of(b), "refs": refs, "folder": str(folder)},
            **{k: v for k, v in res.items() if k not in ("head",)}}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "compare.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=sorted))
    (run_dir / "compare.md").write_text(markdown(res, text))
    runs.append_row(args.out, {"id": run_dir.name, "kind": "compare", "label": args.label, "parent_id": parent,
                               "generated": now, "roots": [str(folder)] if res["measure"] else [], "agent": a["agent"],
                               "refs": refs, "estimator": b["estimator"], "counting_revision": runs.counting_of(b),
                               "walle_version": __version__, "path": str(run_dir / "compare.json")})
    print("\n".join(text))
    print(run_dir / "compare.md")
    return 0


def _thread(s: eve.Session) -> str:
    return (rollout.name_parts(Path(s.path)) or (Path(s.path).stem, None))[0]
