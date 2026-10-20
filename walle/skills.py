"""Find the skills a Claude Code session lists, on disk.

A session lists the name and description of every skill it can call. These come from:
- user skills: ~/.claude/skills/<name>/SKILL.md
- project skills: .claude/skills/<name>/SKILL.md in the folder and each parent
- claude.ai skills synced to disk: ~/.claude/skills/synced/<bucket>/<name>/SKILL.md, listed as
  "anthropic-skills:<name>"
- plugin skills: <plugin root>/skills/<name>/SKILL.md, listed as "<plugin>:<name>". Plugin roots are
  the install paths in ~/.claude/plugins/installed_plugins.json, the claude.ai plugins synced to
  ~/.claude/plugins/synced/<bucket>/<plugin>[~gN], and the copies the desktop host keeps in
  ~/.claude/remote/plugins/<host>/<hash>.
Skills of subfolders load only when the session reads a file in that subfolder (on demand).
Built-in skills of the Claude Code binary are not on disk.

Copies of one skill (several sync buckets or plugin versions) count once, by listed name.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .collect import HOME, SKIP_DIRS, frontmatter

SYNCED_SKILL_PREFIX = "anthropic-skills"  # observed in skill_listing of cli, sdk-cli and desktop sessions
DESCRIPTION_MAX = 1536  # characters of description + when_to_use in one listing line
_BUCKET_SKIP = re.compile(r"^\.")  # .trash, .staging, .bucket-* hold old or partial copies


@dataclass
class Skill:
    name: str  # listed name, with the plugin prefix when there is one
    path: Path
    source: str  # user | project | synced | plugin | synced_plugin | remote_plugin | nested
    description: str
    disabled: bool  # disable-model-invocation: true, not listed

    @property
    def line(self) -> str:
        """The listing line, as the skill_listing attachment records it."""
        return f"- {self.name}: {self.description}"


def read_skill(path: Path, name: str, source: str) -> Skill | None:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    fm = frontmatter(text)
    desc = fm.get("description", "")
    if fm.get("when_to_use"):
        desc = f"{desc} {fm['when_to_use']}".strip()
    if len(desc) > DESCRIPTION_MAX:
        desc = desc[:DESCRIPTION_MAX]
    disabled = fm.get("disable-model-invocation", "").lower() == "true"
    return Skill(name, path, source, desc, disabled)


def _skill_dirs(base: Path):
    """<base>/<name>/SKILL.md, skipping hidden folders."""
    if not base.is_dir():
        return
    for p in sorted(base.glob("*/SKILL.md")):
        if not _BUCKET_SKIP.match(p.parent.name):
            yield p


def _plugin_name(root: Path, hint: str = "") -> str:
    try:
        name = json.loads((root / ".claude-plugin" / "plugin.json").read_text()).get("name")
        if isinstance(name, str) and name:
            return name
    except (OSError, ValueError, AttributeError):
        pass
    return hint or re.sub(r"~g\d+$", "", root.name)


def _buckets(base: Path, org: str | None) -> list[Path]:
    """The sync bucket in use: buckets are named <org uuid>_<account uuid>, and the newest one of the org
    (by its .last-complete-round file) is the signed-in account's. Without an org, the newest bucket."""
    if not base.is_dir():
        return []
    out = [d for d in base.iterdir() if d.is_dir() and not _BUCKET_SKIP.match(d.name)]
    if org:
        out = [d for d in out if d.name.startswith(org)] or out

    def stamp(d: Path) -> float:
        for f in (d / ".last-complete-round", d):
            try:
                return f.stat().st_mtime
            except OSError:
                continue
        return 0.0

    return sorted(out, key=stamp, reverse=True)[:1]


