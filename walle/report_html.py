"""Render a scan as one self-contained HTML page (design direction A).

The page is report.html with three parts filled in: the fonts (IBM Plex, Latin
subsets, base64 woff2), the page data built here from scan.json schema 2, and the
title. The page makes no network requests.
"""

from __future__ import annotations

import base64
import glob
import hashlib
import html
import json
import os
import re
from collections import Counter
from datetime import datetime
from importlib import resources
from pathlib import Path

from . import eve, strings
from .collect import HOME
from .triage import BUDGET

FONTS = (
    ("IBM Plex Sans", "400 600", "IBMPlexSans-Latin.woff2"),
    ("IBM Plex Mono", "400", "IBMPlexMono-Latin-400.woff2"),
    ("IBM Plex Mono", "600", "IBMPlexMono-Latin-600.woff2"),
)

COUNT_NAME = {
    "every_task_step": "every-task step", "conflict": "conflict", "repeated": "loaded twice",
    "loaded_twice": "loaded twice", "model_habit": "dated", "dated": "dated", "dated_lines": "dated", "memory_lines": "dated", "model_name": "dated",
    "vague": "vague", "stacked_emphasis": "vague", "large_layer": "large layer",
}
SEV = {"conflict": 1, "term_clash": 5, "loaded_twice": 4, "similar_twice": 4, "model_habit": 6, "every_task_step": 2, "large_layer": 3, "repeated": 4, "repeated_maybe": 4,
       "dated": 6, "dated_lines": 6, "memory_lines": 6, "model_name": 6, "vague": 7, "stacked_emphasis": 7}
PAIR_LABELS = ("conflict", "term_clash", "repeated_maybe", "similar_twice", "drifted_copy", "different_agents",
               "format_differs", "copied_between", "repeated", "loaded_twice")
PAIR_IMPACT = {
    "conflict": "2 versions load", "term_clash": "2 versions load", "repeated_maybe": "similar lines",
    "similar_twice": "similar lines", "drifted_copy": "never load together", "different_agents": "claude and codex",
    "format_differs": "two formats", "copied_between": "copy in another folder",
    "repeated": "similar lines", "loaded_twice": "similar lines",
}
SINGLE_IMPACT = {
    "every_task_step": "every task", "dated": "may be out of date", "model_name": "may be out of date",
    "model_habit": "may be out of date", "vague": "no concrete pattern", "stacked_emphasis": "no concrete pattern",
}


def tilde(p: str | None, home: str) -> str | None:
    if p is None:
        return None
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


def fmt(n: int | None, budget: int) -> str:
    """Estimated tokens as the brief writes them (4.4): ~ prefix, rounded."""
    return "-" if n is None else strings.approx(n, budget)


def has_ko(s: str | None) -> bool:
    return bool(re.search(r"[가-힣]", s or ""))


def plural(n: int, word: str) -> str:
    return f"{n} {word if n == 1 else word + 's'}"


def _sha1(path: str) -> str | None:
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


