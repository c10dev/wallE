"""Short views of a scan: the terminal brief, brief-md for the plugin skill, walle show and the session alarm.

Every view answers the same three things in the same order (design brief 1.1): the verdict
with the estimated tokens that load before the first prompt, at most three items in the
user's own files (Start here), then where the rest is.

Terminal text is at most 80 columns. Display width comes from unicodedata.east_asian_width
(W and F take two columns), and only ASCII draws structure. Color is optional and never
carries meaning alone. All words come from walle/strings.py.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from . import __version__, runs
from .collect import HOME
from .strings import label as item_label
from .strings import resolve_lang, round_tokens, t, tn
from .stack import estimate_tokens
from .triage import LEAN, OVER, TRIM, norm_id

WIDTH = 80
QUOTE_LINES = 6  # source lines of one item, at most, in the brief and in brief-md
MD_QUOTE_LINES = 2  # in scan.md, so the verdict and Start here fit in its first 25 lines
MD_LINE_CHARS = 160  # a quoted line in Markdown; the agent reads the whole line in the file
BRIEF_MD_TOKENS = 1500  # brief-md size limit in estimated tokens (design brief 3.6)
LINE_CAP = {0: 24, 1: 24, 2: 24, 3: 30}  # lines of the brief by the number of Start here items
TABLE_ROWS = 10
GROUP_ROWS = 10
FORMATS = ("path", "brief", "brief-md", "md", "json")
VERDICT_ORDER = {OVER: 0, TRIM: 1, LEAN: 2}
VERDICT_COLOR = {OVER: "31", TRIM: "33", LEAN: "32"}
GROUPS = ("everything_else", "shared", "copies", "on_demand", "other_agents", "worktree_copies", "kept")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LEAD = re.compile(r"\s*(?:[-*+]\s+|\d+[.)]\s+)?")  # indent and list marker: wrapped lines hang under the text


# ---------------------------------------------------------------------- width and wrap
def width(s: str) -> int:
    """Columns that s takes in a terminal. ANSI color codes take none."""
    n = 0
    for c in ANSI.sub("", s):
        if unicodedata.combining(c):
            continue
        n += 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
    return n


def pad(s: str, cols: int) -> str:
    return s + " " * max(0, cols - width(s))


def _cut(word: str, room: int) -> tuple[str, str]:
    """The longest head of word that fits in room columns (at least one character), and the rest.

    A path breaks after a slash when one fits, so a file name stays in one piece.
    """
    used, i = 0, 0
    while i < len(word) and used + width(word[i]) <= room:
        used += width(word[i])
        i += 1
    i = max(i, 1)
    slash = word.rfind("/", 0, i)
    if 0 < slash < i - 1 and i < len(word):
        i = slash + 1
    return word[:i], word[i:]


def wrap(text: str, limit: int, first: str = "", rest: str = "") -> list[str]:
    """Wrap text at word boundaries. Lines start with first, then with rest. A word longer than a line breaks."""
    out: list[str] = []
    cur, has = first, False
    for word in text.split():
        sep = " " if has else ""
        if width(cur) + width(sep) + width(word) <= limit:
            cur, has = cur + sep + word, True
            continue
        if has:
            out.append(cur)
            cur, has = rest, False
        while word and width(cur) + width(word) > limit:
            head, word = _cut(word, limit - width(cur))
            out.append(cur + head)
            cur = rest
        cur, has = cur + word, bool(word)
    out.append(cur)
    return [line.rstrip() for line in out]


class Paint:
    """ANSI styles, or plain text when color is off."""

    def __init__(self, on: bool):
        self.on = on

    def _sgr(self, codes: str, s: str) -> str:
        return f"\x1b[{codes}m{s}\x1b[0m" if self.on and s else s

    def verdict(self, s: str, verdict: str) -> str:
        return self._sgr(f"1;{VERDICT_COLOR.get(verdict, '0')}", s)

    def bold(self, s: str) -> str:
        return self._sgr("1", s)

    def dim(self, s: str) -> str:
        return self._sgr("2", s)


def use_color(mode: str, isatty: bool, environ: dict) -> bool:
    """--color always or never decide; auto means a terminal and no NO_COLOR."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    return isatty and not environ.get("NO_COLOR")


# ---------------------------------------------------------------------- what to show
def folders(data: dict) -> list[str]:
    return list(dict.fromkeys(e["folder"] for e in data["triage"]["stacks"].values()))


def is_single(data: dict) -> bool:
    return len(folders(data)) <= 1


def primary(data: dict) -> tuple[dict | None, list[dict]]:
    """The stack a one-folder brief shows in full, and the other stacks of that folder.

    Claude comes first. Another agent leads only when it alone reads the user's own files.
    """
    entries = sorted(data["triage"]["stacks"].values(), key=lambda e: (e["agent"] != "claude", e["agent"]))
    if not entries:
        return None, []
    lead = next((e for e in entries if e["split"]["your_files"]), entries[0])
    if entries[0]["split"]["your_files"] or not lead["split"]["your_files"]:
        lead = entries[0]
    return lead, [e for e in entries if e is not lead]


