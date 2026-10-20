"""Static flags for the four causes of instruction-driven degradation.

The rules only surface candidates. Cause 1 (task-unrelated cost) and cause 2
(conflict) need judgment, so their flags say "candidate". Causes 3 and 4 are
lexical and the flags are direct.

1 cost: a directive that adds steps whatever the task is.
2 conflict: rules that disagree, inside one file or across loaded layers (see compact.py).
3 vague: a quality instruction without a concrete pattern.
4 legacy: compensation for an older model: model names, dates, emphasis stacks, repeats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .segment import Unit

C1_PATTERNS = [
    r"항상|매번|모든\s*(?:작업|PR|세션|응답|변경|커밋)",
    r"먼저\s*읽|읽고\s*시작|시작\s*전에?\s*읽|반드시\s*읽",
    r"(?:반드시|항상|매번)\s*\S*\s*(?:실행|돌리|확인|검증|테스트)",
    r"\b(?:always|every time|before (?:every|any|each))\b",
    r"\b(?:must|always)\s+(?:run|read|check|verify|test)\b",
    r"\b(?:for |on )?every (?:task|change|pr|session|turn|commit)\b",
    r"모두\s*통과해야|\bmust (?:all )?pass\b",
    r"\bread\b.{0,40}\bbefore\b.{0,20}\b(?:start|work|any|edit)",
]
C3_PATTERNS = [
    r"자연스럽|깔끔하게|깔끔한\s*(?:디자인|화면|UI|코드)|세련|고품질|높은\s*품질|완성도\s*(?:있|높)|AI\s*스럽|AI\s*같|AI\s*느낌|신중(?:히|하게)|꼼꼼(?:히|하게)|잘\s*(?:만들|해|써)",
    r"\bthink (?:carefully|hard|deeply)\b|\bstep[- ]by[- ]step\b|\bbest practices?\b",
    r"\b(?:beautiful|polished|premium|elegant|stunning|sleek|high[- ]quality)\b",
    r"\b(?:clean|modern)\s+(?:code|design|look|feel|ui|layout|aesthetic|style)\b",
]
# A vague word inside a prohibition ("don't recommend best practices") is not a vague instruction.
NEGATION = r"\b(?:don'?t|do not|never|avoid|not)\b|금지|하지\s*않|말\s*것|쓰지\s*않"
C4_MODEL = r"\b(?:gpt-?\d[\w.]*|claude[- ]?\d[\w.]*|opus|sonnet|haiku|fable|gemini|o\d-mini|codex-?\w+)\b"
C4_DATE = r"\b20\d\d-\d\d(?:-\d\d)?\b"
C4_CAPS = r"\b(?:NEVER|ALWAYS|MUST|IMPORTANT|CRITICAL|DO NOT|MANDATORY)\b"
C4_KO_EMPH = r"절대|반드시|무조건|필수"
C4_HABIT = r"모델이\s*(?:자주|종종|가끔)|tends? to|often forgets?|자주\s*(?:빠뜨|잊|틀)"


@dataclass
class Flag:
    cause: int
    kind: str
    unit: Unit
    match: str
    candidate: bool

    def to_dict(self) -> dict:
        return {
            "cause": self.cause,
            "kind": self.kind,
            "file": self.unit.file,
            "line": self.unit.line,
            "heading": self.unit.heading,
            "match": self.match,
            "candidate": self.candidate,
            "text": self.unit.text[:240],
        }


def _first(patterns: list[str] | str, text: str, flags=re.I) -> str | None:
    for p in [patterns] if isinstance(patterns, str) else patterns:
        m = re.search(p, text, flags)
        if m:
            return m.group(0)
    return None


def _negated(text: str, match: str) -> bool:
    i = text.lower().find(match.lower())
    window = text[max(0, i - 80) : i + len(match) + 20]
    return bool(re.search(NEGATION, window, re.I))


def classify_unit(u: Unit) -> list[Flag]:
    out: list[Flag] = []
    if u.kind == "code":
        return out
    t = u.text
    if m := _first(C1_PATTERNS, t):
        out.append(Flag(1, "always_step", u, m, True))
    if (m := _first(C3_PATTERNS, t)) and not _negated(t, m):
        out.append(Flag(3, "vague_quality", u, m, False))
    if m := _first(C4_MODEL, t):
        out.append(Flag(4, "model_name", u, m, True))
    if m := _first(C4_DATE, t):
        out.append(Flag(4, "dated", u, m, True))
    caps = re.findall(C4_CAPS, t)
    ko = re.findall(C4_KO_EMPH, t)
    if len(caps) + len(ko) >= 2:
        out.append(Flag(4, "emphasis_stack", u, " ".join(caps + ko)[:60], False))
    if m := _first(C4_HABIT, t):
        out.append(Flag(4, "model_habit", u, m, True))
    return out


def emphasis_count(text: str) -> int:
    return len(re.findall(C4_CAPS, text)) + len(re.findall(C4_KO_EMPH, text))
