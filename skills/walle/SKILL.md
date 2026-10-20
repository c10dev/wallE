---
name: walle
description: Audit the instruction files coding agents load (CLAUDE.md, AGENTS.md, skills, MEMORY.md, Cursor/Copilot/Gemini rules) for size, duplicates, drift and vague or outdated rules, and measure their cost from session transcripts. Use when the user asks to audit, trim or compare agent instructions, asks why sessions start heavy or slow, or asks "CLAUDE.md 정리", "지시 문서 점검", "AGENTS.md 줄이기".
---

# walle

Run `walle` if it is on PATH. Otherwise run `python3 <plugin root>/bin/walle`; the plugin root is two folders above this file. It needs Python 3.11+ and nothing else.

1. `walle scan [folder ...]` (default: current folder). It prints the report path.
2. For real cost, `walle eve [--since YYYY-MM-DD] [--before YYYY-MM-DD]`. It reads `~/.claude/projects` transcripts and prints a report path.
3. Read the Markdown report. Section 1 gives the stack per folder, section 3 gives flags by cause, section 4 gives duplicates, drift and other conflict candidates (topic, protocol), section 5 gives worktree copies.
4. Report the top findings with file:line. scan.md shows the first items of each kind; scan.json has all of them. Read a line before you call it a problem. `walle judge` covers cause 1 and conflict candidates; cause 4 flags (dates, model names) are for the user to read.
5. To judge the candidates, read `judging.md` next to this file and follow it. You judge them; walle needs no API key and makes no network call.
6. Do not edit instruction files unless the user asks. After an edit, run `scan` again and `walle diff <old scan.json> <new scan.json>`.

Add `--html` to `scan` for a page the user can filter. Reports go to `~/.walle/runs/`, never into the audited folder.
