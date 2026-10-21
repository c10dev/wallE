---
name: walle
description: Audit the instruction files coding agents load (CLAUDE.md, AGENTS.md, skills, MEMORY.md, Cursor/Copilot/Gemini rules) for size, duplicates, drift and vague or outdated rules, and measure their cost from session transcripts. Use when the user asks to audit, trim or compare agent instructions, asks why sessions start heavy or slow, or asks "CLAUDE.md 정리", "지시 문서 점검", "AGENTS.md 줄이기".
---

# walle

Run `walle` if it is on PATH. Otherwise run `python3 <plugin root>/bin/walle`; the plugin root is two folders above this file. It needs Python 3.11+ and nothing else. Reports go to `~/.walle/runs/`, never into the audited folder.

1. Run `walle scan --format brief-md [folder]` (default: the current folder; add `--lang ko` when the user writes Korean) and answer from its output. Read the full report (the `Report:` line) only when the user asks about something the brief does not show.
2. Answer in at most 8 lines, in the user's language. Use no headings and no tables. One exception: when 4 or more stacks are OVER BUDGET, show them in one table of at most 6 rows.
   - First line: the brief's `walle:` line, word for word.
   - Then at most 3 items (5 if the user asks for more), one line each: the verb, `file:line`, a quote of at most 100 characters cut at a word boundary with `...`, and walle's label.
   - Under each candidate, one line that starts with `My read:` (Korean: `에이전트 판단:`). First read the 3 lines above and below it in the file. Never call a candidate a problem or an error.
   - Add the `Shared:` line and the report path only if lines are left.
   - Last line: one yes/no question. Edit no file before the user says yes.
3. Give session measures only when the user asks. Real cost comes from transcripts: `walle eve [--since YYYY-MM-DD]`.
4. Before you edit a shared file (one outside the folder), name it and say how many folders load it.
5. After an edit, run the scan again and `walle diff <old scan.json> <new scan.json>` (scan.json is next to each report). Report one line, for example `Stack ~3,200 -> ~3,180 est. tokens; item 1 closed.`
6. `walle show <id>` prints one item with the lines around it. To judge candidates with your own agent, read `judging.md` next to this file.
7. When the user asks what to take out of every session's context (connectors, MCP servers, plugins, skills, hook output), read `trimming.md` next to this file.
