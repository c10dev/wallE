"""walle runs, walle pin and walle diff: the run history and the comparison of two scans (brief step 6).

walle runs lists the run index: scans, trim plans, applies, undos and diffs. walle pin <run> fixes the
baseline of the roots of a scan; walle pin --clear lets it be the previous scan again.

walle diff compares two scans:
- no argument: the baseline (the pinned run, else the previous scan of the same roots) -> the newest scan
  of the current folder (or --root);
- last: the newest two scans of those roots;
- A B: two runs, each a run id or a scan.json path (or its run folder).

Two scans compare finding counts only when their rules hashes match, and stack tokens only when their
estimators and counting revisions match (runs.token_notes). Item states between the two scans: closed (an
item of A is gone), new, moved (same id, another line), edited (a gone and a new item of the same rule in
the same file whose text is at least EDITED similar: one item to review, not "closed 1, new 1") and kept.
The applies and undos between the two runs are listed: they link a change of the settings to the scans.

The Sessions lines measure the first calls of the newest scan's folder in Claude Code transcripts, one model
family per block, as walle eve --around does (measure.py). The split is the newest apply between the two
scans, else the time of the newest scan. The n rule of measure.py holds: no value under MIN_SHOW sessions,
a change only with MIN_COMPARE on each side. When the since side is short and its window is still open, a
Next line gives the walle measure command to run later, with the folder when it is not the current one.
Measured values never enter the verdict, Start here or the alarm (brief 2.1, L4).

The result goes to a new run folder (kind diff) as diff.md and diff.json. The last line printed is the
path of diff.md. walle diff does not write into a folder that the two scans scanned.
"""

from __future__ import annotations

import difflib
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, brief, eve, measure, report, runs
from .collect import HOME
from .strings import resolve_lang, round_tokens, t
from .triage import norm_id

EDITED = 0.85
LABEL_COLS = 8


class DiffError(Exception):
    def __init__(self, key: str, **fields):
        super().__init__(key)
        self.key, self.fields = key, fields


