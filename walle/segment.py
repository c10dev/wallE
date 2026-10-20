"""Split a Markdown instruction file into rule units.

A unit is one bullet item (with its continuation lines), one numbered item,
one table row, or one paragraph. Units, not sentences, are the base for counts,
because Korean sentence splitting is not reliable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BULLET_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}")


@dataclass
class Unit:
    file: str
    line: int  # 1-based line of the first line
    heading: str
    text: str
    kind: str = "text"  # text | heading | code

    @property
    def norm(self) -> str:
        return normalize(self.text)


def normalize(text: str) -> str:
    t = text.lower()
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    return re.sub(r"[\W_]+", "", t)


def segment(text: str, file: str = "") -> list[Unit]:
    units: list[Unit] = []
    lines = text.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                i = j + 1
                break
    heading = ""
    in_fence = False
    fence: list[str] = []
    fence_line = 0
    cur: list[str] = []
    cur_line = 0
    cur_indent = 0

    def flush():
        nonlocal cur
        body = " ".join(s.strip() for s in cur).strip()
        if body and len(normalize(body)) >= 4:
            units.append(Unit(file, cur_line, heading, body))
        cur = []

    for idx in range(i, len(lines)):
        line = lines[idx]
        n = idx + 1
        if line.lstrip().startswith("```") or line.lstrip().startswith("~~~"):
            flush()
            if in_fence and len(normalize(" ".join(fence))) >= 4:
                # A fenced block is one unit: templates copied between files are duplicates too.
                units.append(Unit(file, fence_line, heading, "\n".join(fence), "code"))
            in_fence = not in_fence
            fence, fence_line = [], n + 1
            continue
        if in_fence:
            fence.append(line)
            continue
        if not line.strip() or line.strip().startswith("<!--"):
            flush()
            continue
        h = HEADING_RE.match(line)
        if h:
            flush()
            heading = h.group(2).strip()
            units.append(Unit(file, n, heading, heading, "heading"))
            continue
        if line.lstrip().startswith("|"):
            flush()
            if not TABLE_SEP_RE.match(line):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                body = " | ".join(c for c in cells if c)
                if len(normalize(body)) >= 4:
                    units.append(Unit(file, n, heading, body))
            continue
        b = BULLET_RE.match(line)
        if b:
            flush()
            cur = [b.group(2)]
            cur_line = n
            cur_indent = len(b.group(1))
            continue
        if cur and (len(line) - len(line.lstrip()) > cur_indent or not BULLET_RE.match(line)):
            cur.append(line)
            continue
        flush()
        cur = [line]
        cur_line = n
        cur_indent = 0
    flush()
    return units
