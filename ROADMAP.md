# Roadmap

walle grows from one engine into several surfaces. Every surface reads the same `scan.json` and `eve.json`.

Releases 0.3.0 to 0.8.0 follow a comparison of walle with the tools that now do part of its work (Claude Code `/usage`, `/doctor`, `/context`, `claude plugin eval`).
They put first the numbers that users compare with Claude Code, then the check that no other tool makes: after any change, real sessions got lighter and not worse.
The open items of 0.2, 0.3 and the old 0.4 (surfaces) moved into these releases or to Later.

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
- Done: one run on a real scan (296 candidates, 8 subagents). The three conflicts that the manual audit found were judged real; 14 real conflicts and 3 real cost items in all.
- Done: more conflict candidates. Three conflicts from the manual audit were not drift pairs: two rules in one file, a parent and a child CLAUDE.md that load together, and one protocol written in two repositories.
  `scan` now pairs the rules of one load stack that share a key term and differ in sign or in a message form (`topic`).
  It also pairs rules of two repositories that write one message in different forms (`protocol`). The judge reads both as conflicts.
  On the author's machine all three audited pairs are now candidates.

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
- Done: the terminal brief is the default TTY output of `walle scan`. `--lang en|ko` sets its language; the default comes from `LC_ALL`, `LC_MESSAGES` or `LANG`.
- Done: `walle show` (one item of a recent scan with the lines around it) and Markdown for agents (`walle scan --format brief-md`).
- Done: `walle keep` (personal `keep.json` next to the runs folder, team `.wallekeep`). A kept item leaves the verdict, Start here and the trim plan; a changed line releases its keep once.
  The team key uses the remote URL and the path in the repository, so it is the same in every clone. A repository without a remote has no team key yet.
- Done: `walle runs`, `walle pin`, and `walle diff` without arguments or with `last`. Item states closed, new, moved, edited and kept. The diff names the applies between the two scans.
- Done: the history page `~/.walle/runs/index.html` (in the runs folder of `--out`): every run, its estimator and counting revision, pins, and each apply with the runs that measure it.
- Done: the Sessions lines of `walle diff` (step 9): the measured first-call input of the folder that the diff compares, split at the newest apply between the two scans. A `Next` line names `walle measure --around` while the since side is short. Claude Code sessions only.
- Done: the measure rules in `walle eve`: one folder, one model family per row, median [p25-p75] and n, no value under 5 sessions, a change only with 10 on each side, `--around <apply-id|date>`, `--across-folders`, Codex threads.
- Done: the Measure section of `scan.html` draws the before and since panels from `eve.json`.

## 0.3 — trim and measure

- Done: `walle trim --plan`: a plan of trim actions with the expected saving, the risk, the undo and the check for each. Instruction-file changes are patches in the run folder. Rows that need a judgment go to judge batch files (`walle trim --import`).
- Done: `walle context` for one session (`--last`, `--session`, `--agent claude|codex`) and `--usage`: loaded against used for every main session of a period (`--since`, `--before`).
- Done: `walle context --compare A B` and `walle context --compare <apply-id>`: two first calls source by source, the applied rows with the sessions that loaded and used them, and the measured medians around the apply.
- Done: `walle apply <run> --only <id>` and `walle undo <apply-id>`: walle changes only the settings keys the user picks, after a backup, and `walle undo` puts them back. It changes instruction files only as patches in the run folder. It writes nothing into the project folders it scans.
- Done: claude.ai connector rows. In `cli`, IDE and `sdk-cli` sessions one `deniedMcpServers` entry turns a connector off (a walle row). claude-desktop sessions get manual rows.
- Done: billed first-call input. `walle eve`, `walle context` and `--compare` weight cache reads and writes by price (`walle/cost.py`), with dollars at list price in `walle context` and `--compare`.
- Done: the `eve` subagent (`agents/eve.md`). After `walle apply` it runs the measure commands and reports per surface. It is read-only.
- Done: `walle app`, a local web app on 127.0.0.1 (`--port`, `--no-open`, `--allow-host`) with the views Efficiency, Scan, Measure, Trim, Runs and Keep.
  It runs read commands only and refuses `apply`, `undo`, `keep`, `unkeep` and `pin`. Its scan does not change `keep.json`.
  The Efficiency page shows the stack, the measured first call with a composition bar of the newest session, billed tokens and dollars, the open trim rows as a savings list, and each apply with its undo and its `--compare` result.
