"""Compute what a session started in a folder loads before the first prompt.

Only loading rules observed on this machine are encoded:
- Claude Code: ~/.claude/CLAUDE.md, CLAUDE.md / CLAUDE.local.md in the folder and
  every parent, @imports in those files, MEMORY.md of the main repository's slug
  (first 200 lines or 25 KB), and the name+description of each skill it lists that is
  on disk (skills.py).
- Codex: ~/.codex/AGENTS.md, then AGENTS.override.md or AGENTS.md from the
  repository root down to the folder, capped at 32 KiB by default.
Connector tools and built-in skills are not on disk and are not counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import skills
from .collect import HOME, Artifact, find_checkout, frontmatter, is_disabled_skill

MEMORY_LINE_LIMIT = 200
MEMORY_BYTE_LIMIT = 25 * 1024
CODEX_PROJECT_DOC_MAX = 32 * 1024
# Name of the token estimator. Change it when estimate_tokens changes: totals of two runs
# compare only when both used the same estimator.
ESTIMATOR = "bytes-v1"
IMPORT_RE = re.compile(r"(?:^|\s)@((?:~|\.{1,2})?/?[\w./-]+\.md)\b", re.M)


def estimate_tokens(text: str) -> int:
    """Rough token count: ASCII at 4 chars per token, other chars at 1.3 chars per token.

    Calibrate with `walle eve` before trusting absolute numbers.
    """
    ascii_n = sum(1 for c in text if ord(c) < 128)
    other = len(text) - ascii_n
    return round(ascii_n / 4 + other / 1.3)


def memory_slug(folder: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(folder))


def memory_folder(folder: Path) -> Path:
    """The folder whose slug names the auto memory: the main repository root, for its subfolders and
    worktrees too (seen in instructions attachments: atc/controller and atc worktrees load the atc
    memory). Outside a repository, the folder itself."""
    _checkout, repo, _wt = find_checkout(folder)
    return repo or folder


def memory_text(text: str) -> tuple[str, str]:
    """The part of MEMORY.md a session loads, and a note on the cut: the first 200 lines or 25 KB."""
    lines = text.splitlines()
    note = f"{len(lines)} lines"
    if len(lines) > MEMORY_LINE_LIMIT:
        lines = lines[:MEMORY_LINE_LIMIT]
        note += f", truncated to {MEMORY_LINE_LIMIT}"
    kept = "\n".join(lines)
    data = kept.encode()
    if len(data) > MEMORY_BYTE_LIMIT:
        kept = data[:MEMORY_BYTE_LIMIT].decode(errors="ignore")
        kept = kept[: kept.rfind("\n")] if "\n" in kept else kept
        note += f", truncated to {MEMORY_BYTE_LIMIT} bytes"
    return kept, note


@dataclass
class Layer:
    label: str  # global | ancestor | import | memory | skill_catalog | codex_global | codex_project
    path: Path | None
    bytes: int
    tokens: int
    note: str = ""


@dataclass
class Stack:
    folder: Path
    agent: str  # claude | codex
    layers: list[Layer] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(l.bytes for l in self.layers)

    @property
    def tokens(self) -> int:
        return sum(l.tokens for l in self.layers)

    def paths(self) -> list[Path]:
        return [l.path for l in self.layers if l.path and l.label not in ("skill_catalog",)]


def _layer(label: str, path: Path, text: str | None = None, note: str = "") -> Layer:
    if text is None:
        text = path.read_text(errors="replace")
    return Layer(label, path, len(text.encode()), estimate_tokens(text), note)


def _imports(path: Path, text: str, depth: int = 0, seen: set[Path] | None = None) -> list[Layer]:
    seen = seen if seen is not None else set()
    out: list[Layer] = []
    if depth >= 5:
        return out
    in_fence = False
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(re.sub(r"`[^`]*`", "", line))
    for m in IMPORT_RE.finditer("\n".join(lines)):
        ref = m.group(1)
        target = Path(ref).expanduser() if ref.startswith("~") else (path.parent / ref)
        try:
            target = target.resolve()
        except OSError:
            continue
        if target in seen or not target.is_file():
            continue
        seen.add(target)
        t = target.read_text(errors="replace")
        out.append(_layer("import", target, t, note=f"@import from {path.name}"))
        out.extend(_imports(target, t, depth + 1, seen))
    return out


def _skill_catalog(dirs: list[Path]) -> tuple[str, int]:
    parts = []
    count = 0
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*/SKILL.md")):
            text = p.read_text(errors="replace")
            if is_disabled_skill(text):
                continue
            fm = frontmatter(text)
            parts.append(f"- {fm.get('name', p.parent.name)}: {fm.get('description', '')}")
            count += 1
    return "\n".join(parts), count


def claude_stack(folder: Path, home: Path = HOME) -> Stack:
    st = Stack(folder, "claude")
    g = home / ".claude" / "CLAUDE.md"
    if g.is_file():
        t = g.read_text(errors="replace")
        st.layers.append(_layer("global", g, t))
        st.layers.extend(_imports(g, t))
    chain = list(reversed([folder, *folder.parents]))
    for d in chain:
        for name in ("CLAUDE.md", "CLAUDE.local.md"):
            p = d / name
            if p.is_file() and p.resolve() != g.resolve():
                t = p.read_text(errors="replace")
                st.layers.append(_layer("ancestor", p, t))
                st.layers.extend(_imports(p, t))
    mem = home / ".claude" / "projects" / memory_slug(memory_folder(folder)) / "memory" / "MEMORY.md"
    if mem.is_file():
        text, note = memory_text(mem.read_text(errors="replace"))
        st.layers.append(_layer("memory", mem, text, note))
    listed = [s for s in skills.discover(folder, home) if not s.disabled]
    if listed:
        text = "\n".join(s.line for s in listed)
        by_source: dict[str, int] = {}
        for s in listed:
            by_source[s.source] = by_source.get(s.source, 0) + 1
        note = f"{len(listed)} skills on disk (" + ", ".join(f"{k} {v}" for k, v in by_source.items()) + ")"
        st.layers.append(Layer("skill_catalog", None, len(text.encode()), estimate_tokens(text), note))
    return st


def codex_stack(folder: Path, home: Path = HOME) -> Stack:
    st = Stack(folder, "codex")
    g = home / ".codex" / "AGENTS.md"
    if g.is_file():
        st.layers.append(_layer("codex_global", g))
    checkout, _repo, _wt = find_checkout(folder)
    top = checkout or folder
    chain = [top] + [p for p in reversed(folder.parents) if top in p.parents] + ([folder] if folder != top else [])
    used = 0
    for d in chain:
        for name in ("AGENTS.override.md", "AGENTS.md"):
            p = d / name
            if p.is_file():
                t = p.read_text(errors="replace")
                size = len(t.encode())
                note = ""
                if used + size > CODEX_PROJECT_DOC_MAX:
                    note = f"over the {CODEX_PROJECT_DOC_MAX} byte project doc cap: Codex truncates"
                used += size
                st.layers.append(_layer("codex_project", p, t, note))
                break
    text, n = _skill_catalog([home / ".codex" / "skills"])
    if n:
        st.layers.append(Layer("skill_catalog", None, len(text.encode()), estimate_tokens(text), f"{n} codex skills"))
    return st


def session_folders(artifacts: list[Artifact]) -> list[Path]:
    """Folders worth a stack: main-checkout folders that hold a CLAUDE.md or AGENTS.md."""
    out = set()
    for a in artifacts:
        if a.kind in ("claude_md", "agents_md") and not a.is_worktree and "/worktrees/" not in str(a.path):
            out.add(a.path.parent)
    return sorted(out)
