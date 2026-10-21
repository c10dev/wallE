"""Before and after a change: the parts that walle eve --around and walle context --compare share (plan C6).

A split point is an apply id (walle apply) or a date. The windows around it:
- before: from DAYS before the split, or from the previous apply or undo if it is later, to the split;
- after: from the split to DAYS after it, or to the next apply or undo if it is sooner.
So one window holds the sessions of one state of the settings.

Measured values follow brief 5.5. Each side of one model family gets n, the median and p25-p75
(statistics.quantiles, method "inclusive"). A side with fewer than MIN_SHOW sessions has no values at all.
A side with MIN_SHOW to MIN_COMPARE - 1 sessions has values marked "few sessions". The change is judged
only when both sides have MIN_COMPARE sessions or more: "within noise" when the two p25-p75 intervals
overlap, else "lower" or "higher" by the difference of the medians. The record also holds the brief's
half-IQR rule (the difference is under half the before side's IQR) for comparison.

Everything here is a pure function of the sessions and the split, so the same transcripts give the same
result. These values never enter the instruction-stack verdict, Start here or the alarm (brief 2.1, L4).
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import runs

MIN_SHOW = 5
MIN_COMPARE = 10
DAYS = 30
NOISE_RULE = "iqr_overlap"
CHANGE_KINDS = ("apply", "undo")
FAMILY_RE = re.compile(r"^claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$")
DATE_SUFFIX = re.compile(r"-\d{8}$")
RUN_ID = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")


class SplitError(Exception):
    def __init__(self, key: str, **fields):
        super().__init__(key)
        self.key, self.fields = key, fields


# ---------------------------------------------------------------------- time
def parse_when(text: str | None) -> datetime | None:
    """An ISO date or time as an aware datetime. A date or a time without a zone is local time."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone() if dt.tzinfo is None else dt


def utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local(dt: datetime | str | None) -> str:
    """A time as the user reads it: local, to the minute."""
    if isinstance(dt, str):
        dt = parse_when(dt)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else "-"


def session_time(start: str) -> datetime | None:
    return parse_when(start)


# ---------------------------------------------------------------------- model family
def family(model: str) -> str:
    """The model family of a session, with its version: claude-opus-5-5 -> "opus 5.5".

    Two versions of one family can differ in tokenizer and system prompt, so each version is its own
    family here. A date suffix is not part of it. Other models keep their name.
    """
    m = FAMILY_RE.match(model or "")
    if m:
        return f"{m.group(1)} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")
    return DATE_SUFFIX.sub("", model) if model else "unknown"


# ---------------------------------------------------------------------- split point
def _change_rows(out: Path) -> list[dict]:
    return [r for r in runs.read_index(out) if r.get("kind") in CHANGE_KINDS and parse_when(r.get("generated"))]


def find_apply(out: Path, ref: str) -> dict | None:
    """The index line of an apply, else the record that walle apply left in the backups folder."""
    row = next((r for r in runs.read_index(out) if r.get("kind") == "apply" and r.get("id") == ref), None)
    if row:
        return row
    try:
        rec = json.loads((out.parent / "backups" / ref / "undo.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("id") != ref or not rec.get("created"):
        return None
    return {"id": ref, "kind": "apply", "parent_id": (rec.get("plan") or {}).get("id"), "generated": rec["created"],
            "rows": [r.get("id") for r in rec.get("rows") or [] if r.get("changed")], "status": rec.get("status")}


def split_point(out: Path, ref: str, days: int = DAYS) -> dict:
    """The split point of an apply id or a date, and the two windows around it."""
    apply_row = find_apply(out, ref)
    if apply_row:
        at = parse_when(apply_row["generated"])
        kind = "apply"
    else:
        if RUN_ID.match(ref):  # it reads as a time too, but a run id that is not an apply is a typo
            raise SplitError("measure.not_apply", ref=ref, path=str(out / runs.INDEX))
        at = parse_when(ref)
        kind = "time"
        if at is None:
            raise SplitError("measure.bad_split", ref=ref)
    lo, hi = at - timedelta(days=days), at + timedelta(days=days)
    prev = nxt = None
    for r in _change_rows(out):
        if apply_row and r.get("id") == apply_row["id"]:
            continue
        when = parse_when(r["generated"])
        if when <= at and when > lo:
            lo, prev = when, r
        elif when > at and when < hi:
            hi, nxt = when, r
    return {
        "ref": ref, "kind": kind, "at": utc(at),
        "apply": ({k: apply_row.get(k) for k in ("id", "parent_id", "generated", "rows", "status")}
                  if apply_row else None),
        "days": days,
        "windows": {"before": {"from": utc(lo), "to": utc(at)}, "after": {"from": utc(at), "to": utc(hi)}},
        "bounded_by": {"before": _bound(prev), "after": _bound(nxt)},
    }


def _bound(r: dict | None) -> dict | None:
    return {"id": r.get("id"), "kind": r.get("kind"), "generated": r.get("generated")} if r else None


def side_of(start: str, split: dict) -> str | None:
    """before, after, or None for a session outside both windows."""
    t = session_time(start)
    if t is None:
        return None
    w = split["windows"]
    if parse_when(w["before"]["from"]) <= t < parse_when(w["before"]["to"]):
        return "before"
    if parse_when(w["after"]["from"]) <= t < parse_when(w["after"]["to"]):
        return "after"
    return None


# ---------------------------------------------------------------------- statistics
def _half_up(x: float, digits: int = 0):
    q = 10 ** digits
    v = int(abs(x) * q + 0.5) / q
    v = -v if x < 0 else v
    return int(v) if digits == 0 else v


def stats(xs: list, digits: int = 0) -> dict:
    """n, median, p25 and p75 of the values. No values when n < MIN_SHOW (brief 5.5)."""
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    out = {"n": n, "median": None, "p25": None, "p75": None, "few": MIN_SHOW <= n < MIN_COMPARE}
    if n < MIN_SHOW:
        return out
    q = statistics.quantiles(xs, n=4, method="inclusive")
    out.update(median=_half_up(statistics.median(xs), digits), p25=_half_up(q[0], digits), p75=_half_up(q[2], digits))
    return out


def change(before: dict, after: dict) -> dict:
    """The change from before to after of one value, by the n rule and the noise rule."""
    need = max(0, MIN_COMPARE - before["n"]) + max(0, MIN_COMPARE - after["n"])
    if before["n"] < MIN_COMPARE or after["n"] < MIN_COMPARE or before["median"] is None or after["median"] is None:
        return {"state": "too_few", "need_before": max(0, MIN_COMPARE - before["n"]),
                "need_after": max(0, MIN_COMPARE - after["n"]), "need": need}
    delta = after["median"] - before["median"]
    if isinstance(delta, float):
        delta = round(delta, 1)
    overlap = not (after["p75"] < before["p25"] or after["p25"] > before["p75"])
    iqr = before["p75"] - before["p25"]
    state = "within_noise" if overlap else ("lower" if delta < 0 else "higher")
    half = "within_noise" if abs(delta) < iqr / 2 else ("lower" if delta < 0 else "higher")
    return {"state": state, "delta": delta, "overlap": overlap, "half_iqr": half, "before_iqr": iqr}


def mix(values: list[str]) -> dict[str, int]:
    """Counts of each value, largest first, then by name: the same input gives the same order."""
    return dict(sorted(Counter(values).items(), key=lambda kv: (-kv[1], kv[0])))
