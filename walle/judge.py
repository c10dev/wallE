"""Judge: let the user's coding agent decide whether conflict and cost candidates are real.

Static rules find candidates by shape: conflict pairs (drift: similar rules
that changed; topic and protocol: rules that share a term or a message format,
see conflict.py) and cause-1 directives ("always", "every task", "read X
first"). They do not read meaning. The agent does, with a short rubric.

walle itself makes no network call and needs no API key:
1. `walle judge` writes the candidates that are not cached into batch files
   next to scan.json. Each batch file holds the rubric, the items and the
   path of its answer file.
2. The agent (or one subagent per batch) reads a batch file and writes the
   verdict JSON to the answer file.
3. `walle judge --import` checks the answers, caches them and writes judge.md.

Judgments are cached in ~/.walle/cache/judge.json by a hash of the item text
and the rubric. Line numbers are not in the hash, so the next export only
holds rules whose text changed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .collect import HOME
from .report import short
from .segment import segment
from .stack import estimate_tokens

BATCH = 20
MAX_TEXT = 1500  # longest rule text sent for one side of an item
CONTEXT_CHARS = 600  # a heading or "...:" lead-in is sent with the start of its section
CACHE_FILE = HOME / ".walle" / "cache" / "judge.json"
CACHE_VERSION = 3
JUDGE_DIR = "judge"
KEEP = ("a", "b", "both", "neither", "ask")
# Bump when the meaning of the rubric changes. Wording fixes keep the cache.
RUBRIC_VERSION = 3

RUBRIC = """You check rules from instruction files that coding agents load (CLAUDE.md, AGENTS.md, skills, memory).
Each item has a type. "where" gives file:line for each side, so you can find the line.
Judge from this batch file. Open other files only when "depth" is "deep".

"loaded" says when the rules reach an agent together: "session_start" (both load when a session starts), "on_demand" (one side is a skill, command or agent body that loads when it runs, in a session that has the other side), "never" (no session loads both), or "unmodeled" (a Cursor, Copilot, Gemini, Windsurf or Cline rule file whose load rule walle does not model; read it as "session_start").

type "conflict": rules A and B can cover the same case. "pair" says why they were paired.
"drift": the rules look alike. "topic": both load in one session and share the terms in "shared", and one says "do not" or writes a message in another form. "protocol": rules from two repositories write the message in "shared" in different forms. Topic and protocol rules can look different, so read both in full.
- real: true when an agent cannot follow both in one situation, because they give a different value, step, order, message form or permission for the same case. false when they differ only in wording, cover different cases, or each one names its own folder, role or agent.
- When "loaded" is "never", real is true only when one copy is out of date against the other.
- keep: "a" or "b" for the rule to keep, "both" when both stay (always "both" when real is false), "neither" when one new rule must replace both, "ask" when the user must choose (for example which of two names to keep).

type "cost": one directive that a word such as "always", "every task" or "read X first" marked.
- real: true when it adds steps (reads, runs, checks, messages) to tasks that it does not help. false when it is a safety limit, applies only to the tasks it names, or adds no step.
- A "loaded" value of "on_demand" means the file loads only when its skill or command runs. Its cost is lower.
- keep: "a" to keep it where it is, "neither" to move it to an on-demand file or to delete it, "ask" when the user must choose.