def numbered(data: dict) -> list[str]:
    """Item ids in the order the brief numbers them: `walle show 1` is the first."""
    if not is_single(data):
        return list(data["triage"].get("start_here_by_reach") or [])
    lead, _others = primary(data)
    return list(lead["start_here"]) if lead else []


def shown(path: str | None, folder: str | None, home: Path) -> str:
    """A path as the user reads it: relative under the folder, ~/... under home, else absolute."""
    if not path:
        return "-"
    p = str(path)
    if folder:
        f = str(folder).rstrip("/")
        if p.startswith(f + "/"):
            return p[len(f) + 1:]
    h = str(home).rstrip("/")
    if p == h:
        return "~"
    return "~" + p[len(h):] if p.startswith(h + "/") else p


def stamp(data: dict) -> str:
    try:
        return datetime.fromisoformat(data["generated"]).strftime("%Y-%m-%d %H:%M")
    except (KeyError, TypeError, ValueError):
        return str(data.get("generated", ""))


def rules_id(data: dict) -> str:
    return (data.get("run", {}).get("rules_hash") or "----")[:4]


def location(it: dict, folder: str | None, home: Path) -> str:
    first = it["locations"][0]
    where = shown(first["file"], folder, home)
    return f"{where}:{first['line']}" if first.get("line") else where


def certainty(it: dict, lang: str) -> str:
    return t(f"certainty.{it['certainty']}", lang)


def _side(it: dict, side: str) -> dict:
    return next((l for l in it["locations"] if l.get("side") == side), it["locations"][-1])


def reason(it: dict, lang: str, folder: str | None, home: Path) -> str:
    """The sentence that says why walle lists the item and when to keep it."""
    lab = it["label"]
    match = it.get("match") or ""
    if lab == "every_task_step":
        kind = "read" if re.search(r"(?i)\bread\b|읽", match) else "step"
        return t(f"reason.every_task_step.{kind}", lang, match=match)
    if lab in ("dated", "model_name", "model_habit", "vague", "stacked_emphasis"):
        return t(f"reason.{lab}", lang, match=match)
    if lab == "dated_lines":
        return t("reason.dated_lines", lang, n=it["group_size"])
    if lab == "memory_lines":
        return t("reason.memory_lines", lang)
    if lab == "loaded_twice":
        keep = _side(it, "b")
        return t("reason.loaded_twice", lang, file=shown(keep["file"], folder, home), line=keep["line"])
    if lab in ("repeated", "repeated_maybe"):
        return t(f"reason.{lab}", lang, line=_side(it, "b")["line"])
    if lab in ("similar_twice", "conflict", "copied_between"):
        return t(f"reason.{lab}", lang)
    if lab in ("term_clash", "format_differs"):
        terms = ", ".join((it.get("why") or {}).get("shared") or []) or "-"
        return t(f"reason.{lab}", lang, shared=terms)
    if lab == "different_agents":
        agents = sorted({k.rsplit("@", 1)[1] for k in it["stacks"]})
        return t("reason.different_agents", lang, agents=", ".join(agents))
    if lab == "drifted_copy":
        a, b = it["locations"][0]["file"], _side(it, "b")["file"]
        return t("reason.drifted_copy", lang, a=shown(a, folder, home), b=shown(b, folder, home), n=it["group_size"])
    if lab in ("source_of", "copy_of"):
        of = shown(it.get("of"), folder, home)
        n = it["group_size"]
        if lab == "source_of" and not of.endswith("CLAUDE.md"):
            return t("reason.source_of.codex", lang, file=of, n=n)
        return t(f"reason.{lab}", lang, file=of, n=n)
    if lab == "large_layer":
        mv = it.get("move")
        if mv:
            return t("reason.large_layer", lang, heading=mv["heading"], start=mv["line_start"], end=mv["line_end"],
                     tokens=round_tokens(mv["tokens_est"]))
        share = max((it.get("shares") or {0: 0}).values())
        return t("reason.large_layer.file", lang, file=shown(it["locations"][0]["file"], folder, home),
                 tokens=round_tokens(it["tokens_est"]), share=share)
    return ""


def _verdict_sentences(e: dict, lang: str, folder: str | None, home: Path, data: dict) -> list[str]:
    """The sentences of the verdict block after the verdict word."""
    budget = e["budget"]
    out = [t("sentence.load", lang, tokens=round_tokens(e["tokens_est"], budget), budget=f"{budget:,}")]
    if e["verdict"] == OVER:
        big = largest(data, e)
        if big:
            path = shown(big["path"], folder, home)
            path = path if path.startswith(("~", "/")) else "./" + path
            out.append(t("sentence.largest", lang, file=path, tokens=round_tokens(big["tokens_est"])))
    n = e["to_read"]
    out.append(tn("sentence.to_read", n, lang) if n else t("sentence.nothing", lang))
    return out


def largest(data: dict, e: dict) -> dict | None:
    row = next((s for s in data["stacks"] if s["folder"] == e["folder"] and s["agent"] == e["agent"]), None)
    layers = [l for l in (row or {}).get("layers", []) if l.get("path")]
    return max(layers, key=lambda l: l["tokens_est"], default=None)


def split_line(e: dict, lang: str) -> str:
    s = e["split"]
    return t("sentence.split", lang, yours=round_tokens(s["your_files"]), shared=round_tokens(s["shared"]),
             skills=round_tokens(s["skills"]))


