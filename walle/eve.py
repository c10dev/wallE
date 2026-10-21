"""walle eve (alias walle measure): measure real sessions from Claude Code transcripts.

For each top-level session file under ~/.claude/projects/<slug>/<session>.jsonl:
- baseline: input + cache_creation + cache_read tokens of the first API call.
  This is the whole prefix (system prompt, tool catalog, instruction stack)
  plus the first prompt.
- steps: user prompts, tool calls, check calls (test, lint, typecheck, gate),
  tool calls before the first edit.

Sessions whose folder has no project CLAUDE.md are the control group. The
baseline delta of a group is its baseline minus the control median for the
same model, so the system prompt and tool catalog cancel out. That summary of
every folder group is the `groups` list of eve.json (scan.html reads it).

The command shows one folder (default: the current one), one model family per row
(measure.family), median [p25-p75] and n, by the n rule of brief 5.5 (measure.py).
With --around <apply-id|date> it splits the sessions of that folder into a before
and a since window. --agent codex reads Codex rollouts and gives first-call input
only. --across-folders adds a table of every folder, for reference: folders differ
in model, connectors and task mix, so it ranks nothing.

A session that resumes or forks another one starts with that session's history, so
its first call is not comparable: it is left out of the folder rows. The run kind
is "measure"; the files are eve.json and eve.md.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import __version__, brief, measure, runs
from .collect import HOME, find_checkout
from .strings import resolve_lang, t, tn

CHECK_RE = re.compile(
    r"\b(?:pytest|vitest|jest|playwright test|tsc\b|eslint|ruff|mypy|unittest|"
    r"npm (?:run )?(?:test|lint|check|typecheck|build)|pnpm (?:test|lint|check|typecheck|build)|"
    r"yarn (?:test|lint)|make (?:test|check|lint)|go test|cargo test|verify[- ]gate)",
)
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CACHE_VERSION = 4  # 4: entrypoint and resumed
SCRATCH_ROOTS = (Path("/tmp"), Path("/var/tmp"))


@dataclass
class Session:
    path: str
    cwd: str
    start: str
    model: str
    baseline: int
    api_calls: int
    prompts: int
    tool_calls: int
    check_calls: int
    calls_before_edit: int | None
    output_tokens: int
    input_tokens: int
    group: str = ""
    control: bool = False
    entrypoint: str = ""  # claude-desktop, cli, sdk-py ...; for Codex the originator
    resumed: bool = False  # starts with another session's history (resume or fork)


def parse_session(path: Path) -> Session | None:
    cwd = start = model = entrypoint = ""
    resumed = False
    baseline = None
    msg_ids: set[str] = set()
    tool_ids: set[str] = set()
    prompts = checks = out_tok = in_tok = 0
    before_edit: int | None = None
    with path.open("rb") as fh:
        for raw in fh:
            if b'"assistant"' not in raw and b'"user"' not in raw:
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if d.get("isSidechain"):
                continue
            t = d.get("type")
            if not cwd and d.get("cwd"):
                cwd = d["cwd"]
            if not start and d.get("timestamp"):
                start = d["timestamp"]
            entrypoint = entrypoint or d.get("entrypoint") or ""
            if baseline is None and d.get("sessionId") and d["sessionId"] != path.stem:
                resumed = True  # a line copied from the session it resumes
            msg = d.get("message") or {}
            if t == "user" and not d.get("isMeta"):
                c = msg.get("content")
                if isinstance(c, str) or (
                    isinstance(c, list) and any(b.get("type") == "text" for b in c if isinstance(b, dict))
                    and not any(b.get("type") == "tool_result" for b in c if isinstance(b, dict))
                ):
                    prompts += 1
            elif t == "assistant":
                mid = msg.get("id") or d.get("uuid")
                u = msg.get("usage") or {}
                if mid not in msg_ids:
                    msg_ids.add(mid)
                    total_in = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                                + u.get("cache_read_input_tokens", 0))
                    in_tok += total_in
                    out_tok += u.get("output_tokens", 0)
                    if baseline is None and total_in:
                        baseline = total_in
                        model = msg.get("model", "")
                for b in msg.get("content") or []:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    if b.get("id") in tool_ids:
                        continue
                    tool_ids.add(b.get("id"))
                    name = b.get("name", "")
                    if name in EDIT_TOOLS and before_edit is None:
                        before_edit = len(tool_ids) - 1
                    if name == "Bash" and CHECK_RE.search(str((b.get("input") or {}).get("command", ""))):
                        checks += 1
    if baseline is None or not cwd:
        return None
    return Session(str(path), cwd, start, model, baseline, len(msg_ids), prompts,
                   len(tool_ids), checks, before_edit, out_tok, in_tok, entrypoint=entrypoint, resumed=resumed)


def load_sessions(transcripts: Path, cache_file: Path) -> list[Session]:
    cache: dict = {}
    if cache_file.is_file():
        try:
            cache = json.loads(cache_file.read_text())
            if cache.get("_v") != CACHE_VERSION:
                cache = {}
        except ValueError:
            cache = {}
    cache["_v"] = CACHE_VERSION
    out: list[Session] = []
    for p in sorted(transcripts.glob("*/*.jsonl")):
        st = p.stat()
        key = str(p)
        stamp = f"{st.st_size}:{int(st.st_mtime)}"
        hit = cache.get(key)
        if hit and hit.get("stamp") == stamp:
            s = hit.get("s")
        else:
            parsed = parse_session(p)
            s = asdict(parsed) if parsed else None
            cache[key] = {"stamp": stamp, "s": s}
        if s:
            out.append(Session(**s))
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache))
    return out


def group_key(cwd: str) -> tuple[str, bool]:
    """Group by the nearest folder with a project CLAUDE.md, mapped to the main repo."""
    folder = Path(cwd)
    if any(folder == r or r in folder.parents for r in SCRATCH_ROOTS):
        # Scratch and probe sessions are neither a project nor a fair control.
        return ("(scratch: /tmp)", False)
    home_claude = (HOME / ".claude" / "CLAUDE.md").resolve()
    # A deleted worktree still maps to its repository through the nearest folder that exists.
    existing = next((d for d in [folder, *folder.parents] if d.exists()), None)
    checkout, repo, _wt = find_checkout(existing) if existing else (None, None, False)
    for d in [folder, *folder.parents]:
        p = d / "CLAUDE.md"
        if p.is_file() and p.resolve() != home_claude:
            if checkout and repo and (d == checkout or checkout in d.parents):
                sub = d.relative_to(checkout)
                return (repo.name + ("" if str(sub) == "." else f"/{sub}"), False)
            return (str(d), False)
        if checkout and d == checkout:
            break
    return ("(control: no project CLAUDE.md)", True)


def model_family(model: str) -> str:
    m = re.match(r"claude-(\w+)", model or "")
    return m.group(1) if m else (model or "unknown")


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs)) if xs else None


def summarize(sessions: list[Session], since: str | None = None, before: str | None = None,
              min_n: int = 3) -> dict:
    def in_range(s: Session) -> bool:
        return (not since or s.start >= since) and (not before or s.start < before)

    keep = [s for s in sessions if in_range(s)]
    for s in keep:
        s.group, s.control = group_key(s.cwd)
    control: dict[str, int] = {}
    for fam in {model_family(s.model) for s in keep}:
        xs = [s.baseline for s in keep if s.control and model_family(s.model) == fam]
        if len(xs) >= min_n:
            control[fam] = round(statistics.median(xs))
    groups: dict[str, list[Session]] = {}
    for s in keep:
        groups.setdefault(s.group, []).append(s)
    rows = []
    for g, ss in groups.items():
        deltas = [s.baseline - control[model_family(s.model)] for s in ss if model_family(s.model) in control]
        rows.append({
            "group": g,
            "control": ss[0].control,
            "n": len(ss),
            "baseline_median": _median([s.baseline for s in ss]),
            "baseline_delta_median": _median(deltas) if len(deltas) >= min_n else None,
            "prompts_median": _median([s.prompts for s in ss]),
            "tool_calls_median": _median([s.tool_calls for s in ss]),
            "tool_calls_per_prompt_median": _median(
                [s.tool_calls / s.prompts for s in ss if s.prompts]),
            "check_calls_median": _median([s.check_calls for s in ss]),
            "calls_before_first_edit_median": _median([s.calls_before_edit for s in ss]),
            "output_tokens_total": sum(s.output_tokens for s in ss),
            "input_tokens_total": sum(s.input_tokens for s in ss),
        })
    rows.sort(key=lambda r: (-r["n"], r["group"]))
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "since": since,
        "before": before,
        "sessions": len(keep),
        "control_baseline_by_model": control,
        "groups": rows,
    }


def run(since=None, before=None, transcripts: Path | None = None, cache_dir: Path | None = None) -> dict:
    transcripts = transcripts or HOME / ".claude" / "projects"
    cache_dir = cache_dir or HOME / ".walle" / "cache"
    sessions = load_sessions(transcripts, cache_dir / "eve.json")
    return summarize(sessions, since, before)


# ---------------------------------------------------------------------- one folder, one model family per row
METRICS = {"claude": ("first_call", "calls_per_prompt", "calls_before_edit"), "codex": ("first_call",)}


def folder_key(cwd: str, memo: dict | None = None) -> str:
    """The folder a session counts for: its group (group_key); for a folder without a project CLAUDE.md, its
    repository (worktrees fold into it), else the folder itself. The control group is not one folder."""
    if memo is not None and cwd in memo:
        return memo[cwd]
    key, control = group_key(cwd)
    if control:
        folder = Path(cwd)
        existing = next((d for d in [folder, *folder.parents] if d.exists()), None)
        checkout, repo, _wt = find_checkout(existing) if existing else (None, None, False)
        key = repo.name if checkout and repo else str(folder)
    if memo is not None:
        memo[cwd] = key
    return key


def codex_sessions(root: Path, cache_file: Path) -> tuple[list[Session], dict]:
    """One Session per Codex thread, from its first rollout file. Subagent threads are left out; a thread that
    resumes or forks another one is marked resumed. Only first-call input is recorded."""
    from . import codex_context
    rows, skipped = codex_context.load_rows(root, cache_file)
    threads: dict[str, list[tuple[str, dict]]] = {}
    for path, r in rows.items():
        threads.setdefault(r["thread"], []).append((path, r))
    out: list[Session] = []
    left = {"subagent": 0}
    for _tid, rs in sorted(threads.items()):
        path, r = sorted(rs, key=lambda x: (x[1]["page"] is not None, x[0]))[0]
        if r["subagent"]:
            left["subagent"] += 1
            continue
        if r["first_input"] is None or not r["cwd"]:
            continue
        out.append(Session(path, r["cwd"], r["start"], r["model"], r["first_input"], 0, 0,
                           sum((r.get("tools") or {}).values()), 0, None, 0, 0,
                           entrypoint=r["originator"] or "", resumed=bool(r["resumed"] or r["forked"])))
    return out, {**left, **(skipped or {})}


def _value(ss: list[Session], metric: str) -> dict:
    if metric == "first_call":
        return measure.stats([s.baseline for s in ss])
    if metric == "calls_per_prompt":
        return measure.stats([s.tool_calls / s.prompts for s in ss if s.prompts], digits=1)
    st = measure.stats([s.calls_before_edit for s in ss])
    st["no_edit"] = sum(1 for s in ss if s.calls_before_edit is None)
    return st


def _in_period(s: Session, since, before) -> bool:
    when = measure.session_time(s.start)
    if when is None:
        return False
    return (since is None or when >= since) and (before is None or when < before)


def measure_block(sessions: list[Session], agent: str, folder: Path, split: dict | None = None,
                  since: str | None = None, before: str | None = None, across: bool = False) -> dict:
    """The measure of one folder: one row per model family, a before and an after side when split is given,
    else one period side. With across, the same rows for every folder (reference only, never ranked)."""
    lo, hi = measure.parse_when(since), measure.parse_when(before)
    memo: dict = {}
    key = folder_key(str(folder), memo)
    sides = ("before", "after") if split else ("period",)
    cells: dict[tuple[str, str], dict[str, list[Session]]] = {}
    resumed: dict[str, int] = {}
    for s in sessions:
        if not _in_period(s, lo, hi):
            continue
        side = measure.side_of(s.start, split) if split else "period"
        if side is None:
            continue
        k = folder_key(s.cwd, memo)
        if s.resumed:
            resumed[k] = resumed.get(k, 0) + 1
            continue
        cells.setdefault((k, measure.family(s.model)), {x: [] for x in sides})[side].append(s)

    def rows_of(k: str) -> list[dict]:
        rows = []
        for (kk, fam), by in cells.items():
            if kk != k:
                continue
            row = {"family": fam, "models": {x: measure.mix([s.model for s in by[x]]) for x in sides},
                   "entrypoints": {x: measure.mix([s.entrypoint or "unknown" for s in by[x]]) for x in sides}}
            for x in sides:
                row[x] = {"sessions": len(by[x]), **{m: _value(by[x], m) for m in METRICS[agent]}}
            if split:
                row["change"] = {m: measure.change(row["before"][m], row["after"][m]) for m in METRICS[agent]}
            rows.append(row)
        rows.sort(key=lambda r: (-sum(r[x]["sessions"] for x in sides), r["family"]))
        return rows

    block = {"agent": agent, "folder": {"path": str(folder), "key": key}, "sides": list(sides),
             "split": split, "period": {"since": since, "before": before},
             "rules": {"min_show": measure.MIN_SHOW, "min_compare": measure.MIN_COMPARE, "noise": measure.NOISE_RULE,
                       "quantiles": "statistics.quantiles(n=4, method='inclusive')", "family": "model id without date"},
             "metrics": list(METRICS[agent]), "rows": rows_of(key), "left_out": {"resumed": resumed.get(key, 0)}}
    if across:
        block["across"] = sorted(({"folder": k, **r} for k in {k for k, _ in cells} for r in rows_of(k)),
                                 key=lambda r: (-sum(r[x]["sessions"] for x in sides), r["folder"], r["family"]))
    return block


# ---------------------------------------------------------------------- text
def _num(x) -> str:
    if x is None:
        return "-"
    return f"{x:,.1f}" if isinstance(x, float) else f"{x:,}"


def _signed(x) -> str:
    if not x:
        return "0"
    return ("-" if x < 0 else "+") + _num(abs(x))


def cell(st: dict, metric: str, sessions: int, lang: str) -> str:
    """One measured value as median [p25-p75]. Exact numbers, no "~" (brief 4.4)."""
    if st["median"] is None:
        if metric == "calls_before_edit" and sessions >= measure.MIN_SHOW:
            return t("measure.no_edit", lang) if st.get("no_edit") == sessions else t("measure.few_edits", lang, n=st["n"])
        return "-"
    return f"{_num(st['median'])} [{_num(st['p25'])}-{_num(st['p75'])}]"


def change_cell(c: dict, lang: str) -> str:
    if c["state"] == "too_few":
        return ""
    if c["state"] == "within_noise":
        return t("measure.noise_cell", lang, delta=_signed(c["delta"]))
    return t(f"measure.{c['state']}_cell", lang, delta=_signed(c["delta"]))


def _table(rows: list[list[str]], spans: set[int]) -> list[str]:
    """Rows of cells joined with two spaces. A row in spans ends with one cell that may run past its column."""
    n = max(len(r) for r in rows)
    cols = [0] * n
    for i, r in enumerate(rows):
        for j, c in enumerate(r):
            if i in spans and j == len(r) - 1:
                continue
            cols[j] = max(cols[j], brief.width(c))
    out = []
    for i, r in enumerate(rows):
        parts = [c if j == len(r) - 1 else brief.pad(c, cols[j]) for j, c in enumerate(r)]
        out.append("  ".join(parts).rstrip())
    return out


def _rows(block: dict, rows: list[dict], metrics: list[str], lang: str, folder_col: bool) -> tuple[list[list[str]], set[int]]:
    head = ([t("table.folder", lang)] if folder_col else []) + [t("measure.col.model", lang), t("measure.col.side", lang),
                                                                 t("measure.col.sessions", lang)]
    head += [t(f"measure.col.{m}", lang) for m in metrics]
    table, spans = [head], set()
    for r in rows:
        for n, side in enumerate(block["sides"]):
            v = r[side]
            lead = ([r["folder"] if n == 0 else ""] if folder_col else []) + [r["family"] if n == 0 else "",
                                                                               t(f"measure.side.{side}", lang), str(v["sessions"])]
            if v["sessions"] < measure.MIN_SHOW:
                spans.add(len(table))
                table.append(lead + [t("measure.too_few_cell", lang, n=v["sessions"], need=measure.MIN_COMPARE)])
                continue
            cells = [cell(v[m], m, v["sessions"], lang) for m in metrics]
            if v["first_call"]["few"]:
                cells[-1] += "  " + t("measure.few", lang)
                spans.add(len(table))
            table.append(lead + cells)
        ch = r.get("change") or {}
        if not folder_col and any(c["state"] != "too_few" for c in ch.values()):
            table.append(["", t("measure.side.change", lang), ""] + [change_cell(ch[m], lang) for m in metrics])
    return table, spans


def _mix_line(label: str, block: dict, field: str, lang: str) -> list[str]:
    """The model (or app) mix of each side: one line when it fits, else one line per side."""
    sides = []
    for side in block["sides"]:
        total: dict[str, int] = {}
        for r in block["rows"]:
            for k, v in r[field][side].items():
                k = measure.family(k) if field == "models" else k
                total[k] = total.get(k, 0) + v
        items = [f"{k} {v}" for k, v in sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))] or ["-"]
        sides.append((t("measure.side." + side, lang) + ":", items))
    lead = brief.pad(label, 8)
    one = lead + " | ".join(f"{name} {', '.join(items)}" for name, items in sides)
    if brief.width(one) <= brief.WIDTH:
        return [one]
    out = []
    for n, (name, items) in enumerate(sides):
        out += brief.wrap_list((lead if n == 0 else " " * 8) + name + " ", items, " " * 10)
    return out


def _shown_rows(block: dict, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Rows with a value on some side, and rows with too few sessions on every side (one summary line)."""
    shown = [r for r in rows if any(r[s]["sessions"] >= measure.MIN_SHOW for s in block["sides"])]
    return shown, [r for r in rows if r not in shown]


