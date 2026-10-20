"""Compact: find copies of the same rule, and copies that drifted apart.

Two levels:
- File copies: the same instruction file across worktrees of one repository.
- Rule copies: near-duplicate units inside one file or across files that load together.
Similarity is Jaccard on character 5-grams of normalized text (works for Korean too).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .collect import Artifact
from .segment import Unit

K = 5
DUP = 0.85  # at or above: the same rule written twice
DRIFT = 0.5  # between DRIFT and DUP: the same rule that changed (conflict candidate)
MIN_LEN = 20  # ignore very short units (headings in tables, labels)
REPEAT = 0.6  # at or above, inside one file: the same rule written again


def shingles(norm: str) -> frozenset[str]:
    if len(norm) <= K:
        return frozenset([norm])
    return frozenset(norm[i : i + K] for i in range(len(norm) - K + 1))


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Pair:
    kind: str  # duplicate | drift | repeat | topic | protocol (see conflict.py)
    a: Unit
    b: Unit
    score: float
    why: dict = field(default_factory=dict)  # topic and protocol: shared terms, opposite verbs, message forms

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "score": round(self.score, 2),
            "a": {"file": self.a.file, "line": self.a.line, "text": self.a.text[:200]},
            "b": {"file": self.b.file, "line": self.b.line, "text": self.b.text[:200]},
            **self.why,
        }


def rule_pairs(units: list[Unit]) -> list[Pair]:
    """Compare every unit with every other unit that shares at least one shingle."""
    items = [(u, shingles(u.norm)) for u in units if len(u.norm) >= MIN_LEN and u.kind != "heading"]
    index: dict[str, list[int]] = defaultdict(list)
    for i, (_u, sh) in enumerate(items):
        for s in sh:
            index[s].append(i)
    pairs: list[Pair] = []
    for i, (u, sh) in enumerate(items):
        overlap: dict[int, int] = defaultdict(int)
        for s in sh:
            for j in index[s]:
                if j > i:
                    overlap[j] += 1
        for j, inter in overlap.items():
            v, shv = items[j]
            score = inter / (len(sh) + len(shv) - inter)
            if score < DRIFT:
                continue
            if u.file == v.file:
                if score >= REPEAT:
                    pairs.append(Pair("repeat", u, v, score))
            elif score >= DUP:
                pairs.append(Pair("duplicate", u, v, score))
            else:
                pairs.append(Pair("drift", u, v, score))
    return pairs


@dataclass
class CopyGroup:
    repo: str
    rel: str
    copies: list[Artifact]

    @property
    def variants(self) -> dict[str, list[Artifact]]:
        out: dict[str, list[Artifact]] = defaultdict(list)
        for a in self.copies:
            out[a.sha].append(a)
        return out

    def to_dict(self) -> dict:
        main = [a for a in self.copies if not a.is_worktree]
        return {
            "repo": self.repo,
            "file": self.rel,
            "copies": len(self.copies),
            "variants": len(self.variants),
            "main_sha": main[0].sha if main else None,
            "sizes": sorted({a.size for a in self.copies}),
            "by_variant": [
                {"sha": sha, "size": arts[0].size, "count": len(arts),
                 "in_main": any(not a.is_worktree for a in arts),
                 "example": str(arts[0].path)}
                for sha, arts in sorted(self.variants.items(), key=lambda kv: -len(kv[1]))
            ],
        }


def copy_groups(artifacts: list[Artifact]) -> list[CopyGroup]:
    groups: dict[tuple[str, str], list[Artifact]] = defaultdict(list)
    for a in artifacts:
        if a.repo and a.kind in ("claude_md", "agents_md", "skill", "agent", "command"):
            groups[(str(a.repo), a.rel)].append(a)
    out = [CopyGroup(r, rel, arts) for (r, rel), arts in groups.items() if len(arts) > 1]
    return sorted(out, key=lambda g: (-len(g.variants), -len(g.copies), g.repo, g.rel))