- Done: `walle scan --label` (a name in the run index) and `--progress json` (stage events for the app).

### 0.3.0 — first public release

Close 0.3 with what is built, fix the one wrong price, and publish a release that installs in one line.

- Done: `walle/cost.py` prices a Sonnet 5.5 cache read at $0.10 per million tokens (it was $0.20), as Anthropic's price page lists. The `claude-sonnet-5` row stays at $0.20, as the page lists.
  `PRICES_AS_OF` is 2026-10-23, with the price page URL above it. README.md gives Sonnet 5.5 the 0.05x read rate. `walle/eve.py` CACHE_VERSION 6 makes `walle eve` price its cached sessions again.
  Proof: `test_sonnet_5_5_reads_at_the_list_price` in `tests/test_cost.py`, and every row of the price table checked against the page on 2026-10-23 (PR #7). Claude Code sessions only: Codex is not priced.
- Done: a session whose transcript file is deleted still counts in `walle context --usage`, `walle trim` and `walle eve`, also when no transcript file is left. Its cached rows keep counts, names, its folder, transcript path, start time, surface and model, and no prompt or hook output text (a hook output label becomes a digest). README says how to delete them. A CACHE_VERSION change still drops them, until 0.4.0.
  Proof: the deleted-transcript tests in `tests/test_context.py`, `tests/test_measure.py`, `tests/test_compare.py`, `tests/test_trim.py` and `tests/test_runs.py`, on synthetic fixtures. Undoing any one of the 15 rules of the change fails one of them. Claude Code sessions only.
- Done: `walle --version` prints `walle <version>` and exits 0. CI runs it after it installs the package.
  Proof: `test_version_flag_prints_the_package_version` in `tests/test_version.py`, and the install step in `.github/workflows/test.yml` (PR #9). It reads no agent session.
- Done: one version string in `pyproject.toml`, `walle/__init__.py`, `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`, and a test that they match. The version stays 0.1.0 until the release commit.
  Proof: `test_the_four_files_give_one_version` in `tests/test_version.py`. A different version in any one of the four files fails it (PR #9). It reads no agent session.
- A release workflow: a `v*` tag builds the sdist and the wheel and publishes them to PyPI. A test checks that the wheel holds `appui`, `fonts` and `report.html`.
- README.md: remove "Until the package is on PyPI". README.md and README.ko.md: name the repository (`c10dev/wallE`) in `claude plugin marketplace add`.
- The release notes and the CI example in README.md list the known limits: the session-start stack misses `.claude/rules` and the AGENTS.md fallback, so verdicts and `walle check` exit codes can change in 0.4.0. Savings are a per-session low end.
- Maintainer steps: a PyPI account and a trusted publisher for `walle-cli`, then the `v0.3.0` tag.
- Exit: CI passes on Python 3.11 and 3.13. In a new HOME, `pipx install walle-cli==0.3.0` works and `walle --version` prints 0.3.0.
  In a new HOME, `claude plugin marketplace add c10dev/wallE` and `claude plugin install walle@walle` work (checked by hand).
  Exit: after one transcript is deleted, the session counts of `walle context --usage` and `walle eve` stay the same, and the 10-session threshold of `walle trim` still counts that session.

## 0.4.0 — count what Claude Code loads

The sessions and files that walle counts match what Claude Code loads. Verdicts and `walle check` exit codes can change, so this is a minor release and its notes say so first.

- First, an anonymized fixture of a real session that loaded `.claude/rules` files and an AGENTS.md.
- The stack adds `.claude/rules/*.md` without `paths:`, `~/.claude/rules`, `<folder>/.claude/CLAUDE.md`, and AGENTS.md when a folder has no CLAUDE.md. It drops the files in `claudeMdExcludes` (`walle/collect.py`, `walle/stack.py`).
  `.claude/rules` files then also join the conflict pairs.
- `walle context` lists the files that a session loaded but the stack misses, and the reverse.
- Caches: a new `CACHE_VERSION` migrates the rows instead of dropping the file. A cache from a newer walle stays, read-only, with a warning: the plugin copy and the pipx copy can differ.
- walle reads `cleanupPeriodDays`. It warns when the period of `walle context --usage`, `walle trim` or `walle eve` (the before side of `--around`) starts more than that many days ago. walle does not change the key.
- Exit: after `CACHE_VERSION` goes up, the rows of deleted sessions stay, and the session counts of `walle context --usage` and `walle eve` stay the same.
  Exit: on the fixture, the session-start stack lists the `.claude/rules` file and the AGENTS.md of a folder without CLAUDE.md, and `walle context` prints no "loaded, not in the stack" line. After one loaded file is deleted from the fixture folder, it prints exactly one such line.
  Exit: a file in `claudeMdExcludes` is not in the stack, and `<folder>/.claude/CLAUDE.md` is.
  Exit: with `cleanupPeriodDays` set to 7, `walle eve --around <a date 20 days ago>` prints the warning. With the key unset, it does not.

## 0.5.0 — every call

walle reads every call of a session, not only the first.

- First, an anonymized multi-call fixture: several calls, a `subagents/agent-*.jsonl` file, and a cache rewrite after the second call.
- A per-call usage parser replaces the first-call stop in `walle/transcript.py`. It gives the cache rewrites per session and `walle context --cache`.
- A mark for experiment sessions (for example from `walle ablate`): walle records their session ids in `~/.walle`. The `sdk-cli` entrypoint alone is not a mark. Marked sessions stay out of `walle eve`, `walle trim` and the usage totals.
- Exit: on the fixture, `walle context --cache` shows the call count, the subagent calls and the rewrite count that the fixture holds.
  Exit: when one fixture session is marked, the session counts of `walle eve`, `walle trim --plan` and `walle context --usage` drop by one. An unmarked `sdk-cli` session still counts.

## 0.6.0 — exact savings

A saving shows its share of real usage and its dollars at list price, not a per-session low end.

- `cost.PRICES` gets an output price. Code reads a price by name, not by tuple position.
- A saving weights each call by its cache split (read, 5-minute write, 1-hour write) and counts subagent calls. This replaces the per-session low end in `appdata.savings()`.
- Each row shows its share of the period's total from local transcripts (the page names this denominator) and dollars for Claude rows.
  walle shows no plan-limit percentage: that needs an account endpoint.
- Skill rows: when a session's skill list overflowed its budget, the saving is 0 and the row says why.
- Exit: on the 0.5.0 multi-call fixture, the billed total and the dollars of `walle context`, subagent calls included, equal a hand calculation from `cost.PRICES` to the cent.
  Exit: the Efficiency response in `tests/test_app.py` holds each row's share and dollars, and `walle/appui/app.js` has no "low end" text.
  Exit: on an overflow fixture, the skill saving is 0.

## 0.7.0 — check any change

`walle eve` checks a change that walle did not make (by hand, or a fix that Claude Code `/doctor` or `/usage` suggests), and it corrects for Claude Code versions.

- `walle eve` records the Claude Code version of each session. `eve.json` shows the version mix on each side.
- `--around-change` splits at the time the stack hash or the hash of the loaded instructions changed. A source that was turned off and later loads again (by hand or by a sync) is marked "came back".
- The measure rules stay: no value under 5 sessions, a change only with 10 on each side. The comparison stays a pure function of the sessions and the split, with no random step.
- Quality signals beside the token change: tool errors (`is_error`) and repeated reads, per Claude Code version. They do not enter the verdict.
  Retries count only when there are fixtures from Claude Code versions that record them. Without such fixtures, 0.7.0 ships without retries and Later lists them.
- The Measure section of `scan.html` shows the version mix.
- Exit: `--around-change` splits a fixture at its hash change, and shows only n when a side has fewer than 10 sessions.
  Exit: the same input gives a byte-identical `eve.json`.
  Exit: one `is_error` counts as one tool error and does not change the verdict. Three reads of one file count as two repeated reads. A source that a fixture turns off and later loads again shows "came back".
  Exit: the `scan.html` data snapshot (`tests/snapshots/report_html_real0817.json`) holds the version mix in the Measure section.

## 0.8.0 — GitHub surfaces

The corrected numbers reach CI and pull requests. walle itself still sends nothing; the Action posts comments from the user's own workflow.

- Judge verdicts in the items, first. A test that links each item id to its `judge.json` hash comes before this change. A term clash that the judge calls real becomes a conflict (PICK ONE); "no conflict" moves an item to its own folded group.
  The PICK ONE count and `walle check` exit codes can change, and the release notes say so first.
- `walle check` for GitHub: annotations, `--fail-on`, and a badge (`walle check --badge`). The badge follows the `--out` rule: walle refuses a badge path inside a folder that it scans.
- GitHub Action (`action.yml`): a comment on PRs that change instruction files, with the stack delta, new drift and the PICK ONE count. It opens no PR and edits no file.
- Exit: without `judge.json`, a fixture gives the same verdicts and `walle check` exit codes as in 0.7.0.
  Exit: `walle check --fail-on trim` prints `::warning` annotations and exits 1 on TRIM. `walle check --badge` with a path inside a scanned folder exits 2 and writes no badge file.
  Exit: a workflow in this repository runs the Action on a fixture PR event and checks the comment body.
  Exit: `walle/*.py` has no `urlopen`, `requests` or `http.client`.

## Later

In this order:

- Stale references and MOVE patches: flag paths and commands in instruction files that no longer exist. Write a patch that moves a MOVE section to `.claude/rules/<name>.md` with `paths:`. No model call.
- `walle ablate`, for rule blocks only: CLAUDE.md, `@import` files, MEMORY.md and `.claude/rules` blocks. Plugins and skills already have `claude plugin eval`.
  It runs the same prompt with and without one block at a fixed effort, then compares steps and pass/fail. This is the before/after check the vendor guides recommend.
  The first version exports a block as a `claude plugin eval` plugin or a Promptfoo config; a walle runner is the fallback. Runs stay in `~/.walle` with `--no-publish`, and their sessions carry the 0.5.0 mark.
  It waits for two decisions by the maintainer: the default model and the first repository to measure. It goes into the next minor release after both.
- Codex rows in the trim plan (plugin and skill switches in `~/.codex/config.toml`), after a test that they act. They show tokens only: Codex is not priced.
- The `name-only` skill state. It widens what `walle apply` may set, so it needs a decision first.
- Conflict candidates without a shared key term, Korean verbs with a one-syllable stem, and negation in more languages. False-positive tests come first, because `.claude/rules` (0.4.0) adds pairs.
- In `walle app`: trend charts from `eve.json` (after 0.7.0) and click-through from a finding to the line. Local only.
- Retries per Claude Code version, if 0.7.0 ships without them.
- A walle mod panel (opt-in), in place of an MCP server.
- VS Code / Cursor extension: inline hints on CLAUDE.md and AGENTS.md lines (duplicate of X, dated, vague).
- More agents: load rules for Gemini CLI, Cursor and Copilot when their docs or transcripts confirm them.
- A headless judge runner (`claude -p` per batch) for CI, only while it needs no API key.
- A shared rule pack: lexicons for causes 3 and 4 in more languages, as data files, once there are outside contributors.
- Smaller items: MCP tool search awareness, compaction re-injection, managed settings (rows owned by an admin), a risk note for lines that `/code-review` reads, the cost before install and the last-used date, and the organization tool block for connectors.
- Watch: a numbers-only team export, raw request bodies as a cross-check, a manual row for large MCP results, and Codex usage percentages from local rollouts.

## Will not build

Each of these breaks "walle sends nothing", the read-only promise, or the direction of walle (less text, not more).

- A gateway in the request path, or spend caps.
- Plan limits from an account endpoint. A percentage uses local data only.
- Trace upload, an OTel collector, or a hosted dashboard.
- Session insights from a model (clusters, topics).
- Generative prompt optimizers.
- Hosted prompt compression.
- Pull requests that walle opens, or direct edits of instruction files.
- A walle MCP server: it adds tool names and schemas to every session.
- Experiment results published by default.

## Maintenance

- Done: CI runs the unit tests on Python 3.11 and 3.13 for every push to main and every pull request (`.github/workflows/test.yml`).
- Done: format checks on every session fixture (`tests/test_formats.py`). A new Claude Code or Codex version gets an anonymized fixture; a record kind no fixture had before, or a parser that finds no system prompt or zero tokens, fails the test.
- 0.3.0 adds a release workflow (PyPI on `v*` tags) and a test that the version strings match.

## Adoption

- One-line install: pipx (PyPI `walle-cli`) and the plugin marketplace in 0.3.0; the Action in 0.8.0.
- A public "instruction stack" badge from `walle check` (0.8.0): the verdict word and `~tokens/budget`.
- Telemetry stays off. Reports stay on the user's machine. walle sends nothing; judging happens in the user's own agent.
