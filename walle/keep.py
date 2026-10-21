"""keep: lines and sources that the user decided to leave as they are.

walle keep <id> writes an entry to keep.json, next to the runs folder (~/.walle/keep.json by default;
with --out X it is X/../keep.json). An entry is one of:
- an item of a scan (an instruction line, a pair, a group): its id, rule, first file and line, and a
  hash of its lines. scan marks it "kept" while the hash is the same. When the lines change, the
  next scan releases the keep once ("keep released: line changed") and the entry is gone.
- a row of a trim plan for a source (a connector, an MCP server, a plugin, a skill, a hook): its id
  and source key. walle trim --plan leaves it out. A source has no lines, so its keep stays.

A team keeps lines in .wallekeep at the root of the repository: one team key per line, then
"# " and a comment. walle reads it and never writes it: walle keep --print gives the line to add.
The team key is the item id computed with the remote URL and the in-repo path in place of the
local path, so every clone gives the same key (triage.team_locator).

walle writes keep.json only. It never writes into a scanned folder.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

VERSION = 1
FILE = "keep.json"
TEAM_FILE = ".wallekeep"
_TEAM_LINE = re.compile(r"^\s*([0-9a-f]{6})\b\s*(?:#\s?(.*))?$")


def keep_hash(it: dict) -> str:
    """One hash of all lines of an item: it changes when any line of the item changes."""
    joined = "\n".join(str(loc.get("hash") or "") for loc in it.get("locations") or [])
    return hashlib.sha1(joined.encode()).hexdigest()[:12]


def path_for(out: Path) -> Path:
    """keep.json sits next to the runs folder: ~/.walle/keep.json for the default ~/.walle/runs."""
    return out.parent / FILE


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"version": VERSION, "entries": []}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"version": VERSION, "entries": []}
    data["entries"] = [e for e in data["entries"] if isinstance(e, dict) and e.get("id")]
    return data


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(path)


def for_scan(path: Path) -> dict:
    """The keep argument of scan(): personal item entries by id. Team entries come from .wallekeep."""
    return {"items": {e["id"]: e for e in load(path)["entries"] if e.get("kind", "item") == "item"}}


def source_ids(path: Path) -> set[str]:
    """Ids that a trim plan leaves out: kept sources and kept items."""
    return {e["id"] for e in load(path)["entries"]}


def read_wallekeep(path: Path) -> dict[str, dict]:
    """Team key -> {file, line, note} from a .wallekeep file. Other lines are ignored."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        m = _TEAM_LINE.match(line)
        if m:
            out.setdefault(m.group(1), {"file": str(path), "line": n, "note": (m.group(2) or "").strip() or None})
    return out


def release(path: Path, released: list[dict]) -> list[dict]:
    """Remove the entries that a scan released. Returns the entries removed."""
    ids = {r.get("id") for r in released}
    if not ids:
        return []
    data = load(path)
    gone = [e for e in data["entries"] if e["id"] in ids]
    if gone:
        data["entries"] = [e for e in data["entries"] if e["id"] not in ids]
        save(path, data)
    return gone


def item_entry(it: dict, note: str | None) -> dict:
    first = it["locations"][0] if it.get("locations") else {}
    return {"id": it["id"], "kind": "item", "rule": it.get("rule"), "label": it.get("label"),
            "file": first.get("file"), "line": first.get("line"), "hash": keep_hash(it),
            "note": note, "date": date.today().isoformat()}


def source_entry(row: dict, note: str | None) -> dict:
    src = row.get("source") or {}
    return {"id": row["id"], "kind": "source", "source": src.get("type"), "key": src.get("key"),
            "name": src.get("name"), "note": note, "date": date.today().isoformat()}


def add(path: Path, entry: dict) -> bool:
    """Add or update an entry. False when the same id was kept already (its note is updated)."""
    data = load(path)
    old = next((e for e in data["entries"] if e["id"] == entry["id"]), None)
    if old:
        if entry.get("note"):
            old["note"] = entry["note"]
        old.update({k: v for k, v in entry.items() if k not in ("note", "date")})
        save(path, data)
        return False
    data["entries"].append(entry)
    save(path, data)
    return True


def remove(path: Path, kid: str) -> dict | None:
    data = load(path)
    hit = next((e for e in data["entries"] if e["id"] == kid), None)
    if hit:
        data["entries"] = [e for e in data["entries"] if e["id"] != kid]
        save(path, data)
    return hit