def shared_rows(e: dict) -> list[dict]:
    return [r for r in e["groups"]["shared"] if r["items"]]


def shared_text(row: dict, lang: str, home: Path) -> str:
    file, n = shown(row["file"], None, home), len(row["items"])
    if row["loads_in"] > 1:
        return tn("sentence.shared_row", n, lang, file=file, folders=row["loads_in"])
    return tn("sentence.shared_here", n, lang, file=file)


def more_to_see(data: dict, entries: list[dict]) -> bool:
    hidden = data["triage"].get("hidden", {}).get("elsewhere", 0)
    return bool(hidden or any(sum(e["counts"].values()) for e in entries))


def scan_arg(data: dict, cwd: Path | None, home: Path) -> str:
    """The roots as a walle scan argument, or nothing when the scan was of the current folder."""
    roots = data.get("roots") or []
    if cwd is not None and roots == [str(cwd)]:
        return ""
    return " ".join(shown(r, None, home) for r in roots) + " "


def ref_of(n: int, item_id: str, data: dict, cwd: Path | None) -> str:
    """How the Next line names an item: its number when `walle show <n>` in cwd finds this run, else its id."""
    return str(n) if cwd is not None and str(cwd) in (data.get("roots") or []) else item_id


# ---------------------------------------------------------------------- terminal brief
def _quote(it: dict, limit: int, cap: int, ref: str, folder: str | None, home: Path, lang: str) -> list[str]:
    """Source lines with their numbers: `   8 | text`, wrapped under the same gutter, at most cap lines.

    Blank source lines are not shown, but `... N more lines` counts them.
    """
    rows: list[list] = []  # [file header or None, line number, text, source lines it stands for]
    last_file = it["locations"][0]["file"]
    for loc in it["locations"]:
        if loc.get("text") is None:
            continue
        if loc["file"] != last_file:
            rows.append([loc["file"], 0, "", 0])
            last_file = loc["file"]
        for k, s in enumerate(loc["text"].split("\n")):
            if s.strip():
                rows.append([None, loc["line"] + k, s.expandtabs(4), 1])
            elif rows and rows[-1][0] is None:
                rows[-1][3] += 1
    if not any(r[0] is None for r in rows):
        return []
    gut = max(len(str(r[1])) for r in rows if r[0] is None)
    blocks: list[list[str]] = []
    for f, n, s, _w in rows:
        if f is not None:
            blocks.append(wrap(shown(f, folder, home), limit, "   ", "   "))
            continue
        lead = re.match(LEAD, s).group(0)
        first = f"   {n:>{gut}} | {lead}"
        rest = f"   {'':>{gut}} | " + " " * width(lead)
        blocks.append(wrap(s[len(lead):], limit, first, rest))
    if sum(len(b) for b in blocks) <= cap:
        return [line for b in blocks for line in b]
    out: list[str] = []
    left = sum(r[3] for r in rows)
    for b, r in zip(blocks, rows):
        if len(out) + len(b) > cap - 1:
            break
        out.extend(b)
        left -= r[3]
    if not out:  # one long line: show its start
        out = blocks[0][:max(1, cap - 1)]
        left -= rows[0][3]
    more = tn("sentence.more_lines", max(1, left), lang, ref=ref)
    return out + wrap(more, limit, "   ", "   ")


def _head(n: str, it: dict, right: str, limit: int, paint: Paint, folder: str | None, home: Path,
          lang: str, plain_loc: str | None = None) -> list[str]:
    """`1  CHECK  CLAUDE.md:8 ....... every-task step? candidate`. The right part moves down when it does not fit."""
    verb = t(f"verb.{it['verb']}", lang) if it["verb"] else ""
    lead = pad(n, 2) + " " + pad(verb, 6) + " "
    loc = plain_loc if plain_loc is not None else location(it, folder, home)
    lines = wrap(loc, limit, lead, " " * width(lead))
    if verb:
        lines[0] = lines[0].replace(lead, pad(n, 2) + " " + paint.bold(verb) + " " * (width(pad(verb, 6)) - width(verb) + 1), 1)
    if width(lines[-1]) + 2 + width(right) <= limit:
        lines[-1] = lines[-1] + " " * (limit - width(lines[-1]) - width(right)) + right
    else:
        lines += [" " * max(0, limit - width(right)) + line for line in wrap(right, limit)] if width(right) <= limit \
            else wrap(right, limit, "   ", "   ")
    return lines


def _item_right(it: dict, lang: str, paint: Paint) -> str:
    return f"{item_label(it, lang)} {paint.dim(certainty(it, lang)) if it['certainty'] == 'candidate' else certainty(it, lang)}"


def _footer(rows: list[tuple[str, list[str]]], limit: int, paint: Paint) -> list[str]:
    """Label column rows: `Shared  ...`, `Next    ...`."""
    rows = [(k, v) for k, v in rows if v]
    if not rows:
        return []
    short_labels = [width(k) for k, _v in rows if width(k) <= 8] or [6]
    base = max(short_labels) + 2  # a long label (Copies between folders) does not push the others right
    out = []
    for k, texts in rows:
        col = max(base, width(k) + 2)
        for i, text in enumerate(texts):
            first = pad(k, col) if i == 0 else " " * col
            out += wrap(text, limit, first, " " * col)
    return out