class Builder:
    def __init__(self, scan: dict, home: str, runs: list[dict], eve_run: tuple[str, dict] | None, lang: str = "en"):
        self.scan = scan
        self.lang = lang
        self.home = home
        self.B = scan["triage"]["budget"]
        self.roots = sorted(scan.get("roots") or scan["run"]["roots"], key=len, reverse=True)
        self.files = {f["path"]: f for f in scan["files"]}
        self.repos = sorted((scan["run"].get("git_heads") or {}).keys(), key=len, reverse=True)
        self.stack_tokens = {k: v["tokens_est"] for k, v in scan["triage"]["stacks"].items()}
        self.runs = runs
        self.eve_run = eve_run
        # A file counts as changed only when it can be read and its hash differs.
        self.changed = []
        for f in scan["files"]:
            sha = _sha1(f["path"])
            if sha is not None and f.get("sha") and sha != f["sha"]:
                self.changed.append(f["path"])

    # ---------------------------------------------------------------- names
    def t(self, p):
        return tilde(p, self.home)

    def f(self, n):
        return fmt(n, self.B)

    def short_of(self, folder: str) -> str:
        for r in self.roots:
            if folder == r:
                return self.t(r)
            if folder.startswith(r + "/"):
                return folder[len(r) + 1:]
        return self.t(folder)

    def key_of(self, stack_key: str) -> str:
        folder, agent = stack_key.rsplit("@", 1)
        return f"{self.short_of(folder)}@{agent}"

    def repo_of(self, folder: str) -> str | None:
        for r in self.repos:
            if folder == r or folder.startswith(r + "/"):
                return r
        return None

    def rel_to(self, path: str | None, folder: str) -> str | None:
        if path is None:
            return None
        if path.startswith(folder + "/"):
            return "./" + path[len(folder) + 1:]
        parent = os.path.dirname(folder)
        if path.startswith(parent + "/") and any(parent == r or parent.startswith(r + "/") for r in self.roots):
            return "../" + path[len(parent) + 1:]
        return self.t(path)

    # ---------------------------------------------------------------- items
    def loc_of(self, L: dict, folder: str) -> dict:
        f = L["file"]
        return {
            "file": self.t(f), "abs": f, "base": os.path.basename(f),
            "line": L.get("line"), "end": L.get("end_line"),
            "parent": self.t(os.path.dirname(f)) + "/", "rel": self.rel_to(f, folder),
        }

    @staticmethod
    def lines_of(L: dict, cap: int | None = None) -> list:
        out = [[L["line"] + k, t] for k, t in enumerate((L.get("text") or "").split("\n"))]
        return out[:cap] if cap and len(out) > cap else out

    def pair_terms(self, locs: list) -> str:
        """The shared terms of the engine pair behind a two-location item, for the reason text."""
        want = {(L["file"], L["line"]) for L in locs[:2]}
        for p in self.scan.get("pairs", []):
            if {(p["a"]["file"], p["a"]["line"]), (p["b"]["file"], p["b"]["line"])} == want and p.get("shared"):
                return ", ".join(p["shared"][:3])
        return "the same term"

    def item_view(self, it: dict) -> dict:
        f, t, lang = self.f, self.t, self.lang
        lab = it["label"]
        stacks = it["stacks"]
        first = stacks[0].rsplit("@", 1)[0] if stacks else self.roots[0]
        locs = it["locations"]
        L0 = locs[0]
        v = {
            "id": it["id"], "rule": it["rule"], "kind_raw": lab, "verb": it["verb"],
            "label": strings.label(it, lang, short=t), "cert": it["certainty"], "own": it["ownership"],
            "scope": it["scope"], "loc": self.loc_of(L0, first), "tokens": it["tokens_est"],
            "loaded": it["loaded_in_stacks"], "also": [self.label_of(x) for x in (it.get("also") or [])],
            "sev": SEV.get(lab, 9), "blocks": [], "group": None, "impact": "", "reason": "",
        }
        match = it.get("match")
        marks = [m for m in [match] + [L.get("match") for L in locs] if m]
        text_lang = "ko" if any(has_ko(L.get("text")) for L in locs) else "en"
        roles = {L.get("role"): L for L in locs if L.get("role")}

        if lab == "large_layer":
            mv = it.get("move") or {}
            st, en = mv.get("line_start", L0["line"]), mv.get("line_end", L0.get("end_line"))
            tok = mv.get("tokens_est", it["tokens_est"])
            stk = self.stack_tokens.get(stacks[0], 0) if stacks else 0
            share = round(100 * tok / stk) if stk else 0
            v["move"] = {"start": st, "end": en, "tokens": tok, "heading": mv.get("heading")}
            v["impact"] = f"{f(tok)} est. tokens" + (f" · {share}% of stack" if stk else "")
            v["blocks"] = [{"head": None, "lines": self.lines_of(L0), "marks": [], "lang": text_lang}]
            if mv.get("heading"):
                v["reason"] = strings.t("reason.large_layer", lang, heading=mv["heading"], start=st, end=en,
                                        tokens=strings.round_tokens(tok, self.B))
            else:
                v["reason"] = strings.t("reason.large_layer.file", lang, file=t(L0["file"]),
                                        tokens=strings.round_tokens(tok, self.B), share=share)
        elif lab in ("repeated", "loaded_twice") and "delete" in roles and "keep" in roles:
            dl, kl = roles["delete"], roles["keep"]
            v["loc"], v["loc_b"] = self.loc_of(dl, first), self.loc_of(kl, first)
            v["blocks"] = [
                {"head": f"delete · {os.path.basename(dl['file'])}:{dl['line']}", "lines": self.lines_of(dl), "marks": [], "lang": text_lang},
                {"head": f"keep · {os.path.basename(kl['file'])}:{kl['line']}", "lines": self.lines_of(kl), "marks": [], "lang": text_lang},
            ]
            v["impact"] = f"-{f(it['tokens_est'])} est. tokens"
            v["reason"] = (strings.t("reason.repeated", lang, line=kl["line"]) if lab == "repeated"
                           else strings.t("reason.loaded_twice", lang, file=os.path.basename(kl["file"]), line=kl["line"]))
        elif lab in PAIR_LABELS and len(locs) >= 2:
            A, Bl = locs[0], locs[1]
            v["loc_b"] = self.loc_of(Bl, first)
            diff = lab not in ("copied_between", "loaded_twice", "repeated")
            v["blocks"] = [
                {"head": "A", "where": {"file": t(A["file"]), "line": A["line"]}, "lines": self.lines_of(A),
                 "marks": [], "lang": "ko" if has_ko(A.get("text")) else "en", "diff": diff},
                {"head": "B", "where": {"file": t(Bl["file"]), "line": Bl["line"]}, "lines": self.lines_of(Bl),
                 "marks": [], "lang": "ko" if has_ko(Bl.get("text")) else "en", "diff": diff},
            ]
            v["impact"] = PAIR_IMPACT.get(lab, "")
            agents = sorted({k.rsplit("@", 1)[1] for k in stacks}) or ["claude", "codex"]
            fields = {
                "repeated_maybe": {"line": Bl["line"]},
                "repeated": {"line": Bl["line"]},
                "loaded_twice": {"file": os.path.basename(Bl["file"]), "line": Bl["line"]},
                "term_clash": {"shared": self.pair_terms(locs)},
                "format_differs": {"shared": self.pair_terms(locs)},
                "different_agents": {"agents": ", ".join(agents)},
                "drifted_copy": {"a": t(A["file"]), "b": t(Bl["file"]), "n": it.get("group_size") or 1},
            }.get(lab, {})
            v["reason"] = strings.t(f"reason.{lab}", lang, **fields)
        elif lab in ("source_of", "copy_of"):
            v["note"] = True
            end = L0.get("end_line") or L0["line"]
            n_rules = it.get("group_size") or 1
            v["blocks"] = [{"head": None, "lines": [[L0["line"], strings.t("label.lines", lang, start=L0["line"], end=end, n=n_rules)]],
                            "marks": [], "lang": lang}]
            pairs = (it.get("group") or {}).get("pairs") or n_rules
            v["impact"] = "note"
            key = "reason.source_of" + (".codex" if stacks and all(k.endswith("@codex") for k in stacks) else "")
            v["reason"] = (strings.t(key, lang, file=t(it.get("of") or ""), n=pairs) if lab == "source_of"
                           else strings.t("reason.copy_of", lang, file=t(it.get("of") or ""), n=n_rules))
        elif len(locs) > 1:  # grouped dated / model-name / memory lines
            rows = []
            for k, L in enumerate(locs):
                if k:
                    rows.append([None, "..."])
                rows.extend(self.lines_of(L, cap=3))
            n = it.get("group_size") or len(locs)
            kinds = Counter(L.get("kind") or ("dated" if re.match(r"\d{4}-\d{2}-\d{2}", L.get("match") or "") else "model name")
                            for L in locs)
            summ = ", ".join(f"{c} {k}" for k, c in kinds.items())
            v["group"] = {"n": n, "summary": f"{n} lines: {summ}", "first": locs[0]["line"],
                          "last": locs[-1].get("end_line") or locs[-1]["line"]}
            v["blocks"] = [{"head": None, "lines": rows, "marks": marks, "lang": text_lang}]
            v["impact"] = "may be out of date"
            key = lab if lab in ("dated_lines", "memory_lines") else "dated_lines"
            v["reason"] = strings.t(f"reason.{key}", lang, n=n)
        else:
            v["blocks"] = [{"head": None, "lines": self.lines_of(L0), "marks": marks, "lang": text_lang}]
            v["impact"] = SINGLE_IMPACT.get(lab, "")
            key = f"reason.{lab}"
            if lab == "every_task_step":
                key += ".read" if re.search(r"\b(read|open|check)\b|읽", match or "", re.I) else ".step"
            if key in strings.STRINGS:
                v["reason"] = strings.t(key, lang, match=match or "")
        return v

    def label_of(self, lab: str) -> str:
        try:
            return strings.t(f"label.{lab}", self.lang)
        except (strings.UnknownString, KeyError):
            return lab

    # ---------------------------------------------------------------- page data
    def build(self) -> dict:
        scan, f, t, B = self.scan, self.f, self.t, self.B
        items = {it["id"]: self.item_view(it) for it in scan["items"]}
        run = scan["run"]

        # baseline: the parent run (previous scan of the same roots), if the index has it
        from . import runs as runs_mod
        mine = runs_mod.estimator_of(run)
        by_id = {r.get("id"): r for r in self.runs}
        parent = by_id.get(run.get("parent_id"))
        base_est = runs_mod.estimator_of(parent) if parent else None
        base_tokens = {self.key_of(k): v for k, v in (parent or {}).get("per_folder", {}).items()}

        stacks_by_key = {f"{s['folder']}@{s['agent']}": s for s in scan["stacks"]}
        views, all_rows = {}, []
        for skey, T in scan["triage"]["stacks"].items():
            S = stacks_by_key[skey]
            folder, agent = T["folder"], T["agent"]
            k = self.key_of(skey)
            layers = []
            for L in S["layers"]:
                own = "skills" if L["label"] == "skill_catalog" else self.files.get(L["path"], {}).get("ownership") or "shared"
                layers.append({
                    "label": L["label"], "path": t(L["path"]), "abs": L["path"], "tokens": L["tokens_est"],
                    "bytes": L["bytes"], "own": own, "note": L.get("note", ""),
                    "share": round(100 * L["tokens_est"] / S["tokens_est"]) if S["tokens_est"] else 0,
                    "rel": self.rel_to(L["path"], folder) if L["path"] else "skills",
                })
            sums = Counter()
            for L in layers:
                sums[L["own"]] += L["tokens"]
            yours = [L for L in layers if L["own"] in ("owned", "memory")]
            if T["verdict"] == "OVER BUDGET" and yours:
                big = max(yours, key=lambda L: L["tokens"])
                sentence = (f"{f(T['tokens_est'])} est. tokens load before your first prompt (budget {B:,}). "
                            f"Largest: {big['rel']} {f(big['tokens'])}.")
            else:
                n = T["to_read"]
                tail = "Nothing to read in your files." if n == 0 else f"{plural(n, 'item')} to read in your files."
                sentence = f"{f(T['tokens_est'])} est. tokens load before your first prompt (budget {B:,}). {tail}"
            G = T["groups"]
            rest = [i for i in G["everything_else"] if not items[i].get("note")]
            notes = [i for i in G["everything_else"] if items[i].get("note")]
            counts = Counter()
            for i in T["start_here"] + G["everything_else"]:
                nm = COUNT_NAME.get(items[i]["kind_raw"])
                if nm:
                    counts[nm] += 1
            shared = [{
                "file": t(r["file"]), "findings": len(r["items"]), "loads": r["loads_in"], "tokens": r["tokens_est"],
                "reach": r["reach"], "cmd": "walle scan " + t(os.path.dirname(r["file"])), "items": r["items"],
                "changed": r["file"] in self.changed,
            } for r in G["shared"]]
            copies = []
            for r in G["copies"]:
                c = r.get("counts", {})
                bits = []
                if c.get("drifted_copy"):
                    bits.append(f"{c['drifted_copy']} drifted")
                if c.get("copied_between"):
                    bits.append(f"{c['copied_between']} copied")
                bits += [f"{vv} {self.label_of(kk)}" for kk, vv in c.items() if kk not in ("drifted_copy", "copied_between")]
                hint = (f"both folders load {self.rel_to(r['parent'], folder)}: a shared rule can move there"
                        if r.get("parent") else "")
                copies.append({"a": t(r["file"]), "b": t(r["other"]), "summary": " · ".join(bits), "hint": hint,
                               "items": r["items"]})
            worktree = []
            for w in G["worktree_copies"]:
                full = next((c for c in scan["copies"] if c["repo"] == self.repo_of(folder) and c["file"] == w["file"]), None)
                worktree.append({
                    "file": w["file"], "versions": w["versions"], "copies": w["copies"], "main": (w.get("main_sha") or "")[:7],
                    "variants": [{"sha": x["sha"][:7], "size": x["size"], "count": x["count"], "in_main": x["in_main"],
                                  "example": t(x["example"])} for x in (full["by_variant"] if full else [])],
                })
            layer_paths = {L["abs"] for L in layers if L["abs"]}
            views[k] = {
                "key": k, "folder": t(folder), "short": self.short_of(folder), "agent": agent,
                "repo": t(self.repo_of(folder)), "tokens": T["tokens_est"], "bytes": S["bytes"], "verdict": T["verdict"],
                "sentence": sentence, "layers": layers, "sums": dict(sums), "counts": dict(counts), "n_read": T["to_read"],
                "reasons": T["reasons"], "start": T["start_here"], "rest": rest, "file_copy": notes, "shared": shared,
                "copies": copies, "ondemand": G["on_demand"], "other": G["other_agents"], "worktree": worktree, "kept": [],
                "hidden": scan["triage"].get("hidden", {}).get("elsewhere", 0),
                "changed_here": [t(p) for p in self.changed if p in layer_paths],
            }
            all_rows.append({
                "key": k, "short": self.short_of(folder), "agent": agent, "tokens": T["tokens_est"], "verdict": T["verdict"],
                "repo": t(self.repo_of(folder)), "folder": t(folder),
                "layers": [[L["label"], L["tokens"], L["own"], L["rel"]] for L in layers], "base": base_tokens.get(k),
            })

        # history: scans of the same roots from the run index, plus this run
        want = sorted(run["roots"])
        history = []
        for r in self.runs:
            if r.get("kind") != "scan" or sorted(r.get("roots") or []) != want:
                continue
            history.append({"id": r["id"], "label": r.get("label"), "rules": r.get("rules"), "sum": r.get("stack_sum"),
                            "estimator": runs_mod.estimator_of(r), "open": r.get("open"), "kept": r.get("kept"),
                            "per": {self.key_of(kk): vv for kk, vv in (r.get("per_folder") or {}).items()}})
        if run.get("id") and not any(h["id"] == run["id"] for h in history):
            history.append({"id": run["id"], "label": run.get("label"), "rules": run.get("rules_hash"),
                            "estimator": mine, "sum": sum(s["tokens_est"] for s in scan["stacks"]),
                            "open": sum(1 for it in scan["items"] if it["state"] == "open" and it["certainty"] != "info"),
                            "kept": sum(1 for it in scan["items"] if it["state"] == "kept"),
                            "per": {self.key_of(f"{s['folder']}@{s['agent']}"): s["tokens_est"] for s in scan["stacks"]}})
        history.sort(key=lambda r: r["id"])
        # Token counts of two estimators are not a change: the page shows both numbers and this note.
        other = sorted({h["estimator"] for h in history[-10:]} - {mine})
        history_note = strings.t("run.estimator_changed", self.lang, a=", ".join(other), b=mine) if other else None

        # measure: the newest measure run, one row per folder group (no per-family panels yet)
        measure, eve_ref = {}, None
        if self.eve_run:
            eid, ej = self.eve_run
            eve_ref = {"id": eid, "sessions": ej.get("sessions", 0), "groups": ej.get("groups") or []}
            by_group = {g["group"]: g for g in eve_ref["groups"]}
            for skey, T in scan["triage"]["stacks"].items():
                if T["agent"] != "claude":
                    continue
                try:
                    g, _control = eve.group_key(T["folder"])
                except OSError:
                    continue
                row = by_group.get(g)
                measure[self.key_of(skey)] = {"state": "row", "group": g, "row": row} if row else {"state": "none", "group": g}

        used = set()
        for v in views.values():
            used.update(v["start"], v["rest"], v["file_copy"], v["ondemand"], v["other"])
            for r in v["shared"] + v["copies"]:
                used.update(r["items"])
        gen = scan["generated"]
        try:
            gen_ms = int(datetime.fromisoformat(gen).astimezone().timestamp() * 1000)
        except ValueError:
            gen_ms = None
        default_root = self.roots[-1] if self.roots else None
        roots_in_order = [str(r) for r in (scan.get("roots") or run["roots"])]
        first_root = roots_in_order[0] if roots_in_order else default_root
        default = next((self.key_of(k) for k in scan["triage"]["stacks"] if k == f"{first_root}@claude"),
                       next(iter(views), None))
        return {
            "run": {
                "id": run.get("id") or "", "when": gen[:16].replace("T", " "), "generated": gen, "generated_ms": gen_ms,
                "root": t(first_root), "files": scan["artifact_count"], "worktree_copies": scan["copy_count"],
                "stacks": len(scan["stacks"]), "flags": len(scan["flags"]), "pairs": len(scan["pairs"]),
                "items": len(scan["items"]), "version": run["walle_version"], "rules": run["rules_hash"][:4],
                "rules_full": run["rules_hash"], "estimator": run["estimator"],
                "baseline": parent["id"] if parent else None, "baseline_estimator": base_est,
                "baseline_note": (strings.t("run.estimator_changed", self.lang, a=base_est, b=mine)
                                  if base_est and base_est != mine else None),
                "history_note": history_note, "changed_files": [t(p) for p in self.changed],
                "runs_dir": None,
            },
            "budget": B,
            "default": default,
            "features": {"keep": False},
            "verdicts": scan["triage"]["verdicts"],
            "views": views,
            "items": {i: items[i] for i in sorted(used)},
            "all": all_rows,
            "measure": measure,
            "eve_ref": eve_ref,
            "history": history,
        }


