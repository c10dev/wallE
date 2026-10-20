# Roadmap

walle grows from one engine into several surfaces. Every surface calls the same `scan.json` and `eve.json`.

## 0.1 — engine (done)

- `scan`, `eve`, `diff`, `check`, HTML report
- Claude Code plugin with the `walle` skill and an opt-in session-start alarm
- Claude Code and Codex load rules, plus Cursor, Copilot, Gemini, Windsurf and Cline rule files

## 0.2 — judge (built; judged by the user's agent, no API key)

Static rules catch size, duplicates, dates, model names and vague words.
They miss meaning: "send to DUTY" and "never send messages" is a conflict that no regex sees.

- Done: `walle judge` writes the conflict pairs and cause-1 candidates as batch files with a short rubric.
  The user's agent (or one subagent per batch) writes the verdicts; `walle judge --import` turns them into `judge.md` and `judge.json`.
  walle needs no API key and makes no network call.
- Done: judgments are cached by a hash of the rule text and the rubric version. The next export holds only changed rules.
- Done: the judge runs on demand only. It never runs in the hook. `--dry-run` shows the item count, the tokens and each item with file:line first.
- Later, optional: a headless runner (`claude -p` per batch) for CI, still without an API key.
- Next: run it once on a real scan and compare the verdicts with the manual audit.
- Done: more conflict candidates. Three conflicts from the manual audit were not drift pairs: two rules in one file, a parent and a child CLAUDE.md that load together, and one protocol written in two repositories.
  `scan` now pairs the rules of one load stack that share a key term and differ in sign or in a message form (`topic`).
  It also pairs rules of two repositories that write one message in different forms (`protocol`). The judge reads both as conflicts.
  On the author's machine all three audited pairs are now candidates.
- Next: conflict candidates without a shared key term, Korean verbs with a one-syllable stem, and negation in more languages.

## 0.3 — trim and measure

- `walle trim --plan`: a patch that removes duplicates and moves cost candidates into on-demand files. Write it to the run folder, never apply it.
- `walle ablate`: run the same prompt with and without a block of rules (`claude -p`, fixed effort), then compare steps and pass/fail. This is the before/after check the vendor guides recommend.

## 0.4 — surfaces

- **GitHub Action:** comment on PRs that change instruction files, with the stack delta and new drift.
- **Desktop or web app:** history of runs per repo, trend charts from `eve`, and click-through from a finding to the line.
- **VS Code / Cursor extension:** inline hints on CLAUDE.md and AGENTS.md lines (duplicate of X, dated, vague).
- **More agents:** load rules for Gemini CLI, Cursor and Copilot when their docs or transcripts confirm them.

## Adoption

- One-line install for each surface (pipx, plugin marketplace, Action).
- A public "instruction health" badge from `walle check`.
- A shared rule pack: lexicons for causes 3 and 4 in more languages, contributed as data files.
- Telemetry stays off. Reports stay on the user's machine. walle sends nothing; judging happens in the user's own agent.