def _too_few_line(block: dict, rows: list[dict], lang: str, folder_col: bool = False) -> list[str]:
    if not rows:
        return []
    items = []
    for r in rows:
        counts = ", ".join(f"{r[s]['sessions']} {t('measure.side.' + s, lang)}" for s in block["sides"])
        items.append(f"{r['folder'] + ' ' if folder_col else ''}{r['family']} ({counts})")
    return brief.wrap_list(t("measure.too_few_rows", lang) + " ", items, "  ")


def lines(block: dict, lang: str = "en", home: Path = HOME) -> list[str]:
    """The terminal view of a measure block: at most 80 columns, plain ASCII structure."""
    split = block["split"]
    folder = brief.shown(block["folder"]["path"], None, home)
    head = f"walle measure {folder}" + (f" --around {split['ref']}" if split else "") + f"  {block['agent']}"
    out = [head, t("measure.scope", lang)]
    if split:
        w, b = split["windows"], split["bounded_by"]
        for side, edge in (("before", "before"), ("after", "after")):
            text = t("measure.window", lang, side=brief.pad(t(f"measure.side.{side}", lang), 7),
                     a=measure.local(w[side]["from"]), b=measure.local(w[side]["to"]))
            if b[edge]:
                text += " " + t(f"measure.bound.{edge}", lang, kind=b[edge]["kind"], id=b[edge]["id"])
            out.append(text)
        if split["apply"]:
            a = split["apply"]
            out.append(t("measure.apply_line", lang, id=a["id"], plan=a.get("parent_id") or "-", n=len(a.get("rows") or [])))
    else:
        p = block["period"]
        if p["since"] or p["before"]:
            out.append(t("measure.period", lang, a=p["since"] or "-", b=p["before"] or "-"))
        else:
            out.append(t("measure.period_all", lang))
    if block["agent"] == "codex":
        out.append(t("measure.codex", lang))
    out.append("")
    if not block["rows"]:
        out.append(t("measure.no_sessions", lang, folder=folder))
    else:
        metrics = block["metrics"]
        shown, few = _shown_rows(block, block["rows"])
        if shown:
            table, spans = _rows(block, shown, metrics, lang, False)
            text = _table(table, spans)
            if max(brief.width(x) for x in text) > brief.WIDTH and len(metrics) > 1:
                text = _table(*_rows(block, shown, metrics[:1], lang, False))
                text += [""] + _table(*_rows(block, shown, metrics[1:], lang, False))
            out += text
        out += _too_few_line(block, few, lang)
        out += _mix_line(t("measure.models", lang), block, "models", lang)
        if len({k for r in block["rows"] for s in block["sides"] for k in r["entrypoints"][s]}) > 1:
            out += _mix_line(t("measure.apps", lang), block, "entrypoints", lang)
        first = block["rows"][0]
        c = (first.get("change") or {}).get("first_call")
        if c and c["state"] == "too_few":
            out.append(t("measure.need", lang, n=measure.MIN_COMPARE, more=c["need"]))
    files = block["left_out"].get("files") or {}
    if files.get("zstd_missing") or files.get("unreadable"):
        out.append(t("measure.skipped", lang, zstd=files.get("zstd_missing", 0), other=files.get("unreadable", 0)))
    if block["left_out"].get("resumed"):
        out.append(tn("measure.left_out", block["left_out"]["resumed"], lang))
    if block.get("across"):
        out += ["", t("measure.across_head", lang)]
        shown, few = _shown_rows(block, block["across"])
        if shown:
            out += _table(*_rows(block, shown, ["first_call"], lang, True))
        out += _too_few_line(block, few, lang, True)
    out.append(t("measure.not_proof", lang))
    return out