def plugin_roots(home: Path = HOME, org: str | None = None) -> list[tuple[Path, str, str]]:
    """(plugin root, source, plugin name) for every plugin whose skills a session can list."""
    roots: list[tuple[Path, str, str]] = []
    try:
        data = json.loads((home / ".claude" / "plugins" / "installed_plugins.json").read_text())
        enabled = _enabled_plugins(home)
        for key, installs in (data.get("plugins") or {}).items():
            if enabled.get(key) is False:
                continue
            for inst in installs or []:
                p = inst.get("installPath") if isinstance(inst, dict) else None
                if p and Path(p).is_dir():
                    roots.append((Path(p), "plugin", _plugin_name(Path(p), key.split("@", 1)[0])))
    except (OSError, ValueError, AttributeError):
        pass
    for bucket in _buckets(home / ".claude" / "plugins" / "synced", org):
        for d in sorted(bucket.iterdir()):
            if d.is_dir() and not _BUCKET_SKIP.match(d.name) and (d / "skills").is_dir():
                roots.append((d, "synced_plugin", _plugin_name(d)))
    # The desktop host keeps one folder per plugin version it has fetched, newest first here.
    remote = home / ".claude" / "remote" / "plugins"
    if remote.is_dir():
        copies = [d for host in remote.iterdir() if host.is_dir() for d in host.iterdir() if (d / "skills").is_dir()]
        copies.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        roots.extend((d, "remote_plugin", _plugin_name(d)) for d in copies)
    return roots


def _enabled_plugins(home: Path) -> dict:
    try:
        data = json.loads((home / ".claude" / "settings.json").read_text())
        ep = data.get("enabledPlugins")
        return ep if isinstance(ep, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


OWN = ("user", "project")  # skills the user writes; the instruction stack counts only these


def discover(folder: Path | None, home: Path = HOME, org: str | None = None,
             sources: tuple[str, ...] | None = None) -> list[Skill]:
    """Skills a session started in `folder` can list at start, one per listed name.

    Disabled skills are kept with disabled=True, so a caller can count them. `sources` keeps only
    skills from those sources (for example OWN).
    """
    found: dict[str, Skill] = {}

    def add(path: Path, name: str, source: str):
        if name in found or (sources is not None and source not in sources):
            return
        s = read_skill(path, name, source)
        if s:
            found[name] = s

    for p in _skill_dirs(home / ".claude" / "skills"):
        add(p, p.parent.name, "user")
    if folder is not None:
        for d in reversed([folder, *folder.parents]):
            for p in _skill_dirs(d / ".claude" / "skills"):
                add(p, p.parent.name, "project")
    for bucket in _buckets(home / ".claude" / "skills" / "synced", org):
        for p in _skill_dirs(bucket):
            add(p, f"{SYNCED_SKILL_PREFIX}:{p.parent.name}", "synced")
    roots = plugin_roots(home, org)
    for root, source, prefix in roots:
        if source != "remote_plugin":
            for p in _skill_dirs(root / "skills"):
                add(p, f"{prefix}:{p.parent.name}", source)
    if sources is not None and not {"plugin", "synced_plugin", "remote_plugin", "synced"} & set(sources):
        return list(found.values())
    # The host's copies hold every plugin it ever fetched. Use them only for plugins already active above.
    active = {n.split(":", 1)[0] for n in found if ":" in n}
    for root, source, prefix in roots:
        if source == "remote_plugin" and prefix in active:
            for p in _skill_dirs(root / "skills"):
                add(p, f"{prefix}:{p.parent.name}", source)
    return list(found.values())


def nested(folder: Path, limit: int = 5000) -> list[Skill]:
    """Skills of subfolders below `folder`, listed as "<subfolder>:<name>" when the session reads there."""
    out: list[Skill] = []
    seen = 0
    for dirpath, dirnames, _files in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != "worktrees"]
        seen += 1
        if seen > limit:
            break
        here = Path(dirpath)
        if here == folder or here.name != "skills" or here.parent.name != ".claude":
            continue
        sub = here.parent.parent
        if sub == folder:
            continue
        rel = sub.relative_to(folder)
        for p in _skill_dirs(here):
            s = read_skill(p, f"{rel}:{p.parent.name}", "nested")
            if s:
                out.append(s)
    return out
