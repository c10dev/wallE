"""walle command line."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from . import eve, judge, report, report_html, runs
from .collect import HOME
from .scan import scan
from .stack import claude_stack
from .triage import BUDGET

DEFAULT_OUT = HOME / ".walle" / "runs"


def _write(out_dir: Path, name: str, data: dict, md: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (out_dir / f"{name}.md").write_text(md)
    latest = out_dir.parent / "latest"
    if latest.is_symlink() or not latest.exists():
        latest.unlink(missing_ok=True)
        latest.symlink_to(out_dir.name)  # relative, so the runs folder can move
    return out_dir / f"{name}.md"


def _scan(roots: list[Path], budget: int) -> dict:
    # scan(roots) stays the call for the default budget: callers and tests replace scan() with a one-argument function.
    return scan(roots) if budget == BUDGET else scan(roots, budget=budget)


def check(data: dict, max_tokens: int, agent: str) -> int:
    over = [s for s in data["stacks"]
            if s["tokens_est"] > max_tokens and agent in ("all", s["agent"])]
    for s in over:
        print(f"over budget: {s['folder']} ({s['agent']}) ~{s['tokens_est']} tokens > {max_tokens}")
    drift = [p for p in data["pairs"] if p["kind"] == "drift"]
    for p in drift[:10]:
        print(f"drift {p['score']}: {p['a']['file']}:{p['a']['line']} <> {p['b']['file']}:{p['b']['line']}")
    if not over:
        print(f"ok: {len(data['stacks'])} stacks within {max_tokens} estimated tokens")
    return 1 if over else 0


def alarm() -> int:
    """SessionStart hook. Silent unless the option is on and the stack is over budget."""
    if not re.match(r"^(true|1|yes|on)$", os.environ.get("CLAUDE_PLUGIN_OPTION_SESSION_ALARM", ""), re.I):
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    folder = Path(payload.get("cwd") or os.getcwd())
    try:
        budget = int(float(os.environ.get("CLAUDE_PLUGIN_OPTION_BUDGET_TOKENS") or 8000))
    except ValueError:
        budget = 8000
    st = claude_stack(folder)
    if st.tokens <= budget:
        return 0
    biggest = max((l for l in st.layers if l.path), key=lambda l: l.tokens, default=None)
    msg = (f"walle: the instruction stack for this folder is about {st.tokens} tokens, over the {budget} budget"
           + (f"; the largest layer is {biggest.path} (~{biggest.tokens})" if biggest else "")
           + ". Mention this only if the user asks about instructions or session cost.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}))
    return 0


def run_judge(args) -> int:
    # Absolute paths: batch files name their answer files, and a subagent may run in another folder.
    scan_path = (args.scan.expanduser().resolve() if args.scan
                 else judge.latest_scan(args.out, with_export=args.do_import))
    if not scan_path or not scan_path.is_file():
        print("walle judge: no scan.json found. Run `walle scan` first or pass a scan.json path.", file=sys.stderr)
        return 2
    cache = args.out.parent / "cache" / "judge.json"
    depth = "deep" if args.deep else "quick"
    try:
        if args.dry_run:
            print(judge.describe_plan(judge.plan(scan_path, args.batch_size, cache, depth)))
            return 0
        if not args.do_import:
            data = judge.export(scan_path, args.batch_size, cache, depth)
            if data["files"]:
                print(judge.describe_export(data))
                return 0
            # Everything is cached: the report can be written now.
        else:
            data = judge.import_answers(scan_path, cache, judge_name=args.judge_name)
    except judge.JudgeError as e:
        print(f"walle judge: {e}", file=sys.stderr)
        return 2
    # The judgment belongs to the scan, so it goes next to scan.json. `latest` does not move.
    out = scan_path.parent
    (out / "judge.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (out / "judge.md").write_text(report.render_judge(data))
    for e in data["errors"]:
        print(f"walle judge: {e}", file=sys.stderr)
    print(out / "judge.md")
    return 1 if data["errors"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="walle", description="Collect, compact and sort agent instruction files.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="run output folder (default ~/.walle/runs); caches go to its sibling folder cache/")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="static pass over instruction files")
    s.add_argument("roots", nargs="*", type=Path, default=[Path.cwd()])
    s.add_argument("--top", type=int, default=15)
    s.add_argument("--html", action="store_true", help=argparse.SUPPRESS)  # scan.html is always written
    s.add_argument("--max-tokens", type=int, default=BUDGET, help="token budget of one stack (default 8000)")
    s.add_argument("--label", help="a name for this run, kept in the run index (for example 'before trim')")

    c = sub.add_parser("check", help="exit 1 when a stack is over budget (for CI)")
    c.add_argument("roots", nargs="*", type=Path, default=[Path.cwd()])
    c.add_argument("--max-tokens", type=int, default=8000)
    c.add_argument("--agent", choices=["claude", "codex", "all"], default="all")

    sub.add_parser("alarm", help="SessionStart hook: one line of context when over budget")

    e = sub.add_parser("eve", aliases=["measure"], help="measure real sessions from transcripts")
    e.add_argument("--since", help="ISO date, sessions that start on or after it")
    e.add_argument("--before", help="ISO date, sessions that start before it")
    e.add_argument("--transcripts", type=Path, default=HOME / ".claude" / "projects")

    d = sub.add_parser("diff", help="compare two scan.json files")
    d.add_argument("before", type=Path)
    d.add_argument("after", type=Path)

    j = sub.add_parser("judge", help="hand conflict and cause-1 candidates to your agent, then import its verdicts")
    j.add_argument("scan", nargs="?", type=Path, help="scan.json (default: the newest run that has one)")
    j.add_argument("--import", dest="do_import", action="store_true",
                   help="read the answer files, cache the verdicts and write judge.md")
    j.add_argument("--batch-size", type=int, default=judge.BATCH, help="items per batch file")
    j.add_argument("--judge-name", default="agent", help="who judged, stored with each verdict (for example a model id)")
    j.add_argument("--deep", action="store_true",
                   help="let the judge open the instruction files, not only the batch file (more tokens)")
    j.add_argument("--dry-run", action="store_true", help="print the items and the estimated tokens; write nothing")

    args = ap.parse_args(argv)
    args.out = args.out.expanduser().resolve()
    if args.cmd == "judge":
        return run_judge(args)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.cmd == "scan":
        roots = [r.expanduser().resolve() for r in args.roots]
        data = _scan(roots, args.max_tokens)
        run_dir = runs.new_run_dir(args.out)
        data.setdefault("run", runs.run_block(roots, []))
        data["run"].update(id=run_dir.name, label=args.label,
                           parent_id=runs.parent_of(args.out, data["run"]["roots"]))
        path = _write(run_dir, "scan", data, report.render_scan(data, args.top))
        runs.append_index(args.out, data, run_dir / "scan.json")
        (run_dir / "scan.html").write_text(report_html.render(data, args.out), encoding="utf-8")
    elif args.cmd == "check":
        roots = [r.expanduser().resolve() for r in args.roots]
        return check(_scan(roots, args.max_tokens), args.max_tokens, args.agent)
    elif args.cmd == "alarm":
        return alarm()
    elif args.cmd in ("eve", "measure"):
        data = eve.run(args.since, args.before, args.transcripts, args.out.parent / "cache")
        path = _write(args.out / stamp, "eve", data, report.render_eve(data))
    else:
        a = json.loads(args.before.read_text())
        b = json.loads(args.after.read_text())
        md = report.render_diff(a, b)
        sys.stdout.write(md)
        return 0
    print(path)
    return 0