def markdown(text: list[str]) -> str:
    return "# walle measure\n\n```text\n" + "\n".join(text) + "\n```\n"


# ---------------------------------------------------------------------- CLI
def add_parser(sub, default_transcripts: Path):
    e = sub.add_parser("eve", aliases=["measure"],
                       help="measure real sessions from transcripts: one folder, one model family per row")
    e.add_argument("folder", nargs="?", type=Path, help="the folder to measure (default: the current folder)")
    e.add_argument("--around", metavar="APPLY_ID|DATE",
                   help="split the sessions at an apply id (walle apply) or a date: before and since")
    e.add_argument("--days", type=int, default=measure.DAYS,
                   help=f"with --around: days on each side (default {measure.DAYS}); an apply or undo nearer ends a window")
    e.add_argument("--since", help="ISO date, sessions that start on or after it")
    e.add_argument("--before", help="ISO date, sessions that start before it")
    e.add_argument("--agent", choices=["claude", "codex"], default="claude",
                   help="claude: ~/.claude/projects transcripts; codex: ~/.codex rollouts (first-call input only)")
    e.add_argument("--across-folders", action="store_true",
                   help="add every folder for reference; folders differ in model, connectors and task mix")
    e.add_argument("--transcripts", type=Path, default=None,
                   help=f"where the sessions are (default {default_transcripts}; with --agent codex, ~/.codex)")
    e.add_argument("--label", help="a name for this run, kept in the run index")
    e.add_argument("--lang", choices=("en", "ko"))
    e.set_defaults(claude_transcripts=default_transcripts)
    return e