fix: one line of at most 25 words that says what to change. Write "-" when real is false.
why: at most 15 words.
Write fix and why in the language of the rule.
Give one verdict for each id.
Write only a JSON object of the form {"verdicts": [{"id", "real", "keep", "fix", "why"}, ...]} to the answer file. Do not edit any instruction file."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "real": {"type": "boolean"},
                    "keep": {"type": "string", "enum": list(KEEP)},
                    "fix": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["id", "real", "keep", "fix", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


class JudgeError(Exception):
    pass


def latest_scan(runs: Path, with_export: bool = False) -> Path | None:
    """The newest run folder that has a scan.json (and, with with_export, a judge export).

    `latest` can point at an eve run, so it is skipped.
    """
    for d in sorted((d for d in runs.glob("*") if d.name != "latest"), reverse=True):
        if (d / "scan.json").is_file() and (not with_export or (d / JUDGE_DIR / "manifest.json").is_file()):
            return d / "scan.json"
    return None


def _section(units: list, i: int) -> str:
    u = units[i]
    body = u.text
    if u.kind == "heading" or body.rstrip().endswith(":"):
        for v in units[i + 1:]:
            if v.kind == "heading" or len(body) >= CONTEXT_CHARS:
                break
            body += "\n" + v.text
        body = body[:CONTEXT_CHARS]
    return body[:MAX_TEXT]


def _unit_at(units_by_file: dict, file: str, line: int, text: str) -> tuple[str, str, bool]:
    """Return (heading, text, found) for the unit that scan.json saw at file:line.

    scan.json keeps only the first 200-240 characters of a unit, so read the
    whole unit again. A heading or a lead-in that ends with ":" gets the start
    of its section, so the judge sees what the rule covers. When lines moved
    since the scan, the same text is found at its new line, so the item (and
    its cache key) stays the same. When the text itself changed, fall back to
    the scan text (found is False).
    """
    if file not in units_by_file:
        try:
            units_by_file[file] = segment(Path(file).read_text(errors="replace"), file)
        except OSError:
            units_by_file[file] = []
    units = units_by_file[file]
    hits = [i for i, u in enumerate(units) if u.text.startswith(text)]
    if not hits:
        return "", text, False
    i = min(hits, key=lambda i: abs(units[i].line - line))
    return units[i].heading, _section(units, i), True


CONFLICT_PAIRS = ("drift", "topic", "protocol")
ON_DEMAND_KINDS = ("skill", "agent", "command")


def _scope(path: str, home: Path = HOME) -> tuple[str, str | None]:
    """(agent, folder) where an on-demand file is visible. Folder None means every folder."""
    for agent, base in (("claude", home / ".claude"), ("codex", home / ".codex")):
        if path.startswith(str(base) + "/"):
            return agent, None
    for agent, marker in (("claude", "/.claude/"), ("codex", "/.codex/")):
        if marker in path:
            return agent, path.split(marker)[0]
    return "claude", None


def select_items(scan: dict, home: Path = HOME) -> list[dict]:
    """Conflict pairs (cause 2 candidates) and cause-1 candidate flags from a scan.

    `ask` is what the judge sees and what the cache key hashes. It has no line
    numbers, so a rule that only moved keeps its judgment.
    """
    kinds = {f["path"]: f for f in scan.get("files", [])}
    stacks = [(s.get("agent", "claude"), s.get("folder", ""), {l["path"] for l in s["layers"] if l["path"]})
              for s in scan.get("stacks", [])]

    def kind(path: str) -> str:
        return kinds.get(path, {}).get("kind", "")

    def reaches(path: str, agent: str, folder: str, layers: set) -> bool:
        if path in layers:
            return True
        if kind(path) not in ON_DEMAND_KINDS:
            return False
        a, root = _scope(path, home)
        return a == agent and (root is None or folder == root or folder.startswith(root + "/"))

    def loaded(a: str, b: str) -> str:
        if any(a in layers and b in layers for _ag, _f, layers in stacks):
            return "session_start"
        if kind(a) == "other_agent" or kind(b) == "other_agent":
            return "unmodeled"
        if any(reaches(a, *st) and reaches(b, *st) for st in stacks):
            return "on_demand"
        return "never"

    def loaded_one(path: str) -> str:
        if kind(path) == "other_agent":
            return "unmodeled"
        if kinds.get(path, {}).get("loaded_in_stacks"):
            return "session_start"
        if kind(path) in ON_DEMAND_KINDS:
            return "on_demand"
        return "never"

    units: dict[str, list] = {}
    items = []
    seen = set()
    for p in scan.get("pairs", []):
        a, b = p["a"], p["b"]
        refs = [f"{a['file']}:{a['line']}", f"{b['file']}:{b['line']}"]
        if p["kind"] not in CONFLICT_PAIRS or tuple(sorted(refs)) in seen:
            continue
        seen.add(tuple(sorted(refs)))
        ha, ta, fa = _unit_at(units, a["file"], a["line"], a["text"])
        hb, tb, fb = _unit_at(units, b["file"], b["line"], b["text"])
        ask = {
            "type": "conflict",
            "pair": p["kind"],
            "loaded": loaded(a["file"], b["file"]),
            "a": {"file": short(a["file"]), "heading": ha, "text": ta},
            "b": {"file": short(b["file"]), "heading": hb, "text": tb},
        }
        if p.get("shared"):
            ask["shared"] = p["shared"]
        items.append({
            "id": f"{p['kind']}:{a['file']}:{a['line']}|{b['file']}:{b['line']}",
            "type": "conflict",
            "pair": p["kind"],
            "refs": refs,
            "score": p["score"],
            "stale": not (fa and fb),
            "ask": ask,
        })
    for fl in scan.get("flags", []):
        if fl["cause"] != 1 or not fl.get("candidate"):
            continue
        heading, text, found = _unit_at(units, fl["file"], fl["line"], fl["text"])
        items.append({
            "id": f"cost:{fl['file']}:{fl['line']}",
            "type": "cost",
            "refs": [f"{fl['file']}:{fl['line']}"],
            "match": fl["match"],
            "stale": not found,
            "ask": {
                "type": "cost",
                "loaded": loaded_one(fl["file"]),
                "file": short(fl["file"]),
                "file_kind": kind(fl["file"]),
                "heading": heading or fl.get("heading", ""),
                "text": text,
            },
        })
    return items


def item_key(ask: dict) -> str:
    raw = json.dumps([RUBRIC_VERSION, ask], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def load_cache(path: Path) -> dict:
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"_v": CACHE_VERSION}
    return cache if cache.get("_v") == CACHE_VERSION else {"_v": CACHE_VERSION}


def _needs_judging(cache: dict, key: str, depth: str) -> bool:
    v = cache.get(key)
    # A deep export also re-judges what a quick run judged.
    return v is None or (depth == "deep" and v.get("depth") != "deep")


def _batch_doc(batch: list[dict], answer_file: Path, depth: str) -> dict:
    # "where" has line numbers; it is outside "ask", so it does not change the cache key.
    return {
        "rubric": RUBRIC,
        "schema": SCHEMA,
        "depth": depth,
        "answer_file": str(answer_file),
        "items": [{"id": str(i), "where": [short(r) for r in it["refs"]], **it["ask"]}
                  for i, it in enumerate(batch, 1)],
    }


def _prepare(scan_path: Path, batch_size: int, cache_file: Path, depth: str = "quick"):
    scan = json.loads(scan_path.read_text())
    items = select_items(scan)
    cache = load_cache(cache_file)
    todo, keys = [], set()
    for it in items:
        it["key"] = item_key(it["ask"])
        # Two rows with the same text and context are one question.
        if _needs_judging(cache, it["key"], depth) and it["key"] not in keys:
            keys.add(it["key"])
            todo.append(it)
    size = max(1, batch_size)
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    plan = {
        "items": len(items),
        "conflict": sum(1 for it in items if it["type"] == "conflict"),
        "by_pair": {k: sum(1 for it in items if it.get("pair") == k) for k in CONFLICT_PAIRS},
        "cost": sum(1 for it in items if it["type"] == "cost"),
        "cached": sum(1 for it in items if it["key"] not in keys),
        "to_judge": sum(1 for it in items if it["key"] in keys),
        "questions": len(todo),
        "batches": len(batches),
        "input_tokens_est": sum(estimate_tokens(json.dumps(_batch_doc(b, Path("x"), depth), ensure_ascii=False))
                                for b in batches),
        "depth": depth,
    }
    head = {"generated": datetime.now().isoformat(timespec="seconds"), "scan": str(scan_path),
            "scan_generated": scan.get("generated"), "plan": plan}
    return scan, items, cache, batches, head


def _rows(items: list[dict], cache: dict, judged_now: set) -> list[dict]:
    rows = []
    for it in items:
        v = cache.get(it["key"])
        rows.append({
            "id": it["id"], "type": it["type"], "pair": it.get("pair"), "refs": it["refs"], "stale": it["stale"],
            "shared": it["ask"].get("shared"),
            "score": it.get("score"), "match": it.get("match"),
            "texts": [it["ask"]["a"]["text"], it["ask"]["b"]["text"]] if it["type"] == "conflict" else [it["ask"]["text"]],
            "loaded": it["ask"].get("loaded"),
            "verdict": {k: v[k] for k in ("real", "keep", "fix", "why")} if v else None,
            "judge": v.get("judge") if v else None,
            "depth": v.get("depth") if v else None,
            "from_cache": bool(v) and it["key"] not in judged_now,
        })
    return rows


def _counts(rows: list[dict], items: list[dict], judged_now: set) -> dict:
    return {"judged_now": sum(1 for it in items if it["key"] in judged_now),
            "cached": sum(1 for r in rows if r["from_cache"]),
            "to_judge": sum(1 for r in rows if not r["verdict"])}


def plan(scan_path: Path, batch_size: int = BATCH, cache_file: Path | None = None, depth: str = "quick") -> dict:
    """What an export would hold. Writes nothing."""
    _scan, items, cache, _b, head = _prepare(scan_path, batch_size, cache_file or CACHE_FILE, depth)
    return {**head, "items": [
        {"id": it["id"], "type": it["type"], "pair": it.get("pair"), "refs": it["refs"],
         "shared": it["ask"].get("shared"), "match": it.get("match"),
         "cached": not _needs_judging(cache, it["key"], depth)}
        for it in items]}


def _new_export_dir(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d, n = root / stamp, 2
    while d.exists():
        d, n = root / f"{stamp}-{n}", n + 1
    return d


def export(scan_path: Path, batch_size: int = BATCH, cache_file: Path | None = None, depth: str = "quick") -> dict:
    """Write one batch file per batch of questions into a new export folder.

    Each export gets its own folder, so a second export never deletes answers
    that were written but not yet imported, and a late answer can only land in
    the export it belongs to. judge/manifest.json names the current export.
    """
    _scan, items, cache, batches, head = _prepare(scan_path, batch_size, cache_file or CACHE_FILE, depth)
    root = scan_path.parent / JUDGE_DIR
    files = []
    if batches:
        folder = _new_export_dir(root)
        folder.mkdir(parents=True)
        for n, batch in enumerate(batches, 1):
            doc = _batch_doc(batch, folder / f"batch-{n:03d}.answer.json", depth)
            path = folder / f"batch-{n:03d}.json"
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
            files.append({"batch": str(path), "answer": doc["answer_file"],
                          "keys": {str(i): it["key"] for i, it in enumerate(batch, 1)}})
        manifest = {**head, "export": folder.name, "depth": depth, "files": files}
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return {**head, "files": files, "errors": [], "items": _rows(items, cache, set())}


def parse_answer(text: str) -> dict[str, dict]:
    """Verdicts by batch id from an answer file. A verdict with a missing or bad field is dropped."""
    try:
        data = json.loads(text)
    except ValueError:
        # Tolerate prose or a code fence around the object.
        i, j = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[i:j + 1]) if 0 <= i < j else None
        except ValueError:
            data = None
    if isinstance(data, list):
        data = {"verdicts": data}
    if not isinstance(data, dict) or not isinstance(data.get("verdicts"), list):
        raise JudgeError("the answer is not the verdict JSON")
    out = {}
    for v in data["verdicts"]:
        if (isinstance(v, dict) and isinstance(v.get("real"), bool) and v.get("keep") in KEEP
                and isinstance(v.get("fix"), str)):
            out[str(v.get("id"))] = {"real": v["real"], "keep": v["keep"],
                                     "fix": v["fix"].strip(), "why": str(v.get("why", "")).strip()}
    return out


def import_answers(scan_path: Path, cache_file: Path | None = None, judge_name: str = "agent") -> dict:
    """Read the answer files of every export of this scan, cache valid verdicts, and return the report data.

    Answers of an older export still count: their keys are in that export's
    manifest. Errors list only what the current export still lacks.
    """
    cache_file = cache_file or CACHE_FILE
    _scan, items, cache, _b, head = _prepare(scan_path, BATCH, cache_file)
    root = scan_path.parent / JUDGE_DIR
    current_path = root / "manifest.json"
    if not current_path.is_file():
        raise JudgeError(f"no export for {scan_path}. Run `walle judge` first.")
    current = json.loads(current_path.read_text())
    exports = sorted(root.glob("*/manifest.json"))
    judged_now: set[str] = set()
    answered = 0
    errors: list[str] = []
    stamp = datetime.now().isoformat(timespec="seconds")
    for mpath in exports:
        manifest = json.loads(mpath.read_text())
        depth = manifest.get("depth", "quick")
        for f in manifest.get("files", []):
            answer = Path(f["answer"])
            if not answer.is_file():
                continue
            try:
                verdicts = parse_answer(answer.read_text(errors="replace"))
            except JudgeError as e:
                if manifest.get("export") == current.get("export"):
                    errors.append(f"{answer.name}: {e}")
                continue
            answered += 1
            for i, key in f["keys"].items():
                if i in verdicts:
                    cache[key] = {**verdicts[i], "judge": judge_name, "depth": depth, "at": stamp}
                    judged_now.add(key)
    if judged_now:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, ensure_ascii=False))
    # Report only what the current export still lacks: a re-answered batch may cover only its missing ids.
    for f in current.get("files", []):
        open_ids = [i for i, key in f["keys"].items() if key not in cache]
        if not open_ids:
            continue
        name = Path(f["answer"]).name
        errors.append(f"no answer yet: {name}" if not Path(f["answer"]).is_file()
                      else f"{name}: no valid verdict for id {', '.join(open_ids)}")
    rows = _rows(items, cache, judged_now)
    head["plan"] = {**head["plan"], "answered_files": answered, **_counts(rows, items, judged_now)}
    return {**head, "files": current.get("files", []), "errors": errors, "items": rows}