# ---------------------------------------------------------------------- finding runs
def _load(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or "stacks" not in data:
        raise ValueError(path)
    return data


def resolve(out: Path, ref: str) -> tuple[dict, dict | None, Path]:
    """(scan data, index line or None, scan.json path) for a run id, a scan.json or a run folder."""
    by_id = {r.get("id"): r for r in runs.scans(out)}
    if ref in by_id:
        path = Path(by_id[ref]["path"])
        try:
            return _load(path), by_id[ref], path
        except (OSError, ValueError):
            raise DiffError("diff.not_found", ref=ref) from None
    path = Path(ref).expanduser()
    if path.is_dir():
        path = path / "scan.json"
    try:
        data = _load(path)
    except (OSError, ValueError):
        raise DiffError("diff.not_found", ref=ref) from None
    rid = (data.get("run") or {}).get("id")
    return data, by_id.get(rid) if rid else None, path.resolve()


def pick(out: Path, args_runs: list[str], folder: Path) -> tuple[str, str, bool]:
    """(baseline ref, newest ref, pinned) for the arguments of walle diff."""
    if len(args_runs) == 2:
        return args_runs[0], args_runs[1], False
    if len(args_runs) > 2 or (args_runs and args_runs[0] != "last"):
        raise DiffError("diff.args")
    newest = runs.newest_scan_of(out, folder)
    if not newest:
        raise DiffError("diff.no_scan", folder=str(folder))
    if args_runs == ["last"]:
        same = runs.scans(out, newest.get("roots") or [])
        if len(same) < 2:
            raise DiffError("empty.first_run")
        return same[-2]["id"], same[-1]["id"], False
    base, pinned = runs.baseline_of(out, newest)
    if not base:
        raise DiffError("empty.first_run")
    if base["id"] == newest["id"]:
        raise DiffError("diff.same_run", id=newest["id"])
    return base["id"], newest["id"], pinned


# ---------------------------------------------------------------------- comparing
def _first(it: dict) -> tuple:
    loc = (it.get("locations") or [{}])[0]
    return loc.get("file"), loc.get("line")


def _text(it: dict) -> str:
    return norm_id(" ".join(str(loc.get("text") or "") for loc in it.get("locations") or []))


def item_states(a: dict, b: dict) -> dict:
    """Ids of the items of b against a: closed, new, moved, edited ([a id, b id]) and kept."""
    ia = {it["id"]: it for it in a.get("items") or [] if it.get("certainty") != "info"}
    ib = {it["id"]: it for it in b.get("items") or [] if it.get("certainty") != "info"}
    gone = [i for i, it in ia.items() if i not in ib and it.get("state") != "kept"]
    new = [i for i, it in ib.items() if i not in ia and it.get("state") != "kept"]
    pairs = []
    for x in gone:
        for y in new:
            A, B = ia[x], ib[y]
            if A.get("rule") != B.get("rule") or _first(A)[0] != _first(B)[0]:
                continue
            ratio = difflib.SequenceMatcher(None, _text(A), _text(B)).ratio()
            if ratio >= EDITED:
                pairs.append((ratio, x, y))
    edited, used = [], set()
    for _r, x, y in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
        if x not in used and y not in used:
            edited.append([x, y])
            used |= {x, y}
    return {
        "closed": [i for i in gone if i not in used],
        "new": [i for i in new if i not in used],
        "moved": [i for i in ib if i in ia and _first(ia[i]) != _first(ib[i])],
        "edited": edited,
        "kept": [i for i, it in ib.items() if it.get("state") == "kept"],
    }


def stack_rows(a: dict, b: dict) -> list[dict]:
    sa = {(s["folder"], s["agent"]): s["tokens_est"] for s in a.get("stacks") or []}
    out = []
    for s in b.get("stacks") or []:
        before = sa.get((s["folder"], s["agent"]))
        after = s["tokens_est"]
        d = None if before is None else after - before
        pct = round(100 * d / before) if d is not None and before else None
        out.append({"folder": s["folder"], "agent": s["agent"], "a": before, "b": after, "delta": d, "pct": pct})
    return out


def stale_files(data: dict) -> list[str]:
    """Files of a scan whose content changed since the scan read them."""
    from .report_html import _sha1
    out = []
    for f in data.get("files") or []:
        sha = _sha1(f["path"])
        if sha is not None and f.get("sha") and sha != f["sha"]:
            out.append(f["path"])
    return out


def between(out: Path, a_id: str | None, b_id: str | None) -> list[dict]:
    """Apply and undo lines of the index after run a and before run b. The index is in the order of the runs."""
    rows = runs.read_index(out)
    at = {r.get("id"): n for n, r in enumerate(rows)}
    if a_id not in at or b_id not in at:
        return []
    return [r for r in rows[at[a_id] + 1:at[b_id]] if r.get("kind") in ("apply", "undo")]


def compare(a: dict, b: dict, out: Path) -> dict:
    ra, rb = a.get("run") or {}, b.get("run") or {}
    rules_a, rules_b = ra.get("rules_hash") or "----", rb.get("rules_hash") or "----"
    tokens_ok = not runs.token_notes([a], b, "en")
    rules_ok = rules_a == rules_b and "items" in a and "items" in b
    return {
        "rules": [rules_a, rules_b], "estimator": [runs.estimator_of(a), runs.estimator_of(b)],
        "counting_revision": [runs.counting_of(a), runs.counting_of(b)],
        "comparable": {"findings": rules_ok, "tokens": tokens_ok},
        "stacks": stack_rows(a, b) if tokens_ok else [],
        "items": item_states(a, b) if rules_ok else None,
        "applied": between(out, ra.get("id"), rb.get("id")),
        "stale_files": stale_files(b),
    }


def sessions_of(b: dict, applied: list[dict], out: Path, transcripts: Path, cwd: Path) -> dict | None:
    """The measure block of the first root of scan b, split at the newest apply of applied, else at b's time.

    None when there are no transcripts or no time to split at. ref is what walle measure --around takes.
    next is ref when the since side of the largest family has fewer than MIN_COMPARE sessions and the since
    window is still open: a later apply or undo, or the end of its days, closes it. next_folder is the folder
    for walle measure when it is not cwd.
    """
    roots = (b.get("run") or {}).get("roots") or []
    if not roots or not transcripts.is_dir() or not any(transcripts.glob("*/*.jsonl")):
        return None
    applies = [r for r in applied if r.get("kind") == "apply" and measure.parse_when(r.get("generated"))]
    ref = applies[-1]["id"] if applies else b.get("generated")
    if not ref:
        return None
    try:
        split = measure.split_point(out, ref)
    except measure.SplitError:
        return None
    sessions = eve.load_sessions(transcripts, out.parent / "cache" / "eve.json")
    block = eve.measure_block(sessions, "claude", Path(roots[0]), split)
    first = block["rows"][0]["change"]["first_call"] if block["rows"] else None
    short = first is None or (first["state"] == "too_few" and first["need_after"] > 0)
    open_ = measure.parse_when(split["windows"]["after"]["to"]) > datetime.now(timezone.utc)
    folder = str(Path(roots[0]))
    return {"split_at": "apply" if applies else "scan", "ref": ref, "folder": folder,
            "next": ref if short and open_ else None, "next_folder": None if Path(folder) == cwd else folder,
            "measure": block}


# ---------------------------------------------------------------------- text
def _signed(n: int) -> str:
    if n == 0:
        return "0"
    return ("-" if n < 0 else "+") + round_tokens(abs(n))


def _short(p: str, home: Path) -> str:
    return brief.shown(p, None, home)


def _label(key: str, lang: str) -> str:
    return brief.pad(t(key, lang), LABEL_COLS)


def _side(v: dict, lang: str) -> str:
    """One side of the first-call input: median [p25-p75] n=, or too few sessions (n of 10)."""
    if v["sessions"] < measure.MIN_SHOW:
        return t("measure.too_few_cell", lang, n=v["sessions"], need=measure.MIN_COMPARE)
    st = v["first_call"]
    text = t("diff.sessions_value", lang, value=eve.cell(st, "first_call", v["sessions"], lang), n=st["n"])
    return text + ("  " + t("measure.few", lang) if st["few"] else "")


def session_lines(s: dict, lang: str, home: Path) -> list[str]:
    """The Sessions blocks and the Next line. Exact numbers, no color: a measured change is not a verdict."""
    block, indent = s["measure"], " " * LABEL_COLS
    shown, few = eve._shown_rows(block, block["rows"])
    out = []
    for r in shown:
        out.append(t("diff.sessions", lang, family=r["family"]))
        out.append(f"{indent}{t('measure.col.first_call', lang)} {_side(r['before'], lang)}")
        after = f"{indent}-> {_side(r['after'], lang)}"
        change = eve.change_cell(r["change"]["first_call"], lang)
        if change and brief.width(f"{after}  {change}") > brief.WIDTH:
            out.append(after)
            after = f"{indent}   {change}"
        elif change:
            after += f"  {change}"
        out.append(after)
    out += eve._too_few_line(block, few, lang)
    if not block["rows"]:
        out.append(t("measure.no_sessions", lang, folder=_short(block["folder"]["path"], home)))
    if any(r["change"]["first_call"]["state"] != "too_few" for r in shown):
        out.append(t("measure.not_proof", lang))
    if s["next"]:
        folder = ""
        if s.get("next_folder"):
            shown = _short(s["next_folder"], home)  # ~ must stay unquoted for the shell to expand it
            rest = shown.removeprefix("~")
            folder = " " + (shown if shlex.quote(rest) == rest else shlex.quote(s["next_folder"]))
        cmd = _label("diff.next", lang) + t("diff.next_measure", lang, folder=folder, ref=s["next"])
        after = t("diff.next_after", lang, n=measure.MIN_COMPARE)
        out += ([f"{cmd} {after}"] if brief.width(f"{cmd} {after}") <= brief.WIDTH else [cmd, indent + after])
    return out


def lines(c: dict, a: dict, b: dict, pinned: bool, lang: str, home: Path, green=lambda s: s) -> list[str]:
    ra, rb = a.get("run") or {}, b.get("run") or {}
    roots = ", ".join(_short(r, home) for r in rb.get("roots") or []) or "-"
    head = f"walle diff  {ra.get('id') or '-'} -> {rb.get('id') or '-'}  {roots}"
    if pinned:
        head += f"  ({t('diff.pinned', lang)})"
    out = [head]
    ru = [x[:4] for x in c["rules"]]
    rules = (t("diff.rules_same", lang, a=ru[0], b=ru[1]) if c["rules"][0] == c["rules"][1]
             else t("diff.rules_changed", lang, a=ru[0], b=ru[1]))
    est = c["estimator"]
    if c["comparable"]["tokens"]:
        out.append(f"{rules}  {t('diff.estimator_same', lang, a=est[0], b=est[1])}")
    else:
        out.append(rules)
        out.append(t("diff.estimator_changed", lang) if est[0] != est[1] else t("diff.counting_changed", lang))
    out.append("")
    if c["comparable"]["tokens"]:
        shown = [s for s in c["stacks"] if s["a"] or s["b"]]  # an agent with no instruction file on either side
        one_folder = len({s["folder"] for s in shown}) == 1
        names = [s["agent"] if one_folder else f"{_short(s['folder'], home)} {s['agent']}" for s in shown]
        cols = max((brief.width(x) for x in names), default=0) + 2
        for n, (s, name) in enumerate(zip(shown, names)):
            lead = _label("diff.stack", lang) if n == 0 else " " * LABEL_COLS
            name = brief.pad(name, cols)
            if s["a"] is None:
                out.append(f"{lead}{name}~{round_tokens(s['b'])} {t('term.est_tokens', lang)}")
                continue
            change = _signed(s["delta"]) + (f", {s['pct']:+d}%" if s["pct"] else "")
            if s["delta"] < 0:
                change = green(change)
            out.append(f"{lead}{name}~{round_tokens(s['a'])} -> ~{round_tokens(s['b'])} "
                       f"{t('term.est_tokens', lang)}  ({change})")
    if c["items"] is not None:
        i = c["items"]
        out.append(_label("diff.items", lang) + t("diff.items_line", lang, closed=len(i["closed"]), new=len(i["new"]),
                                                   moved=len(i["moved"]), edited=len(i["edited"]), kept=len(i["kept"])))
    for n, r in enumerate(c["applied"]):
        lead = _label("diff.applied", lang) if n == 0 else " " * LABEL_COLS
        if r.get("kind") == "apply":
            out.append(lead + t("diff.applied_line", lang, id=r["id"], n=len(r.get("rows") or []),
                                plan=r.get("parent_id") or "-"))
        else:
            out.append(lead + t("diff.undone_line", lang, id=r["id"], apply=r.get("parent_id") or "-"))
    if c["stale_files"]:
        out.append(t("diff.stale", lang, n=len(c["stale_files"]), id=rb.get("id") or "-"))
    if c.get("sessions"):
        out += session_lines(c["sessions"], lang, home)
    return out


def markdown(c: dict, a: dict, b: dict, pinned: bool, lang: str, home: Path, text: list[str]) -> str:
    md = ["# walle diff", "", "```text", *text, "```", ""]
    if c["comparable"]["tokens"] and c["stacks"]:
        md += [f"## {t('level.stacks', lang)}", ""]
        md += report.table([t("table.folder", lang), t("table.agent", lang), t("table.run", lang) + " A",
                            t("table.run", lang) + " B", "+/-"],
                           [[_short(s["folder"], home), s["agent"], "-" if s["a"] is None else "~" + round_tokens(s["a"]),
                             "~" + round_tokens(s["b"]), "-" if s["delta"] is None else _signed(s["delta"])]
                            for s in c["stacks"]]).splitlines()
        md.append("")
    if c["items"] is not None:
        ib = {it["id"]: it for it in b.get("items") or []}
        ia = {it["id"]: it for it in a.get("items") or []}
        md += [f"## {t('diff.items', lang)}", ""]
        for state in ("closed", "new", "moved", "edited", "kept"):
            ids = c["items"][state]
            if not ids:
                continue
            md.append(f"### {t('diff.state.' + state, lang)} ({len(ids)})")
            md.append("")
            for i in ids:
                x, y = (i if state == "edited" else (i, i))
                it = ib.get(y) or ia.get(x) or {}
                f, ln = _first(it)
                ref = f"{x} -> {y}" if state == "edited" else x
                md.append(f"- {ref} {it.get('rule', '-')} {_short(f or '-', home)}:{ln}")
            md.append("")
    if c["stale_files"]:
        md += [f"## {t('diff.stale_head', lang)}", ""] + [f"- {_short(p, home)}" for p in c["stale_files"]] + [""]
    if not c["comparable"]["findings"] and ("items" not in a or "items" not in b):
        md += [report.render_diff(a, b)]  # schema 1 scans: the counts by cause
    return "\n".join(md) + "\n"


# ---------------------------------------------------------------------- CLI
def add_parsers(sub) -> None:
    r = sub.add_parser("runs", help="list the runs: scans, trim plans, applies, undos and diffs")
    r.add_argument("--root", type=Path, help="only scans and diffs whose roots include this folder")
    r.add_argument("--limit", type=int, default=30, help="the newest N runs (0: all)")
    r.add_argument("--html", action="store_true",
                   help="write the history page index.html in the runs folder again and print its path")
    r.add_argument("--lang", choices=("en", "ko"))
    p = sub.add_parser("pin", help="fix a scan as the baseline of its roots for walle diff")
    p.add_argument("run", nargs="?", help="a scan run id")
    p.add_argument("--clear", action="store_true", help="unpin the roots of the current folder (or --root)")
    p.add_argument("--root", type=Path, help="with --clear: a folder instead of the current one")
    p.add_argument("--lang", choices=("en", "ko"))
    d = sub.add_parser("diff", help="compare two scans: baseline -> newest (no argument), last, or A B")
    d.add_argument("runs", nargs="*", help="nothing, last, or two runs (ids or scan.json paths)")
    d.add_argument("--root", type=Path, help="the folder whose scans to compare (default: the current folder)")
    d.add_argument("--transcripts", type=Path, default=None,
                   help="where the Claude Code sessions are for the Sessions lines (default ~/.claude/projects)")
    d.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    d.add_argument("--lang", choices=("en", "ko"))


def run_cli(args, home: Path = HOME) -> int:
    lang = resolve_lang(args.lang)
    if args.cmd == "runs":
        return runs_cli(args, home, lang)
    if args.cmd == "pin":
        return pin_cli(args, home, lang)
    return diff_cli(args, home, lang)


def _folder(args) -> Path:
    return (args.root or Path.cwd()).expanduser().resolve()


def _note(r: dict, pins: set[str], lang: str) -> str:
    kind = r.get("kind")
    if kind == "scan":
        return t("runs.pinned", lang) if r.get("id") in pins else ""
    if kind == "trim":
        return t("runs.note.trim", lang, n=r.get("rows", "-"))
    if kind == "apply":
        return t("runs.note.apply", lang, plan=r.get("parent_id"), n=len(r.get("rows") or []), status=r.get("status"))
    if kind == "undo":
        return t("runs.note.undo", lang, apply=r.get("parent_id"), status=r.get("status"))
    if kind == "diff":
        return t("runs.note.diff", lang, a=r.get("baseline_id"), b=r.get("parent_id"))
    if kind == "context":
        if r.get("usage"):
            return t("runs.note.context_usage", lang, agent=r.get("agent") or "-")
        return t("runs.note.context", lang, agent=r.get("agent") or "-", session=(r.get("session") or "-")[:8])
    if kind == "measure":
        if r.get("around"):
            return t("runs.note.measure", lang, agent=r.get("agent") or "-", ref=r["around"])
        return t("runs.note.measure_period", lang, agent=r.get("agent") or "-")
    if kind == "compare":
        return t("runs.note.compare", lang, agent=r.get("agent") or "-", refs=" ".join(r.get("refs") or []))
    return ""


def runs_cli(args, home: Path, lang: str) -> int:
    if getattr(args, "html", False):
        from . import history
        print(history.write(args.out, home) if (args.out / runs.INDEX).is_file() else t("runs.empty", lang, path=args.out))
        return 0
    rows = runs.read_index(args.out)
    if args.root:
        folder = str(args.root.expanduser().resolve())
        rows = [r for r in rows if not r.get("roots") or folder in r["roots"]]
    if not rows:
        print(t("runs.empty", lang, path=args.out))
        return 0
    if args.limit:
        rows = rows[-args.limit:]
    pins = set(runs.load_pins(args.out).values())
    table = [[t(f"table.{c}", lang) for c in ("run", "kind", "label", "rules", "stack_sum", "open", "kept")] + [""]]
    for r in rows:
        scan = r.get("kind") == "scan"
        table.append([r.get("id") or "-", r.get("kind") or "-", brief.clip(r.get("label") or "-", 14),
                      (r.get("rules") or "-")[:4] if scan else "-",
                      "~" + round_tokens(r.get("stack_sum") or 0) if scan else "-",
                      str(r.get("open", "-")) if scan else "-", str(r.get("kept", "-")) if scan else "-",
                      _note(r, pins, lang)])
    cols = [max(brief.width(row[i]) for row in table) + 2 for i in range(7)]
    for row in table:
        line = "".join(brief.pad(c, w) for c, w in zip(row, cols))
        if brief.width(line + row[7]) <= brief.WIDTH:
            print((line + row[7]).rstrip())
        else:  # the note goes under the row: terminal text stays within 80 columns
            print(line.rstrip())
            print(" " * cols[0] + row[7])
    print(args.out / runs.INDEX)
    return 0


def pin_cli(args, home: Path, lang: str) -> int:
    pins = runs.load_pins(args.out)
    if args.clear:
        newest = runs.newest_scan_of(args.out, _folder(args))
        roots = newest.get("roots") if newest else [str(_folder(args))]
        shown = ", ".join(_short(r, home) for r in roots)
        if pins.pop(runs.roots_key(roots), None) is None:
            print(t("pin.none", lang, roots=shown))
            return 0
        runs.save_pins(args.out, pins)
        print(t("pin.cleared", lang, roots=shown))
        print(args.out / runs.PINS)
        return 0
    if not args.run:
        print(t("pin.need_run", lang), file=sys.stderr)
        return 2
    row = next((r for r in runs.scans(args.out) if r.get("id") == args.run), None)
    if not row:
        print(t("pin.not_scan", lang, id=args.run, path=args.out / runs.INDEX), file=sys.stderr)
        return 2
    pins[runs.roots_key(row.get("roots") or [])] = row["id"]
    runs.save_pins(args.out, pins)
    print(t("pin.done", lang, id=row["id"], roots=", ".join(_short(r, home) for r in row.get("roots") or [])))
    print(args.out / runs.PINS)
    return 0


def diff_cli(args, home: Path, lang: str) -> int:
    from .cli import out_in_scan
    try:
        ref_a, ref_b, pinned = pick(args.out, args.runs, _folder(args))
        a, _row_a, _pa = resolve(args.out, ref_a)
        b, _row_b, _pb = resolve(args.out, ref_b)
    except DiffError as e:
        if e.key in ("empty.first_run", "diff.same_run"):
            print(t(e.key, lang, **e.fields))
            return 0
        print(t(e.key, lang, **e.fields), file=sys.stderr)
        return 2
    roots = [Path(r) for r in {*((a.get("run") or {}).get("roots") or []), *((b.get("run") or {}).get("roots") or [])}]
    hit = out_in_scan(args.out, roots, home)
    if hit:
        print(t("out.in_scan", lang, path=hit[0], root=hit[1]), file=sys.stderr)
        return 2
    c = compare(a, b, args.out)
    c["sessions"] = sessions_of(b, c["applied"], args.out,
                                (args.transcripts or home / ".claude" / "projects").expanduser(), Path.cwd().resolve())
    color = brief.use_color(args.color, sys.stdout.isatty(), os.environ)
    text = lines(c, a, b, pinned, lang, home, (lambda s: f"\x1b[32m{s}\x1b[0m") if color else (lambda s: s))
    run_dir = runs.new_run_dir(args.out)
    ra, rb = a.get("run") or {}, b.get("run") or {}
    now = datetime.now().isoformat(timespec="seconds")
    data = {"schema": 1, "generated": now,
            "run": {"id": run_dir.name, "kind": "diff", "label": None, "parent_id": rb.get("id"),
                    "baseline_id": ra.get("id"), "baseline_pinned": pinned, "walle_version": __version__,
                    "roots": rb.get("roots") or []},
            "a": {"id": ra.get("id"), "generated": a.get("generated")},
            "b": {"id": rb.get("id"), "generated": b.get("generated")}, **c}
    plain = lines(c, a, b, pinned, lang, home)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "diff.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    (run_dir / "diff.md").write_text(markdown(c, a, b, pinned, lang, home, plain))
    runs.append_row(args.out, {"id": run_dir.name, "kind": "diff", "label": None, "parent_id": rb.get("id"),
                               "baseline_id": ra.get("id"), "generated": now, "roots": rb.get("roots") or [],
                               "walle_version": __version__, "path": str(run_dir / "diff.json")})
    print("\n".join(text))
    print(run_dir / "diff.md")
    return 0
