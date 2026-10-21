"""The one table of display strings, in English and Korean.

Every surface takes its words from here: verdicts, verbs, item labels, fixed terms and
sentences. JSON keys and engine kinds (rule ids, scopes) are never translated.
A key that is not in the table raises UnknownString, so a typo fails a test and not a
user's screen. Fields in braces must be the same in both languages.

Numbers: estimated tokens are rounded by round_tokens() and written with "~" (English
and compact lines) or "약" (Korean sentences). Measured values are exact.
"""

from __future__ import annotations

import os

LANGS = ("en", "ko")


class UnknownString(KeyError):
    pass


# key: (English, Korean)
STRINGS: dict[str, tuple[str, str]] = {
    # Verdicts (triage.OVER, TRIM, LEAN)
    "verdict.OVER BUDGET": ("OVER BUDGET", "예산 초과"),
    "verdict.TRIM": ("TRIM", "다듬기"),
    "verdict.LEAN": ("LEAN", "가벼움"),
    # Verbs
    "verb.DELETE": ("DELETE", "삭제"),
    "verb.PICK ONE": ("PICK ONE", "하나만"),
    "verb.CHECK": ("CHECK", "확인"),
    "verb.REWRITE": ("REWRITE", "고쳐 쓰기"),
    "verb.MOVE": ("MOVE", "옮기기"),
    "verb.SYNC?": ("SYNC?", "맞추기?"),
    # Certainty
    "certainty.certain": ("certain", "확실"),
    "certainty.candidate": ("candidate", "후보"),
    "certainty.info": ("note", "노트"),
    # Item labels (triage.LABELS)
    "label.conflict": ("conflict?", "충돌?"),
    "label.every_task_step": ("every-task step?", "매 작업 단계?"),
    "label.large_layer": ("large layer", "큰 층"),
    "label.loaded_twice": ("loaded twice", "두 번 로드"),
    "label.similar_twice": ("similar rule loaded twice?", "비슷한 규칙 두 번?"),
    "label.repeated": ("repeated in file", "파일 안 반복"),
    "label.repeated_maybe": ("repeated?", "반복?"),
    "label.model_habit": ("old-model habit: still needed?", "구모델 습관: 아직 필요한가?"),
    "label.dated": ("dated: still true?", "날짜: 아직 맞나?"),
    "label.model_name": ("model name: still true?", "모델명: 아직 맞나?"),
    "label.dated_lines": ("{n} dated lines: still true?", "날짜 줄 {n}개: 아직 맞나?"),
    "label.memory_lines": ("{n} memory lines: still true?", "메모리 줄 {n}개: 아직 맞나?"),
    "label.vague": ("vague: name the pattern", "모호: 패턴을 적는다"),
    "label.stacked_emphasis": ("stacked emphasis", "강조 중첩"),
    "label.different_agents": ("loads in different agents' stacks", "서로 다른 에이전트 스택에서 읽힘"),
    "label.copied_between": ("copied between folders", "폴더 사이 복사본"),
    "label.drifted_copy": ("drifted copy", "달라진 사본"),
    "label.format_differs": ("format differs across repos?", "저장소마다 형식이 다름?"),
    "label.source_of": ("source of {file}", "{file}의 원본"),
    "label.copy_of": ("copy of {file}", "{file}의 사본"),
    "label.term_clash": ("same term, opposite wording?", "같은 용어, 반대 표현?"),
    "label.loose_group": ("loose group", "느슨한 묶음"),
    "label.over_budget": ("over budget", "예산 초과"),
    "label.lines": ("lines {start}-{end} ({n} rules)", "{start}-{end}줄 (규칙 {n}개)"),
    # Fixed terms
    "term.est_tokens": ("est. tokens", "토큰(추정)"),
    "term.your_files": ("your files", "내 파일"),
    "term.shared": ("shared", "공용"),
    "term.skills": ("skills", "스킬"),
    "term.memory": ("memory", "메모리"),
    "term.keep": ("keep", "남기기"),
    "term.budget": ("budget", "예산"),
    "term.stack": ("stack", "스택"),
    "term.layer": ("layer", "층"),
    "term.item": ("item", "항목"),
    "term.finding": ("finding", "발견"),
    "term.reach": ("reach", "도달"),
    "term.run": ("run", "실행"),
    "term.baseline": ("baseline", "기준 실행"),
    "term.measured": ("measured", "실측"),
    "term.conflict": ("conflict", "충돌"),
    "term.drifted_copy": ("drifted copy", "달라진 사본"),
    # Levels and groups
    "level.verdict": ("Verdict", "판정"),
    "level.start_here": ("Start here", "여기부터"),
    "level.start_here_by_reach": ("Start here (by reach: est. tokens x folders that load it)",
                                  "여기부터 (도달 순: 토큰(추정) x 읽는 폴더 수)"),
    "level.all_findings": ("All findings", "전체 발견"),
    "level.stacks": ("Stacks", "스택"),
    "level.measure": ("Measure", "세션 실측"),
    "level.history": ("History", "이력"),
    "level.method": ("Method and limits", "방법과 한계"),
    "group.everything_else": ("Everything else", "나머지"),
    "group.shared": ("Shared files", "공용 파일"),
    "group.copies": ("Copies between folders", "폴더 사이 사본"),
    "group.on_demand": ("On-demand files", "필요할 때 읽는 파일"),
    "group.other_agents": ("Other agents' files", "다른 에이전트 파일"),
    "group.worktree_copies": ("Worktree copies", "워크트리 사본"),
    "group.kept": ("Kept", "남긴 줄"),
    "group.on_demand_note": ("loads when a skill or agent runs", "스킬이나 에이전트가 돌 때 읽힌다"),
    "group.other_agents_note": ("load rules not modeled", "로드 규칙을 모델링하지 않음"),
    # Verdict block
    "sentence.load": ("~{tokens} est. tokens load before your first prompt (budget {budget}).",
                      "첫 프롬프트 전에 약 {tokens} 토큰(추정)을 읽는다(예산 {budget})."),
    "sentence.to_read.one": ("1 item to read in your files.", "내 파일에서 읽어 볼 항목이 1개다."),
    "sentence.to_read.other": ("{n} items to read in your files.", "내 파일에서 읽어 볼 항목이 {n}개다."),
    "sentence.nothing": ("Nothing to read in your files.", "내 파일에서 읽어 볼 항목이 없다."),
    "sentence.largest": ("Largest: {file} ~{tokens}.", "가장 큰 층: {file} 약 {tokens}."),
    "sentence.split": ("your files ~{yours} | shared ~{shared} | skills ~{skills}",
                       "내 파일 ~{yours} | 공용 ~{shared} | 스킬 ~{skills}"),
    "sentence.shared_row.one": ("{file}: 1 finding, loads in {folders} folders",
                                "{file}: 발견 1건, {folders}개 폴더에서 읽힌다"),
    "sentence.shared_row.other": ("{file}: {n} findings, loads in {folders} folders",
                                  "{file}: 발견 {n}건, {folders}개 폴더에서 읽힌다"),
    "sentence.hidden_elsewhere": ("{n} findings in files outside this folder's stacks are hidden (walle scan --all)",
                                  "이 폴더 스택 밖 파일의 발견 {n}건은 숨겼다(walle scan --all)"),
    "sentence.more_lines": ("... {n} more lines (walle show {ref})", "... {n}줄 더 (walle show {ref})"),
    "sentence.more_lines.one": ("... 1 more line (walle show {ref})", "... 1줄 더 (walle show {ref})"),
    "sentence.more_lines.other": ("... {n} more lines (walle show {ref})", "... {n}줄 더 (walle show {ref})"),
    # Reasons, one for each label. The quoted phrase is what the rule matched.
    "reason.every_task_step.step": ('"{match}" adds a step to every task. Keep it if every task needs it.',
                                    '"{match}"는 모든 작업에 단계 하나를 더한다. 모든 작업에 필요하면 남긴다.'),
    "reason.every_task_step.read": ('"{match}" adds a read to every task. Keep it if every task needs it.',
                                    '"{match}"는 모든 작업에 읽기 하나를 더한다. 모든 작업에 필요하면 남긴다.'),
    "reason.dated": ('Dated "{match}". Is the rule still true? Keep it if the date records a decision you still follow.',
                     '날짜 "{match}". 규칙이 아직 맞나? 아직 따르는 결정을 적은 날짜면 남긴다.'),
    "reason.model_name": ('Names a model ("{match}"). Keep it if the rule still applies to the model you run.',
                          '모델 "{match}"를 적었다. 지금 쓰는 모델에도 맞는 규칙이면 남긴다.'),
    "reason.model_habit": ('"{match}" corrects an old model habit. Keep it if the model you run still needs it.',
                           '"{match}"는 구모델 습관을 바로잡는다. 지금 쓰는 모델에도 필요하면 남긴다.'),
    "reason.vague": ('"{match}" names no pattern. Write the pattern to use or to avoid.',
                     '"{match}"는 패턴을 말하지 않는다. 쓸 패턴이나 피할 패턴을 적는다.'),
    "reason.stacked_emphasis": ('Stacked emphasis ("{match}"). Write the rule once, without the extra emphasis words.',
                                '강조가 겹친다("{match}"). 규칙을 한 번, 강조어 없이 쓴다.'),
    "reason.dated_lines": ("{n} lines carry dates or model names. Check that each rule is still true. "
                           "Keep a line if it still is.",
                           "날짜나 모델명이 있는 줄이 {n}개다. 규칙마다 아직 맞는지 확인한다. 아직 맞으면 남긴다."),
    "reason.memory_lines": ("Dated notes are the normal form of memory. Read the lines that set a rule for the agent. "
                            "Keep the ones that only record what happened.",
                            "날짜가 붙은 메모는 메모리의 정상 형태다. 에이전트에게 규칙을 정하는 줄을 읽는다. "
                            "일어난 일만 적은 줄은 남긴다."),
    "reason.loaded_twice": ("Loaded twice: same rule as {file}:{line} in the same session. Keep line {line}.",
                            "두 번 로드: {file}:{line}과 같은 규칙이 한 세션에 두 번 들어간다. {line}번 줄을 남긴다."),
    "reason.repeated": ("Repeated in file: same rule as line {line}. Keep line {line}.",
                        "파일 안 반복: {line}번 줄과 같은 규칙이다. {line}번 줄을 남긴다."),
    "reason.repeated_maybe": ("Candidate: similar to line {line} in the same file. "
                              "Keep both if one is a reference and the other a rule.",
                              "후보: 같은 파일 {line}번 줄과 비슷하다. 한쪽이 참조이고 다른 쪽이 지시면 둘 다 남긴다."),
    "reason.similar_twice": ("Candidate: two similar lines load in the same session. Read both. "
                             "Keep one if they say the same thing.",
                             "후보: 비슷한 두 줄이 한 세션에 함께 읽힌다. 둘 다 읽는다. 같은 말이면 하나만 남긴다."),
    "reason.conflict": ("Candidate: these two lines load in the same session and say different things. "
                        "Which one should win?",
                        "후보: 두 줄이 한 세션에 함께 읽히고 다르게 말한다. 어느 쪽을 따를까?"),
    "reason.term_clash": ('Candidate: both lines name {shared}, and one says "do not" or writes it in another form. '
                          "Most such pairs agree. Read both, or run walle judge.",
                          '후보: 두 줄이 {shared}를 함께 말하고, 한쪽은 "하지 않는다"고 하거나 다른 형식으로 쓴다. '
                          "이런 쌍은 대개 같은 말이다. 둘 다 읽거나 walle judge를 돌린다."),
    "reason.different_agents": ("These lines load in different agents' stacks ({agents}). You may need both. "
                                "To share one rule, @import a common file.",
                                "두 줄은 서로 다른 에이전트 스택({agents})에서 읽힌다. 둘 다 필요할 수 있다. "
                                "규칙 하나로 합치려면 공통 파일을 @import한다."),
    "reason.drifted_copy": ("{a} and {b} never load together. {n} rules drifted.",
                            "{a}와 {b}는 함께 읽히지 않는다. 규칙 {n}개가 달라졌다."),
    "reason.copied_between": ("The same rule is in two folders that never load together. "
                              "Move it to a shared parent if both should follow it.",
                              "함께 읽히지 않는 두 폴더에 같은 규칙이 있다. 둘 다 따라야 하면 공통 부모로 옮긴다."),
    "reason.parent": ("Both folders load {parent}; a shared rule can move there.",
                      "두 폴더 모두 {parent}를 읽으므로 공통 규칙을 그리로 옮길 수 있다."),
    "reason.format_differs": ("Candidate: two repositories write {shared} in different forms. "
                              "Use one form if the two talk to each other.",
                              "후보: 두 저장소가 {shared}를 다른 형식으로 쓴다. 서로 주고받는 메시지면 한 형식으로 맞춘다."),
    "reason.source_of": ("Source of {file}: its rules load twice in this folder ({n} pairs). "
                         "Rename it, or exclude it with claudeMdExcludes.",
                         "{file}의 원본이다: 이 폴더에서 그 규칙이 두 번 읽힌다({n}쌍). "
                         "이름을 바꾸거나 claudeMdExcludes로 뺀다."),
    "reason.source_of.codex": ("Source of {file}: its rules load twice in this folder ({n} pairs). Rename it.",
                               "{file}의 원본이다: 이 폴더에서 그 규칙이 두 번 읽힌다({n}쌍). 이름을 바꾼다."),
    "reason.copy_of": ("Copy of {file}: {n} of its rules load twice in this folder. Keep the rules in one file.",
                       "{file}의 사본이다: 그 규칙 {n}개가 이 폴더에서 두 번 읽힌다. 규칙을 한 파일에 둔다."),
    "reason.large_layer": ('"{heading}" (lines {start}-{end}) is ~{tokens} est. tokens and loads in every session. '
                           "Move it to a skill so it loads only when that work runs.",
                           '"{heading}"({start}-{end}줄)은 약 {tokens} 토큰(추정)이고 모든 세션에 읽힌다. '
                           "스킬로 옮기면 그 작업을 할 때만 읽힌다."),
    "reason.large_layer.file": ("{file} is ~{tokens} est. tokens, {share}% of the stack, and loads in every session. "
                                "Move parts of it to skills so they load only when that work runs.",
                                "{file}은 약 {tokens} 토큰(추정)으로 스택의 {share}%이고 모든 세션에 읽힌다. "
                                "일부를 스킬로 옮기면 그 작업을 할 때만 읽힌다."),
    # Empty and edge states
    "empty.no_files": ("No instruction files found in {path}. walle looks for CLAUDE.md, AGENTS.md, GEMINI.md, "
                       ".cursor/rules, .github/copilot-instructions.md, .windsurfrules, .clinerules and skills. "
                       "Another folder: walle scan <folder>",
                       "{path}에서 지시 파일을 찾지 못했다. 찾는 파일: CLAUDE.md, AGENTS.md, GEMINI.md, .cursor/rules, "
                       ".github/copilot-instructions.md, .windsurfrules, .clinerules, 스킬. 다른 폴더: walle scan <folder>"),
    "empty.lean": ("LEAN · ~{tokens} est. tokens (budget {budget}). Checked: duplicates, conflicts, every-task steps, "
                   "dates, model names, vague words. Nothing to read in your files.",
                   "가벼움 · 약 {tokens} 토큰(추정)(예산 {budget}). 중복, 충돌, 매 작업 단계, 날짜, 모델명, "
                   "모호한 품질어를 확인했다. 내 파일에서 읽어 볼 항목이 없다."),
    "empty.all_kept": ("0 open items. {n} kept lines are not counted. List: walle keep --list",
                       "열린 항목 0개. 남긴 줄 {n}개는 세지 않는다. 목록: walle keep --list"),
    "empty.no_transcripts": ("No session transcripts in {path}. Open a few sessions, then run walle eve again.",
                             "{path}에 세션 기록이 없다. 세션을 몇 번 연 뒤 walle eve를 다시 돌린다."),
    "empty.first_run": ("First run for this folder. It is the baseline for walle diff.",
                        "이 폴더의 첫 실행이다. walle diff의 기준 실행이 된다."),
    # Run comparison and measure
    "run.rules_changed": ("walle rules changed since the baseline ({a} -> {b}). "
                          "Finding counts are not comparable; stack tokens are.",
                          "기준 실행 뒤 walle 규칙이 바뀌었다({a} -> {b}). 발견 수는 비교하지 않고, 스택 토큰만 비교한다."),
    "run.estimator_changed": ("estimator changed ({a} -> {b}): token totals are not comparable.",
                              "추정기가 바뀌었다({a} -> {b}): 토큰 합은 비교하지 않는다."),
    "run.counting_changed": ("token counting changed (revision {a} -> {b}): token totals are not comparable.",
                             "토큰 세는 방식이 바뀌었다(개정 {a} -> {b}): 토큰 합은 비교하지 않는다."),
    "run.counting_of": ("counting revision {n}", "세는 방식 개정 {n}"),
    "measure.too_few": ("Too few sessions to compare: {n} since {date}. Need 10.",
                        "비교할 세션이 적다: {date} 이후 {n}개. 10개가 필요하다."),
    "measure.within_noise": ("No clear change for {folder} ({model}; before n={before}, after n={after}). "
                             "The difference is within noise.",
                             "{folder}의 변화가 분명하지 않다({model}, 이전 {before}개, 이후 {after}개). "
                             "차이가 잡음 범위 안이다."),
    "measure.not_proof": ("A change in median is not proof of cause: task mix and connectors move it.",
                          "중앙값의 변화는 원인의 증거가 아니다: 작업 구성과 커넥터도 값을 움직인다."),
    "measure.scope": ("This folder only. One model family per row. Median [p25-p75].",
                      "이 폴더만 본다. 모델 계열마다 한 행이다. 중앙값 [p25-p75]."),
    "measure.window": ("{side} {a} to {b}", "{side} {a} ~ {b}"),
    "measure.bound.before": ("(starts at {kind} {id})", "({kind} {id}에서 시작)"),
    "measure.bound.after": ("(ends at {kind} {id})", "({kind} {id}에서 끝남)"),
    "measure.apply_line": ("Split at apply {id} (plan {plan}, {n} changed).", "apply {id}에서 나눈다(계획 {plan}, {n}개 바꿈)."),
    "measure.period": ("Sessions from {a} to {b}.", "{a}부터 {b}까지의 세션."),
    "measure.period_all": ("All sessions. Split them with --around <apply-id|date>.",
                           "모든 세션. --around <apply-id|날짜>로 나눈다."),
    "measure.codex": ("Codex threads: first-call input only. Resumed, forked and subagent threads are left out.",
                      "Codex 스레드: 첫 호출 입력만 잰다. 이어받은 스레드, 갈라진 스레드, 하위 에이전트 스레드는 뺀다."),
    "measure.col.model": ("model", "모델"),
    "measure.col.side": ("side", "구간"),
    "measure.col.sessions": ("sessions", "세션"),
    "measure.col.first_call": ("first-call input", "첫 호출 입력"),
    "measure.col.calls_per_prompt": ("calls/prompt", "호출/프롬프트"),
    "measure.col.calls_before_edit": ("calls before edit", "첫 편집 전 호출"),
    "measure.side.before": ("before", "이전"),
    "measure.side.after": ("since", "이후"),
    "measure.side.period": ("period", "기간"),
    "measure.side.change": ("change", "변화"),
    "measure.too_few_cell": ("too few sessions ({n} of {need})", "세션이 적다 ({need}개 중 {n}개)"),
    "measure.few": ("(few sessions)", "(세션 적음)"),
    "measure.too_few_rows": ("Too few sessions for a value:", "값을 내기에 세션이 적다:"),
    "measure.skipped": ("Skipped {zstd} compressed rollouts (install zstd to read them) and {other} unreadable files.",
                        "압축된 기록 {zstd}개(zstd를 설치하면 읽는다)와 읽지 못한 파일 {other}개를 건너뛰었다."),
    "measure.no_edit": ("no edit", "편집 없음"),
    "measure.few_edits": ("too few edits ({n})", "편집이 적다 ({n}개)"),
    "measure.noise_cell": ("within noise ({delta})", "잡음 범위 ({delta})"),
    "measure.lower_cell": ("{delta} lower", "{delta} 낮음"),
    "measure.higher_cell": ("{delta} higher", "{delta} 높음"),
    "measure.models": ("models", "모델"),
    "measure.apps": ("apps", "앱"),
    "measure.need": ("Need {n} sessions on each side. {more} more to go.", "양쪽에 세션이 {n}개씩 필요하다. {more}개 남았다."),
    "measure.left_out.one": ("Left out: {n} session that resumes or forks another session.",
                             "뺀 세션: 다른 세션을 이어받거나 갈라진 세션 {n}개."),
    "measure.left_out.other": ("Left out: {n} sessions that resume or fork another session.",
                               "뺀 세션: 다른 세션을 이어받거나 갈라진 세션 {n}개."),
    "measure.across_head": ("Folders differ in model, connectors and task mix. Do not rank folders by this.",
                            "폴더마다 모델, 커넥터, 작업 구성이 다르다. 이 표로 폴더 순위를 매기지 않는다."),
    "measure.no_sessions": ("No sessions for {folder} in these windows.", "이 구간에 {folder}의 세션이 없다."),
    "measure.bad_split": ("walle eve: {ref} is not an apply id or a date such as 2026-10-21 or 2026-10-21T09:00.",
                          "walle eve: {ref}는 apply id도 날짜(2026-10-21, 2026-10-21T09:00)도 아니다."),
    "measure.not_apply": ("walle eve: {ref} is not an apply in {path}. Give an apply id or a date.",
                          "walle eve: {ref}는 {path}에 있는 apply가 아니다. apply id나 날짜를 준다."),
    "measure.bad_date": ("walle eve: {value} is not a date such as 2026-10-21.", "walle eve: {value}는 날짜(예: 2026-10-21)가 아니다."),
    # walle context --compare
    "compare.args": ("walle context --compare: give two runs or session ids (A B), or one apply id or date.",
                     "walle context --compare: 실행이나 세션 id 둘(A B), 또는 apply id나 날짜 하나를 준다."),
    "compare.bad_ref": ("walle context --compare: {ref} is not an apply id or a date. Two refs compare two runs or sessions.",
                        "walle context --compare: {ref}는 apply id도 날짜도 아니다. 실행이나 세션 둘은 A B로 준다."),
    "compare.not_found": ("walle context --compare: {ref} is not a context run or a session ({why}).",
                          "walle context --compare: {ref}는 context 실행도 세션도 아니다({why})."),
    "compare.no_call": ("walle context --compare: session {ref} has no API call yet.",
                        "walle context --compare: 세션 {ref}에 아직 API 호출이 없다."),
    "compare.usage_run": ("walle context --compare: {ref} is a --usage run. Compare runs of one session each.",
                          "walle context --compare: {ref}는 --usage 실행이다. 세션 하나씩의 실행을 비교한다."),
    "compare.agents_differ": ("walle context --compare: {a} and {b} sessions do not compare. Give two of one agent.",
                              "walle context --compare: {a} 세션과 {b} 세션은 비교하지 않는다. 한 에이전트의 세션 둘을 준다."),
    "compare.no_pair": ("No session {side} {at} yet. Start a new session, then run this again.",
                        "{at} {side} 세션이 아직 없다. 새 세션을 연 뒤 다시 돌린다."),
    "compare.split_at": ("Split at {at}. The newest session on each side:", "{at}에서 나눈다. 양쪽의 가장 최근 세션:"),
    "compare.newest": ("The newest session on each side:", "양쪽의 가장 최근 세션:"),
    "compare.other_folders": ("This folder has no session on one side, so the two sessions come from any folder.",
                              "이 폴더의 세션이 한쪽에 없어서 두 세션을 모든 폴더에서 골랐다."),
    "compare.revision": ("counting revision {a} = {b}", "세는 방식 개정 {a} = {b}"),
    "compare.split_changed": ("The first call was split another way: source bytes are not comparable.",
                              "첫 호출을 나눈 방식이 다르다: 원천별 바이트는 비교하지 않는다."),
    "compare.col.source": ("source (bytes)", "원천 (바이트)"),
    "compare.col.tokens": ("est. tokens +/-", "토큰(추정) +/-"),
    "compare.per_session": ("* changes with every session. It is not a setting.", "* 세션마다 바뀐다. 설정이 아니다."),
    "compare.measured": ("Measured first call  {a} -> {b} tokens ({d})", "첫 호출 실측  {a} -> {b} 토큰 ({d})"),
    "compare.one_each": ("One session on each side is not a median: walle eve --around gives medians.",
                         "한쪽에 세션 하나는 중앙값이 아니다: 중앙값은 walle eve --around로 본다."),
    "compare.measured_family": ("Measured first call  {a} ({fa}) and {b} ({fb})", "첫 호출 실측  {a} ({fa}), {b} ({fb})"),
    "compare.family_differs": ("The model families differ, so the measured calls are not comparable.",
                               "모델 계열이 달라 실측 호출은 비교하지 않는다."),
    "compare.no_loaded": ("Loaded and used: a transcript is gone, so walle cannot tell.",
                          "실림과 사용: 세션 기록 하나가 없어서 알 수 없다."),
    "compare.only.servers": ("MCP servers only in {side}:", "{side}에만 있는 MCP 서버:"),
    "compare.only.skills": ("Skills only in {side}:", "{side}에만 있는 스킬:"),
    "compare.inside": ("Largest changes inside a source (bytes):", "원천 안에서 가장 크게 바뀐 것(바이트):"),
    "compare.more": ("and {n} more", "외 {n}개"),
    "compare.applied_head": ("Applied rows: sessions that loaded and used each, before -> since (all folders).",
                             "적용한 행: 실은 세션과 쓴 세션, 이전 -> 이후(모든 폴더)."),
    "compare.applied_line": ("{id} {what}: loaded {la} of {na} -> {lb} of {nb}, used {ua} -> {ub}",
                             "{id} {what}: 실림 {na}개 중 {la}개 -> {nb}개 중 {lb}개, 사용 {ua} -> {ub}"),
    "compare.applied_none": ("{id} {what}: sessions do not show this row. See its check in the plan.",
                             "{id} {what}: 세션 기록에 이 행이 보이지 않는다. 계획의 검증 기준을 본다."),
    "compare.measure_head": ("Measured first call in {folder}, one model family per row:",
                             "{folder}의 첫 호출 실측, 모델 계열마다 한 행:"),
    # walle apply: where to look after the change
    "apply.next": ("Then    walle context --compare {id}  (after a new session)",
                   "그다음  walle context --compare {id}  (새 세션 뒤)"),
    "apply.next_measure": ("        walle eve --around {id}  (after 10 sessions)",
                           "        walle eve --around {id}  (세션 10개 뒤)"),
    "empty.no_rollouts": ("No Codex rollouts in {path}. Open a few Codex threads, then run walle eve --agent codex again.",
                          "{path}에 Codex 기록이 없다. Codex 스레드를 몇 번 연 뒤 walle eve --agent codex를 다시 돌린다."),
    # Terminal brief (walle scan in a terminal)
    "brief.header": ("walle {version}  scan {root}  {agent}  {date}", "walle {version}  scan {root}  {agent}  {date}"),
    "brief.many_header": ("walle {version}  scan {root}  {stacks} stacks  {files} files ({copies} worktree copies)",
                          "walle {version}  scan {root}  스택 {stacks}개  파일 {files}개 (워크트리 사본 {copies}개)"),
    "brief.shared": ("Shared", "공용"),
    "brief.also": ("Also", "그 밖"),
    "brief.next": ("Next", "다음"),
    "brief.other_stack": ("{agent} stack {verdict}, ~{tokens} est. tokens, {n} to read",
                          "{agent} 스택 {verdict}, 약 {tokens} 토큰(추정), 읽어 볼 항목 {n}개"),
    "brief.budget": ("budget {budget} est. tokens", "예산 {budget} 토큰(추정)"),
    "brief.more_rows": ("+{n} more: walle scan {root} --all", "+{n}개 더: walle scan {root} --all"),
    "brief.copies": ("{n} file pairs ({scan})", "파일 쌍 {n}개 ({scan})"),
    "brief.move_size": ("~{tokens}, {share}% of stack", "약 {tokens}, 스택의 {share}%"),
    "brief.folders": ("{n} folders", "폴더 {n}개"),
    "sentence.shared_here.one": ("{file}: 1 finding", "{file}: 발견 1건"),
    "sentence.shared_here.other": ("{file}: {n} findings", "{file}: 발견 {n}건"),
    "table.folder": ("Folder", "폴더"),
    "table.agent": ("agent", "에이전트"),
    "table.tokens": ("est. tokens", "토큰(추정)"),
    "table.verdict": ("verdict", "판정"),
    "table.to_read": ("to read", "읽을 항목"),
    "table.layer": ("layer", "층"),
    "table.file": ("file", "파일"),
    "table.run": ("run", "실행"),
    "table.label": ("label", "라벨"),
    "table.rules": ("rules", "규칙"),
    "table.estimator": ("estimator", "추정기"),
    "table.stack_sum": ("stack sum", "스택 합"),
    "table.open": ("open", "열림"),
    "table.kept": ("kept", "남김"),
    "group.elsewhere": ("Outside this folder's stacks", "이 폴더 스택 밖"),
    "progress.done": ("walle: collected {files} files, compacted {pairs} pairs, sorted {items} items ({secs}s)",
                      "walle: 파일 {files}개 수거, 쌍 {pairs}개 압축, 항목 {items}개 정렬 ({secs}초)"),
    # brief-md: the plugin skill reads it and answers from it
    "brief_md.load": ("{folder} loads ~{tokens} est. tokens before your first prompt (budget {budget}).",
                      "{folder} 폴더는 첫 프롬프트 전에 약 {tokens} 토큰(추정)을 읽는다(예산 {budget})."),
    "brief_md.folder": ("Folder: {folder} ({agent} stack). Paths below are relative to it.",
                        "폴더: {folder} ({agent} 스택). 아래 경로는 이 폴더 기준이다."),
    "brief_md.report": ("Report: {path}", "리포트: {path}"),
    # scan.md
    "md.title": ("walle scan · {root} ({agent})", "walle scan · {root} ({agent})"),
    "md.title_many": ("walle scan · {root}", "walle scan · {root}"),
    "md.counts": ("{stacks} stacks · {files} files ({copies} worktree copies)",
                  "스택 {stacks}개 · 파일 {files}개 (워크트리 사본 {copies}개)"),
    "md.meta": ("scan {date} · rules {rules}", "스캔 {date} · 규칙 {rules}"),
    "md.more": ("+{n} more in scan.json", "+{n}개 더: scan.json"),
    "md.none": ("None.", "없다."),
    "md.copies_row": ("{a} ~ {b}: {counts}", "{a} ~ {b}: {counts}"),
    "md.move_to_parent": ("move to {parent}?", "{parent}로 옮길까?"),
    "md.worktree_row": ("{file}: {versions} versions in {copies} copies, main = {sha}",
                        "{file}: 사본 {copies}개에 판 {versions}개, main = {sha}"),
    "md.not_in_main": ("not in main", "main에 없음"),
    "md.measure_none": ("This run has no session measure. Session cost comes from transcripts: run walle eve.",
                        "이 실행에는 세션 실측이 없다. 세션 비용은 세션 기록에서 잰다: walle eve를 돌린다."),
    "md.method_estimate": ("Token counts are bytes-based estimates (estimator {estimator}).",
                           "토큰 수는 바이트 기반 추정치다(추정기 {estimator})."),
    "md.method_rules": ("Rules {rules}: two runs with the same rules id count findings the same way.",
                        "규칙 {rules}: 규칙 id가 같은 두 실행은 발견을 같은 방식으로 센다."),
    "md.method_load": ("Load rules follow what this machine shows. Connector tools, plugin skills and the system "
                       "prompt are not in the stack. walle eve measures them from transcripts.",
                       "로드 규칙은 이 기계에서 관찰한 것을 따른다. 커넥터 도구, 플러그인 스킬, 시스템 프롬프트는 "
                       "스택에 없다. walle eve가 세션 기록에서 잰다."),
    "md.method_candidates": ("A candidate is a line to read. walle matched words, not meaning.",
                             "후보는 읽어 볼 줄이다. walle는 뜻이 아니라 글자를 맞췄다."),
    "md.cause.1": ("lines that may add a step to every task", "모든 작업에 단계를 더할 수 있는 줄"),
    "md.cause.2": ("lines that may say different things", "다르게 말할 수 있는 줄"),
    "md.cause.3": ("words that name no pattern", "패턴을 말하지 않는 낱말"),
    "md.cause.4": ("copies, dates, model names and old-model habits", "사본, 날짜, 모델명, 구모델 습관"),
    "table.cause": ("rule cause", "규칙 원인"),
    "table.meaning": ("lines it finds", "찾는 줄"),
    # walle show
    "show.resolved": ("{ref} -> {id} (run {run})", "{ref} -> {id} (실행 {run})"),
    "show.no_recent": ("no recent scan for this folder; use the id", "이 폴더의 최근 스캔이 없다. id를 쓴다."),
    "show.no_number": ("no item {ref} in the last scan of this folder", "이 폴더의 마지막 스캔에 {ref}번 항목이 없다."),
    "show.no_item": ("no item {ref} in recent scans; run walle scan", "최근 스캔에 항목 {ref}가 없다. walle scan을 돌린다."),
    "show.changed": ("file changed since scan; run walle scan", "스캔 뒤 파일이 바뀌었다. walle scan을 다시 돌린다."),
    # --out of scan and check: walle never writes into a folder that it scans
    "out.in_scan": ("walle: {path} is inside {root}, which this command scans. "
                    "Use an --out folder outside it, such as ~/.walle/runs.",
                    "walle: {path}는 이 명령이 스캔하는 {root} 안에 있다. 그 밖의 --out 폴더를 쓴다(예: ~/.walle/runs)."),
    # Session alarm: one line of agent context, OVER BUDGET only, at most 200 characters
    "alarm.over": ("walle: this folder loads ~{tokens} est. tokens of instructions before the first prompt "
                   "(budget {budget}); largest {file} ~{largest}. Mention only if asked about instructions or cost.",
                   "walle: 이 폴더는 첫 프롬프트 전에 지시 약 {tokens} 토큰(추정)을 읽는다(예산 {budget}). "
                   "가장 큰 층은 {file} 약 {largest}. 지시나 비용을 물을 때만 말한다."),
    "alarm.over.short": ("walle: this folder loads ~{tokens} est. tokens of instructions before the first prompt "
                         "(budget {budget}). Mention only if asked about instructions or cost.",
                         "walle: 이 폴더는 첫 프롬프트 전에 지시 약 {tokens} 토큰(추정)을 읽는다(예산 {budget}). "
                         "지시나 비용을 물을 때만 말한다."),
    # keep (walle keep)
    "keep.done.one": ("Kept 1 line. walle skips it from the next run. If the line changes, walle counts it again.",
                      "1줄을 남겼다. 다음 실행부터 세지 않는다. 줄이 바뀌면 다시 센다."),
    "keep.done.other": ("Kept {n} lines. walle skips them from the next run. If a line changes, walle counts it again.",
                        "{n}줄을 남겼다. 다음 실행부터 세지 않는다. 줄이 바뀌면 다시 센다."),
    "keep.released": ("Keep released: line changed. The item is open again.",
                      "남김 풀림: 줄이 바뀌었다. 항목이 다시 열렸다."),
    "keep.released.at": ("{id} {where}: keep released: line changed. The item is open again.",
                         "{id} {where}: 남김 풀림: 줄이 바뀌었다. 항목이 다시 열렸다."),
    "keep.kept": ("kept {id} ({where}). walle skips it from the next run. If the line changes, walle counts it again.",
                  "남김 {id} ({where}). 다음 실행부터 세지 않는다. 줄이 바뀌면 다시 센다."),
    "keep.kept_source": ("kept {id} ({where}). walle trim --plan leaves it out from the next plan.",
                         "남김 {id} ({where}). 다음 walle trim --plan부터 빠진다."),
    "keep.already": ("{id} is kept already ({where}).", "{id}는 이미 남겼다({where})."),
    "keep.unkept": ("unkept {id} ({where}). walle counts it again from the next run.",
                    "남김 해제 {id} ({where}). 다음 실행부터 다시 센다."),
    "keep.not_kept": ("{id} is not kept. List: walle keep --list", "{id}는 남긴 목록에 없다. 목록: walle keep --list"),
    "keep.no_ref": ("no item or plan row {ref} in recent runs; run walle scan or walle trim --plan",
                    "최근 실행에 항목이나 계획 행 {ref}가 없다. walle scan이나 walle trim --plan을 돌린다."),
    "keep.need_ref": ("walle keep: give an item id, its number, or a plan row id. List: walle keep --list",
                      "walle keep: 항목 id, 번호, 계획 행 id 중 하나를 준다. 목록: walle keep --list"),
    "keep.list_empty": ("Nothing kept. Keep a line or a source: walle keep <id>",
                        "남긴 것이 없다. 줄이나 원천을 남긴다: walle keep <id>"),
    "keep.list_count": ("{n} kept. File: {path}", "남긴 것 {n}개. 파일: {path}"),
    "keep.no_team_key": ("{id} has no team key: its file is not in a repository with a remote. "
                         "A personal keep works: walle keep {id}",
                         "{id}에는 팀 키가 없다. 파일이 원격이 있는 저장소에 있지 않다. 개인 남기기는 된다: walle keep {id}"),
    "keep.source_no_team": ("{id} is a source, not a line. .wallekeep holds lines only. "
                            "A personal keep works: walle keep {id}",
                            "{id}는 줄이 아니라 원천이다. .wallekeep에는 줄만 둔다. 개인 남기기는 된다: walle keep {id}"),
    # walle apply and walle undo (plan step C5, owner decision (b))
    "apply.head": ("walle apply {id}  plan {plan}", "walle apply {id}  계획 {plan}"),
    "apply.head_dry": ("walle apply --dry-run  plan {plan}. Nothing is written.",
                       "walle apply --dry-run  계획 {plan}. 아무것도 쓰지 않는다."),
    "apply.need_only": ("walle apply: give the row ids to apply: --only <id>[,<id>]. There is no default choice.",
                        "walle apply: 적용할 행 id를 준다: --only <id>[,<id>]. 기본 선택은 없다."),
    "apply.unknown_id": ("walle apply: {ids} is not a row of plan {plan}. Nothing changed.",
                         "walle apply: {ids}는 계획 {plan}의 행이 아니다. 아무것도 바꾸지 않았다."),
    "apply.no_hash": ("walle apply: plan {plan} has no file hash for {file}. Run walle trim --plan again. Nothing changed.",
                      "walle apply: 계획 {plan}에 {file}의 파일 해시가 없다. walle trim --plan을 다시 돌린다. 아무것도 바꾸지 않았다."),
    "apply.hash_differs": ("walle apply: {file} changed since plan {plan} (sha256 {a} -> {b}). "
                           "Run walle trim --plan again. Nothing changed.",
                           "walle apply: 계획 {plan} 뒤에 {file}이 바뀌었다(sha256 {a} -> {b}). "
                           "walle trim --plan을 다시 돌린다. 아무것도 바꾸지 않았다."),
    "apply.not_allowed": ("walle apply: {file} is not a settings file that walle changes. Nothing changed.",
                          "walle apply: {file}은 walle가 바꾸는 설정 파일이 아니다. 아무것도 바꾸지 않았다."),
    "apply.key_not_allowed": ("walle apply: {keys} in {file} is not a key that walle changes. Nothing changed.",
                              "walle apply: {file}의 {keys}는 walle가 바꾸는 키가 아니다. 아무것도 바꾸지 않았다."),
    "apply.in_scan": ("walle apply: {path} is inside {root}, a folder that walle scans. Nothing changed.",
                      "walle apply: {path}는 walle가 스캔하는 폴더 {root} 안에 있다. 아무것도 바꾸지 않았다."),
    "apply.bad_json": ("walle apply: {file} is not a JSON object. Nothing changed.",
                       "walle apply: {file}은 JSON 객체가 아니다. 아무것도 바꾸지 않았다."),
    "apply.conflict": ("walle apply: {keys} already exists in {file}. Nothing changed.",
                       "walle apply: {file}에 {keys}가 이미 있다. 아무것도 바꾸지 않았다."),
    "apply.missing": ("walle apply: {keys} is not in {file}. Nothing changed.",
                      "walle apply: {file}에 {keys}가 없다. 아무것도 바꾸지 않았다."),
    "apply.need_app_closed": ("walle apply: {file} needs --app-closed. Close every Claude app and session first. "
                              "The app rewrites this file while it runs.",
                              "walle apply: {file}에는 --app-closed가 필요하다. 먼저 모든 Claude 앱과 세션을 닫는다. "
                              "앱은 실행 중에 이 파일을 다시 쓴다."),
    "apply.app_running": ("walle apply: a Claude process runs ({procs}). Close it, then run the command again. Nothing changed.",
                          "walle apply: Claude 프로세스가 실행 중이다({procs}). 닫은 뒤 명령을 다시 돌린다. 아무것도 바꾸지 않았다."),
    "apply.ps_failed": ("walle apply: walle cannot list processes, so it cannot tell that the app is closed. Nothing changed.",
                        "walle apply: 프로세스 목록을 읽지 못해 앱이 닫혔는지 알 수 없다. 아무것도 바꾸지 않았다."),
    "apply.write_failed": ("walle apply: writing {file} failed ({error}). Files written before it are restored.",
                           "walle apply: {file}을 쓰지 못했다({error}). 그 전에 쓴 파일은 되돌렸다."),
    "apply.lost": ("apply lost: {file} changed right after walle wrote it. Another program rewrote it. "
                   "Check the file, then walle undo {id} or apply again.",
                   "적용 유실: walle가 쓴 직후 {file}이 바뀌었다. 다른 프로그램이 다시 썼다. "
                   "파일을 확인한 뒤 walle undo {id}로 되돌리거나 다시 적용한다."),
    "apply.changed": ("changed  {id}  {what}", "바꿈     {id}  {what}"),
    "apply.would": ("would    {id}  {what}", "바꿀 것  {id}  {what}"),
    "apply.already": ("already  {id}  {what}", "이미     {id}  {what}"),
    "apply.manual_head": ("Manual rows: walle cannot reach these places. Change them yourself:",
                          "수동 행: walle가 닿지 않는 곳이다. 직접 바꾼다:"),
    "apply.unconfirmed": ("[unconfirmed] walle has not confirmed this menu path.",
                          "[미확인] walle가 이 메뉴 경로를 확인하지 않았다."),
    "apply.manual_undo": ("undo: {undo}", "되돌리기: {undo}"),
    "apply.patch_head": ("Patch rows: walle does not edit these files. Your agent applies the patch in the repository:",
                         "patch 행: walle는 이 파일을 고치지 않는다. 에이전트가 저장소에 patch를 적용한다:"),
    "apply.backup": ("Backup  {path}  (undo: walle undo {id})", "백업  {path}  (되돌리기: walle undo {id})"),
    "apply.check": ("Check   start a new session, then run walle context --last.",
                    "확인   새 세션을 연 뒤 walle context --last를 돌린다."),
    "apply.nothing": ("Nothing to write: every key already has the planned value.",
                      "쓸 것이 없다: 모든 키가 이미 계획한 값이다."),
    "undo.head": ("walle undo {id}", "walle undo {id}"),
    "undo.no_backup": ("walle undo: no backup {id} in {path}.", "walle undo: {path}에 백업 {id}가 없다."),
    "undo.not_walle": ("walle undo: {path} is not a backup that walle apply wrote. Nothing changed. Restore it by hand.",
                       "walle undo: {path}는 walle apply가 쓴 백업이 아니다. 아무것도 바꾸지 않았다. 직접 복원한다."),
    "undo.already": ("walle undo: {id} was undone at {at}. Nothing changed.",
                     "walle undo: {id}는 {at}에 이미 되돌렸다. 아무것도 바꾸지 않았다."),
    "undo.changed_since": ("walle undo: {file} changed after apply {id}. Nothing is restored.",
                           "walle undo: apply {id} 뒤에 {file}이 바뀌었다. 아무것도 복원하지 않았다."),
    "undo.diff_line": ("  {kind}: {keys}", "  {kind}: {keys}"),
    "undo.diff_more": ("  and {n} more key paths", "  키 경로 {n}개 더"),
    "undo.touched": ("  a key this apply changed: {keys}", "  이 apply가 바꾼 키: {keys}"),
    "undo.by_hand": ("The file before the apply: {path}. Put the keys back by hand, or run walle undo after the file "
                     "is back to the apply's state.",
                     "apply 전 파일: {path}. 키를 직접 되돌리거나, 파일이 apply 직후 상태로 돌아온 뒤 walle undo를 돌린다."),
    "undo.restored": ("restored {file}", "복원함 {file}"),
    "undo.lost": ("undo lost: {file} changed right after walle restored it. Another program rewrote it.",
                  "되돌리기 유실: walle가 복원한 직후 {file}이 바뀌었다. 다른 프로그램이 다시 썼다."),
    "diff.kind.added": ("added", "추가"),
    "diff.kind.removed": ("removed", "삭제"),
    "diff.kind.changed": ("changed", "바뀜"),
    # walle runs, walle pin, walle diff (brief step 6)
    "runs.empty": ("No runs in {path}. Run walle scan first.", "{path}에 실행이 없다. 먼저 walle scan을 돌린다."),
    "runs.pinned": ("pinned", "고정"),
    "runs.note.trim": ("rows: {n}", "행 {n}개"),
    "runs.note.apply": ("plan {plan}: {n} changed, {status}", "계획 {plan}: {n}개 바꿈, {status}"),
    "runs.note.undo": ("undo of {apply}: {status}", "{apply} 되돌림: {status}"),
    "runs.note.diff": ("{a} -> {b}", "{a} -> {b}"),
    "runs.note.context": ("{agent} session {session}", "{agent} 세션 {session}"),
    "runs.note.context_usage": ("{agent} usage of a period", "{agent} 기간의 실림 대 사용"),
    "runs.note.measure": ("{agent} around {ref}", "{agent} {ref} 전후"),
    "runs.note.measure_period": ("{agent} one period", "{agent} 한 기간"),
    "runs.note.compare": ("{agent} {refs}", "{agent} {refs}"),
    "table.kind": ("kind", "종류"),
    "diff.state.closed": ("closed", "닫힘"),
    "diff.state.new": ("new", "새 항목"),
    "diff.state.moved": ("moved", "옮김"),
    "diff.state.edited": ("edited (review)", "고침 (검토)"),
    "diff.state.kept": ("kept", "남김"),
    "diff.stale_head": ("Files changed since the newest scan", "최신 스캔 뒤 바뀐 파일"),
    "pin.done": ("pinned {id} as the baseline of {roots}. walle diff compares the newest scan with it.",
                 "{id}를 {roots}의 기준 실행으로 고정했다. walle diff는 최신 스캔을 이 실행과 비교한다."),
    "pin.cleared": ("cleared the pin of {roots}. The baseline is the previous scan again.",
                    "{roots}의 고정을 풀었다. 기준 실행은 다시 직전 스캔이다."),
    "pin.none": ("No pin for {roots}.", "{roots}에는 고정이 없다."),
    "pin.not_scan": ("walle pin: {id} is not a scan in {path}. List: walle runs",
                     "walle pin: {id}는 {path}의 스캔이 아니다. 목록: walle runs"),
    "pin.need_run": ("walle pin: give a run id, or --clear. List: walle runs",
                     "walle pin: 실행 id나 --clear를 준다. 목록: walle runs"),
    "diff.no_scan": ("walle diff: no scan of {folder} in the runs folder. Run walle scan, or name two runs: walle diff A B",
                     "walle diff: 실행 폴더에 {folder}의 스캔이 없다. walle scan을 돌리거나 두 실행을 준다: walle diff A B"),
    "diff.not_found": ("walle diff: {ref} is not a run id or a scan.json path.",
                       "walle diff: {ref}는 실행 id도 scan.json 경로도 아니다."),
    "diff.same_run": ("The baseline is the newest scan ({id}). Run walle scan after a change, then walle diff.",
                      "기준 실행이 최신 스캔이다({id}). 바꾼 뒤 walle scan을 돌리고 walle diff를 돌린다."),
    "diff.args": ("walle diff: give no run, last, or two runs (ids or scan.json paths).",
                  "walle diff: 인자 없음, last, 두 실행(id 또는 scan.json 경로) 중 하나를 준다."),
    "diff.rules_same": ("rules {a} = {b}", "규칙 {a} = {b}"),
    "diff.rules_changed": ("rules changed {a} -> {b}: finding counts not comparable",
                           "규칙이 바뀜 {a} -> {b}: 발견 수는 비교하지 않는다"),
    "diff.estimator_same": ("estimator {a} = {b}", "추정기 {a} = {b}"),
    "diff.estimator_changed": ("estimator changed: token totals not comparable",
                               "추정기가 바뀜: 토큰 합은 비교하지 않는다"),
    "diff.counting_changed": ("counting revision changed: token totals not comparable",
                              "세는 방식 개정이 바뀜: 토큰 합은 비교하지 않는다"),
    "diff.stack": ("Stack", "스택"),
    "diff.items": ("Items", "항목"),
    "diff.items_line": ("closed {closed} | new {new} | moved {moved} | edited {edited} (review) | kept {kept}",
                        "닫힘 {closed} | 새 항목 {new} | 옮김 {moved} | 고침 {edited} (검토) | 남김 {kept}"),
    "diff.applied": ("Applied", "적용"),
    "diff.applied_line": ("{id} (plan {plan}, {n} changed)", "{id} (계획 {plan}, {n}개 바꿈)"),
    "diff.undone_line": ("{id} undo of {apply}", "{id} {apply} 되돌림"),
    "diff.pinned": ("baseline pinned", "기준 실행 고정"),
    "diff.stale": ("{n} files changed since {id}. Run walle scan.", "{id} 뒤에 파일 {n}개가 바뀌었다. walle scan을 다시 돌린다."),
}