def render_brief(data: dict, lang: str = "en", limit: int = WIDTH, color: bool = False, home: Path = HOME,
                 report: str | None = None, cwd: Path | None = None, show_all: bool = False) -> str:
    """The terminal brief of a scan (design brief 3.1 and 3.2). The last line is the report path."""
    paint = Paint(color)
    limit = min(limit, WIDTH)
    report_line = shown(report, None, home) if report else ""
    if not data["triage"]["stacks"] or data.get("artifact_count", 1) == 0:
        root = shown((data.get("roots") or ["."])[0], None, home)
        lines = wrap(t("empty.no_files", lang, path=root), limit)
        return "\n".join(lines + ([report_line] if report_line else [])) + "\n"
    if not is_single(data):
        return _render_many(data, lang, limit, paint, home, report_line, cwd, show_all)
    return _render_one(data, lang, limit, paint, home, report_line, cwd, show_all)


def _render_one(data, lang, limit, paint, home, report_line, cwd, show_all) -> str:
    lead, others = primary(data)
    folder = lead["folder"]
    items = {it["id"]: it for it in data["items"]}
    start = [items[i] for i in lead["start_here"] if i in items]
    out = [t("brief.header", lang, version=__version__, root=shown(folder, None, home), agent=lead["agent"],
             date=stamp(data)), ""]

    vword = t(f"verdict.{lead['verdict']}", lang)
    indent = " " * (width(vword) + 2)
    sentences = _verdict_sentences(lead, lang, folder, home, data)
    if lead["verdict"] == LEAN and not start:
        body = t("empty.lean", lang, tokens=round_tokens(lead["tokens_est"], lead["budget"]),
                 budget=f"{lead['budget']:,}").partition(" · ")[2]
        sentences = [body]
    block = wrap(sentences[0], limit, pad(vword, width(vword) + 2), indent)
    block[0] = paint.verdict(vword, lead["verdict"]) + block[0][len(vword):]
    for s in sentences[1:]:
        block += wrap(s, limit, indent, indent)
    block += wrap(split_line(lead, lang), limit, indent, indent)
    out += block

    footer: list[tuple[str, list[str]]] = []
    footer.append((t("brief.shared", lang), [shared_text(r, lang, home) for r in shared_rows(lead)]))
    also = [t("brief.other_stack", lang, agent=e["agent"], verdict=t(f"verdict.{e['verdict']}", lang),
              tokens=round_tokens(e["tokens_est"], e["budget"]), n=e["to_read"])
            for e in others if e["split"]["your_files"]]
    footer.append((t("brief.also", lang), also))
    nxt = []
    if start:
        nxt.append(f"walle show {ref_of(1, start[0]['id'], data, cwd)}")
    if not show_all and more_to_see(data, [lead, *others]):
        nxt.append(f"walle scan {scan_arg(data, cwd, home)}--all")
    footer.append((t("brief.next", lang), [" | ".join(nxt)] if nxt else []))
    foot = _footer(footer, limit, paint)
    tail = foot + ([report_line] if report_line else [])

    if start:
        heads, reasons = [], []
        for n, it in enumerate(start, 1):
            heads.append(_head(str(n), it, _item_right(it, lang, paint), limit, paint, folder, home, lang))
            reasons.append(wrap(reason(it, lang, folder, home), limit, "   ", "   "))
        fixed = len(out) + 2 + len(tail) + 1  # blank, "Start here", items, blank, footer
        fixed += sum(len(h) + len(r) for h, r in zip(heads, reasons)) + (len(start) - 1)
        room = LINE_CAP.get(len(start), 30) - fixed
        # Quote lines share what the line cap leaves; earlier items get the remainder.
        caps = [QUOTE_LINES if show_all else max(1, min(QUOTE_LINES, room // len(start) + (i < room % len(start))))
                for i in range(len(start))]
        out += ["", paint.bold(t("level.start_here", lang))]
        for n, (it, h, r, cap) in enumerate(zip(start, heads, reasons, caps), 1):
            if n > 1:
                out.append("")
            ref = ref_of(n, it["id"], data, cwd)
            out += h + _quote(it, limit, cap, ref, folder, home, lang) + r
    if show_all:
        out += _all_findings(data, [lead, *others], lead["start_here"], lang, limit, paint, home, folder)
    out.append("")
    out += tail
    return "\n".join(out) + "\n"


def _all_line(it: dict, lang: str, limit: int, folder: str | None, home: Path) -> list[str]:
    verb = t(f"verb.{it['verb']}", lang) if it["verb"] else "-"
    text = f"{location(it, folder, home)}  {item_label(it, lang)} {certainty(it, lang)} [{it['rule']}]"
    return wrap(text, limit, f"  {it['id']}  {pad(verb, 6)} ", " " * 18)


def collect_groups(entries: list[dict], on_screen) -> dict[str, list]:
    """The L2 groups of several stacks, each member once.

    Item groups hold ids. shared, copies and worktree_copies hold rows; rows of the same
    file (or file pair) merge. The Start here items of a stack whose list is not on screen
    join Everything else, so nothing that a stack lists is left out.
    """
    seen = set(on_screen)
    out: dict[str, list] = {g: [] for g in GROUPS}
    rows: dict[str, dict] = {"shared": {}, "copies": {}, "worktree_copies": {}}
    for e in entries:
        for g in ("everything_else", "on_demand", "other_agents", "kept"):
            ids = (e["start_here"] if g == "everything_else" else []) + list(e["groups"][g])
            for i in ids:
                if i not in seen:
                    seen.add(i)
                    out[g].append(i)
        for r in e["groups"]["shared"]:
            row = rows["shared"].setdefault(r["file"], {**r, "items": []})
            row["loads_in"] = max(row["loads_in"], r["loads_in"])
            row["items"] += [i for i in r["items"] if i not in row["items"]]
        for r in e["groups"]["copies"]:
            row = rows["copies"].setdefault("\0".join(sorted((r["file"], r["other"]))), {**r, "items": []})
            row["items"] += [i for i in r["items"] if i not in row["items"]]
        for r in e["groups"]["worktree_copies"]:
            rows["worktree_copies"].setdefault(r["file"], r)
    for g, by in rows.items():
        out[g] = [r for r in by.values() if g == "worktree_copies" or r["items"]]
    return out


def group_count(g: str, members: list) -> int:
    if g in ("shared", "copies"):
        return len({i for r in members for i in r["items"]})
    return len(members)


def _all_findings(data, entries, on_screen, lang, limit, paint, home, folder) -> list[str]:
    """--all: every group with every item, its id and its rule id."""
    items = {it["id"]: it for it in data["items"]}
    out = ["", paint.bold(t("level.all_findings", lang))]
    for g, members in collect_groups(entries, on_screen).items():
        out.append(f"{t(f'group.{g}', lang)} ({group_count(g, members)})")
        for m in members:
            if g == "worktree_copies":
                out += wrap(_worktree_text(m, lang), limit, "  ", "    ")
                continue
            for i in ([m] if isinstance(m, str) else m["items"]):
                if i in items:
                    out += _all_line(items[i], lang, limit, folder, home)
    hidden = [it for it in data["items"] if it["scope"] == "elsewhere"]
    if hidden:
        out.append(f"{t('group.elsewhere', lang)} ({len(hidden)})")
        for it in hidden:
            out += _all_line(it, lang, limit, folder, home)
    return out


def _worktree_text(r: dict, lang: str) -> str:
    sha = (r.get("main_sha") or "")[:8] or t("md.not_in_main", lang)
    return t("md.worktree_row", lang, file=r["file"], versions=r["versions"], copies=r["copies"], sha=sha)


def _stack_rows(data: dict) -> list[dict]:
    return sorted(data["triage"]["stacks"].values(),
                  key=lambda e: (VERDICT_ORDER.get(e["verdict"], 3), -e["tokens_est"], e["folder"], e["agent"]))


def _folder_name(folder: str, data: dict, home: Path) -> str:
    roots = data.get("roots") or []
    if len(roots) == 1:
        r = roots[0].rstrip("/")
        if folder == r:
            return Path(r).name or r
        if folder.startswith(r + "/"):
            return folder[len(r) + 1:]
    return shown(folder, None, home)


def _clip_left(s: str, cols: int) -> str:
    """Cut a path from the left, so the folder name stays: `...ject/sub`."""
    if width(s) <= cols:
        return s
    while s and width(s) > cols - 3:
        s = s[1:]
    return "..." + s


def _table(data: dict, lang: str, paint: Paint, home: Path, rows: list[dict]) -> list[str]:
    cols = (24, 8, 11, 12, 9)
    head = [t("table.folder", lang), t("table.agent", lang), t("table.tokens", lang), t("table.verdict", lang),
            t("table.to_read", lang)]
    cols = (max(cols[0], width(head[0])), max(cols[1], width(head[1])), max(cols[2], width(head[2])),
            max(cols[3], width(head[3]), *(width(t(f"verdict.{v}", lang)) for v in VERDICT_ORDER)),
            max(cols[4], width(head[4])))

    def line(c):
        return (pad(c[0], cols[0]) + " " + pad(c[1], cols[1]) + " " + " " * (cols[2] - width(c[2])) + c[2]
                + "  " + pad(c[3], cols[3]) + " " * (cols[4] - width(c[4])) + c[4])
    out = [line(head)]
    for e in rows:
        word = t(f"verdict.{e['verdict']}", lang)
        text = line([_clip_left(_folder_name(e["folder"], data, home), cols[0]), e["agent"],
                     "~" + round_tokens(e["tokens_est"], e["budget"]), word, str(e["to_read"])])
        out.append(text.replace(word, paint.verdict(word, e["verdict"]), 1) if paint.on else text)
    return out


def _copy_pairs(data: dict) -> int:
    pairs = set()
    for e in data["triage"]["stacks"].values():
        for r in e["groups"]["copies"]:
            pairs.add(frozenset((r["file"], r["other"])))
    return len(pairs)


def _reach_right(it: dict, lang: str, paint: Paint) -> str:
    if it["label"] == "large_layer":
        share = max((it.get("shares") or {0: 0}).values())
        return t("brief.move_size", lang, tokens=round_tokens(it["tokens_est"]), share=share)
    right = _item_right(it, lang, paint)
    if len(it["stacks"]) > 1:
        right += ", " + t("brief.folders", lang, n=len({k.rsplit("@", 1)[0] for k in it["stacks"]}))
    return right


def _render_many(data, lang, limit, paint, home, report_line, cwd, show_all) -> str:
    stacks = _stack_rows(data)
    budget = data["triage"]["budget"]
    roots = " ".join(shown(r, None, home) for r in data.get("roots") or [])
    out = [t("brief.many_header", lang, version=__version__, root=roots, stacks=len(stacks),
             files=data.get("artifact_count", 0), copies=data.get("copy_count", 0)), ""]
    counts = data["triage"]["verdicts"]
    parts = " | ".join(f"{paint.verdict(t(f'verdict.{v}', lang), v)} {counts.get(v, 0)}" for v in VERDICT_ORDER)
    right = t("brief.budget", lang, budget=f"{budget:,}")
    out += [parts + " " * max(2, limit - width(parts) - width(right)) + right
            if width(parts) + 2 + width(right) <= limit else parts, ""]
    shown_rows = stacks if show_all else stacks[:TABLE_ROWS]
    out += _table(data, lang, paint, home, shown_rows)
    arg = scan_arg(data, cwd, home)
    if len(stacks) > len(shown_rows):
        out += wrap(t("brief.more_rows", lang, n=len(stacks) - len(shown_rows), root=arg.strip() or "."), limit)

    items = {it["id"]: it for it in data["items"]}
    start = [items[i] for i in numbered(data) if i in items]
    if start:
        out += ["", paint.bold(t("level.start_here_by_reach", lang))]
        for n, it in enumerate(start, 1):
            loc = shown(it["locations"][0]["file"], None, home) if it["label"] == "large_layer" else None
            if loc is None:
                first = it["locations"][0]
                loc = f"{shown(first['file'], None, home)}:{first['line']}"
            out += _head(str(n), it, _reach_right(it, lang, paint), limit, paint, None, home, lang, plain_loc=loc)
    if show_all:
        out += _all_findings(data, stacks, numbered(data), lang, limit, paint, home, None)
    out.append("")
    footer: list[tuple[str, list[str]]] = []
    pairs = _copy_pairs(data)
    if pairs:
        footer.append((t("group.copies", lang), [t("brief.copies", lang, n=pairs, scan=f"walle scan {arg}--all")]))
    nxt = []
    if start:
        nxt.append(f"walle show {ref_of(1, start[0]['id'], data, cwd)}")
    if not show_all:
        nxt.append(f"walle scan {arg}--all")
    footer.append((t("brief.next", lang), [" | ".join(nxt)]))
    out += _footer(footer, limit, paint)
    if report_line:
        out.append(report_line)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------- brief-md (plugin skill input)
def clip(s: str, chars: int) -> str:
    """s cut at a word boundary to at most chars characters, with ' ...' when cut."""
    s = " ".join(s.split())
    if len(s) <= chars:
        return s
    head = s[:chars - 4]
    cut = head.rfind(" ")
    return (head[:cut] if cut > chars // 2 else head).rstrip() + " ..."


def _md_quote(it: dict, cap: int, ref: str, lang: str) -> list[str]:
    """Quoted source lines, `   > 8: text`, each cut to MD_LINE_CHARS, at most cap lines."""
    lines = []
    for loc in it["locations"]:
        if loc.get("text") is None:
            continue
        for k, s in enumerate(loc["text"].split("\n")):
            if s.strip():
                lines.append((loc["line"] + k, s.rstrip()))
    if not lines or cap <= 0:
        return []
    out = [f"   > {n}: {clip(s, MD_LINE_CHARS)}" for n, s in lines]
    if len(out) > cap:
        more = [f"   > {tn('sentence.more_lines', len(lines) - cap + 1, lang, ref=ref)}"] if cap > 1 else []
        out = out[:cap - len(more)] + more
    return out


def _md_item(n: int, it: dict, lang: str, folder: str | None, home: Path, cap: int, data: dict,
             cwd: Path | None, with_id: bool) -> list[str]:
    verb = t(f"verb.{it['verb']}", lang) if it["verb"] else "-"
    tail = f" · id {it['id']}" if with_id else ""
    head = f"{n}. **{verb}** `{location(it, folder, home)}` · {item_label(it, lang)} · {certainty(it, lang)}{tail}"
    return [head + "  ", "   " + reason(it, lang, folder, home)] + _md_quote(it, cap, ref_of(n, it["id"], data, cwd), lang)


def _md_counts(entries: list[dict], on_screen, lang: str) -> str:
    groups = collect_groups(entries, on_screen)
    return " · ".join(f"{t(f'group.{g}', lang)} {group_count(g, m)}" for g, m in groups.items())


def render_brief_md(data: dict, lang: str = "en", home: Path = HOME, report: str | None = None,
                    cwd: Path | None = None) -> str:
    """The skill's input (design brief 3.6): verdict, Start here, group counts, shared rows, report path.

    It stays at or under BRIEF_MD_TOKENS estimated tokens: quotes get shorter until it fits.
    """
    for cap in (QUOTE_LINES, 3, 1, 0):
        text = _brief_md(data, lang, home, report, cwd, cap)
        if estimate_tokens(text) <= BRIEF_MD_TOKENS:
            break
    return text


def _brief_md(data: dict, lang: str, home: Path, report: str | None, cwd: Path | None, cap: int) -> str:
    items = {it["id"]: it for it in data["items"]}
    out: list[str] = []
    if not data["triage"]["stacks"] or data.get("artifact_count", 1) == 0:
        root = shown((data.get("roots") or ["."])[0], None, home)
        out.append(t("empty.no_files", lang, path=root))
    elif is_single(data):
        lead, others = primary(data)
        folder = lead["folder"]
        start = [items[i] for i in lead["start_here"] if i in items]
        vword = t(f"verdict.{lead['verdict']}", lang)
        sentences = _verdict_sentences(lead, lang, folder, home, data)
        sentences[0] = t("brief_md.load", lang, folder=Path(folder).name, tokens=round_tokens(lead["tokens_est"],
                         lead["budget"]), budget=f"{lead['budget']:,}")
        if lead["verdict"] == LEAN and not start:
            sentences = [t("empty.lean", lang, tokens=round_tokens(lead["tokens_est"], lead["budget"]),
                           budget=f"{lead['budget']:,}").partition(" · ")[2]]
        out.append(f"walle: {vword} · " + " ".join(sentences))
        out.append(split_line(lead, lang).replace(" | ", " · "))
        out.append(t("brief_md.folder", lang, folder=folder, agent=lead["agent"]))
        if start:
            out += ["", t("level.start_here", lang)]
            for n, it in enumerate(start, 1):
                out += _md_item(n, it, lang, folder, home, cap, data, cwd, True)
        out += ["", f"{t('level.all_findings', lang)}: {_md_counts([lead, *others], lead['start_here'], lang)}"]
        for r in shared_rows(lead):
            out.append(f"{t('brief.shared', lang)}: {shared_text(r, lang, home)}")
        for e in others:
            if e["split"]["your_files"]:
                out.append(f"{t('brief.also', lang)}: " + t("brief.other_stack", lang, agent=e["agent"],
                           verdict=t(f"verdict.{e['verdict']}", lang),
                           tokens=round_tokens(e["tokens_est"], e["budget"]), n=e["to_read"]))
    else:
        stacks = _stack_rows(data)
        counts = data["triage"]["verdicts"]
        out.append("walle: " + " | ".join(f"{t(f'verdict.{v}', lang)} {counts.get(v, 0)}" for v in VERDICT_ORDER)
                   + " · " + t("brief.budget", lang, budget=f"{data['triage']['budget']:,}"))
        out += ["", "| " + " | ".join(t(f"table.{c}", lang) for c in ("folder", "agent", "tokens", "verdict", "to_read"))
                + " |", "|---|---|---|---|---|"]
        for e in stacks[:TABLE_ROWS]:
            word = t(f"verdict.{e['verdict']}", lang)
            tokens = round_tokens(e["tokens_est"], e["budget"])
            out.append(f"| {_folder_name(e['folder'], data, home)} | {e['agent']} | ~{tokens} | {word} | {e['to_read']} |")
        if len(stacks) > TABLE_ROWS:
            out.append(t("brief.more_rows", lang, n=len(stacks) - TABLE_ROWS, root=scan_arg(data, cwd, home).strip() or "."))
        start = [items[i] for i in numbered(data) if i in items]
        if start:
            out += ["", t("level.start_here_by_reach", lang)]
            for n, it in enumerate(start, 1):
                out += _md_item(n, it, lang, None, home, cap, data, cwd, True)
        out += ["", f"{t('level.all_findings', lang)}: {_md_counts(stacks, numbered(data), lang)}"]
    if report:
        out.append(t("brief_md.report", lang, path=report))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------- walle show
def find_item(out: Path, ref: str, cwd: Path, limit: int = 50) -> tuple[dict | None, dict | None, str | None]:
    """(item, scan data, error key). A number is the n-th item the newest scan of cwd listed; else an id."""
    rows = [r for r in runs.read_index(out) if r.get("kind") == "scan"][::-1][:limit]
    if ref.isdigit():
        row = next((r for r in rows if str(cwd) in (r.get("roots") or [])), None)
        data = _load(row)
        if data is None:
            return None, None, "show.no_recent"
        ids = numbered(data)
        n = int(ref)
        if not 1 <= n <= len(ids):
            return None, data, "show.no_number"
        return next(it for it in data["items"] if it["id"] == ids[n - 1]), data, None
    for row in rows:
        data = _load(row)
        if data is None:
            continue
        it = next((it for it in data.get("items", []) if it["id"] == ref), None)
        if it:
            return it, data, None
    return None, None, "show.no_item"


def _load(row: dict | None) -> dict | None:
    if not row:
        return None
    try:
        data = json.loads(Path(row["path"]).read_text())
    except (OSError, ValueError, KeyError):
        return None
    return data if data.get("schema", 1) >= 2 else None


def stale(it: dict) -> bool:
    """A line of the item is not in its file as the scan read it."""
    for loc in it["locations"]:
        if loc.get("text") is None or not loc.get("line"):
            continue
        try:
            lines = Path(loc["file"]).read_text(errors="replace").splitlines()
        except OSError:
            return True
        live = "\n".join(lines[loc["line"] - 1:loc["end_line"] or loc["line"]])
        if norm_id(live) != norm_id(loc["text"]):
            return True
    return False


def render_show(it: dict, data: dict, ref: str, lang: str = "en", limit: int = WIDTH, color: bool = False,
                home: Path = HOME, context: int = 3) -> str:
    """One item with the lines around it, read again from the files."""
    paint = Paint(color)
    limit = min(limit, WIDTH)
    run = (data.get("run") or {}).get("id") or "-"
    out = wrap(t("show.resolved", lang, ref=ref, id=it["id"], run=run), limit) if ref != it["id"] else []
    folder = None
    if is_single(data):
        lead, _o = primary(data)
        folder = lead["folder"] if lead else None
    out += _head("", it, _item_right(it, lang, paint), limit, paint, None, home, lang,
                 plain_loc=location(it, None, home))
    out += wrap(f"[{it['rule']}] {it['id']}", limit, "   ", "   ")
    by_file: dict[str, set[int]] = {}
    for loc in it["locations"]:
        if loc.get("line"):
            by_file.setdefault(loc["file"], set()).update(range(loc["line"], (loc.get("end_line") or loc["line"]) + 1))
    for file, marked in by_file.items():
        try:
            lines = Path(file).read_text(errors="replace").splitlines()
        except OSError:
            continue
        want = sorted({n for m in marked for n in range(m - context, m + context + 1) if 1 <= n <= len(lines)})
        gut = len(str(want[-1])) if want else 1
        out.append("")
        out += wrap(shown(file, None, home), limit)
        prev = None
        for n in want:
            if prev is not None and n != prev + 1:
                out.append(" " * (gut + 3) + "...")
            mark = "> " if n in marked else "  "
            s = lines[n - 1].expandtabs(4)
            lead = re.match(LEAD, s).group(0)
            first = f"{mark}{n:>{gut}} | {lead}"
            rest = f"  {'':>{gut}} | " + " " * width(lead)
            out += wrap(s[len(lead):], limit, first, rest) if s.strip() else [f"{mark}{n:>{gut}} |"]
            prev = n
    out.append("")
    out += wrap(reason(it, lang, folder, home), limit)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------- session alarm
ALARM_MAX = 200


def alarm_text(tokens: int, budget: int, largest_path: str | None, largest_tokens: int, lang: str = "en") -> str:
    """The SessionStart context line (design brief 3.11): at most ALARM_MAX characters, no list of findings."""
    fields = {"tokens": round_tokens(tokens, budget), "budget": f"{budget:,}"}
    if largest_path:
        msg = t("alarm.over", lang, file=largest_path, largest=round_tokens(largest_tokens), **fields)
        if len(msg) <= ALARM_MAX:
            return msg
    return t("alarm.over.short", lang, **fields)[:ALARM_MAX]


def relative_to(path: Path, folder: Path, home: Path) -> str:
    """./x under the folder, ~/x under home, else the absolute path."""
    p, f = str(path), str(folder).rstrip("/")
    if p.startswith(f + "/"):
        return "./" + p[len(f) + 1:]
    return shown(p, None, home)


# ---------------------------------------------------------------------- command output
def _columns(tty: bool) -> int:
    return shutil.get_terminal_size((WIDTH, 24)).columns if tty else WIDTH


def print_scan(args, data: dict, path: Path, fmt: str, elapsed: float, home: Path = HOME) -> int:
    """Print a scan in the chosen format. path prints the report path alone, as walle always did."""
    lang = resolve_lang(args.lang)
    if not args.quiet and elapsed >= 1 and sys.stderr.isatty():
        print(t("progress.done", lang, files=data.get("artifact_count", 0), pairs=len(data.get("pairs", [])),
                items=len(data.get("items", [])), secs=f"{elapsed:.1f}"), file=sys.stderr)
    cwd = Path.cwd().resolve()
    if fmt == "path":
        print(path)
    elif fmt == "json":
        print(json.dumps(data, ensure_ascii=False, indent=1))
    elif fmt == "md":
        sys.stdout.write(path.read_text())
    elif fmt == "brief-md":
        sys.stdout.write(render_brief_md(data, lang, home, str(path), cwd))
    else:
        tty = sys.stdout.isatty()
        html = path.with_name("scan.html")
        sys.stdout.write(render_brief(data, lang, _columns(tty), use_color(args.color, tty, os.environ), home,
                                      str(html if html.exists() else path), cwd, args.show_all))
    return 0


def show_command(args, home: Path = HOME) -> int:
    """walle show <n|id>: exit 0 shown, 1 the file changed since the scan, 2 no such item."""
    lang = resolve_lang(args.lang)
    it, data, err = find_item(args.out, args.ref, Path.cwd().resolve())
    if err:
        print(f"walle show: {t(err, lang, ref=args.ref)}", file=sys.stderr)
        return 2
    if stale(it):
        print(t("show.changed", lang))
        return 1
    tty = sys.stdout.isatty()
    sys.stdout.write(render_show(it, data, args.ref, lang, _columns(tty), use_color(args.color, tty, os.environ), home))
    return 0
