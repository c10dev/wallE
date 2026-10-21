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
- Done: one run on a real scan (296 candidates, 8 subagents). The three conflicts that the manual audit found were judged real; 14 real conflicts and 3 real cost items in all.
- Done: more conflict candidates. Three conflicts from the manual audit were not drift pairs: two rules in one file, a parent and a child CLAUDE.md that load together, and one protocol written in two repositories.
  `scan` now pairs the rules of one load stack that share a key term and differ in sign or in a message form (`topic`).
  It also pairs rules of two repositories that write one message in different forms (`protocol`). The judge reads both as conflicts.
  On the author's machine all three audited pairs are now candidates.
- Next: conflict candidates without a shared key term, Korean verbs with a one-syllable stem, and negation in more languages.

### 0.2 — one answer per folder (scan.json schema 2)

Every surface gives the same answer in the same order: one verdict word, at most 3 lines in your own files to read first, then folded groups.

- Done: engine v2 and run records (`walle/triage.py`, `walle/runs.py`).
  `scan.json` schema 2 adds `run`, `items` and `triage`, and renames nothing.
  Items have stable ids, a scope (session start, on demand, not loaded together, other agent, elsewhere), an ownership (your files, memory, shared, skills) and whole text with line ranges.
  Each stack gets a verdict (OVER BUDGET, TRIM, LEAN) and a Start here list. DELETE keeps the broader copy and never touches a shared file.
  Consecutive copied lines, dated lines of one file, a MEMORY.md and a whole copied file are one item each.
  Each scan adds a line to `runs/index.jsonl` with its parent run, rules hash, estimator and counting revision.
- Done: one table of display strings in English and Korean (`walle/strings.py`): verdicts, verbs, item labels, fixed terms, reasons and empty states.
  An unknown key fails a test. The command name stays `walle eve`; `walle measure` stays its alias.
- Next: the terminal brief as the default TTY output of `walle scan`, `--lang en|ko` (default from `LANG`), `walle show`, and Markdown for agents (`--format brief-md`).
- Done: `walle keep` (personal `keep.json` next to the runs folder, team `.wallekeep`). A kept item leaves the verdict, Start here and the trim plan; a changed line releases its keep once.
  The team key uses the remote URL and the path in the repository, so it is the same in every clone. A repository without a remote has no team key yet.
- Next: `walle runs`, `walle pin` and `walle diff` without arguments.
- Next: judge verdicts in the items. A term clash that the judge calls real becomes a conflict (PICK ONE); "no conflict" moves an item to its own folded group.
- Next: the HTML report, `walle check` for GitHub (annotations, a PR comment, `--fail-on`, a badge), and the measure rules (p25-p75, one model family per row).
  The badge (`walle check --badge`) will follow the `--out` rule: walle will refuse a badge path inside a folder that it scans.

## 0.3 — trim and measure

- Done: `walle trim --plan`: a plan of trim actions with the expected saving, the risk, the undo and the check for each. Instruction-file changes are patches in the run folder. Rows that need a judgment go to judge batch files (`walle trim --import`).
- `walle apply <run> --only <id>` and `walle undo <apply-id>`: walle changes only the settings keys the user picks, after a backup, and `walle undo` puts them back. It changes instruction files only as patches in the run folder. It writes nothing into the project folders it scans.
- `walle ablate`: run the same prompt with and without a block of rules (`claude -p`, fixed effort), then compare steps and pass/fail. This is the before/after check the vendor guides recommend.

## 0.4 — surfaces

- **GitHub Action:** comment on PRs that change instruction files, with the stack delta and new drift.
- **Desktop or web app:** history of runs per repo, trend charts from `eve`, and click-through from a finding to the line.
- **VS Code / Cursor extension:** inline hints on CLAUDE.md and AGENTS.md lines (duplicate of X, dated, vague).
- **More agents:** load rules for Gemini CLI, Cursor and Copilot when their docs or transcripts confirm them.

## Adoption

- One-line install for each surface (pipx, plugin marketplace, Action).
- A public "instruction stack" badge from `walle check`: the verdict word and `~tokens/budget`.
- A shared rule pack: lexicons for causes 3 and 4 in more languages, contributed as data files.
- Telemetry stays off. Reports stay on the user's machine. walle sends nothing; judging happens in the user's own agent.
