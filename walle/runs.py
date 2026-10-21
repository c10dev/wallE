"""Run records: the `run` block of scan.json and the run index, runs/index.jsonl.

Every scan run gets an id (its folder name under the runs folder), a parent (the
previous scan of the same roots), the walle version, the rules hash, the token
estimator and the counting revision. Two runs compare finding counts only when their
rules hashes match, and token totals only when their estimators and their counting
revisions match.

Other runs also get an index line: trim (a plan), apply and undo (walle apply, walle
undo: parent_id links them to the plan and to the apply), diff, context (walle context),
measure (walle eve) and compare (walle context --compare). A measure or compare run around
an apply has that apply as its parent_id. Code that reads the index for scans filters on
kind "scan". After each line, history.py writes the page index.html next to the index.

pins.json in the runs folder holds the baseline that `walle pin` fixed for a set of
roots. Without a pin, the baseline of a scan is its parent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import __version__, estimate, strings
from .collect import find_checkout
from .triage import rules_hash

INDEX = "index.jsonl"
PINS = "pins.json"
# Scans without an estimator record (schema 1 scan.json, which has no run block) counted with bytes-v1.
UNRECORDED_ESTIMATOR = "bytes-v1"
# Which text of a file scan counts. Bump it when that text changes: token totals of two revisions do not compare.
#   1: the whole file. Runs without a counting_revision record counted this way.
#   2: a file that Claude Code loads counts as loaded, without its HTML comment blocks (stack.claude_loaded).
#   3: the Codex skill list counts as Codex writes it: a skill is left out by allow_implicit_invocation: false in
#      agents/openai.yaml (not by disable-model-invocation), and each line ends with "(file: r0/<folder>/SKILL.md)".
COUNTING_REVISION = 3
UNRECORDED_COUNTING = 1


def estimator_of(record: dict | None) -> str:
    """The estimator of a run: an index line, a scan.json or its run block. Unrecorded means bytes-v1."""
    record = record or {}
    name = record.get("estimator") or (record.get("run") or {}).get("estimator")
    return name or UNRECORDED_ESTIMATOR


def counting_of(record: dict | None) -> int:
    """The counting revision of a run: an index line, a scan.json or its run block. Unrecorded means 1."""
    record = record or {}
    rev = record.get("counting_revision") or (record.get("run") or {}).get("counting_revision")
    return rev or UNRECORDED_COUNTING


def token_notes(others: list[dict | None], mine: dict, lang: str) -> list[str]:
    """Notices for runs whose token totals do not compare with this run: another estimator or another
    counting revision. Empty when every run counted like this one."""
    est, rev = estimator_of(mine), counting_of(mine)
    other_est = sorted({estimator_of(r) for r in others} - {est})
    other_rev = sorted({counting_of(r) for r in others} - {rev})
    notes = []
    if other_est:
        notes.append(strings.t("run.estimator_changed", lang, a=", ".join(other_est), b=est))
    if other_rev:
        notes.append(strings.t("run.counting_changed", lang, a=", ".join(map(str, other_rev)), b=rev))
    return notes


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


def run_block(roots: list[Path], folders: list[Path], estimator: str = estimate.DEFAULT) -> dict:
    """The `run` block, without id, parent and label: the CLI sets them when it writes the run.

    `estimator` is the full name of the estimator of every token count in the scan (bytes-v1 or bytes-v2).
    `counting_revision` tells which text of each file the scan counted (COUNTING_REVISION).
    """
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
        "estimator": estimate.name(estimator),
        "counting_revision": COUNTING_REVISION,
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
        "counting_revision": counting_of(run),
        "walle_version": run["walle_version"],
        "stack_sum": sum(s["tokens_est"] for s in data["stacks"]),
        "per_folder": {f"{s['folder']}@{s['agent']}": s["tokens_est"] for s in data["stacks"]},
        "verdicts": {k: e["verdict"] for k, e in stacks.items()},
        "open": sum(1 for it in data.get("items", []) if it["state"] == "open" and it["certainty"] != "info"),
        "kept": sum(1 for it in data.get("items", []) if it["state"] == "kept"),
        "path": str(path),
    }


def append_index(out: Path, data: dict, path: Path) -> dict:
    return append_row(out, summary(data, path))


def append_row(out: Path, row: dict) -> dict:
    """Add one line to the run index, then write the history page again (history.py).

    A scan's line comes from summary(); other kinds write their own. A page that cannot be written, for any
    reason, does not stop the run: the index line is the record.
    """
    out.mkdir(parents=True, exist_ok=True)
    with open(out / INDEX, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    from . import history
    try:
        history.write(out)
    except Exception:  # noqa: BLE001 - the index line is the record; the page must never fail the command
        pass
    return row


def roots_key(roots: list[str]) -> str:
    """One key for a set of roots: the sorted paths, one per line."""
    return "\n".join(sorted(roots))


def scans(out: Path, roots: list[str] | None = None) -> list[dict]:
    """Index lines of scan runs, oldest first; with roots, only scans of exactly that set of roots."""
    rows = [r for r in read_index(out) if r.get("kind") == "scan"]
    if roots is not None:
        want = sorted(roots)
        rows = [r for r in rows if sorted(r.get("roots") or []) == want]
    return rows


def newest_scan_of(out: Path, folder: Path) -> dict | None:
    """The newest scan whose roots include folder, else None."""
    return next((r for r in reversed(scans(out)) if str(folder) in (r.get("roots") or [])), None)


def load_pins(out: Path) -> dict[str, str]:
    """Roots key -> the run id pinned as the baseline of those roots."""
    try:
        data = json.loads((out / PINS).read_text())
    except (OSError, ValueError):
        return {}
    pins = data.get("pins") if isinstance(data, dict) else None
    return {k: v for k, v in (pins or {}).items() if isinstance(k, str) and isinstance(v, str)}


def save_pins(out: Path, pins: dict[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / (PINS + ".tmp")
    tmp.write_text(json.dumps({"version": 1, "pins": pins}, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(out / PINS)


def baseline_of(out: Path, row: dict) -> tuple[dict | None, bool]:
    """(baseline index line, pinned) of a scan: the pinned run of its roots, else its parent scan."""
    by_id = {r.get("id"): r for r in scans(out)}
    pinned = load_pins(out).get(roots_key(row.get("roots") or []))
    if pinned and pinned in by_id:
        return by_id[pinned], True
    return by_id.get(row.get("parent_id")), False


def history(out: Path, data: dict, path: Path, n: int = 10) -> list[dict]:
    """The newest n index lines of these roots, this run last (it is not in the index yet)."""
    want = sorted(data["run"]["roots"])
    rows = [r for r in read_index(out) if r.get("kind") == "scan" and sorted(r.get("roots") or []) == want]
    return (rows + [summary(data, path)])[-n:]
