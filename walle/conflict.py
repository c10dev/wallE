"""Conflict candidates that do not look alike.

Drift pairs (compact.py) catch a rule that someone copied and then changed.
Two rules can also disagree with few characters in common. Two sources find them:

- topic: two units of one load stack, or of one file, share a key term (a
  backticked token, an ALL-CAPS word, a tool name such as SendMessage). One says
  "do not" to a verb that the other uses without "do not", or the two write one
  protocol message in different forms.
- protocol: units of two repositories write one protocol message
  (`READBACK D-xxxx`) in different forms. scan compares other rules only
  inside one repository.

Both are candidates. `walle judge` reads them as conflicts.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations

from .compact import MIN_LEN, Pair
from .segment import Unit

TICK_RE = re.compile(r"`([^`\n]+)`")
# ASCII lookarounds, so "DUTY가" and "OCC와" still match.
CAPS_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Z][A-Z0-9_]{2,}(?![A-Za-z0-9_-])")
TOOL_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+(?![A-Za-z0-9_])")
# Emphasis and generic acronyms are not a topic.
CAPS_STOP = {
    "NEVER", "ALWAYS", "MUST", "IMPORTANT", "CRITICAL", "MANDATORY", "NOT", "NOTE", "TODO", "YES",
    "THE", "AND", "FOR", "API", "URL", "JSON", "YAML", "HTML", "CSS", "CLI", "SDK", "HTTP", "HTTPS",
    "SSH", "SQL", "README", "CLAUDE", "AGENTS", "SKILL", "MEMORY",
}
# Korean: the stem before "-지 않/말/마/못". One-syllable stems (쓰, 읽, 두) match too many words.
KO_NEG_RE = re.compile(r"(?<![가-힣])([가-힣]{2,}?)지\s*(?:않|말|마|못)")
EN_NEG_RE = re.compile(r"\b(?:do not|don'?t|never|must not|mustn'?t|cannot|can'?t|should not|shouldn'?t|no longer)"
                       r"\s+([a-z]{3,})", re.I)
EN_SKIP = {"the", "all", "any", "only", "just", "also", "yet", "even", "more", "have", "been", "use", "get"}
WORD_RE = re.compile(r"[가-힣]+|[A-Za-z]+")
LEAD_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
PLACEHOLDER_RE = re.compile(r"<[^<>]*>")

RARE_SHARE = 0.2  # a term in more than this share of a stack's units pairs nothing
RARE_MIN = 3  # ...unless it is in this many units or fewer
PER_STACK = 40  # topic pairs kept per stack, highest score first


def _fold(s: str) -> str:
    """Lowercase and split Hangul syllables into jamo, so the stem 보내 is a prefix of 보낸다."""
    return unicodedata.normalize("NFD", s.lower())


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def key_terms(text: str) -> set[str]:
    out = set()
    for span in TICK_RE.findall(text):
        s = span.strip()
        if CAPS_RE.fullmatch(s) or TOOL_RE.fullmatch(s):
            out.add(s)  # `SendMessage` and SendMessage are one term
        elif len(s) >= 3:
            out.add(f"`{s}`")
    bare = TICK_RE.sub(" ", text)
    out.update(m for m in CAPS_RE.findall(bare) if m not in CAPS_STOP)
    out.update(TOOL_RE.findall(bare))
    return out


def negated(text: str) -> set[str]:
    """Folded stems of the verbs a unit says "do not" to."""
    out = {_fold(m) for m in KO_NEG_RE.findall(text)}
    for m in EN_NEG_RE.findall(text):
        w = m.lower()
        if w not in EN_SKIP:
            out.add(_fold(w[:-1] if w.endswith("e") and len(w) > 3 else w))
    return out


def message_forms(text: str) -> dict[str, dict[str, str]]:
    """Backticked protocol messages by their first two words: {message: {form: as written}}.

    `READBACK D-xxxx @xxxxxx` and `READBACK D-xxxx` are two forms of "READBACK D-xxxx".
    A placeholder name does not count: `<why>` and `<reason>` are the same form.
    """
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for span in TICK_RE.findall(text):
        toks = PLACEHOLDER_RE.sub("<>", span.replace("[", " ").replace("]", " ")).split()
        if len(toks) >= 2 and LEAD_RE.match(toks[0]):
            out[" ".join(toks[:2])].setdefault(" ".join(toks), span.strip())
    return out


def _covers(f: str, g: str) -> bool:
    """f stands for g: the same form, or f ends with "…" and g starts with the rest of f."""
    if f == g:
        return True
    for tail in ("…", "..."):
        if f.endswith(tail) and g.startswith(f[: -len(tail)].rstrip()):
            return True
    return False


@dataclass
class Info:
    unit: Unit
    terms: set[str]
    neg: set[str]
    words: list[str]
    forms: dict[str, dict[str, str]]


def info(u: Unit) -> Info:
    forms = message_forms(u.text)
    return Info(u, key_terms(u.text) | set(forms), negated(u.text),
                [_fold(w) for w in WORD_RE.findall(u.text)], forms)


def opposite(a: Info, b: Info) -> list[str]:
    """Stems one side says "do not" to and the other side uses without "do not"."""
    out = set()
    for x, y in ((a, b), (b, a)):
        for s in x.neg:
            if s not in y.neg and any(w.startswith(s) for w in y.words):
                out.add(_nfc(s))
    return sorted(out)


def variants(a: Info, b: Info) -> list[dict]:
    """Protocol messages that both sides write, where each side has a form the other lacks."""
    out = []
    for k in sorted(set(a.forms) & set(b.forms)):
        fa, fb = a.forms[k], b.forms[k]
        only_a = sorted(fa[f] for f in fa if not any(_covers(f, g) or _covers(g, f) for g in fb))
        only_b = sorted(fb[f] for f in fb if not any(_covers(f, g) or _covers(g, f) for g in fa))
        if only_a and only_b:
            out.append({"message": k, "a": only_a, "b": only_b})
    return out


def _infos(units: list[Unit]) -> list[Info]:
    return [info(u) for u in units if u.kind == "text" and len(u.norm) >= MIN_LEN]


def _key(a: Unit, b: Unit) -> tuple:
    return tuple(sorted([(a.file, a.line), (b.file, b.line)]))


def _ordered(a: Info, b: Info) -> tuple[Info, Info]:
    return (a, b) if (a.unit.file, a.unit.line) <= (b.unit.file, b.unit.line) else (b, a)


def topic_pairs(stacks: list[tuple[str, list[Unit]]], skip: set) -> list[Pair]:
    """Pairs inside one load stack that share a key term and disagree in sign or form.

    stacks: (label, units) for each load stack. skip: pair keys already found
    (drift pairs). The score adds 1/df for each shared term, so rare shared
    terms rank first; each stack keeps its PER_STACK best pairs.
    """
    seen = set(skip)
    out: list[Pair] = []
    for label, units in stacks:
        infos = _infos(list({(u.file, u.line): u for u in units}.values()))
        df = Counter(t for i in infos for t in i.terms)
        cap = max(RARE_MIN, int(RARE_SHARE * len(infos)))
        index: dict[str, list[int]] = defaultdict(list)
        for n, i in enumerate(infos):
            for t in i.terms:
                if df[t] <= cap:
                    index[t].append(n)
        shared: dict[tuple[int, int], set[str]] = defaultdict(set)
        for t, ns in index.items():
            for x, y in combinations(ns, 2):
                shared[(x, y)].add(t)
        found = []
        for (x, y), terms in shared.items():
            a, b = _ordered(infos[x], infos[y])
            k = _key(a.unit, b.unit)
            if k in seen:
                continue
            opp, var = opposite(a, b), variants(a, b)
            if not opp and not var:
                continue
            score = sum(1 / df[t] for t in terms)
            why = {"shared": sorted(terms, key=lambda t: (df[t], t))[:6], "stack": label}
            if opp:
                why["opposite"] = opp
            if var:
                why["variants"] = var
            found.append((score, k, Pair("topic", a.unit, b.unit, score, why)))
        found.sort(key=lambda f: (-f[0], f[1]))
        for _score, k, p in found[:PER_STACK]:
            seen.add(k)
            out.append(p)
    return out


def protocol_pairs(scopes: dict[str, list[Unit]], skip: set) -> list[Pair]:
    """Pairs from two repositories that write one protocol message in different forms."""
    by_msg: dict[str, list[tuple[str, Info]]] = defaultdict(list)
    for scope, units in scopes.items():
        for i in _infos(units):
            for k in i.forms:
                by_msg[k].append((scope, i))
    found: dict[tuple, Pair] = {}
    for _k, rows in sorted(by_msg.items()):
        for (sa, a), (sb, b) in combinations(rows, 2):
            if sa == sb:
                continue
            a, b = _ordered(a, b)
            k = _key(a.unit, b.unit)
            if k in skip or k in found:
                continue
            var = variants(a, b)
            if var:
                found[k] = Pair("protocol", a.unit, b.unit, float(len(var)),
                                {"shared": [v["message"] for v in var], "variants": var})
    return sorted(found.values(), key=lambda p: (-p.score, p.a.file, p.a.line, p.b.file, p.b.line))