def t(key: str, lang: str = "en", **fields) -> str:
    """The string for key in lang, with its fields filled. Unknown keys and missing fields raise."""
    try:
        en, ko = STRINGS[key]
    except KeyError:
        raise UnknownString(key) from None
    if lang not in LANGS:
        raise ValueError(f"unknown language {lang!r}: use one of {', '.join(LANGS)}")
    text = ko if lang == "ko" else en
    return text.format(**fields) if fields or "{" in text else text


def tn(key: str, n: int, lang: str = "en", **fields) -> str:
    """A string with a count: key.one when n is 1, else key.other."""
    return t(f"{key}.{'one' if n == 1 else 'other'}", lang, n=n, **fields)


def resolve_lang(flag: str | None = None, environ: dict | None = None) -> str:
    """--lang when given, else Korean when LC_ALL, LC_MESSAGES or LANG starts with "ko", else English."""
    if flag:
        if flag not in LANGS:
            raise ValueError(f"unknown language {flag!r}: use one of {', '.join(LANGS)}")
        return flag
    env = os.environ if environ is None else environ
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = env.get(name)
        if value:
            return "ko" if value.lower().startswith("ko") else "en"
    return "en"


def round_tokens(n: int, budget: int | None = None) -> str:
    """An estimated token count as written: rounded to 10 below 1,000 and to 100 above, with commas.

    Within 100 of the budget the count is rounded to 10, and a count on one side of the
    budget is never written as the budget itself.
    """
    if n <= 0:
        return "0"
    near = budget is not None and abs(n - budget) <= 100
    step = 10 if n < 1000 or near else 100
    r = max(step, int(n / step + 0.5) * step)
    if near and n != budget and r == budget:
        r = budget - step if n < budget else budget + step
    return f"{r:,}"


def approx(n: int, budget: int | None = None) -> str:
    return "~" + round_tokens(n, budget)


def label(item: dict, lang: str = "en", short=str) -> str:
    """The display label of a scan.json item. short() shortens a path (for example to ~/...)."""
    fields = {}
    if item["label"] in ("dated_lines", "memory_lines"):
        fields["n"] = item["group_size"]
    if item["label"] in ("source_of", "copy_of"):
        fields["file"] = short(item.get("of") or "")
    return t(f"label.{item['label']}", lang, **fields)