# ---------------------------------------------------------------------- finding what to keep
def newest_plans(out: Path, limit: int = 20) -> list[Path]:
    runs = sorted((d for d in out.glob("*") if d.is_dir() and (d / "plan.json").is_file()), reverse=True)
    return [d / "plan.json" for d in runs[:limit]]


def find_row(out: Path, ref: str) -> dict | None:
    """A row of a recent trim plan by its id."""
    for p in newest_plans(out):
        try:
            rows = json.loads(p.read_text()).get("rows") or []
        except (OSError, ValueError):
            continue
        hit = next((r for r in rows if r.get("id") == ref), None)
        if hit:
            return hit
    return None


def resolve(out: Path, ref: str, cwd: Path) -> tuple[dict | None, dict | None, str | None]:
    """(scan item, plan row, error key). A number or an id of a recent scan item, else a plan row id.

    A row id can be six digits ("557199"): a number that is not a scan item is looked up as a row id too.
    """
    from .brief import find_item
    it, _data, err = find_item(out, ref, cwd)
    if it:
        return it, None, None
    row = find_row(out, ref)
    if row:
        return None, row, None
    return None, None, err if ref.isdigit() else "keep.no_ref"


# ---------------------------------------------------------------------- commands
def where(e: dict, home: Path) -> str:
    from .brief import shown
    if e.get("kind") == "source":
        return str(e.get("name") or e.get("key"))
    return f"{shown(e.get('file'), None, home)}:{e.get('line')}"


def add_parser(sub) -> None:
    k = sub.add_parser("keep", help="leave a line or a source as it is: later scans and trim plans skip it")
    k.add_argument("ref", nargs="?", help="an item id or its number in the newest scan of this folder, "
                                          "or a row id of a recent trim plan")
    k.add_argument("--why", "--note", dest="why", help="why it stays, kept with the entry")
    k.add_argument("--list", action="store_true", help="list the kept lines and sources")
    k.add_argument("--print", dest="print_line", action="store_true",
                   help="print the .wallekeep line for the item (the team key); walle does not write it")
    k.add_argument("--lang", choices=("en", "ko"))
    u = sub.add_parser("unkeep", help="count a kept line or source again")
    u.add_argument("ref", help="the id of a kept line or source")
    u.add_argument("--lang", choices=("en", "ko"))


def run_cli(args, home: Path) -> int:
    from .strings import resolve_lang, t
    lang = resolve_lang(args.lang)
    path = path_for(args.out)
    if args.cmd == "unkeep":
        hit = remove(path, args.ref)
        if not hit:
            print(t("keep.not_kept", lang, id=args.ref), file=sys.stderr)
            return 2
        print(t("keep.unkept", lang, id=args.ref, where=where(hit, home)))
        return 0
    if args.list:
        entries = load(path)["entries"]
        if not entries:
            print(t("keep.list_empty", lang))
            return 0
        for e in entries:
            note = f"  {e['note']}" if e.get("note") else ""
            print(f"{e['id']}  {e.get('label') or e.get('source') or '-'}  {where(e, home)}  {e.get('date') or ''}{note}")
        print(t("keep.list_count", lang, n=len(entries), path=path))
        return 0
    if not args.ref:
        print(t("keep.need_ref", lang), file=sys.stderr)
        return 2
    it, row, err = resolve(args.out, args.ref, Path.cwd().resolve())
    if err:
        print(f"walle keep: {t(err, lang, ref=args.ref)}", file=sys.stderr)
        return 2
    if row and row.get("item"):
        it = row["item"]  # an instruction row of a plan: the item it came from
    if args.print_line:
        if not it:
            print(t("keep.source_no_team", lang, id=row["id"]), file=sys.stderr)
            return 2
        if not it.get("team_key"):
            print(t("keep.no_team_key", lang, id=it["id"]), file=sys.stderr)
            return 2
        from .collect import find_checkout
        first = it["locations"][0] if it.get("locations") else {}
        f = Path(first.get("file") or "-")
        checkout = find_checkout(f.parent)[0]
        rel = f.relative_to(checkout) if checkout and checkout in f.parents else f.name
        why = f" {args.why}" if args.why else ""
        print(f"{it['team_key']}  # {it.get('rule')} {rel}:{first.get('line')}{why}")
        return 0
    entry = item_entry(it, args.why) if it else source_entry(row, args.why)
    new = add(path, entry)
    if not new:
        print(t("keep.already", lang, id=entry["id"], where=where(entry, home)))
    elif entry["kind"] == "item":
        print(t("keep.kept", lang, id=entry["id"], where=where(entry, home)))
    else:
        print(t("keep.kept_source", lang, id=entry["id"], where=where(entry, home)))
    return 0
