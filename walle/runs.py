"""Run records: the `run` block of scan.json and the run index, runs/index.jsonl.

Every scan run gets an id (its folder name under the runs folder), a parent (the
previous scan of the same roots), the walle version, the rules hash and the token
estimator. Two runs compare finding counts only when their rules hashes match, and
token totals only when their estimators match.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import __version__
from .collect import find_checkout
from .stack import ESTIMATOR
from .triage import rules_hash

INDEX = "index.jsonl"


def git_head(folder: Path) -> str | None:
    """The commit a checkout has checked out, read from .git without running git."""
    checkout, _repo, _wt = find_checkout(folder)
    if not checkout:
        return None
    git = checkout / ".git"
    try:
        if git.is_file():
            m = re.match(r"gitdir:\s*(.+)", git.read_text(errors="replace").strip())
            if not m:
                return None
            gitdir = Path(m.group(1))
            gitdir = gitdir if gitdir.is_absolute() else (checkout / gitdir).resolve()
        else:
            gitdir = git
        common = gitdir
        if (gitdir / "commondir").is_file():
            common = (gitdir / (gitdir / "commondir").read_text().strip()).resolve()
        head = (gitdir / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head or None
        ref = head[4:].strip()
        for base in (gitdir, common):
            if (base / ref).is_file():
                return (base / ref).read_text().strip() or None
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(errors="replace").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        return None
    return None


def run_block(roots: list[Path], folders: list[Path]) -> dict:
    """The `run` block, without id, parent and label: the CLI sets them when it writes the run."""
    heads = {}
    for f in [*roots, *folders]:
        checkout = find_checkout(f)[0]
        if checkout and str(checkout) not in heads:
            heads[str(checkout)] = git_head(checkout)
    return {
        "id": None,
        "kind": "scan",
        "parent_id": None,
        "label": None,
        "walle_version": __version__,
        "rules_hash": rules_hash(),
        "estimator": ESTIMATOR,
        "roots": [str(r) for r in roots],
        "git_heads": heads,
    }


def new_run_dir(out: Path, now: datetime | None = None) -> Path:
    """A new folder under the runs folder. Its name is the run id."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    d, n = out / stamp, 2
    while d.exists():
        d, n = out / f"{stamp}-{n}", n + 1
    return d


def read_index(out: Path) -> list[dict]:
    rows = []
    try:
        text = (out / INDEX).read_text(errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def parent_of(out: Path, roots: list[str], kind: str = "scan") -> str | None:
    """The id of the newest run of this kind with the same roots, or None for a first run."""
    want = sorted(roots)
    for row in reversed(read_index(out)):
        if row.get("kind") == kind and sorted(row.get("roots") or []) == want:
            return row.get("id")
    return None


def summary(data: dict, path: Path) -> dict:
    """One line of the run index."""
    run = data["run"]
    stacks = data.get("triage", {}).get("stacks", {})
    return {
        "id": run["id"],
        "kind": run["kind"],
        "label": run["label"],
        "parent_id": run["parent_id"],
        "generated": data.get("generated"),
        "roots": run["roots"],
        "rules": run["rules_hash"],
        "estimator": run["estimator"],
        "walle_version": run["walle_version"],
        "stack_sum": sum(s["tokens_est"] for s in data["stacks"]),
        "per_folder": {f"{s['folder']}@{s['agent']}": s["tokens_est"] for s in data["stacks"]},
        "verdicts": {k: e["verdict"] for k, e in stacks.items()},
        "open": sum(1 for it in data.get("items", []) if it["state"] == "open" and it["certainty"] != "info"),
        "kept": sum(1 for it in data.get("items", []) if it["state"] == "kept"),
        "path": str(path),
    }


def append_index(out: Path, data: dict, path: Path) -> dict:
    row = summary(data, path)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / INDEX, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
