"""Render scan and eve results as Markdown."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from .collect import HOME

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


def render_scan(r: dict, top: int = 15) -> str:
    md = [f"# walle 지시 문서 리포트\n\n생성: {r['generated']} · 루트: {', '.join(short(x) for x in r['roots'])}\n"]
    md.append(f"수거한 지시 파일 {r['artifact_count']}개 (워크트리 사본 {r['copy_count']}개 포함). "
              "토큰은 바이트 기반 추정치다.\n")

    md.append("## 1. 세션 시작 스택 (큰 순)\n")
    md.append("세션을 그 폴더에서 열 때 첫 프롬프트 전에 읽히는 지시. 커넥터 도구와 claude.ai 플러그인 스킬은 빠졌다.\n")
    rows = sorted(r["stacks"], key=lambda s: -s["tokens_est"])
    md.append(table(
        ["폴더", "에이전트", "추정 토큰", "바이트", "지시 단위", "원인1", "원인3", "원인4", "구성"],
        [[short(s["folder"]), s["agent"], s["tokens_est"], s["bytes"], s["units"],
          s["flags"]["1"], s["flags"]["3"], s["flags"]["4"],
          ", ".join(f"{l['label']}:{l['tokens_est']}" + (" ⚠" if l["note"].startswith("over") else "")
                    for l in s["layers"])]
         for s in rows[:top * 2]]))

    md.append("\n## 2. 파일별 (로드되는 스택 수, 크기 순)\n")
    md.append(table(
        ["파일", "종류", "로드 스택 수", "바이트", "지시 단위", "강조어", "원인1", "원인3", "원인4"],
        [[short(f["path"]), f["kind"], f["loaded_in_stacks"], f["bytes"], f["units"], f["emphasis"],
          f["flags"]["1"], f["flags"]["3"], f["flags"]["4"]] for f in r["files"][:top * 2]]))

    md.append("\n## 3. 원인별 발견\n")
    by = defaultdict(list)
    for fl in r["flags"]:
        by[(fl["cause"], fl["kind"])].append(fl)
    for (cause, kind), fls in sorted(by.items()):
        files = Counter(short(f["file"]) for f in fls)
        md.append(f"### 원인 {cause} {CAUSE[cause]} · {kind} · {len(fls)}건"
                  + (" (후보: 사람이 판정)" if fls[0]["candidate"] else "") + "\n")
        md.append("많은 파일: " + ", ".join(f"{f} {n}" for f, n in files.most_common(5)) + "\n")
        for fl in fls[:5]:
            md.append(f"- `{short(fl['file'])}:{fl['line']}` [{fl['match']}] {fl['text'][:140]}")
        md.append("")

    md.append("## 4. 중복, drift, 충돌 후보\n")
    md.append("duplicate: 같은 규칙이 두 곳에. drift: 비슷하지만 달라진 규칙(원인 2 충돌 후보). "
              "topic: 한 세션에 함께 읽히고 핵심어(백틱 토큰, 대문자 용어, 도구 이름)를 공유하는데 "
              "한쪽이 부정하거나 메시지 형식이 다른 규칙(원인 2 충돌 후보, 글자가 닮지 않아도 잡는다). "
              "protocol: 두 저장소가 같은 메시지를 다른 형식으로 쓴 규칙(원인 2 충돌 후보). "
              "repeat: 한 파일 안 반복(원인 4).\n")
    kinds = Counter(p["kind"] for p in r["pairs"])
    md.append(", ".join(f"{k} {n}건" for k, n in kinds.most_common()) + "\n")
    for kind in ("drift", "topic", "protocol", "duplicate", "repeat"):
        ps = [p for p in r["pairs"] if p["kind"] == kind][:top]
        if not ps:
            continue
        md.append(f"### {kind}\n")
        for p in ps:
            if kind in ("topic", "protocol"):
                why = [f"공유: {', '.join(p.get('shared', []))}"]
                if p.get("opposite"):
                    why.append(f"부정이 갈린 말: {', '.join(p['opposite'])}")
                why += [f"형식: {' / '.join(v['a'])} ↔ {' / '.join(v['b'])}" for v in p.get("variants", [])]
                head = " · ".join(why)
            else:
                head = str(p["score"])
            md.append(f"- {head} · `{short(p['a']['file'])}:{p['a']['line']}` ↔ "
                      f"`{short(p['b']['file'])}:{p['b']['line']}`\n  - A: {p['a']['text'][:120]}\n"
                      f"  - B: {p['b']['text'][:120]}")
        md.append("")

    md.append("## 5. 워크트리 사본\n")
    drifted = [c for c in r["copies"] if c["variants"] > 1]
    md.append(f"사본이 있는 파일 {len(r['copies'])}개, 그중 내용이 갈라진 파일 {len(drifted)}개.\n")
    md.append(table(
        ["저장소", "파일", "사본", "변형", "main 판", "크기들"],
        [[Path(c["repo"]).name, c["file"], c["copies"], c["variants"],
          c["main_sha"] or "main에 없음", ", ".join(map(str, c["sizes"][:5]))]
         for c in drifted[:top * 2]]))

    md.append("\n## 6. 한계\n")
    md.append("- 원인 1과 원인 2는 정규식과 유사도로 찾은 후보다. 작업과 무관한지, 실제로 충돌하는지는 사람이 판정한다.\n"
              "- 로드 규칙은 이 기계에서 관찰한 것만 반영한다. 커넥터 도구, 플러그인 스킬, 시스템 프롬프트는 `walle eve`의 실측으로 본다.\n"
              "- 코드 품질은 보지 않는다. 코드 쪽은 에이전트 세션에 영향을 주는 것(워크트리 사본, 스킬 사본)만 본다.")
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
    sa = {(s["folder"], s["agent"]): s for s in a["stacks"]}
    rows = []
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
