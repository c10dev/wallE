"""The run history page, runs/index.html (brief 3.13 and 3.8.7).

walle rewrites the page after every run that adds a line to the run index (runs.append_row), so it lists
every run in the runs folder of --out. One row per run, newest first: the run id, kind, label, roots, rules,
estimator and counting revision, the stack sum and the open and kept counts of a scan, and a link to the
run's report (scan.html of a scan, else its Markdown file).

- A pinned scan says so. The pins are listed with their roots.
- A divider with a text label sits between two scans of the same roots whose rules, estimator or counting
  revision differ: their counts or token totals do not compare (brief 5.6). In the newest-first table it is
  below the newer scan.
- Index lines without a string id and kind are left out (clean()). The page never stops a run.
- Each apply id is listed with its plan, the rows it changed and its undo, the two commands that measure it
  (walle context --compare and walle eve --around), and links to the compare and measure runs made around it.

The page is one static file: no script, no request. Its colors are the design direction A tokens of
report.html, read from that file, and its fonts are the ones scan.html embeds. Estimated tokens carry "~";
measured runs show no estimate.
"""

from __future__ import annotations

import html
import re
from importlib import resources
from pathlib import Path

from . import brief, runs
from .collect import HOME
from .strings import round_tokens

PAGE = "index.html"
MISSING = '<span class="muted">-</span>'
REPORTS = {"scan": ("scan.html", "scan.md"), "trim": ("plan.md",), "apply": ("apply.md",), "undo": ("undo.md",),
           "diff": ("diff.md",), "context": ("context.md",), "measure": ("eve.md",), "compare": ("compare.md",)}