def run_cli(args, home: Path = HOME) -> int:
    import sys
    from .cli import out_in_scan
    lang = resolve_lang(args.lang)
    folder = (args.folder or Path.cwd()).expanduser().resolve()
    agent = args.agent
    root = (args.transcripts or (home / ".codex" if agent == "codex" else args.claude_transcripts)).expanduser()
    hit = out_in_scan(args.out, [folder], home)
    if hit:
        print(t("out.in_folder", lang, path=hit[0], root=hit[1]), file=sys.stderr)
        return 2
    for value in (args.since, args.before):
        if value and measure.parse_when(value) is None:
            print(t("measure.bad_date", lang, value=value), file=sys.stderr)
            return 2
    split = None
    if args.around:
        try:
            split = measure.split_point(args.out, args.around, args.days)
        except measure.SplitError as e:
            print(t(e.key, lang, **e.fields), file=sys.stderr)
            return 2
    cache = args.out.parent / "cache"
    skipped: dict = {}
    if agent == "codex":
        sessions, skipped = codex_sessions(root, cache / "context-usage-codex.json")
        if not sessions:
            print(t("empty.no_rollouts", lang, path=brief.shown(str(root), None, home)))
            return 0
        data = {"generated": datetime.now().isoformat(timespec="seconds"), "since": args.since, "before": args.before,
                "sessions": len(sessions), "control_baseline_by_model": {}, "groups": []}
    else:
        if not root.is_dir() or not any(root.glob("*/*.jsonl")):
            print(t("empty.no_transcripts", lang, path=brief.shown(str(root), None, home)))
            return 0
        sessions = load_sessions(root, cache / "eve.json")
        data = summarize(sessions, args.since, args.before)  # every folder group: scan.html reads it
    block = measure_block(sessions, agent, folder, split, args.since, args.before, args.across_folders)
    if skipped:
        block["left_out"]["files"] = skipped
    run_dir = runs.new_run_dir(args.out)
    parent = (split or {}).get("apply") or {}
    data = {"schema": 2, **data,
            "run": {"id": run_dir.name, "kind": "measure", "parent_id": parent.get("id"), "label": args.label,
                    "walle_version": __version__, "agent": agent, "transcripts": str(root), "folder": str(folder),
                    "around": args.around},
            "measure": block}
    text = lines(block, lang, home)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "eve.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (run_dir / "eve.md").write_text(markdown(text))
    runs.append_row(args.out, {"id": run_dir.name, "kind": "measure", "label": args.label, "parent_id": parent.get("id"),
                               "generated": data["generated"], "roots": [str(folder)], "agent": agent,
                               "around": args.around, "walle_version": __version__,
                               "rows": len(block["rows"]), "path": str(run_dir / "eve.json")})
    print("\n".join(text))
    print(run_dir / "eve.md")
    return 0
