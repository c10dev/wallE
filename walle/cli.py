"""walle command line."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from . import apply, brief, context, estimate, eve, judge, keep, report, report_html, runs, trim
from .collect import HOME
from .scan import scan
from .stack import claude_stack
from .strings import LANGS, resolve_lang, t
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


ESTIMATOR_CHOICES = [*estimate.ALIASES, *estimate.ESTIMATORS]
ESTIMATOR_HELP = ("token estimator: v2 (bytes-v2, default; the same prose rates as walle context) or v1 "
                  "(bytes-v1, the first estimator). Each has its own default budget")


def _budget(args) -> int:
    """--max-tokens, or the default budget of the estimator: 15,000 for bytes-v2, 8,000 for bytes-v1."""
    return args.max_tokens if args.max_tokens is not None else estimate.budget(args.estimator)


def _scan(roots: list[Path], budget: int, estimator: str = estimate.DEFAULT, kept: dict | None = None) -> dict:
    # scan(roots) stays the call for the default budget and estimator with nothing kept: callers and tests
    # replace scan() with a one-argument function.
    estimator = estimate.name(estimator)
    if budget == BUDGET and estimator == estimate.DEFAULT and not (kept or {}).get("items"):
        return scan(roots)
    if not (kept or {}).get("items"):
        return scan(roots, budget=budget, estimator=estimator)
    return scan(roots, budget=budget, estimator=estimator, keep=kept)


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def out_in_scan(out: Path, roots: list[Path], home: Path = HOME) -> tuple[Path, Path] | None:
    """(path, root) when --out or its sibling cache/ is inside a folder that the command scans, else None.

    walle does not write into the folders it scans. A scan reads only what is under its roots (os.walk does
    not follow links), so every checkout it scans is inside a root. ~/.walle is walle's own folder: it stays
    allowed when a root holds it, for example `walle scan ~` with the default --out.
    """
    own = (home / ".walle").resolve()
    for path in (out, out.parent / "cache"):
        for root in roots:
            if _inside(path, root) and not (_inside(own, root) and _inside(path, own)):
                return path, root
    return None


def _refuse_out(args, roots: list[Path]) -> bool:
    """Print one line and return True when --out is inside a scanned folder (scan and check: exit 2)."""
    hit = out_in_scan(args.out, roots, HOME)
    if hit:
        print(t("out.in_scan", resolve_lang(getattr(args, "lang", None)), path=hit[0], root=hit[1]), file=sys.stderr)
    return bool(hit)


def check(data: dict, max_tokens: int, agent: str) -> int:
    over = [s for s in data["stacks"]
            if s["tokens_est"] > max_tokens and agent in ("all", s["agent"])]
    for s in over:
        print(f"over budget: {s['folder']} ({s['agent']}) ~{s['tokens_est']} tokens > {max_tokens}")
    # A drift pair whose lines are all in kept items is not listed.
    kept = {(l["file"], l["line"]) for it in data.get("items", []) if it.get("state") == "kept" for l in it["locations"]}
    drift = [p for p in data["pairs"] if p["kind"] == "drift"
             and not {(p["a"]["file"], p["a"]["line"]), (p["b"]["file"], p["b"]["line"])} <= kept]
    for p in drift[:10]:
        print(f"drift {p['score']}: {p['a']['file']}:{p['a']['line']} <> {p['b']['file']}:{p['b']['line']}")
    if not over:
        print(f"ok: {len(data['stacks'])} stacks within {max_tokens} estimated tokens")
    return 1 if over else 0


def alarm() -> int:
    """SessionStart hook. Silent unless the option is on and the stack is OVER BUDGET; never blocks a session.

    It reads only the Claude stack of the folder (no scan), so it ends well inside the 5 second hook timeout.
    The stack and the budget are in bytes-v2 tokens, as in `walle scan`.
    """
    if not re.match(r"^(true|1|yes|on)$", os.environ.get("CLAUDE_PLUGIN_OPTION_SESSION_ALARM", ""), re.I):
        return 0
    try:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            payload = {}
        folder = Path(payload.get("cwd") or os.getcwd())
        try:
            budget = int(float(os.environ.get("CLAUDE_PLUGIN_OPTION_BUDGET_TOKENS") or BUDGET))
        except ValueError:
            budget = BUDGET
        st = claude_stack(folder, HOME)
        if st.tokens <= budget:  # TRIM and LEAN: nothing
            return 0
        biggest = max((l for l in st.layers if l.path), key=lambda l: l.tokens, default=None)
        # A large layer the user kept is not named: the alarm gives the total only.
        kept = {e.get("file") for e in keep.load(keep.path_for(DEFAULT_OUT))["entries"] if e.get("rule") == "large_layer"}
        if biggest and ({str(biggest.path), str(biggest.path.resolve())} & kept):
            biggest = None
        msg = brief.alarm_text(st.tokens, budget, brief.relative_to(biggest.path, folder, HOME) if biggest else None,
                               biggest.tokens if biggest else 0, resolve_lang())
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}))
    except Exception:  # an alarm must never stop a session from starting
        return 0
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
                    help="run output folder (default ~/.walle/runs); caches go to its sibling folder cache/. "
                         "scan and check refuse a folder inside the folders they scan")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="static pass over instruction files")
    s.add_argument("roots", nargs="*", type=Path, default=[Path.cwd()])
    s.add_argument("--top", type=int, default=15)
    s.add_argument("--html", action="store_true", help=argparse.SUPPRESS)  # scan.html is always written
    s.add_argument("--max-tokens", type=int, default=None,
                   help=f"token budget of one stack (default {BUDGET} with v2, {estimate.budget('bytes-v1')} with v1)")
    s.add_argument("--estimator", choices=ESTIMATOR_CHOICES, default="v2", help=ESTIMATOR_HELP)
    s.add_argument("--label", help="a name for this run, kept in the run index (for example 'before trim')")
    s.add_argument("--format", choices=brief.FORMATS,
                   help="path: the report path (default when stdout is not a terminal); brief: verdict and "
                        "Start here (default in a terminal); brief-md: the same as Markdown, for agents; md; json")
    s.add_argument("--json", dest="format", action="store_const", const="json", help="same as --format json")
    s.add_argument("--all", dest="show_all", action="store_true",
                   help="brief: every folder and every item, with item ids and rule ids")
    s.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="auto: color in a terminal unless NO_COLOR is set")
    s.add_argument("-q", "--quiet", action="store_true", help="no progress line on stderr")
    s.add_argument("--lang", choices=LANGS, help="en or ko (default: from LC_ALL, LC_MESSAGES or LANG)")

    c = sub.add_parser("check", help="exit 1 when a stack is over budget (for CI)")
    c.add_argument("roots", nargs="*", type=Path, default=[Path.cwd()])
    c.add_argument("--max-tokens", type=int, default=None,
                   help=f"token budget of one stack (default {BUDGET} with v2, {estimate.budget('bytes-v1')} with v1)")
    c.add_argument("--estimator", choices=ESTIMATOR_CHOICES, default="v2", help=ESTIMATOR_HELP)
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

    w = sub.add_parser("show", help="show an item of a recent scan with the lines around it")
    w.add_argument("ref", help="an item id, or its number in the newest scan of the current folder")
    w.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    w.add_argument("--lang", choices=LANGS)

    context.add_parser(sub, HOME / ".claude" / "projects")
    trim.add_parser(sub, HOME / ".claude" / "projects")
    keep.add_parser(sub)
    apply.add_parser(sub)

    argv = list(sys.argv[1:] if argv is None else argv)
    rest = [a for i, a in enumerate(argv) if not (a == "--out" or a.startswith("--out=")
                                                   or (i and argv[i - 1] == "--out"))]
    if not rest:
        argv.append("scan")  # `walle` alone scans the current folder
    args = ap.parse_args(argv)
    args.out = args.out.expanduser().resolve()
    if args.cmd == "judge":
        return run_judge(args)
    if args.cmd == "show":
        return brief.show_command(args, HOME)
    if args.cmd == "context":
        return context.run_cli(args)
    if args.cmd == "trim":
        return trim.run_cli(args)
    if args.cmd in ("keep", "unkeep"):
        return keep.run_cli(args, HOME)
    if args.cmd in ("apply", "undo"):
        return apply.run_cli(args, HOME)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.cmd == "scan":
        started = time.monotonic()
        fmt = args.format or ("brief" if sys.stdout.isatty() else "path")
        roots = [r.expanduser().resolve() for r in args.roots]
        if _refuse_out(args, roots):
            return 2
        keep_file = keep.path_for(args.out)
        data = _scan(roots, _budget(args), args.estimator, keep.for_scan(keep_file))
        # A kept line that changed is open again: say so once, then the entry is gone.
        lang = resolve_lang(args.lang)
        for e in keep.release(keep_file, data.get("triage", {}).get("keep_released") or []):
            print(t("keep.released.at", lang, id=e["id"], where=keep.where(e, HOME)), file=sys.stderr)
        run_dir = runs.new_run_dir(args.out)
        data.setdefault("run", runs.run_block(roots, [], args.estimator))
        data["run"].update(id=run_dir.name, label=args.label,
                           parent_id=runs.parent_of(args.out, data["run"]["roots"]))
        history = runs.history(args.out, data, run_dir / "scan.json")
        path = _write(run_dir, "scan", data, report.render_scan(data, args.top, resolve_lang(args.lang), history))
        runs.append_index(args.out, data, run_dir / "scan.json")
        (run_dir / "scan.html").write_text(report_html.render(data, args.out), encoding="utf-8")
    elif args.cmd == "check":
        roots = [r.expanduser().resolve() for r in args.roots]
        if _refuse_out(args, roots):
            return 2
        budget = _budget(args)
        # check reads kept items but never writes keep.json: it may run in CI.
        return check(_scan(roots, budget, args.estimator, keep.for_scan(keep.path_for(args.out))), budget, args.agent)
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
    if args.cmd == "scan":
        return brief.print_scan(args, data, path, fmt, time.monotonic() - started, HOME)
    print(path)
    return 0