TOKENS_RE = re.compile(r"\n(:root \{.*?)\n@media \(prefers-reduced-motion", re.S)
CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 400 15px/24px var(--sans); }
.page { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
a { color: var(--link); text-decoration: underline; text-underline-offset: 2px; }
a:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
code, .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
code { font-size: 13px; overflow-wrap: anywhere; }
.muted { color: var(--muted); }
.top { display: flex; align-items: baseline; gap: 4px 12px; flex-wrap: wrap; min-height: 48px; }
.wordmark { font: 600 18px/24px var(--mono); letter-spacing: .01em; }
.meta { font: 400 12px/16px var(--mono); color: var(--muted); overflow-wrap: anywhere; }
section { margin-top: 32px; }
.sh { display: flex; align-items: center; gap: 12px; margin: 0 0 16px; font: 600 12px/20px var(--mono); text-transform: uppercase; letter-spacing: .14em; }
.sh::after { content: ""; flex: 1; height: 0; border-top: 1px solid var(--line); }
.sh .shn { font-weight: 400; letter-spacing: 0; text-transform: none; color: var(--muted); }
.panelbox { background: var(--surface); border: 1px solid var(--line); border-radius: 18px; box-shadow: var(--shadow); padding: 16px; }
.tblwrap { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; line-height: 20px; }
.tbl th { font-weight: 600; text-align: left; color: var(--muted); font-size: 12px; line-height: 16px; padding: 6px 8px 6px 0; border-bottom: 1px solid var(--line-strong); white-space: nowrap; }
.tbl td { padding: 6px 8px 6px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
.tbl .r { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
.tbl td.p { font-family: var(--mono); min-width: 12ch; }
.tbl td.id { font-family: var(--mono); white-space: nowrap; }
.tbl tr.div td { font: 600 11px/16px var(--mono); text-transform: uppercase; letter-spacing: .1em; color: var(--muted); border-bottom: 1px dashed var(--line-strong); padding-top: 12px; }
.kind { font: 600 11px/16px var(--mono); text-transform: uppercase; letter-spacing: .1em; }
.tag { display: inline-block; font: 600 11px/16px var(--mono); text-transform: uppercase; letter-spacing: .1em; border: 1px solid var(--line-strong); border-radius: 99px; padding: 0 8px; margin-left: 6px; }
.gnote { font-size: 13px; line-height: 20px; color: var(--muted); margin: 8px 0 0; }
.list { margin: 0; padding: 0; list-style: none; }
.list > li { padding: 10px 0; border-top: 1px solid var(--line); font-size: 13px; line-height: 20px; }
.list > li:first-child { border-top: 0; padding-top: 0; }
.list .cmd { display: block; margin-top: 4px; }
@media (max-width: 639px) {
  .panelbox { padding: 12px; }
  .tbl thead { display: none; }
  .tbl, .tbl tbody, .tbl tr, .tbl td { display: block; width: 100%; }
  .tbl tr { padding: 8px 0; border-bottom: 1px solid var(--line); }
  .tbl td, .tbl .r, .tbl td.id { border: 0; padding: 2px 0; text-align: left; white-space: normal; }
  .tbl td.e { display: none; }
  .tbl td[data-l]:not(.id)::before { content: attr(data-l) "  "; color: var(--muted); font: 400 12px/20px var(--sans); }
  .tbl tr.div { border-bottom: 1px dashed var(--line-strong); }
  .tbl tr.div td { padding: 8px 0 4px; }
}
"""


def tokens_css() -> str:
    """The color and type tokens of report.html (light, dark by system, dark by choice)."""
    tpl = (resources.files("walle") / "report.html").read_text(encoding="utf-8")
    m = TOKENS_RE.search(tpl)
    return m.group(1) if m else ""


def _e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _report(out: Path, r: dict) -> str | None:
    """The link of a run's report, relative to the runs folder, when the file is there."""
    rid = r.get("id")
    if not rid:
        return None
    for name in REPORTS.get(r.get("kind"), ()):
        if (out / rid / name).is_file():
            return f"{rid}/{name}"
    return None


def _td(label: str, value: str | None, cls: str = "") -> str:
    """One cell. data-l names it in the stacked layout of a narrow screen, where an empty cell is hidden."""
    classes = " ".join(x for x in (cls, "" if value else "e") if x)
    attr = f' class="{classes}"' if classes else ""
    return f'<td data-l="{label}"{attr}>{value or MISSING}</td>'


def _roots(roots: list[str], home: Path) -> str:
    return ", ".join(brief.shown(x, None, home) for x in roots) or "-"


def _when(rid: str) -> str:
    m = re.match(r"^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)", rid or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}" if m else ""


def _str(x) -> str | None:
    return x if isinstance(x, str) and x else None


def _int(x) -> int | None:
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _strs(x) -> list[str]:
    return [v for v in x if isinstance(v, str)] if isinstance(x, list) else []


def clean(rows: list) -> list[dict]:
    """The index lines the page can show, with each field of the type it expects.

    A line without a string id and kind is left out; a field of another type is left empty. A broken
    line never stops the page, and the page never stops a run (runs.append_row).
    """
    out = []
    for r in rows:
        if not isinstance(r, dict) or not _str(r.get("id")) or not _str(r.get("kind")):
            continue
        c = {k: _str(r.get(k)) for k in ("id", "kind", "label", "parent_id", "baseline_id", "rules", "estimator",
                                          "status", "agent", "around", "session", "generated")}
        c.update(roots=_strs(r.get("roots")), refs=_strs(r.get("refs")), stack_sum=_int(r.get("stack_sum")),
                 open=_int(r.get("open")), kept=_int(r.get("kept")), counting_revision=_int(r.get("counting_revision")),
                 usage=r.get("usage") is True,
                 rows=_strs(r.get("rows")) if isinstance(r.get("rows"), list) else _int(r.get("rows")))
        out.append(c)
    return out


def dividers(rows: list[dict]) -> dict[str, list[str]]:
    """Run id -> the labels of the divider below it in the newest-first table: what changed between this scan
    and the previous scan of the same roots. The divider sits between the two."""
    last: dict[str, dict] = {}
    out: dict[str, list[str]] = {}
    for r in rows:
        if r.get("kind") != "scan":
            continue
        key = runs.roots_key(r.get("roots") or [])
        p = last.get(key)
        if p:
            labels = []
            if (p.get("rules") or "") != (r.get("rules") or ""):
                labels.append(f"rules changed {(p.get('rules') or '----')[:4]} -> {(r.get('rules') or '----')[:4]}: "
                              "open counts not comparable")
            if runs.estimator_of(p) != runs.estimator_of(r):
                labels.append(f"estimator changed {runs.estimator_of(p)} -> {runs.estimator_of(r)}: "
                              "token totals not comparable")
            if runs.counting_of(p) != runs.counting_of(r):
                labels.append(f"counting revision changed {runs.counting_of(p)} -> {runs.counting_of(r)}: "
                              "token totals not comparable")
            if labels:
                out[r["id"]] = labels
        last[key] = r
    return out


def _note(r: dict, pins: set[str], by_parent: dict[str, list[dict]]) -> str:
    kind = r.get("kind")
    parts = []
    if kind == "scan" and r.get("id") in pins:
        parts.append('<span class="tag">pinned</span>')
    if kind == "trim":
        parts.append(f"{_e(r['rows'] if isinstance(r.get('rows'), int) else '-')} rows")
    if kind == "apply":
        n = len(r["rows"]) if isinstance(r.get("rows"), list) else 0
        parts.append(f"plan <code>{_e(r.get('parent_id') or '-')}</code>: {n} changed, {_e(r.get('status') or '-')}")
    if kind == "undo":
        parts.append(f"undo of <code>{_e(r.get('parent_id') or '-')}</code>: {_e(r.get('status') or '-')}")
    if kind == "diff":
        parts.append(f"<code>{_e(r.get('baseline_id') or '-')}</code> -&gt; <code>{_e(r.get('parent_id') or '-')}</code>")
    if kind == "context":
        parts.append("usage of a period" if r.get("usage") else f"session <code>{_e((r.get('session') or '-')[:8])}</code>")
    if kind == "measure":
        parts.append(f"around <code>{_e(r['around'])}</code>" if r.get("around") else "one period")
    if kind == "compare":
        parts.append(" ".join(f"<code>{_e(x)}</code>" for x in r.get("refs") or []))
    for child in by_parent.get(r.get("id") or "", []) if kind == "apply" else []:
        parts.append(f"{_e(child['kind'])} <code>{_e(child['id'])}</code>")
    if r.get("agent") and kind in ("context", "measure", "compare"):
        parts.append(_e(r["agent"]))
    return " · ".join(parts)


def render(out: Path, home: Path = HOME, fonts: bool = True) -> str:
    """The page for the run index of out."""
    rows = clean(runs.read_index(out))
    pins = runs.load_pins(out)
    pinned = set(pins.values())
    by_parent: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("kind") in ("compare", "measure", "undo") and r.get("parent_id"):
            by_parent.setdefault(r["parent_id"], []).append(r)
    divs = dividers(rows)
    body = []
    for r in reversed(rows):
        rid = r["id"]
        scan = r.get("kind") == "scan"
        link = _report(out, r)
        roots = _roots(r.get("roots") or [], home)
        est = runs.estimator_of(r) if r.get("kind") in ("scan", "trim", "context", "compare") else None
        rev = runs.counting_of(r) if r.get("kind") in ("scan", "context", "compare") else None
        note = _note(r, pinned, by_parent)
        report = f'<a href="{_e(link)}">{_e(link.rsplit("/", 1)[-1])}</a>' if link else ""
        stack = "~" + round_tokens(r.get("stack_sum") or 0) if scan else None
        cells = [
            _td("Run", f'{_e(rid)}<br><span class="muted">{_e(_when(rid))}</span>', "id"),
            _td("Kind", f'<span class="kind">{_e(r.get("kind") or "-")}</span>'),
            _td("Label", _e(r.get("label")) or None),
            _td("Roots", _e(roots).replace("/", "/<wbr>") if r.get("roots") else None, "p"),
            _td("Rules", _e((r.get("rules") or "")[:4]) or None if scan else None, "mono"),
            _td("Estimator", _e(est) if est else ("measured" if r.get("kind") == "measure" else None), "mono"),
            _td("Counting", _e(rev) if rev else None, "r"),
            _td("Stack sum", stack, "r"),
            _td("Open", _e(r["open"]) if scan and r["open"] is not None else None, "r"),
            _td("Kept", _e(r["kept"]) if scan and r["kept"] is not None else None, "r"),
            _td("Notes", " · ".join(x for x in (note, report) if x) or None, "n"),
        ]
        body.append("<tr>" + "".join(cells) + "</tr>")
        for label in divs.get(rid, []):  # below the scan: between it and the older scan it differs from
            body.append(f'<tr class="div"><td colspan="11">{_e(label)}</td></tr>')
    applies = []
    for r in reversed(rows):
        if r.get("kind") != "apply":
            continue
        rid = _e(r.get("id"))
        kids = by_parent.get(r.get("id") or "", [])
        links = [f'<a href="{_e(x)}">{_e(k["kind"])} {_e(k["id"])}</a>' for k in kids for x in [_report(out, k)] if x]
        changed = r["rows"] if isinstance(r.get("rows"), list) else []
        applies.append(
            f'<li><code>{rid}</code> · plan <code>{_e(r.get("parent_id") or "-")}</code> · '
            f'{len(changed)} rows changed ({_e(", ".join(changed) or "-")}) · '
            f'{_e(r.get("status") or "-")}'
            f'<span class="cmd"><code>walle context --compare {rid}</code> · '
            f'<code>walle eve --around {rid}</code> (after 10 sessions)</span>'
            + (f'<span class="cmd">{" · ".join(links)}</span>' if links else "") + "</li>")
    pin_rows = [f"<li><code>{_e(v)}</code> · {_e(_roots(k.splitlines(), home))}</li>"
                for k, v in sorted(pins.items(), key=lambda kv: kv[1])]
    where = brief.shown(str(out), None, home)
    head = ('<tr><th>Run</th><th>Kind</th><th>Label</th><th>Roots</th><th>Rules</th>'
            '<th>Estimator</th><th class="r">Counting</th><th class="r">Stack sum</th><th class="r">Open</th>'
            '<th class="r">Kept</th><th>Notes · report</th></tr>')
    table = (f'<div class="tblwrap"><table class="tbl"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>'
             if body else f'<p class="gnote">No runs in {_e(where)}. Run walle scan first.</p>')
    sections = [
        f'<section aria-labelledby="rh"><h2 class="sh" id="rh">Runs <span class="shn">{len(rows)}</span></h2>'
        f'<div class="panelbox">{table}'
        '<p class="gnote">Open counts compare only between runs with the same rules. Token counts compare only '
        "between runs with the same estimator and counting revision. A counting revision belongs to its kind: "
        "scans count instruction files, context runs split a first call. Stack sums are est. tokens.</p>"
        f'<p class="gnote">All runs: <code>{_e(where)}/{runs.INDEX}</code></p></div></section>']
    sections.append(
        '<section aria-labelledby="ah"><h2 class="sh" id="ah">Applies</h2><div class="panelbox">'
        + (f'<ul class="list">{"".join(applies)}</ul>' if applies else '<p class="gnote">No walle apply yet.</p>')
        + '<p class="gnote">Measured values compare one folder and one model family at a time, with at least 10 '
          "sessions on each side.</p></div></section>")
    sections.append(
        '<section aria-labelledby="ph"><h2 class="sh" id="ph">Pins</h2><div class="panelbox">'
        + (f'<ul class="list">{"".join(pin_rows)}</ul>' if pin_rows else
           '<p class="gnote">No pinned baseline. walle diff compares a scan with the previous scan of its roots.</p>')
        + "</div></section>")
    faces = ""
    if fonts:
        from .report_html import font_faces
        faces = font_faces()
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n<link rel="icon" href="data:,">\n'
            f"<title>walle runs · {_e(where)}</title>\n<style>\n{faces}\n{tokens_css()}\n{CSS}</style>\n</head>\n<body>\n"
            f'<div class="page">\n<header class="top"><span class="wordmark">walle</span>'
            f'<span class="meta">runs · {_e(where)} · {len(rows)} runs</span></header>\n<main>\n'
            + "\n".join(sections) + "\n</main>\n</div>\n</body>\n</html>\n")


def write(out: Path, home: Path = HOME) -> Path:
    path = out / PAGE
    tmp = out / (PAGE + ".tmp")
    tmp.write_text(render(out, home), encoding="utf-8")
    tmp.replace(path)
    return path
