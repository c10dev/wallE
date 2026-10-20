"""walle eve (alias walle measure): measure real sessions from Claude Code transcripts.

For each top-level session file under ~/.claude/projects/<slug>/<session>.jsonl:
- baseline: input + cache_creation + cache_read tokens of the first API call.
  This is the whole prefix (system prompt, tool catalog, instruction stack)
  plus the first prompt.
- steps: user prompts, tool calls, check calls (test, lint, typecheck, gate),
  tool calls before the first edit.

Sessions whose folder has no project CLAUDE.md are the control group. The
baseline delta of a group is its baseline minus the control median for the
same model, so the system prompt and tool catalog cancel out.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .collect import HOME, find_checkout

CHECK_RE = re.compile(
    r"\b(?:pytest|vitest|jest|playwright test|tsc\b|eslint|ruff|mypy|unittest|"
    r"npm (?:run )?(?:test|lint|check|typecheck|build)|pnpm (?:test|lint|check|typecheck|build)|"
    r"yarn (?:test|lint)|make (?:test|check|lint)|go test|cargo test|verify[- ]gate)",
)
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CACHE_VERSION = 3
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


def parse_session(path: Path) -> Session | None:
    cwd = start = model = ""
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
                   len(tool_ids), checks, before_edit, out_tok, in_tok)


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
