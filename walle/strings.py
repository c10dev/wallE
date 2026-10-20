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
    "measure.too_few": ("Too few sessions to compare: {n} since {date}. Need 10.",
                        "비교할 세션이 적다: {date} 이후 {n}개. 10개가 필요하다."),
    "measure.within_noise": ("No clear change for {folder} ({model}; before n={before}, after n={after}). "
                             "The difference is within noise.",
                             "{folder}의 변화가 분명하지 않다({model}, 이전 {before}개, 이후 {after}개). "
                             "차이가 잡음 범위 안이다."),
    "measure.not_proof": ("A change in median is not proof of cause: task mix and connectors move it.",
                          "중앙값의 변화는 원인의 증거가 아니다: 작업 구성과 커넥터도 값을 움직인다."),
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
