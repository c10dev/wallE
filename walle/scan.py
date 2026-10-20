"""Run the full static pass: collect, stack, segment, classify, compact, triage.

scan.json schema 2 adds `schema`, `run`, `items`, `triage` and three fields of
`files[]` (sha, ownership, sections). The fields of schema 1 keep their names and
values, so readers of schema 1 (judge, diff, the reports) keep working.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

from .classify import classify_unit, emphasis_count
from .collect import HOME, Artifact, collect
from .compact import copy_groups, rule_pairs
from .conflict import protocol_pairs, topic_pairs
from .runs import run_block
from .segment import segment
from .stack import claude_stack, codex_stack, estimate_tokens, session_folders
from .triage import BUDGET, sections, triage

SCHEMA = 2

PROSE_KINDS = {"claude_md", "agents_md", "skill", "agent", "command", "memory_index",
               "global_claude", "global_codex", "other_agent"}


def is_copy(a: Artifact) -> bool:
    """A worktree copy of a file that also lives in a main checkout."""
    return a.is_worktree or "/worktrees/" in str(a.path)


def scan(roots: list[Path], home: Path = HOME, budget: int = BUDGET) -> dict:
    artifacts = collect(roots, home)
    by_path = {str(a.path): a for a in artifacts}

    folders = session_folders(artifacts)
    for r in roots:
        if r not in folders:
            folders.insert(0, r)
    stacks = []
    for f in folders:
        stacks.append(claude_stack(f, home))
        cx = codex_stack(f, home)
        # A Codex stack is only worth a row when a project AGENTS.md is in it, or for a root.
        if f in roots or any(l.label == "codex_project" for l in cx.layers):
            stacks.append(cx)
    loaded_count: Counter[str] = Counter()
    for st in stacks:
        for p in st.paths():
            loaded_count[str(p)] += 1

    files = []
    unit_cache: dict[str, list] = {}
    file_sections: dict[str, list[dict]] = {}
    flags_all = []
    for a in artifacts:
        if a.kind not in PROSE_KINDS or is_copy(a):
            continue
        units = segment(a.text, str(a.path))
        unit_cache[str(a.path)] = units
        file_sections[str(a.path)] = sections(a.text)
        flags = [fl for u in units for fl in classify_unit(u)]
        flags_all.extend(flags)
        by_cause = Counter(fl.cause for fl in flags)
        files.append({
            "path": str(a.path),
            "kind": a.kind,
            "repo": str(a.repo) if a.repo else None,
            "bytes": a.size,
            "tokens_est": estimate_tokens(a.text),
            "units": sum(1 for u in units if u.kind == "text"),
            "emphasis": emphasis_count(a.text),
            "loaded_in_stacks": loaded_count.get(str(a.path), 0),
            "flags": {str(k): by_cause.get(k, 0) for k in (1, 3, 4)},
            "sha": a.sha1,
            "ownership": None,  # set from the stacks after triage
            "sections": file_sections[str(a.path)],
        })

    # Rule pairs are compared inside one scope: every prose file of one repository's
    # main checkout, plus the global files that load next to them.
    globals_ = [p for p, a in by_path.items() if a.kind in ("global_claude", "global_codex")]
    scopes: dict[str, list[str]] = {}
    for p in unit_cache:
        a = by_path.get(p)
        key = str(a.repo) if a and a.repo else "(global)"
        scopes.setdefault(key, []).append(p)
    pairs = []
    seen = set()
    for key, paths in scopes.items():
        units = [u for p in dict.fromkeys(paths + globals_) for u in unit_cache.get(p, [])]
        for pr in rule_pairs(units):
            pk = tuple(sorted([(pr.a.file, pr.a.line), (pr.b.file, pr.b.line)]))
            if pk not in seen:
                seen.add(pk)
                pairs.append(pr)

    # Conflict candidates that do not look alike (conflict.py): pairs inside one load
    # stack, then protocol messages across repositories. A drift pair is not listed again.
    known = {tuple(sorted([(p.a.file, p.a.line), (p.b.file, p.b.line)])) for p in pairs if p.kind == "drift"}
    topics = topic_pairs([(f"{st.folder} ({st.agent})", [u for p in st.paths() for u in unit_cache.get(str(p), [])])
                          for st in stacks], known)
    known |= {tuple(sorted([(p.a.file, p.a.line), (p.b.file, p.b.line)])) for p in topics}
    pairs += topics + protocol_pairs({k: [u for p in ps for u in unit_cache[p]] for k, ps in scopes.items()}, known)

    stack_rows = []
    for st in stacks:
        st_flags = Counter()
        n_units = 0
        for p in st.paths():
            us = unit_cache.get(str(p), [])
            n_units += sum(1 for u in us if u.kind == "text")
            for u in us:
                for fl in classify_unit(u):
                    st_flags[fl.cause] += 1
        stack_rows.append({
            "folder": str(st.folder),
            "agent": st.agent,
            "bytes": st.bytes,
            "tokens_est": st.tokens,
            "units": n_units,
            "flags": {str(k): st_flags.get(k, 0) for k in (1, 3, 4)},
            "layers": [
                {"label": l.label, "path": str(l.path) if l.path else None,
                 "bytes": l.bytes, "tokens_est": l.tokens, "note": l.note}
                for l in st.layers
            ],
        })

    copies = [g.to_dict() for g in copy_groups(artifacts)]
    tri = triage(roots, home, artifacts, stacks, unit_cache, flags_all, pairs, copies, file_sections, budget)
    for f in files:
        f["ownership"] = tri["ownership"][f["path"]]
    return {
        "schema": SCHEMA,
        "run": run_block(roots, [st.folder for st in stacks]),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "roots": [str(r) for r in roots],
        "artifact_count": len(artifacts),
        "copy_count": sum(1 for a in artifacts if is_copy(a)),
        "stacks": stack_rows,
        "files": sorted(files, key=lambda f: (-f["loaded_in_stacks"], -f["bytes"])),
        "flags": [fl.to_dict() for fl in flags_all],
        "pairs": [p.to_dict() for p in sorted(pairs, key=lambda p: (p.kind, -p.score))],
        "copies": copies,
        "items": tri["items"],
        "triage": tri["triage"],
    }