def describe_plan(data: dict) -> str:
    p = data["plan"]
    by = ", ".join(f"{k} {n}" for k, n in p["by_pair"].items())
    lines = [
        f"walle judge --dry-run: {p['items']} items from {data['scan']}",
        f"  conflict candidates: {p['conflict']} ({by})",
        f"  cost candidates (cause 1): {p['cost']}",
        f"  cached: {p['cached']}, to judge: {p['to_judge']} as {p['questions']} questions in {p['batches']} batches",
        f"  estimated tokens to read the batch files: ~{p['input_tokens_est']} ({p['depth']}; "
        "judging adds output, and deep mode adds file reads)",
        "Items (* = cached):",
    ]
    for it in data.get("items", []):
        kind = it["pair"] if it["type"] == "conflict" else "cost"
        note = ", ".join(it["shared"]) if it.get("shared") else (it.get("match") or "")
        lines.append(f"  {'*' if it['cached'] else ' '} {kind:<8} {' <> '.join(short(r) for r in it['refs'])}"
                     + (f"  [{note}]" if note else ""))
    lines.append("Nothing was written.")
    return "\n".join(lines)


def describe_export(data: dict) -> str:
    p = data["plan"]
    lines = [f"walle judge: {p['questions']} questions in {p['batches']} batch files "
             f"(~{p['input_tokens_est']} est. tokens to read, {p['depth']}); {p['cached']} items cached."]
    for f in data["files"]:
        lines.append(f"  {f['batch']} -> {f['answer']}")
    lines.append("Next: for each batch file, follow its rubric and write the verdict JSON to its answer file. "
                 "Then run: walle judge --import")
    return "\n".join(lines)
