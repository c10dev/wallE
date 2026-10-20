"""Collect instruction artifacts: the files an agent session reads as instructions."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", ".venv", "venv",
    "__pycache__", ".turbo", ".cache", "coverage", ".expo", "Pods",
}

# File name -> kind. Skills and agents are matched by path below.
ROOT_FILES = {
    "CLAUDE.md": "claude_md",
    "CLAUDE.local.md": "claude_md",
    "AGENTS.md": "agents_md",
    "AGENTS.override.md": "agents_md",
    "GEMINI.md": "other_agent",
    ".cursorrules": "other_agent",
    ".windsurfrules": "other_agent",
    ".clinerules": "other_agent",
    "copilot-instructions.md": "other_agent",
}

# Folder rule files: (parent folder, suffix).
RULE_DIRS = {(".cursor/rules", ".mdc"), (".cursor/rules", ".md"), (".windsurf/rules", ".md"),
             (".clinerules", ".md"), (".github/instructions", ".md")}


@dataclass
class Artifact:
    path: Path
    kind: str  # claude_md | agents_md | skill | agent | command | memory_index | global_claude | global_codex
    repo: Path | None = None  # main repository root (worktrees map to their main repo)
    checkout: Path | None = None  # the checkout that holds the file
    is_worktree: bool = False
    size: int = 0
    sha: str = ""
    sha1: str = ""  # sha1 of the bytes, for scan.json files[].sha
    text: str = field(default="", repr=False)

    @property
    def rel(self) -> str:
        """Path inside its checkout, used to compare copies across worktrees."""
        if self.checkout:
            try:
                return str(self.path.relative_to(self.checkout))
            except ValueError:
                pass
        return str(self.path)


def read_artifact(path: Path, kind: str) -> Artifact:
    data = path.read_bytes()
    checkout, repo, is_wt = find_checkout(path.parent)
    return Artifact(
        path=path,
        kind=kind,
        repo=repo,
        checkout=checkout,
        is_worktree=is_wt,
        size=len(data),
        sha=hashlib.sha256(data).hexdigest()[:12],
        sha1=hashlib.sha1(data).hexdigest(),
        text=data.decode("utf-8", errors="replace"),
    )


def find_checkout(start: Path) -> tuple[Path | None, Path | None, bool]:
    """Return (checkout root, main repo root, is linked worktree) for a path."""
    for d in [start, *start.parents]:
        git = d / ".git"
        if git.is_dir():
            return d, d, False
        if git.is_file():
            m = re.match(r"gitdir:\s*(.+)", git.read_text(errors="replace").strip())
            if m:
                gitdir = Path(m.group(1))
                if not gitdir.is_absolute():
                    gitdir = (d / gitdir).resolve()
                # <main>/.git/worktrees/<name>
                if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                    return d, gitdir.parent.parent.parent, True
            return d, d, True
    return None, None, False


def walk_roots(roots: list[Path]):
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            yield Path(dirpath), dirnames, filenames


def classify_path(path: Path) -> str | None:
    name = path.name
    if name in ROOT_FILES:
        return ROOT_FILES[name]
    parts = path.parts
    if name == "SKILL.md" and "skills" in parts:
        return "skill"
    if path.suffix == ".md" and len(parts) >= 3 and parts[-2] == "agents" and parts[-3] == ".claude":
        return "agent"
    if path.suffix == ".md" and len(parts) >= 3 and parts[-2] == "commands" and parts[-3] == ".claude":
        return "command"
    for folder, suffix in RULE_DIRS:
        depth = folder.count("/") + 1
        if path.suffix == suffix and "/".join(parts[-1 - depth : -1]) == folder:
            return "other_agent"
    return None


def collect(roots: list[Path], home: Path = HOME) -> list[Artifact]:
    """Collect project and global instruction artifacts."""
    found: list[Artifact] = []
    seen: set[Path] = set()

    def add(path: Path, kind: str):
        rp = path.resolve()
        if rp in seen or not path.is_file():
            return
        seen.add(rp)
        found.append(read_artifact(path, kind))

    folder_slugs = set()
    for dirpath, _dirs, files in walk_roots(roots):
        folder_slugs.add(re.sub(r"[^A-Za-z0-9]", "-", str(dirpath)))
        for f in files:
            p = dirpath / f
            kind = classify_path(p)
            if kind:
                add(p, kind)

    add(home / ".claude" / "CLAUDE.md", "global_claude")
    add(home / ".codex" / "AGENTS.md", "global_codex")
    for base in (home / ".claude" / "skills", home / ".codex" / "skills"):
        if base.is_dir():
            for p in sorted(base.glob("*/SKILL.md")):
                add(p, "skill")
    # Claude Code keeps one MEMORY.md per session folder. Collect only those of folders under the roots,
    # so a scan of one repository does not report other projects' memory.
    mem_root = home / ".claude" / "projects"
    # The slug is lossy (tennis-web and tennis/web look alike), so match it against the folders walked.
    if mem_root.is_dir():
        for p in sorted(mem_root.glob("*/memory/MEMORY.md")):
            if p.parent.parent.name in folder_slugs:
                add(p, "memory_index")
    return found


def is_disabled_skill(text: str) -> bool:
    fm = frontmatter(text)
    return fm.get("disable-model-invocation", "").lower() == "true"


def frontmatter(text: str) -> dict[str, str]:
    """Top-level `key: value` pairs of a YAML front matter block.

    Reads block scalars (`>`, `>-`, `|`, `|-`), plain values that go on over indented lines,
    and quoted values over several lines. Nested keys stay in their parent's text.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    key, style, parts = None, "", []

    def close():
        if key is None:
            return
        if style.startswith("|"):
            value = "\n".join(parts)
        elif style.startswith(">"):
            value = " ".join(p for p in parts if p)
        else:
            value = " ".join(p for p in parts if p).strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = re.sub(r'\\(.)', lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), value[1:-1])
            elif len(value) >= 2 and value[0] == value[-1] == "'":
                value = value[1:-1].replace("''", "'")
        out[key] = value.strip()

    for line in text[3:end].splitlines():
        m = re.match(r"([A-Za-z0-9_-]+):(?:\s+(.*)|$)", line)
        if m and not line[0].isspace():
            close()
            key, value = m.group(1), (m.group(2) or "").strip()
            style = value if re.fullmatch(r"[>|][+-]?", value) else ""
            parts = [] if style or not value else [value]
        elif key is not None and (line[:1].isspace() or not line.strip()):
            parts.append(line.strip())
    close()
    return out