def latest_measure(out: Path) -> tuple[str, dict] | None:
    """The newest eve.json or measure.json under the runs folder, as (run id, data)."""
    found = []
    for name in ("eve.json", "measure.json"):
        found += glob.glob(str(out / "*" / name))
    for p in sorted(found, key=lambda p: os.path.basename(os.path.dirname(p)), reverse=True):
        try:
            return os.path.basename(os.path.dirname(p)), json.loads(Path(p).read_text())
        except (OSError, ValueError):
            continue
    return None


def page_data(data: dict, out: Path | None = None, home: Path = HOME, lang: str = "en") -> dict:
    """The JSON the page renders. `out` is the runs folder: it gives history, baseline and measure."""
    from . import runs as runs_mod
    index = runs_mod.read_index(out) if out else []
    eve_run = latest_measure(out) if out else None
    D = Builder(data, str(home), index, eve_run, lang).build()
    D["run"]["runs_dir"] = tilde(str(out), str(home)) if out else "~/.walle/runs"
    return D


def font_faces() -> str:
    pkg = resources.files("walle") / "fonts"
    out = []
    for family, weight, name in FONTS:
        b64 = base64.b64encode((pkg / name).read_bytes()).decode()
        out.append(f"@font-face{{font-family:'{family}';font-weight:{weight};font-display:block;"
                   f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}")
    return "\n".join(out)


def render(data: dict, out: Path | None = None, home: Path = HOME, lang: str = "en") -> str:
    """One HTML page for a schema 2 scan. A schema 1 scan (no triage) gets a page that says so."""
    if "triage" not in data or "items" not in data:
        raise ValueError("scan.html needs scan.json schema 2 (items and triage)")
    D = page_data(data, out, home, lang)
    payload = json.dumps(D, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tpl = (resources.files("walle") / "report.html").read_text(encoding="utf-8")
    title = "walle scan · " + (D["run"]["root"] or "")
    return (tpl.replace("/*__FONTS__*/", font_faces())
               .replace("/*__LANG__*/", html.escape(lang))
               .replace("/*__TITLE__*/", html.escape(title))
               .replace("/*__DATA__*/", payload))


__all__ = ["render", "page_data", "fmt", "BUDGET"]
