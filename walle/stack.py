"""Compute what a session started in a folder loads before the first prompt.

Only loading rules observed on this machine are encoded:
- Claude Code: ~/.claude/CLAUDE.md, CLAUDE.md / CLAUDE.local.md in the folder and
  every parent, @imports in those files, MEMORY.md of the main repository's slug
  (first 200 lines or 25 KB), and the name+description of the user and project skills.
  Synced claude.ai skills and plugin skills load too, but they are not the user's files: the layer
  note counts them, and `walle context` measures them. The budget stays on the user's own files.
- Codex: ~/.codex/AGENTS.md, then AGENTS.override.md or AGENTS.md from the
  repository root down to the folder, capped at project_doc_max_bytes of ~/.codex/config.toml
  (32 KiB when unset), and the skills of ~/.codex/skills as Codex lists them: a skill whose
  agents/openai.yaml sets allow_implicit_invocation: false is not listed (Claude's
  disable-model-invocation does not apply). System skills (~/.codex/skills/.system) and plugin
  skills load too; like synced and plugin skills of Claude Code, the layer note counts them and
  `walle context --agent codex` measures them.
Connector tools and built-in skills are not on disk and are not counted.

Claude Code drops block-level HTML comments from every file it loads (CLAUDE.md, its @imports and
MEMORY.md) and trims the text: claude_loaded() counts that text. Codex layers count the whole file:
the one Codex transcript on the author's machine recorded AGENTS.md as it is on disk, and no AGENTS.md
there has an HTML comment, so it is not known whether Codex drops them.

Layer tokens come from estimate.py: bytes-v2 prose rates by default, bytes-v1 on request.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import estimate, skills
from .collect import HOME, Artifact, find_checkout, frontmatter

MEMORY_LINE_LIMIT = 200
MEMORY_BYTE_LIMIT = 25 * 1024
CODEX_PROJECT_DOC_MAX = 32 * 1024  # Codex's default project_doc_max_bytes
IMPORT_RE = re.compile(r"(?:^|\s)@((?:~|\.{1,2})?/?[\w./-]+\.md)\b", re.M)
# Claude Code 2.1 lexes an instruction file with marked (gfm off) and drops its top-level HTML comment
# blocks. These patterns follow marked's block rules for the cases that instruction files use.
# A comment block: up to 3 spaces, the comment, the rest of its last line, the line break(s) after it.
# An unclosed comment runs to the end of the text.
COMMENT_BLOCK_RE = re.compile(r" {0,3}<!--(?:-?>|[\s\S]*?(?:-->|\Z))[^\n]*(\n*)")
COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})")
BULLET_RE = re.compile(r" {0,3}(?:[-*+]|\d{1,9}[.)])(?: +|$)")
HEADING_RE = re.compile(r" {0,3}#{1,6}(?: |$)")
HR_RE = re.compile(r" {0,3}([-*_])(?: *\1){2,} *$")
# An HTML block other than a comment runs to the next blank line. Block tags start one anywhere; another
# tag alone on its line starts one only outside a paragraph.
HTML_BLOCK_TAGS = ("address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|"
                   "dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|"
                   "header|hr|html|iframe|legend|li|link|main|menu|menuitem|meta|nav|noframes|ol|optgroup|"
                   "option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul")
HTML_BLOCK_RE = re.compile(rf" {{0,3}}</?(?:{HTML_BLOCK_TAGS})(?: |/?>|$)", re.I)
HTML_TAG_LINE_RE = re.compile(r" {0,3}</?[A-Za-z][\w-]*(?:\s[^>]*)?/?>\s*$")


def claude_loaded(text: str) -> str:
    """The text of an instruction file as Claude Code puts it into the context.

    Claude Code 2.1 lexes the file as Markdown. It removes each top-level HTML block that starts with
    "<!--" and holds "-->": the comments go, and the block goes when nothing else is left in it. Then the
    text is trimmed. Comments in code fences, in list items, in other HTML blocks, on a line that
    continues a paragraph, and after text on a line stay. Transcripts record the result: a
    ~/.claude/CLAUDE.md of 5,323 bytes with two comment lines is 5,156 bytes there.
    Known gaps: a comment line that a "---" line turns into a heading, and a comment that continues a
    quote inside a list item, go here but stay in Claude Code.
    """
    if "<!--" in text:
        text = _drop_comment_blocks(text)
    return text.strip()


def _drop_comment_blocks(text: str) -> str:
    out: list[str] = []
    pos, n = 0, len(text)
    fence = None  # the open code fence, for example "```"
    item = None  # the content column of the open top-level list item
    para = False  # the line before is paragraph text: an indented line continues it
    html = False  # an HTML block is open until the next blank line
    while pos < n:
        end = text.find("\n", pos)
        end = n if end < 0 else end + 1
        line = text[pos:end]
        body = line.rstrip("\n")
        bare = body.strip()
        indent = len(body) - len(body.lstrip(" "))
        if fence:
            if bare and set(bare) == {fence[0]} and len(bare) >= len(fence):
                fence = None
        elif not bare:
            para = html = False
        elif html:
            pass
        elif FENCE_RE.match(body) and (item is None or indent < item):
            fence, para = FENCE_RE.match(body).group(1), False
        elif (indent <= 3 and bare.startswith("<!--")
              and (indent < item if item is not None else not (para and indent))):
            m = COMMENT_BLOCK_RE.match(text, pos)
            raw = m.group(0)
            if "-->" not in raw:  # unclosed: Claude Code keeps it, and it runs to the end
                out.append(text[pos:])
                break
            breaks = m.group(1)
            block = raw[:len(raw) - len(breaks)]
            # marked keeps the line breaks before a blank line as a token of their own.
            block, breaks = (block, breaks) if len(breaks) > 1 else (raw, "")
            rest = COMMENT_RE.sub("", block)
            out.append((rest if rest.strip() else "") + breaks)
            item, para, pos = None, False, m.end()  # an HTML block also ends a list
            continue
        elif HTML_BLOCK_RE.match(body) or (not para and HTML_TAG_LINE_RE.match(body)):
            html, para = True, False
            if item is not None and indent < item:
                item = None
        else:
            b = BULLET_RE.match(body)
            if b and (item is None or indent < item):
                item = len(b.group(0)) if body[len(b.group(0)):].strip() else indent + 2
                para = True
            elif HEADING_RE.match(body) or HR_RE.match(body) or (indent >= 4 and item is None and not para):
                item = None if item is not None and indent < item else item
                para = False  # a heading, a rule or indented code is not a paragraph
            else:
                if item is not None and indent < item and not para:
                    item = None  # a paragraph after a blank line ends the list
                para = True
        out.append(line)
        pos = end
    return "".join(out)


# bytes-v1, kept for sizes that are not instruction stacks (the brief-md cap, judge batch files).
estimate_tokens = estimate.bytes_v1


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
    estimator: str = estimate.DEFAULT

    @property
    def bytes(self) -> int:
        return sum(l.bytes for l in self.layers)

    @property
    def tokens(self) -> int:
        return sum(l.tokens for l in self.layers)

    def paths(self) -> list[Path]:
        return [l.path for l in self.layers if l.path and l.label not in ("skill_catalog",)]


def _layer(label: str, path: Path, text: str | None = None, note: str = "",
           estimator: str = estimate.DEFAULT) -> Layer:
    if text is None:
        text = path.read_text(errors="replace")
    return Layer(label, path, len(text.encode()), estimate.estimate(text, "instructions", estimator), note)


def _catalog_layer(text: str, note: str, estimator: str) -> Layer:
    return Layer("skill_catalog", None, len(text.encode()), estimate.estimate(text, "skill_listing", estimator), note)


def _imports(path: Path, text: str, depth: int = 0, seen: set[Path] | None = None,
             estimator: str = estimate.DEFAULT) -> list[Layer]:
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
        t = claude_loaded(target.read_text(errors="replace"))
        out.append(_layer("import", target, t, note=f"@import from {path.name}", estimator=estimator))
        out.extend(_imports(target, t, depth + 1, seen, estimator))
    return out


def claude_stack(folder: Path, home: Path = HOME, estimator: str = estimate.DEFAULT) -> Stack:
    est = estimate.name(estimator)
    st = Stack(folder, "claude", estimator=est)
    g = home / ".claude" / "CLAUDE.md"
    if g.is_file():
        t = claude_loaded(g.read_text(errors="replace"))
        st.layers.append(_layer("global", g, t, estimator=est))
        st.layers.extend(_imports(g, t, estimator=est))
    chain = list(reversed([folder, *folder.parents]))
    for d in chain:
        for name in ("CLAUDE.md", "CLAUDE.local.md"):
            p = d / name
            if p.is_file() and p.resolve() != g.resolve():
                t = claude_loaded(p.read_text(errors="replace"))
                st.layers.append(_layer("ancestor", p, t, estimator=est))
                st.layers.extend(_imports(p, t, estimator=est))
    mem = home / ".claude" / "projects" / memory_slug(memory_folder(folder)) / "memory" / "MEMORY.md"
    if mem.is_file():
        text, note = memory_text(claude_loaded(mem.read_text(errors="replace")))
        st.layers.append(_layer("memory", mem, text, note, est))
    every = [s for s in skills.discover(folder, home) if not s.disabled]
    listed = [s for s in every if s.source in skills.OWN]
    if listed:
        text = "\n".join(s.line for s in listed)
        by_source: dict[str, int] = {}
        for s in listed:
            by_source[s.source] = by_source.get(s.source, 0) + 1
        note = f"{len(listed)} skills on disk (" + ", ".join(f"{k} {v}" for k, v in by_source.items()) + ")"
        if len(every) > len(listed):
            note += f"; {len(every) - len(listed)} more synced or plugin skills are measured by walle context"
        st.layers.append(_catalog_layer(text, note, est))
    return st


def codex_settings(home: Path = HOME) -> dict:
    """The few settings walle reads from ~/.codex/config.toml.

    Only key names, booleans, the approval policy and project_doc_max_bytes: other values (MCP server
    env and headers, shell environment filters) can hold secrets and are never read into the result.
    """
    try:
        with (home / ".codex" / "config.toml").open("rb") as fh:
            cfg = tomllib.load(fh)
    except (OSError, ValueError):  # tomllib.TOMLDecodeError is a ValueError
        cfg = {}
    cap = cfg.get("project_doc_max_bytes")
    cap_set = isinstance(cap, int) and not isinstance(cap, bool) and cap >= 0
    features: dict[str, bool] = {}
    for k, v in (cfg.get("features") if isinstance(cfg.get("features"), dict) else {}).items():
        if isinstance(v, bool):
            features[k] = v
        elif isinstance(v, dict) and isinstance(v.get("enabled"), bool):
            features[k] = v["enabled"]
    plugins = cfg.get("plugins") if isinstance(cfg.get("plugins"), dict) else {}
    servers = cfg.get("mcp_servers") if isinstance(cfg.get("mcp_servers"), dict) else {}
    policy = cfg.get("approval_policy")
    return {"project_doc_max_bytes": cap if cap_set else CODEX_PROJECT_DOC_MAX, "project_doc_max_bytes_set": cap_set,
            "approval_policy": policy if isinstance(policy, str) else None, "features": features,
            "mcp_servers": sorted(str(k) for k in servers),
            "plugins": {str(k): bool(v.get("enabled", True)) for k, v in plugins.items() if isinstance(v, dict)},
            "hooks": bool(cfg.get("hooks"))}


IMPLICIT_OFF_RE = re.compile(r"^\s*allow_implicit_invocation:\s*false\b", re.M)


def codex_implicit(skill_dir: Path) -> bool:
    """False when the skill's agents/openai.yaml sets allow_implicit_invocation: false. Codex does not list
    such a skill; it runs only when the user names it."""
    try:
        return not IMPLICIT_OFF_RE.search((skill_dir / "agents" / "openai.yaml").read_text(errors="replace"))
    except OSError:
        return True


def codex_skill_line(path: Path, root: str = "r0") -> str:
    """The skill list line Codex writes for a skill: "- name: description (file: r0/<folder>/SKILL.md)"."""
    fm = frontmatter(path.read_text(errors="replace"))
    return f"- {fm.get('name', path.parent.name)}: {fm.get('description', '')} (file: {root}/{path.parent.name}/SKILL.md)"


def _codex_catalog(home: Path) -> tuple[str, int, str]:
    base = home / ".codex" / "skills"
    lines, hidden = [], 0
    if base.is_dir():
        for p in sorted(base.glob("*/SKILL.md")):
            if p.parent.name.startswith("."):
                continue
            if not codex_implicit(p.parent):
                hidden += 1
                continue
            lines.append(codex_skill_line(p))
    system = sum(1 for p in (base / ".system").glob("*/SKILL.md") if codex_implicit(p.parent)) if base.is_dir() else 0
    note = f"{len(lines)} codex skills"
    if hidden:
        note += f"; {hidden} not listed (agents/openai.yaml allow_implicit_invocation: false)"
    if system:
        note += f"; {system} system skills and the plugin skills are measured by walle context --agent codex"
    return "\n".join(lines), len(lines), note


def codex_stack(folder: Path, home: Path = HOME, estimator: str = estimate.DEFAULT) -> Stack:
    est = estimate.name(estimator)
    st = Stack(folder, "codex", estimator=est)
    g = home / ".codex" / "AGENTS.md"
    if g.is_file():
        st.layers.append(_layer("codex_global", g, estimator=est))
    cap = codex_settings(home)["project_doc_max_bytes"]
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
                if used + size > cap:
                    note = f"over the {cap} byte project doc cap: Codex truncates"
                used += size
                st.layers.append(_layer("codex_project", p, t, note, est))
                break
    text, n, note = _codex_catalog(home)
    if n:
        st.layers.append(_catalog_layer(text, note, est))
    return st


def session_folders(artifacts: list[Artifact]) -> list[Path]:
    """Folders worth a stack: main-checkout folders that hold a CLAUDE.md or AGENTS.md."""
    out = set()
    for a in artifacts:
        if a.kind in ("claude_md", "agents_md") and not a.is_worktree and "/worktrees/" not in str(a.path):
            out.add(a.path.parent)
    return sorted(out)
