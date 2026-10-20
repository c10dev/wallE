"""Triage: turn flags and pairs into items, a verdict for each stack, and a Start here list.

scan.json schema 2 holds the output of this module ("items", "triage") next to the raw
findings of the engine ("flags", "pairs"), which keep their old form.

- An item is one thing to read or change. One flagged line is one item. Lines that
  belong together are one item: the dated and model-name lines of one file (from
  PER_FILE_GROUP lines), all flagged lines of one MEMORY.md, a run of consecutive
  copied lines between two files, and a file that copies another file of its stack
  (a file_copy note).
- scope tells when the lines of an item reach an agent: session_start (in the layers of
  a stack), on_demand (a skill, agent or command body), not_together (two lines that no
  stack loads together), other_agent (Cursor, Copilot, Gemini, Windsurf and Cline files,
  whose load rules walle does not model) or elsewhere (outside every stack of the scan).
- relation (pairs only): same_file, same_stack, sibling_copy (one agent, two folders),
  cross_agent (stacks of two agents) or file_copy.
- ownership tells whose file it is: owned (inside the git checkout of the folder),
  memory (the MEMORY.md of the folder), shared (global files and parent files outside
  the checkout) or skills (global skill bodies).
- verdict of a stack: OVER BUDGET when its estimated tokens exceed the budget, TRIM when
  the stack has certain DELETE lines of TRIM_DELETE_TOKENS or more or a conflict (two
  drifted lines that load together), or the user's own files have TRIM_CANDIDATES open
  candidates, else LEAN.
- topic and protocol pairs in one stack are term clashes: CHECK candidates outside Start
  here and the verdict, until `walle judge` reads them.
- Start here: at most START_HERE items in the user's own files, by SEVERITY.

Labels and verbs are keys, not display text. walle/strings.py gives the text in English and Korean.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .classify import Flag
from .collect import Artifact, find_checkout
from .compact import DUP, MIN_LEN, Pair
from .segment import Unit
from .stack import Stack, estimate_tokens

BUDGET = 8000
SAFE_DELETE = 0.95  # duplicate or repeat at or above: DELETE (certain); below: CHECK (candidate)
LARGE_LAYER_TOKENS = 2000  # an owned layer above this many tokens...
LARGE_LAYER_SHARE = 0.4  # ...and above this share of its stack: MOVE
FILE_COPY_SHARE = 0.8  # this share of a file's rules copied from another file of the stack: file_copy
TRIM_DELETE_TOKENS = 100
TRIM_CANDIDATES = 3
GROUP_GAP = 2  # consecutive pairs: at most this many units apart on each side
PER_FILE_GROUP = 3  # dated and model-name lines of one file become one item from this count
START_HERE = 3
PER_RULE = 2  # Start here holds at most this many items of one rule when other rules wait
# Bump when the item logic changes in a way that the constants above do not show.
RULES_REVISION = 2

OVER, TRIM, LEAN = "OVER BUDGET", "TRIM", "LEAN"
YOURS = ("owned", "memory")
ON_DEMAND = ("skill", "agent", "command")
OWN_RANK = {"owned": 0, "memory": 1, "shared": 2, "skills": 3}
CAUSE = {"duplicate": 4, "repeat": 4, "drift": 2, "topic": 2, "protocol": 2}

VERBS = ("DELETE", "PICK ONE", "CHECK", "REWRITE", "MOVE", "SYNC?")
LABELS = (
    "conflict", "every_task_step", "large_layer", "loaded_twice", "similar_twice", "repeated", "repeated_maybe",
    "model_habit", "dated", "model_name", "dated_lines", "memory_lines", "vague", "stacked_emphasis",
    "different_agents", "copied_between", "drifted_copy", "format_differs", "source_of", "copy_of", "term_clash",
)
# Out of Start here and out of the verdict: mirrors go to Everything else; term clashes wait for a judge.
NOT_IN_POOL = ("different_agents", "term_clash")
SEVERITY = {
    "conflict": 1, "every_task_step": 2, "large_layer": 3,
    "loaded_twice": 4, "similar_twice": 4, "repeated": 4, "repeated_maybe": 4,
    "model_habit": 5, "dated": 6, "model_name": 6, "dated_lines": 6, "memory_lines": 6,
    "vague": 7, "stacked_emphasis": 7,
}
FLAG_LABEL = {
    "always_step": ("every_task_step", "CHECK"),
    "model_habit": ("model_habit", "CHECK"),
    "dated": ("dated", "CHECK"),
    "model_name": ("model_name", "CHECK"),
    "vague_quality": ("vague", "REWRITE"),
    "emphasis_stack": ("stacked_emphasis", "REWRITE"),
}
# Pairs of these labels chain into one item when their lines are consecutive.
# Copies that do not load together chain with drifted copies: one renamed term must not
# become one item for each line.
CHAIN_CLASS = {"loaded_twice": "loaded_twice", "similar_twice": "similar_twice",
               "different_agents": "different_agents", "copied_between": "copy", "drifted_copy": "copy"}

_ASCII_LOWER = {i: i + 32 for i in range(65, 91)}


def norm_id(text: str) -> str:
    """Trim, fold runs of white space to one space, lowercase ASCII letters only."""
    return re.sub(r"\s+", " ", (text or "").strip()).translate(_ASCII_LOWER)


def text_hash(text: str) -> str:
    return hashlib.sha1(norm_id(text).encode()).hexdigest()[:12]


def _id(parts: list[str]) -> str:
    return hashlib.sha1("\0".join(parts).encode()).hexdigest()[:6]


def stack_key(st: Stack) -> str:
    return f"{st.folder}@{st.agent}"


def _under(path: str, root: Path | str) -> bool:
    r = str(root).rstrip("/")
    return path == r or path.startswith(r + "/")


def rules_hash() -> str:
    """sha1 of every pattern and threshold that decides what the scan finds.

    Two runs with the same hash count findings the same way. The token estimator is
    named separately (stack.ESTIMATOR): token totals compare when only the rules changed.
    """
    from . import classify, compact, conflict
    parts = {
        "classify": [classify.C1_PATTERNS, classify.C3_PATTERNS, classify.NEGATION, classify.C4_MODEL,
                     classify.C4_DATE, classify.C4_CAPS, classify.C4_KO_EMPH, classify.C4_HABIT],
        "compact": [compact.K, compact.DUP, compact.DRIFT, compact.MIN_LEN, compact.REPEAT],
        "conflict": [conflict.TICK_RE.pattern, conflict.CAPS_RE.pattern, conflict.TOOL_RE.pattern,
                     sorted(conflict.CAPS_STOP), conflict.KO_NEG_RE.pattern, conflict.EN_NEG_RE.pattern,
                     sorted(conflict.EN_SKIP), conflict.RARE_SHARE, conflict.RARE_MIN, conflict.PER_STACK],
        "triage": [SAFE_DELETE, LARGE_LAYER_TOKENS, LARGE_LAYER_SHARE, FILE_COPY_SHARE, TRIM_DELETE_TOKENS,
                   TRIM_CANDIDATES, GROUP_GAP, PER_FILE_GROUP, START_HERE, PER_RULE, RULES_REVISION],
    }
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def sections(text: str) -> list[dict]:
    """`##` sections: heading, first and last line (trailing blank lines cut), estimated tokens."""
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                start = j + 1
                break
    heads: list[tuple[int, int, str]] = []
    fence = False
    for i in range(start, len(lines)):
        s = lines[i].lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
            continue
        m = None if fence else re.match(r"^(#{1,6})\s+(.*)$", lines[i])
        if m and len(m.group(1)) <= 2:
            heads.append((i + 1, len(m.group(1)), m.group(2).strip()))
    out = []
    for j, (n, level, title) in enumerate(heads):
        if level != 2:
            continue
        end = heads[j + 1][0] - 1 if j + 1 < len(heads) else len(lines)
        while end > n and not lines[end - 1].strip():
            end -= 1
        out.append({"heading": title, "line_start": n, "line_end": end,
                    "tokens_est": estimate_tokens("\n".join(lines[n - 1:end]))})
    return out


def chain_runs(rows: list, ix, iy, gap: int = GROUP_GAP) -> list[list]:
    """Split the pairs of two files into runs of consecutive pairs.

    ix and iy give the unit index of a pair on each side. A pair joins the run whose last
    pair is at most `gap` units before it on side x and at most `gap` units away on side y;
    of several such runs it joins the closest. So a far-off match of one line does not
    break the run of its neighbours.
    """
    runs: list[list] = []
    for r in sorted(rows, key=lambda r: (ix(r), iy(r))):
        best = None
        for run in runs:
            dx, dy = ix(r) - ix(run[-1]), abs(iy(r) - iy(run[-1]))
            if 0 <= dx <= gap and dy <= gap and (best is None or (dx, dy) < best[0]):
                best = ((dx, dy), run)
        if best:
            best[1].append(r)
        else:
            runs.append([r])
    return runs


class _Triage:
    def __init__(self, roots, home, artifacts, stacks, units, budget, copies):
        self.roots, self.home, self.budget, self.copies = roots, home, budget, copies
        self.by_path = {str(a.path): a for a in artifacts}
        self.units = units
        self.unit_index = {p: {u.line: i for i, u in enumerate(us)} for p, us in units.items()}
        self.lines = {p: a.text.splitlines() for p, a in self.by_path.items() if p in units}
        self.stacks = stacks
        self.keys = [stack_key(st) for st in stacks]
        self.order = {k: i for i, k in enumerate(self.keys)}
        # A layer names a file by the path the loader uses (~/.claude/CLAUDE.md, a resolved @import).
        # collect() keeps one artifact per resolved file, under the first path it walked.
        # Every layer path is mapped to that artifact path, so a symlinked file keeps its stacks.
        self.canon = {str(a.path.resolve()): p for p, a in self.by_path.items()}
        self.loads: dict[str, set[str]] = defaultdict(set)
        self.layer_rank: dict[str, dict[str, int]] = {}
        self.own: dict[str, dict[str, str]] = {}
        self.checkout: dict[str, Path] = {}
        for st, k in zip(stacks, self.keys):
            self.checkout[k] = find_checkout(st.folder)[0] or st.folder
            self.layer_rank[k] = {}
            self.own[k] = {}
            for i, l in enumerate(x for x in st.layers if x.path):
                p = self.layer_path(l)
                self.loads[p].add(k)
                self.layer_rank[k].setdefault(p, i)
                if l.label == "memory":
                    self.own[k].setdefault(p, "memory")
                elif l.label in ("global", "codex_global"):
                    self.own[k].setdefault(p, "shared")
                else:
                    self.own[k].setdefault(p, "owned" if _under(p, self.checkout[k]) else "shared")
        self.file_own = {p: self.file_ownership(p) for p in self.by_path}
        self.items: dict[str, dict] = {}

    def layer_path(self, layer) -> str | None:
        if not layer.path:
            return None
        p = str(layer.path)
        try:
            return self.canon.get(str(layer.path.resolve()), p)
        except OSError:
            return p

    def file_ownership(self, p: str) -> str:
        """Whose file it is (files[].ownership, items[].ownership), in agreement with the stacks.

        A file that loads in a stack takes that stack's view: shared when any stack sees it as
        a global file or a parent outside its checkout. A file that loads nowhere takes the
        view of the deepest stack folder that holds it.
        """
        a = self.by_path[p]
        if a.kind == "memory_index":
            return "memory"
        if a.kind in ("global_claude", "global_codex"):
            return "shared"
        if a.kind in ON_DEMAND and (_under(p, self.home / ".claude") or _under(p, self.home / ".codex")):
            return "skills"
        views = {self.own[k][p] for k in self.loads.get(p, ())}
        if views:
            return "shared" if "shared" in views else ("memory" if views == {"memory"} else "owned")
        holders = [(len(str(st.folder)), k) for st, k in zip(self.stacks, self.keys) if _under(p, st.folder)]
        if holders:
            return "owned" if _under(p, self.checkout[max(holders)[1]]) else "shared"
        return "shared"

    # ------------------------------------------------------------------ helpers
    def kind(self, path: str) -> str:
        a = self.by_path.get(path)
        return a.kind if a else ""

    def sorted_keys(self, keys) -> list[str]:
        return sorted(keys, key=lambda k: self.order[k])

    def agents(self, keys) -> set[str]:
        return {k.rsplit("@", 1)[1] for k in keys}

    def locator(self, path: str) -> str:
        """The path part of an item id: the file path, as ~/<path> under home.

        Every file has its own locator, so two checkouts with the same folder name (or two
        sibling folders) never share an id. The id does not depend on the scan roots, so a
        folder scanned alone and inside its parent gives the same ids. It is the same on one
        machine only: a clone in another place gets other ids.
        """
        h = str(self.home).rstrip("/")
        return "~" + path[len(h):] if path.startswith(h + "/") else path

    def file_scope(self, path: str) -> str:
        if self.loads.get(path):
            return "session_start"
        k = self.kind(path)
        if k == "other_agent":
            return "other_agent"
        if k in ON_DEMAND:
            return "on_demand"
        return "elsewhere"

    def lines_of(self, path: str) -> list[str]:
        """Lines of a file. An @import target can be a layer without being a collected file."""
        if path not in self.lines:
            a = self.by_path.get(path)
            try:
                text = a.text if a else Path(path).read_text(errors="replace")
            except OSError:
                text = ""
            self.lines[path] = text.splitlines()
        return self.lines[path]

    def raw(self, path: str, first: int, last: int) -> str:
        return "\n".join(self.lines_of(path)[first - 1:last])

    def loc(self, path: str, u: Unit, match: str | None = None, role: str | None = None,
            side: str | None = None) -> dict:
        out = {"file": path, "line": u.line, "end_line": u.end_line, "hash": text_hash(u.text),
               "text": self.raw(path, u.line, u.end_line) or u.text}
        if side:
            out["side"] = side
        if role:
            out["role"] = role
        if match:
            out["match"] = match
            for k, line in enumerate(out["text"].splitlines()):
                i = line.lower().find(match.lower())
                if i >= 0:
                    out.update(match_line=u.line + k, col_start=i + 1, col_end=i + 1 + len(match))
                    break
        return out

    def base(self, rule: str, cause: int, verb: str | None, certainty: str, label: str, path: str) -> dict:
        return {
            "id": "", "rule": rule, "cause": cause, "verb": verb, "certainty": certainty, "label": label,
            "scope": self.file_scope(path), "relation": None, "ownership": self.file_own.get(path, "shared"),
            "stacks": self.sorted_keys(self.loads.get(path, ())), "locations": [], "match": None,
            "tokens_est": 0, "loaded_in_stacks": len(self.loads.get(path, ())), "group_size": 1,
            "score": None, "state": "open", "keep": None,
        }

    def put(self, it: dict, parts: list[str]) -> None:
        """Add an item. The same parts in the same files are the same rule again: its locations join."""
        key = "\0".join(parts + sorted({l["file"] for l in it["locations"]}))
        it["id"] = _id(parts)
        n = 1
        while it["id"] in self.items:
            old = self.items[it["id"]]
            if old["_key"] == key:
                old["locations"].extend(it["locations"])
                old["group_size"] += it["group_size"]
                old["stacks"] = self.sorted_keys(set(old["stacks"]) | set(it["stacks"]))
                return
            it["id"] = _id(parts + [str(n)])  # a 6-hex collision: rare, so the next id is good enough
            n += 1
        it["_key"] = key
        self.items[it["id"]] = it

    # ------------------------------------------------------------------ flags
    def flag_items(self, flags: list[Flag]) -> None:
        by_file: dict[str, dict[int, list[Flag]]] = defaultdict(lambda: defaultdict(list))
        for fl in flags:
            by_file[fl.unit.file][fl.unit.line].append(fl)
        for path, by_line in by_file.items():
            # One line, one item: the most severe flag leads, the others are side labels.
            tops = {ln: sorted(fls, key=lambda f: SEVERITY[FLAG_LABEL[f.kind][0]]) for ln, fls in by_line.items()}
            entries = sorted(tops.items())
            if self.kind(path) == "memory_index":
                # Dated notes are the normal form of memory: all flagged lines of one MEMORY.md are one item.
                self.flag_group(path, entries, "memory_lines")
                continue
            dated = [(ln, fls) for ln, fls in entries if fls[0].kind in ("dated", "model_name")]
            grouped = set()
            if len(dated) >= PER_FILE_GROUP:
                self.flag_group(path, dated, "dated_lines")
                grouped = {ln for ln, _ in dated}
            for ln, fls in entries:
                if ln not in grouped:
                    self.flag_item(path, fls)

    def flag_item(self, path: str, fls: list[Flag]) -> None:
        f, u = fls[0], fls[0].unit
        label, verb = FLAG_LABEL[f.kind]
        it = self.base(f.kind, f.cause, verb, "candidate" if f.candidate else "certain", label, path)
        it["locations"] = [self.loc(path, u, f.match)]
        it["match"] = f.match
        it["tokens_est"] = estimate_tokens(u.text)
        it["also"] = [FLAG_LABEL[g.kind][0] for g in fls[1:]]
        self.put(it, [f.kind, norm_id(u.text), self.locator(path)])

    def flag_group(self, path: str, entries: list, label: str) -> None:
        rules = Counter(fls[0].kind for _ln, fls in entries)
        rule = rules.most_common(1)[0][0]
        cause = next(fls[0].cause for _ln, fls in entries if fls[0].kind == rule)
        it = self.base(rule, cause, "CHECK", "candidate", label, path)
        it["locations"] = [self.loc(path, fls[0].unit, fls[0].match) for _ln, fls in entries]
        it["tokens_est"] = sum(estimate_tokens(fls[0].unit.text) for _ln, fls in entries)
        it["group_size"] = len(entries)
        it["group"] = {"kind": "memory" if label == "memory_lines" else "per_file", "rules": dict(rules)}
        # The id names the file, not the lines: it stays while lines join or leave the group.
        self.put(it, [label, self.locator(path)])

    # ------------------------------------------------------------------ pairs
    def pair_place(self, p: Pair) -> tuple[str, str | None, set[str]]:
        """(scope, relation, stacks) of a pair."""
        fa, fb = p.a.file, p.b.file
        la, lb = self.loads.get(fa, set()), self.loads.get(fb, set())
        if fa == fb:
            return self.file_scope(fa), "same_file", la
        if la & lb:
            return "session_start", "same_stack", la & lb
        kinds = (self.kind(fa), self.kind(fb))
        if "other_agent" in kinds:
            return "other_agent", None, la | lb
        if any(k in ON_DEMAND for k in kinds):
            return "on_demand", None, la | lb
        if la and lb:
            return "not_together", "sibling_copy" if self.agents(la) & self.agents(lb) else "cross_agent", la | lb
        return "elsewhere", None, la | lb

    def safe_keep(self, a: Unit, b: Unit) -> tuple[Unit, Unit] | None:
        """(keep, delete) when every stack that loads the delete side also loads the keep side.

        The broader side stays. When both load in the same stacks, the earlier layer stays.
        A shared file (a global file, a parent above the roots) is never the delete side:
        it loads in folders outside the scan too.
        """
        la, lb = self.loads.get(a.file, set()), self.loads.get(b.file, set())

        def deletable(u: Unit) -> bool:
            return self.file_own.get(u.file, "shared") not in ("shared", "skills")
        a_ok, b_ok = lb <= la and deletable(b), la <= lb and deletable(a)
        if a_ok and b_ok:
            k = self.sorted_keys(la & lb)[0]
            ra, rb = self.layer_rank[k][a.file], self.layer_rank[k][b.file]
            return (a, b) if (ra, a.line) <= (rb, b.line) else (b, a)
        if a_ok:
            return a, b
        if b_ok:
            return b, a
        return None

    def ordered(self, a: Unit, b: Unit) -> tuple[Unit, Unit]:
        """The user's own side first."""
        def k(u):
            return OWN_RANK.get(self.file_own.get(u.file, "shared"), 9), u.file, u.line
        return (a, b) if k(a) <= k(b) else (b, a)

    def pair_verdict(self, p: Pair, scope: str, relation: str | None):
        """(label, verb, certainty, first unit, second unit, roles).

        The first unit is the line to act on. Roles ("delete", "keep") mark DELETE pairs only.
        """
        a, b = p.a, p.b
        if p.kind == "repeat":
            keep, dele = (a, b) if a.line <= b.line else (b, a)
            if p.score >= SAFE_DELETE:
                return "repeated", "DELETE", "certain", dele, keep, ("delete", "keep")
            return "repeated_maybe", "CHECK", "candidate", dele, keep, None
        if scope == "session_start":
            if p.kind == "duplicate":
                kd = self.safe_keep(a, b)
                if kd is None:
                    x, y = self.ordered(a, b)
                    agents = self.agents(self.loads[a.file]) != self.agents(self.loads[b.file])
                    return ("different_agents" if agents else "similar_twice"), "CHECK", "candidate", x, y, None
                keep, dele = kd
                if p.score >= SAFE_DELETE:
                    return "loaded_twice", "DELETE", "certain", dele, keep, ("delete", "keep")
                return "similar_twice", "CHECK", "candidate", dele, keep, None
            x, y = self.ordered(a, b)
            if p.kind == "drift":
                return "conflict", "PICK ONE", "candidate", x, y, None
            # topic and protocol pairs share a term or a message, not the wording. Most of them agree
            # when read: they are judge candidates, not conflicts, until `walle judge` says otherwise.
            return "term_clash", "CHECK", "candidate", x, y, None
        x, y = self.ordered(a, b)
        if p.kind == "protocol":
            return "format_differs", "CHECK", "candidate", x, y, None
        if relation == "cross_agent":
            return "different_agents", "CHECK", "candidate", x, y, None
        if p.kind == "duplicate":
            return "copied_between", None, "info", x, y, None
        return "drifted_copy", "SYNC?", "candidate", x, y, None

    def pair_items(self, pairs: list[Pair]) -> None:
        placed = [(p, *self.pair_place(p)) for p in pairs]
        copies = self.file_copies([(p, s, r) for p, s, r, _st in placed])
        chains: dict[tuple, list] = defaultdict(list)
        for p, scope, relation, stacks in placed:
            files = frozenset([p.a.file, p.b.file])
            if p.kind == "duplicate" and relation == "same_stack" and files in copies:
                copies[files]["pairs"].append(p)
                continue
            label, verb, cert, x, y, roles = self.pair_verdict(p, scope, relation)
            row = {"p": p, "scope": scope, "relation": relation, "stacks": stacks, "label": label, "verb": verb,
                   "cert": cert, "x": x, "y": y, "roles": roles}
            cls = CHAIN_CLASS.get(label)
            if cls and p.kind in ("duplicate", "drift"):
                chains[(cls, scope, relation, x.file, y.file)].append(row)
            else:
                self.pair_item(row)
        for rows in chains.values():
            for chain in self.chain(rows):
                if len(chain) == 1:
                    self.pair_item(chain[0])
                else:
                    self.chain_item(chain)
        for files, c in copies.items():
            self.copy_note(c)

    def pair_item(self, r: dict) -> None:
        p, x, y = r["p"], r["x"], r["y"]
        roles = r["roles"] or (None, None)
        it = self.base(p.kind, CAUSE.get(p.kind, 4), r["verb"], r["cert"], r["label"], x.file)
        it.update(scope=r["scope"], relation=r["relation"], stacks=self.sorted_keys(r["stacks"]),
                  score=round(p.score, 2), tokens_est=estimate_tokens(x.text),
                  locations=[self.loc(x.file, x, role=roles[0], side="a"), self.loc(y.file, y, role=roles[1], side="b")])
        if p.why:
            it["why"] = p.why
        sides = sorted([(norm_id(x.text), self.locator(x.file)), (norm_id(y.text), self.locator(y.file))])
        self.put(it, [p.kind, sides[0][0], sides[1][0], sides[0][1], sides[1][1]])

    def chain(self, rows: list[dict]) -> list[list[dict]]:
        def idx(u: Unit) -> int:
            return self.unit_index.get(u.file, {}).get(u.line, -10 ** 6)
        return chain_runs(rows, lambda r: idx(r["x"]), lambda r: idx(r["y"]))

    def span(self, units: list[Unit], role: str | None, side: str) -> dict:
        """One location from the first to the last line of units. Only file_copy notes use it."""
        path = units[0].file
        first, last = min(u.line for u in units), max(u.end_line for u in units)
        joined = "\n".join(norm_id(u.text) for u in sorted({(u.line, u.text): u for u in units}.values(),
                                                              key=lambda u: u.line))
        out = {"file": path, "line": first, "end_line": last,
               "hash": hashlib.sha1(joined.encode()).hexdigest()[:12], "text": self.raw(path, first, last),
               "side": side}
        if role:
            out["role"] = role
        return out

    def chain_item(self, chain: list[dict]) -> None:
        head = chain[0]
        drifted = any(r["p"].kind == "drift" for r in chain)
        label, verb, cert = head["label"], head["verb"], head["cert"]
        if CHAIN_CLASS[label] == "copy":
            label, verb, cert = ("drifted_copy", "SYNC?", "candidate") if drifted else ("copied_between", None, "info")
        rule = "drift" if drifted else "duplicate"
        # One location for each member line, side a first: a DELETE names only the copied lines,
        # never a line between them.
        xs = sorted({r["x"].line: r["x"] for r in chain}.values(), key=lambda u: u.line)
        ys = sorted({r["y"].line: r["y"] for r in chain}.values(), key=lambda u: u.line)
        roles = head["roles"] or (None, None)
        it = self.base(rule, CAUSE[rule], verb, cert, label, head["x"].file)
        stacks = set().union(*(r["stacks"] for r in chain))
        it.update(scope=head["scope"], relation=head["relation"], stacks=self.sorted_keys(stacks),
                  score=round(min(r["p"].score for r in chain), 2), group_size=len(chain),
                  tokens_est=sum(estimate_tokens(u.text) for u in xs),
                  locations=[self.loc(u.file, u, role=roles[0], side="a") for u in xs]
                  + [self.loc(u.file, u, role=roles[1], side="b") for u in ys])
        it["group"] = {
            "kind": "consecutive",
            "loose": any(r["p"].score < DUP for r in chain),
            "members": [{"kind": r["p"].kind, "score": round(r["p"].score, 2),
                         "a": {"file": r["x"].file, "line": r["x"].line, "end_line": r["x"].end_line},
                         "b": {"file": r["y"].file, "line": r["y"].line, "end_line": r["y"].end_line}}
                        for r in chain],
        }
        x0 = head["x"]
        self.put(it, [rule, "chain", norm_id(x0.text), self.locator(x0.file), self.locator(head["y"].file)])

    def file_copies(self, placed) -> dict[frozenset, dict]:
        """File pairs of one stack where one file holds FILE_COPY_SHARE or more of the other's rules."""
        covered: dict[frozenset, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
        for p, _scope, relation in placed:
            if p.kind == "duplicate" and relation == "same_stack":
                c = covered[frozenset([p.a.file, p.b.file])]
                c[p.a.file].add(p.a.line)
                c[p.b.file].add(p.b.line)
        out = {}
        for files, c in covered.items():
            share = {}
            for f in files:
                rules = sum(1 for u in self.units.get(f, []) if len(u.norm) >= MIN_LEN and u.kind != "heading")
                share[f] = len(c[f]) / rules if rules else 0.0
            fa, fb = sorted(files)
            glob = [f for f in files if self.kind(f) in ("global_claude", "global_codex")]
            if len(glob) == 1:
                # The other file is the source of the global file. When only the global file is
                # covered, the other file holds it plus rules of its own: the copied lines are DELETE items.
                src = glob[0]
                copy = fb if src == fa else fa
                label = "source_of"
            else:
                k = self.sorted_keys(self.loads[fa] & self.loads[fb])[0]
                # The copy holds the larger share of the other's rules; on a tie, the later layer.
                copy = max(files, key=lambda f: (share[f], self.layer_rank[k][f]))
                src = fb if copy == fa else fa
                label = "copy_of"
            if share[copy] < FILE_COPY_SHARE:
                continue
            out[files] = {"copy": copy, "source": src, "label": label, "share": share[copy], "pairs": []}
        return out

    def copy_note(self, c: dict) -> None:
        copy, src, ps = c["copy"], c["source"], c["pairs"]
        mine = [p.a if p.a.file == copy else p.b for p in ps]
        theirs = [p.b if p.a.file == copy else p.a for p in ps]
        stacks = self.loads[copy] & self.loads[src]
        it = self.base("duplicate", 4, None, "info", c["label"], copy)
        it.update(scope="session_start", relation="file_copy", stacks=self.sorted_keys(stacks),
                  group_size=len(ps), score=round(min(p.score for p in ps), 2),
                  tokens_est=sum(estimate_tokens(u.text) for u in {u.line: u for u in mine}.values()))
        it["of"] = src
        locs = []
        for units, role, side in ((mine, "copy", "a"), (theirs, "source", "b")):
            s = self.span(units, role, side)
            s["text"] = None  # a note names the files; the copied lines are in the files themselves
            locs.append(s)
        it["locations"] = locs
        it["group"] = {"kind": "file_copy", "pairs": len(ps), "share": round(c["share"], 2)}
        self.put(it, ["file_copy", self.locator(copy), self.locator(src)])

    # ------------------------------------------------------------------ large layers
    def large_layers(self, file_sections: dict[str, list[dict]]) -> None:
        rows: dict[str, list] = defaultdict(list)
        for st, k in zip(self.stacks, self.keys):
            total = st.tokens
            for l in st.layers:
                p = self.layer_path(l)
                if (p and self.own[k].get(p) == "owned" and l.tokens > LARGE_LAYER_TOKENS
                        and l.tokens > LARGE_LAYER_SHARE * total):
                    rows[p].append((k, l.tokens, total))
        for p, rs in rows.items():
            secs = file_sections.get(p) or []
            if p not in file_sections:
                secs = sections("\n".join(self.lines_of(p)))
            best = max(secs, key=lambda s: s["tokens_est"]) if secs else None
            first, last = (best["line_start"], best["line_end"]) if best else (1, max(1, len(self.lines_of(p))))
            text = self.raw(p, first, last)
            it = self.base("large_layer", 1, "MOVE", "candidate", "large_layer", p)
            it.update(stacks=self.sorted_keys(k for k, _t, _s in rs), tokens_est=rs[0][1], ownership="owned",
                      locations=[{"file": p, "line": first, "end_line": last, "hash": text_hash(text), "text": text}])
            it["shares"] = {k: round(100 * t / s) for k, t, s in rs}
            it["move"] = best
            self.put(it, ["large_layer", self.locator(p)])

    # ------------------------------------------------------------------ verdicts
    def files_of(self, it: dict) -> list[str]:
        return [l["file"] for l in it["locations"]]

    def yours(self, it: dict, k: str) -> str | None:
        """The ownership in stack k that makes the item the user's, or None."""
        own = self.own[k]
        files = self.files_of(it)
        if it["verb"] == "DELETE":
            files = files[:1]  # the line to delete must be the user's
        for f in files:
            if own.get(f) in YOURS:
                return own[f]
        return None

    @staticmethod
    def is_open(it: dict) -> bool:
        return it["scope"] == "session_start" and it["state"] == "open" and it["certainty"] != "info"

    def actionable(self, it: dict) -> bool:
        """An item that can be in Start here."""
        return self.is_open(it) and it["label"] not in NOT_IN_POOL

    def counts(self, here: list[dict], k: str) -> dict:
        """The numbers behind the verdict of stack k (design brief 2.2).

        DELETE tokens and conflicts count in the whole stack, shared files too. Candidates
        count in your files only, mirrors (different_agents) included. Term clashes count
        nowhere until a judge reads them.
        """
        mine = [it for it in here if self.is_open(it) and self.yours(it, k) and it["label"] != "term_clash"]
        return {
            "delete_tokens": sum(it["tokens_est"] for it in here if self.is_open(it) and it["verb"] == "DELETE"),
            "conflicts": sum(1 for it in here if self.is_open(it) and it["label"] == "conflict"),
            "candidates": sum(1 for it in mine if it["certainty"] == "candidate"),
            "to_read": len(mine),
        }

    def rank(self, verdict: str, own_of):
        def key(it):
            sev = SEVERITY.get(it["label"], 9)
            if verdict == OVER and it["label"] == "large_layer":
                sev = 0
            first = it["locations"][0]
            return (own_of(it) != "owned", sev, -(it["tokens_est"] * it["loaded_in_stacks"]),
                    first["line"] or 0, first["file"])
        return key

    @staticmethod
    def pick(pool: list[dict]) -> list[dict]:
        rules = {it["rule"] for it in pool}
        out, per = [], Counter()
        for it in pool:
            if len(rules) > 1 and per[it["rule"]] >= PER_RULE:
                continue
            out.append(it)
            per[it["rule"]] += 1
            if len(out) == START_HERE:
                break
        return out

    def visible(self, path: str, st: Stack) -> bool:
        """An on-demand or other-agent file is visible to a session in st.folder."""
        folder = str(st.folder)
        if self.kind(path) in ON_DEMAND:
            for agent, base in (("claude", self.home / ".claude"), ("codex", self.home / ".codex")):
                if _under(path, base):
                    return agent == st.agent
            for agent, marker in (("claude", "/.claude/"), ("codex", "/.codex/")):
                if marker in path:
                    return agent == st.agent and _under(folder, path.split(marker)[0])
        a = self.by_path.get(path)
        root = (a.checkout if a and a.checkout else Path(path).parent)
        return _under(folder, root) or _under(path, self.checkout[stack_key(st)])

    def stack_entry(self, st: Stack, k: str) -> dict:
        own = self.own[k]
        items = list(self.items.values())
        here = [it for it in items if k in it["stacks"] and it["scope"] == "session_start"]
        pool = [it for it in here if self.actionable(it) and self.yours(it, k)]
        checks = self.counts(here, k)
        reasons = []
        if checks["delete_tokens"] >= TRIM_DELETE_TOKENS:
            reasons.append("delete_tokens")
        if checks["conflicts"]:
            reasons.append("conflict")
        if checks["candidates"] >= TRIM_CANDIDATES:
            reasons.append("candidates")
        verdict = OVER if st.tokens > self.budget else (TRIM if reasons else LEAN)
        key = self.rank(verdict, lambda it: self.yours(it, k))
        pool.sort(key=key)
        start = self.pick(pool)
        start_ids = {it["id"] for it in start}

        notes = [it for it in here if it["relation"] == "file_copy" and self.yours(it, k)]
        mirrors = [it for it in here if it["label"] == "different_agents" and self.yours(it, k)]
        clashes = [it for it in here if it["label"] == "term_clash" and self.yours(it, k)]
        rest = (notes + [it for it in pool if it["id"] not in start_ids]
                + sorted(mirrors, key=key) + sorted(clashes, key=key))

        shared_rows = []
        for l in st.layers:
            p = self.layer_path(l)
            if not p or own.get(p) != "shared" or any(r["file"] == p for r in shared_rows):
                continue
            ids = [it["id"] for it in here
                   if not self.yours(it, k) and next((f for f in self.files_of(it) if own.get(f) == "shared"), None) == p]
            loads = len(self.loads[p])
            shared_rows.append({"file": p, "items": ids, "loads_in": loads, "tokens_est": l.tokens,
                                "reach": l.tokens * loads})

        copy_rows: dict[tuple[str, str], dict] = {}
        for it in items:
            if it["scope"] != "not_together":
                continue
            files = self.files_of(it)
            mine = [f for f in files if own.get(f) in YOURS]
            if not mine:
                continue
            other = next((f for f in files if f != mine[0]), mine[0])
            row = copy_rows.setdefault((mine[0], other), {"file": mine[0], "other": other, "items": [],
                                                          "counts": Counter(), "parent": None})
            row["items"].append(it["id"])
            row["counts"][it["label"]] += 1
        for (mine, other), row in copy_rows.items():
            row["counts"] = dict(row["counts"])
            need = self.loads.get(mine, set()) | self.loads.get(other, set())
            parents = [p for p, ks in self.loads.items() if p not in (mine, other)
                       and self.kind(p) in ("claude_md", "agents_md") and need <= ks]
            row["parent"] = max(parents, key=len) if parents else None

        on_demand = [it["id"] for it in items if it["scope"] == "on_demand"
                     and any(self.visible(f, st) for f in self.files_of(it) if self.kind(f) in ON_DEMAND)]
        other_agents = [it["id"] for it in items if it["scope"] == "other_agent"
                        and any(self.visible(f, st) for f in self.files_of(it) if self.kind(f) == "other_agent")]
        repo = find_checkout(st.folder)[1]
        worktrees = [{"file": c["file"], "versions": c["variants"], "copies": c["copies"], "main_sha": c["main_sha"]}
                     for c in self.copies
                     if repo and c["repo"] == str(repo) and c["variants"] >= 2
                     and (str(repo / c["file"]) in own or _under(str(repo / c["file"]), st.folder))]

        split = Counter()
        for l in st.layers:
            p = self.layer_path(l)
            o = "skills" if l.label == "skill_catalog" else ("your_files" if own.get(p) in YOURS else "shared")
            split[o] += l.tokens
        groups = {
            "everything_else": [it["id"] for it in rest],
            "shared": shared_rows,
            "copies": sorted(copy_rows.values(), key=lambda r: (-len(r["items"]), r["file"], r["other"])),
            "on_demand": on_demand,
            "other_agents": other_agents,
            "worktree_copies": worktrees,
            "kept": [],
        }
        counts = {"everything_else": len(groups["everything_else"]),
                  "shared": sum(len(r["items"]) for r in shared_rows),
                  "copies": sum(len(r["items"]) for r in groups["copies"]),
                  "on_demand": len(on_demand), "other_agents": len(other_agents),
                  "worktree_copies": len(worktrees), "kept": 0}
        return {
            "folder": str(st.folder), "agent": st.agent, "verdict": verdict, "tokens_est": st.tokens,
            "budget": self.budget, "split": {x: split.get(x, 0) for x in ("your_files", "shared", "skills")},
            "to_read": checks.pop("to_read"),
            "reasons": reasons if verdict == TRIM else ([] if verdict == LEAN else ["over_budget"]),
            "checks": checks,
            "start_here": [it["id"] for it in start], "groups": groups, "counts": counts,
        }

    def by_reach(self, entries: dict[str, dict]) -> list[str]:
        """Start here for a scan of several folders: shared files count, ranked by reach."""
        over = {k for k, e in entries.items() if e["verdict"] == OVER}
        pool = [it for it in self.items.values() if self.actionable(it)]

        def key(it):
            first = it["locations"][0]
            if it["label"] == "large_layer" and over & set(it["stacks"]):
                return (0, 0, 0, -it["tokens_est"], first["file"], first["line"])
            reach = it["tokens_est"] * len(it["stacks"])
            return (1, it["ownership"] == "memory", SEVERITY.get(it["label"], 9), -reach, first["file"], first["line"])
        return [it["id"] for it in self.pick(sorted(pool, key=key))]

    def run(self, flags: list[Flag], pairs: list[Pair], file_sections: dict[str, list[dict]]) -> dict:
        self.flag_items(flags)
        self.pair_items(pairs)
        self.large_layers(file_sections)
        entries = {k: self.stack_entry(st, k) for st, k in zip(self.stacks, self.keys)}
        folders = {str(st.folder) for st in self.stacks}
        scope_rank = {"session_start": 0, "on_demand": 1, "not_together": 2, "other_agent": 3, "elsewhere": 4}
        items = sorted(self.items.values(), key=lambda it: (scope_rank[it["scope"]], it["locations"][0]["file"],
                                                             it["locations"][0]["line"] or 0, it["id"]))
        for it in items:
            it.pop("_key", None)
        return {
            "items": items,
            "triage": {
                "budget": self.budget,
                "verdicts": {v: sum(1 for e in entries.values() if e["verdict"] == v) for v in (OVER, TRIM, LEAN)},
                "stacks": entries,
                "start_here_by_reach": self.by_reach(entries) if len(folders) > 1 else [],
                "hidden": {"elsewhere": sum(1 for it in items if it["scope"] == "elsewhere")},
            },
            "ownership": dict(self.file_own),
        }


def triage(roots: list[Path], home: Path, artifacts: list[Artifact], stacks: list[Stack],
           units: dict[str, list[Unit]], flags: list[Flag], pairs: list[Pair], copies: list[dict],
           file_sections: dict[str, list[dict]], budget: int = BUDGET) -> dict:
    """Items, the triage block of scan.json schema 2, and the ownership of each collected file."""
    return _Triage(roots, home, artifacts, stacks, units, budget, copies).run(flags, pairs, file_sections)
