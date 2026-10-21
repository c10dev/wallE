"""Render scan and eve results as Markdown."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import brief, runs
from .collect import HOME
from .strings import label as item_label
from .strings import round_tokens, t
from .triage import LEAN

CAUSE = {1: "무관 비용 후보", 2: "충돌 후보", 3: "모호한 품질", 4: "구모델 보정"}


def short(p: str | None) -> str:
    if not p:
        return "-"
    h = str(HOME)
    return "~" + p[len(h):] if p.startswith(h) else p


def table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def _md_item(n: int, it: dict, lang: str, home: Path, cap: int, data: dict) -> list[str]:
    """`1. **CHECK** `path:8` · label · candidate`, the reason, then at most cap quoted source lines."""
    verb = f"**{t('verb.' + it['verb'], lang)}** " if it["verb"] else ""
    head = f"{n}. {verb}`{brief.location(it, None, home)}` · {item_label(it, lang)} · {brief.certainty(it, lang)}"
    return [head + "  ", "   " + brief.reason(it, lang, None, home)] + brief._md_quote(it, cap, it["id"], lang)


def _md_line(it: dict, lang: str, home: Path, indent: str = "") -> list[str]:
    """One item of All findings: verb, location, label, certainty and id, then the reason."""
    verb = f"**{t('verb.' + it['verb'], lang)}** " if it["verb"] else ""
    return [f"{indent}- {verb}`{brief.location(it, None, home)}` · {item_label(it, lang)} · "
            f"{brief.certainty(it, lang)} · `{it['id']}`  ",
            f"{indent}  {brief.reason(it, lang, None, home)}"]


def _md_groups(data: dict, entries: list[dict], on_screen, lang: str, home: Path) -> list[str]:
    items = {it["id"]: it for it in data["items"]}
    md = [f"## {t('level.all_findings', lang)}", ""]
    for g, members in brief.collect_groups(entries, on_screen).items():
        md.append(f"### {t(f'group.{g}', lang)} ({brief.group_count(g, members)})")
        note = {"on_demand": "group.on_demand_note", "other_agents": "group.other_agents_note"}.get(g)
        if note and members:
            md.append(t(note, lang) + "  ")
        blocks: list[list[str]] = []
        for m in members:
            if g == "worktree_copies":
                blocks.append(["- " + brief._worktree_text(m, lang)])
                continue
            if isinstance(m, str):
                if m in items:
                    blocks.append(_md_line(items[m], lang, home))
                continue
            if g == "shared":
                row = "- " + brief.shared_text(m, lang, home)
            else:
                counts = ", ".join(f"{t(f'label.{k}', lang)} {v}" for k, v in sorted(m["counts"].items()))
                row = "- " + t("md.copies_row", lang, a=f"`{short(m['file'])}`", b=f"`{short(m['other'])}`",
                               counts=counts)
                if m.get("parent"):
                    row += " · " + t("md.move_to_parent", lang, parent=f"`{short(m['parent'])}`")
            sub = [line for i in m["items"][:brief.GROUP_ROWS] if i in items for line in _md_line(items[i], lang, home, "  ")]
            if len(m["items"]) > brief.GROUP_ROWS:
                sub.append("  - " + t("md.more", lang, n=len(m["items"]) - brief.GROUP_ROWS))
            blocks.append([row] + sub)
        for b in blocks[:brief.GROUP_ROWS]:
            md += b
        if len(blocks) > brief.GROUP_ROWS:
            md.append("- " + t("md.more", lang, n=len(blocks) - brief.GROUP_ROWS))
        md.append("")
    return md


def _md_stack_table(data: dict, entries: list[dict], lang: str, home: Path) -> list[str]:
    head = [t(f"table.{c}", lang) for c in ("folder", "agent", "tokens", "verdict", "to_read")]
    return table(head, [[brief._folder_name(e["folder"], data, home), e["agent"],
                         "~" + round_tokens(e["tokens_est"], e["budget"]), t(f"verdict.{e['verdict']}", lang),
                         e["to_read"]] for e in entries]).splitlines()


def _md_tail(data: dict, entries: list[dict], lang: str, home: Path, history: list[dict] | None) -> list[str]:
    md = [f"## {t('level.stacks', lang)}", ""]
    if brief.is_single(data):
        rows = {(s["folder"], s["agent"]): s for s in data["stacks"]}
        for e in entries:
            st = rows.get((e["folder"], e["agent"]))
            if not st:
                continue
            md.append(f"### {e['agent']} · {t('verdict.' + e['verdict'], lang)} · "
                      f"~{round_tokens(e['tokens_est'], e['budget'])} {t('term.est_tokens', lang)}")
            md += table([t("table.layer", lang), t("table.file", lang), t("table.tokens", lang)],
                        [[l["label"], short(l["path"]) if l["path"] else l["note"],
                          "~" + round_tokens(l["tokens_est"]) + (" over" if l["note"].startswith("over") else "")]
                         for l in st["layers"]]).splitlines()
            md.append("")
    else:
        md += _md_stack_table(data, brief._stack_rows(data), lang, home) + [""]
    md += [f"## {t('level.measure', lang)}", "", t("md.measure_none", lang), ""]
    md += [f"## {t('level.history', lang)}", ""]
    rows = (history or [])[-10:]
    if len(rows) <= 1:
        md.append(t("empty.first_run", lang))
    else:
        # A stack sum is in the tokens of its run's estimator and counting revision. Sums of two estimators or
        # two revisions are not a change: say so, and name the revision of the runs that differ.
        for note in runs.token_notes(rows, data, lang):
            md += [note, ""]
        rev = runs.counting_of(data)

        def counted(r):
            e = runs.estimator_of(r)
            return e if runs.counting_of(r) == rev else f"{e} ({t('run.counting_of', lang, n=runs.counting_of(r))})"

        md += table([t(f"table.{c}", lang) for c in ("run", "label", "rules", "estimator", "stack_sum", "open", "kept")],
                    [[r.get("id") or "-", r.get("label") or "-", (r.get("rules") or "----")[:4], counted(r),
                      "~" + round_tokens(r.get("stack_sum") or 0), r.get("open", 0), r.get("kept", 0)]
                     for r in rows]).splitlines()
    md += ["", f"## {t('level.method', lang)}", ""]
    run = data.get("run") or {}
    md.append("- " + t("md.method_estimate", lang, estimator=run.get("estimator") or "-"))
    md.append("- " + t("md.method_rules", lang, rules=brief.rules_id(data)))
    md.append("- " + t("md.method_load", lang))
    md.append("- " + t("md.method_candidates", lang))
    hidden = data["triage"].get("hidden", {}).get("elsewhere", 0)
    if hidden:
        md.append("- " + t("sentence.hidden_elsewhere", lang, n=hidden))
    md.append("")
    md += table([t("table.cause", lang), t("table.meaning", lang)],
                [[c, t(f"md.cause.{c}", lang)] for c in (1, 2, 3, 4)]).splitlines()
    return md


def render_scan(r: dict, top: int = 15, lang: str = "en", history: list[dict] | None = None,
                home: Path = HOME) -> str:
    """scan.md in the order of design brief 3.6: verdict and Start here in the first 25 lines, then
    All findings, Stacks, Measure, History, Method and limits. `top` is kept for old callers."""
    items = {it["id"]: it for it in r["items"]}
    if not r["triage"]["stacks"] or r.get("artifact_count", 1) == 0:
        root = brief.shown((r.get("roots") or ["."])[0], None, home)
        return f"# {t('md.title_many', lang, root=root)}\n\n{t('empty.no_files', lang, path=root)}\n"
    meta = t("md.meta", lang, date=brief.stamp(r), rules=brief.rules_id(r))
    if brief.is_single(r):
        lead, others = brief.primary(r)
        entries = [lead, *others]
        start = [items[i] for i in lead["start_here"] if i in items]
        vword = t(f"verdict.{lead['verdict']}", lang)
        sentences = brief._verdict_sentences(lead, lang, lead["folder"], home, r)
        if lead["verdict"] == LEAN and not start:
            sentences = [t("empty.lean", lang, tokens=round_tokens(lead["tokens_est"], lead["budget"]),
                           budget=f"{lead['budget']:,}").partition(" · ")[2]] + brief.all_kept(lead, lang)
        md = [f"# {t('md.title', lang, root=short(lead['folder']), agent=lead['agent'])}", "",
              f"**{vword}** · " + " ".join(sentences),
              brief.split_line(lead, lang).replace(" | ", " · ") + " · " + meta, ""]
        on_screen = lead["start_here"]
        heading, cap = t("level.start_here", lang), brief.MD_QUOTE_LINES + 1
    else:
        entries = brief._stack_rows(r)
        counts = r["triage"]["verdicts"]
        words = [f"{t(f'verdict.{v}', lang)} {counts.get(v, 0)}" for v in brief.VERDICT_ORDER]
        roots = " ".join(short(x) for x in r["roots"])
        md = [f"# {t('md.title_many', lang, root=roots)}", "",
              f"**{words[0]}** · {words[1]} · {words[2]} · "
              + t("brief.budget", lang, budget=f"{r['triage']['budget']:,}"),
              t("md.counts", lang, stacks=len(entries), files=r.get("artifact_count", 0),
                copies=r.get("copy_count", 0)) + " · " + meta,
              ""]
        md += _md_stack_table(r, entries[:5], lang, home)
        if len(entries) > 5:
            md.append(t("brief.more_rows", lang, n=len(entries) - 5, root=roots))
        md.append("")
        start = [items[i] for i in brief.numbered(r) if i in items]
        on_screen = brief.numbered(r)
        heading, cap = t("level.start_here_by_reach", lang), 1
    md.append(f"## {heading}")
    if start:
        for n, it in enumerate(start, 1):
            md += _md_item(n, it, lang, home, cap, r)
    else:
        md.append(t("sentence.nothing", lang))
    md.append("")
    md += _md_groups(r, entries, on_screen, lang, home)
    md += _md_tail(r, entries, lang, home, history)
    return "\n".join(md) + "\n"


def render_eve(r: dict, top: int = 30) -> str:
    md = [f"# walle 세션 실측\n\n생성: {r['generated']} · 세션 {r['sessions']}개"
          + (f" · since {r['since']}" if r["since"] else "") + (f" · before {r['before']}" if r["before"] else "") + "\n"]
    md.append("baseline: 첫 API 호출의 입력 토큰 전체(시스템 프롬프트, 도구 목록, 지시 스택, 첫 프롬프트). "
              "delta: 같은 모델의 대조군(프로젝트 CLAUDE.md 없음) 중앙값을 뺀 값.\n")
    md.append("대조군 중앙값: " + (", ".join(f"{k} {v}" for k, v in r["control_baseline_by_model"].items()) or "없음") + "\n")
    md.append(table(
        ["그룹", "세션", "baseline 중앙값", "delta 중앙값", "프롬프트", "도구 호출", "호출/프롬프트",
         "검사 호출", "첫 편집 전 호출"],
        [[g["group"], g["n"], g["baseline_median"], g["baseline_delta_median"] if g["baseline_delta_median"] is not None else "-",
          g["prompts_median"], g["tool_calls_median"], g["tool_calls_per_prompt_median"],
          g["check_calls_median"], g["calls_before_first_edit_median"] if g["calls_before_first_edit_median"] is not None else "-"]
         for g in r["groups"][:top]]))
    md.append("\n중앙값은 세션 단위다. 세션 수가 적은 그룹은 흔들린다.")
    return "\n".join(md) + "\n"


def render_judge(r: dict) -> str:
    p = r["plan"]
    md = [f"# walle 판정 리포트\n\n생성: {r['generated']} · 판정: 에이전트 · 스캔: {short(r['scan'])}"
          f" ({r['scan_generated']})\n"]
    by = ", ".join(f"{k} {n}" for k, n in p.get("by_pair", {}).items())
    md.append(f"후보 {p['items']}건(충돌 후보 {p['conflict']}건: {by} · 비용 후보 원인 1 {p['cost']}건). "
              f"이번에 답 파일 {p.get('answered_files', 0)}개에서 {p.get('judged_now', 0)}건을 읽었고, "
              f"{p['cached']}건은 캐시에서 읽었다. 남은 후보 {p['to_judge']}건.\n")
    if r["errors"]:
        md.append("오류: " + "; ".join(r["errors"]) + "\n")

    def where(it):
        tag = f"[{it['pair']}] " if it.get("pair") else ""
        return tag + " ↔ ".join(f"`{short(x)}`" for x in it["refs"]) + (" (스캔 뒤 바뀜)" if it["stale"] else "")

    def block(title, its, full):
        md.append(f"## {title} · {len(its)}건\n")
        for it in its:
            v = it["verdict"]
            if not full:
                md.append(f"- {where(it)} — {v['why']}")
                continue
            md.append(f"- {where(it)} · 남길 줄: {v['keep']} · 고칠 것: {v['fix']}\n  - 이유: {v['why']}")
            for label, t in zip("AB", it["texts"]):
                # A code unit or a section can span lines; keep one list item per text.
                md.append(f"  - {label if len(it['texts']) > 1 else '줄'}: {' '.join(t.split())[:160]}")
        md.append("")

    judged = [it for it in r["items"] if it["verdict"]]
    block("1. 실제 충돌", [it for it in judged if it["type"] == "conflict" and it["verdict"]["real"]], True)
    block("2. 작업과 무관한 상시 비용", [it for it in judged if it["type"] == "cost" and it["verdict"]["real"]], True)
    block("3. 문제 아님", [it for it in judged if not it["verdict"]["real"]], False)
    missing = [it for it in r["items"] if not it["verdict"]]
    if missing:
        md.append(f"## 4. 판정 못 함 · {len(missing)}건\n")
        md.extend(f"- {where(it)}" for it in missing)
        md.append("\n`walle judge`를 다시 실행하면 이 항목만 배치 파일로 내보낸다.\n")

    md.append("## 한계\n")
    md.append("- 판정은 에이전트 출력이다. 고치기 전에 줄을 직접 읽는다.\n"
              "- 충돌 후보는 drift 쌍, topic 쌍(한 스택에서 핵심어를 공유하고 부정이나 메시지 형식이 갈린 규칙), "
              "protocol 쌍(두 저장소가 같은 메시지를 다른 형식으로 쓴 규칙)이다. 핵심어를 공유하지 않거나, "
              "한 음절 동사(\"쓰지 않는다\", \"읽지 않는다\")로 갈리거나, 한국어·영어가 아닌 부정은 후보에 없다.\n"
              "- 캐시 키는 규칙 글과 루브릭의 해시다. 줄 번호만 바뀐 규칙은 다시 판정하지 않는다.")
    return "\n".join(md) + "\n"


def render_diff(a: dict, b: dict) -> str:
    md = ["# walle 비교\n", f"이전: {a['generated']} → 이후: {b['generated']}\n"]
    ea, eb = runs.estimator_of(a), runs.estimator_of(b)
    ca, cb = runs.counting_of(a), runs.counting_of(b)
    sa = {(s["folder"], s["agent"]): s for s in a["stacks"]}
    rows = []
    notes = runs.token_notes([a], b, "ko")
    if notes:
        # Token counts of two estimators or two counting revisions are not a change: show both numbers,
        # each with how it was counted.
        md += [n + "\n" for n in notes]
        for s in b["stacks"]:
            o = sa.get((s["folder"], s["agent"]))
            if o:
                rows.append([short(s["folder"]), s["agent"], o["tokens_est"], s["tokens_est"],
                             f"{s['units'] - o['units']:+}"])
        ha, hb = ((ea, eb) if ca == cb else (f"{ea}, {t('run.counting_of', 'ko', n=ca)}",
                                             f"{eb}, {t('run.counting_of', 'ko', n=cb)}"))
        md.append("## 스택 변화\n")
        md.append(table(["폴더", "에이전트", f"이전 토큰({ha})", f"이후 토큰({hb})", "지시 단위 차"], rows)
                  if rows else "같은 스택 없음")
    else:
        for s in b["stacks"]:
            o = sa.get((s["folder"], s["agent"]))
            if not o:
                continue
            d = s["tokens_est"] - o["tokens_est"]
            du = s["units"] - o["units"]
            if d or du:
                rows.append([short(s["folder"]), s["agent"], o["tokens_est"], s["tokens_est"], f"{d:+}", f"{du:+}"])
        md.append("## 스택 변화\n")
        md.append(table(["폴더", "에이전트", "이전 토큰", "이후 토큰", "토큰 차", "지시 단위 차"], rows) if rows else "변화 없음")
    fa = Counter((f["cause"]) for f in a["flags"])
    fb = Counter((f["cause"]) for f in b["flags"])
    md.append("\n## 원인별 건수\n")
    md.append(table(["원인", "이전", "이후"], [[f"{c} {CAUSE[c]}", fa.get(c, 0), fb.get(c, 0)] for c in (1, 3, 4)]))
    pa = Counter(p["kind"] for p in a["pairs"])
    pb = Counter(p["kind"] for p in b["pairs"])
    md.append("\n## 중복·drift·충돌 후보\n")
    md.append(table(["종류", "이전", "이후"], [[k, pa.get(k, 0), pb.get(k, 0)]
                                          for k in ("drift", "topic", "protocol", "duplicate", "repeat")]))
    return "\n".join(md) + "\n"
