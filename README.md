# walle

**English** · [한국어](README.ko.md)

walle collects the instruction files that coding agents load, compacts them, and sorts what it finds.
It reads CLAUDE.md, AGENTS.md, skills, MEMORY.md, and Cursor, Copilot, Gemini, Windsurf and Cline rules.
It then measures what those files cost in real sessions.

It works like a compactor: collect what piled up, press the duplicates together, sort the rest, and keep what is worth keeping.

## Why

Agents follow instruction files closely, so every extra rule costs steps.
Research on AGENTS.md files, skills and instruction density points the same way.
Short, relevant, non-conflicting instructions help.
Long, duplicated, vague or outdated ones add cost and can lower success.
walle finds the second kind, so you can cut it and measure the effect.

## What it reports

| Command | Output |
|---|---|
| `walle scan [folder ...] [--max-tokens N] [--estimator v1\|v2] [--label L]` | For each folder, the stack a session loads before the first prompt: global file, parent files, `@imports`, MEMORY.md, and skill descriptions. It gives each stack a verdict (OVER BUDGET, TRIM or LEAN) and at most 3 items to read first in your own files. It also reports per-file size and rule count, flags by cause, duplicate and drifted rules, and worktree copies that diverged. See [scan.json](#scanjson). |
| `walle eve [--since D] [--before D]` | Real sessions from `~/.claude/projects` transcripts. For each folder group: baseline input tokens, tool calls, check calls, and calls before the first edit. Each value is compared with sessions that have no project CLAUDE.md. |
| `walle context [--last \| --session ID] [--agent claude\|codex] [--usage]` | Measured, not estimated: the first API call of one session, by source (system prompt, tool lists, skill list, instruction files, first prompt), with a residual row for what no record explains. `--usage` counts, over all sessions, what each MCP server, tool and skill cost and whether a session used it. `--agent codex` reads Codex rollouts in `~/.codex` (zstd files need the `zstd` program). |
| `walle diff old/scan.json new/scan.json` | What changed after you trimmed: stack tokens, rule counts, flags. |
| `walle check [folder] [--max-tokens N] [--estimator v1\|v2]` | Exit 1 when a stack is over budget. Use it in CI. |
| `walle trim --plan [--since D] [--before D]` | A plan to take unused sources out of every session's context: claude.ai connectors, MCP servers, plugins, skills, hook output, and lines that a scan found loaded twice. One row per action: the change (a settings key, a patch or a place in an app), who makes it, the expected saving as a lower bound in bytes-v2 tokens per session, the sessions that loaded and used the source, the risk, the undo and the check. Writes `plan.json`, `plan.md`, `patches/` and judge batch files in a new run folder. `walle trim --import` reads the judge answers into the plan. It changes nothing. |
| `walle keep <id> [--why W]`, `walle keep --list`, `walle unkeep <id>`, `walle keep --print <id>` | Leave a line or a source as it is. A kept item leaves the verdict, Start here and the trim plan. If its lines change, the next scan says `keep released: line changed` once and counts it again. `--print` gives the line for a team's `.wallekeep`. |
| `walle judge [scan.json] [--dry-run \| --import]` | Hands the conflict pairs (drift, topic, protocol) and cause-1 candidates of a scan to your coding agent as batch files with a short rubric. The agent writes a verdict per item: real or not, which line to keep, and a one-line fix. `--import` reads the answers and writes `judge.md` and `judge.json` next to `scan.json`. `--dry-run` lists the items with file:line and the estimated tokens, and writes nothing. |

The four causes:

1. **Cost (candidate):** a directive that adds steps whatever the task is ("always run", "read X first", "every task").
2. **Conflict (candidate):** two rules that say different things. `walle scan` finds three kinds of pairs. `walle judge` reads their meaning.
   - **drift:** rules that are similar but changed.
   - **topic:** rules that load in one session, share a key term (a backticked token, an ALL-CAPS word, a tool name such as `SendMessage`), and differ in sign ("send" and "do not send") or in the form of one message. The two rules can be in one file.
   - **protocol:** rules in two repositories that write one message (`READBACK D-xxxx`) in different forms.
3. **Vague quality:** "make it clean", "don't look AI-made", "think carefully", "best practices", without a concrete pattern to avoid.
4. **Legacy:** model names, dates, NEVER/ALWAYS stacks, and the same rule written again.

Candidates need a human or `walle judge` to read the line. walle shows them with file and line.

## Install

Python 3.11 or later. No dependencies.
The package name is `walle-cli`. The command is `walle`.

```bash
pipx install walle-cli
```

Until the package is on PyPI, install from the repository: `pipx install git+<repository url>`.

Or run it from a checkout:

```bash
python3 bin/walle scan ~/projects --html
```

Reports go to `~/.walle/runs/<time>/` (`scan.md`, `scan.json`, `scan.html`). Each scan also adds one line to `~/.walle/runs/index.jsonl`. walle never writes into the folders it reads.
`walle scan` and `walle check` stop with exit code 2 when `--out` or its sibling `cache/` is inside a folder they scan, except under `~/.walle`.

### Claude Code plugin

```bash
claude plugin marketplace add <repository url or local path>
claude plugin install walle@walle
```

The plugin adds:

- a `walle` skill. Ask "audit my CLAUDE.md" and the agent runs the scan and reads the report.
- an optional session-start alarm, off by default. Turn on `session_alarm` and set `budget_tokens` (default 15,000) to get one line of context when a folder's stack is over budget.
  The alarm counts bytes-v2 tokens. If you saved `budget_tokens` as 8000 for the earlier bytes-v1 counts, change it to 15000.

### Codex and other agents

Install the CLI with pipx, then copy `skills/walle` into your agent's skills folder, for example `~/.codex/skills/walle`.

### CI

```yaml
- run: pipx install walle-cli
- run: walle check . --max-tokens 15000
```

### Judge

walle does not call a model and needs no API key. Your agent does the judging, with the session and account it already has.

1. `walle judge` writes the uncached candidates into batch files of about 20 items under `<run>/judge/`. Each file holds the rubric, the items with file:line and the path of its answer file. With `--deep`, the judge may also open the instruction files; it finds more and costs much more.
2. The agent judges each batch, or starts one subagent per batch, and writes the answer files. The `walle` skill tells it how (`skills/walle/judging.md`).
3. `walle judge --import` checks the answers, caches them in `~/.walle/cache/judge.json` and writes `judge.md`.

The cache key is a hash of the rule text and the rubric version. The next export holds only rules whose text changed.
Caches go to the `cache/` folder next to the runs folder (`--out`), by default `~/.walle/cache/`. A rule that only moved to another line keeps its judgment.

### Trim plan and keep

`walle trim --plan` reads the sessions of a period (as `walle context --usage` does) and the newest scan in the runs folder. A source is proposed when it loaded in at least 10 sessions, sessions used it in fewer than 10% of them, and it had fewer calls than sessions that loaded it. A skill is proposed when no session called it.

- **walle** rows change one settings key: `enabledPlugins`, `skillOverrides` or an `mcpServers` key of `~/.claude.json`. `walle apply` (plan step C5) is not built yet, so you change them by hand.
- **manual** rows are in an app: claude.ai connectors and plugins that claude.ai syncs.
- **patch** rows are patch files in `patches/`, or a change your agent writes (hook output, a section to move). walle never edits an instruction file.

Rows that need "does this user's work need it?" go to judge batch files with their own rubric; `walle trim --import` reads the answers. The judge cache is `cache/trim-judge.json`.

`walle keep` writes `keep.json` next to the runs folder (`~/.walle/keep.json` by default). A team keeps lines in `.wallekeep` at the repository root: one team key per line, then `# ` and a note. The team key uses the remote URL and the path in the repository, so every clone has the same key. walle reads `.wallekeep` and never writes it. A repository without a remote has no team key.

## scan.json

`scan.json` is the data that every surface reads. Schema 2 adds fields and renames none, so readers of schema 1 keep working.

| Field | Content |
|---|---|
| `schema` | `2` |
| `run` | `id` (the run folder name), `kind`, `parent_id` (the previous scan of the same roots), `label`, `walle_version`, `rules_hash`, `estimator`, `counting_revision`, `roots`, `git_heads` |
| `items` | One entry for each thing to read or change, with a stable 6-hex `id` |
| `triage` | For each stack: `verdict`, token split, `start_here` (item ids) and folded `groups` |
| `files[]` | Also `sha`, `ownership` and the `##` sections with their line ranges |
| `stacks`, `files`, `flags`, `pairs`, `copies` | As in schema 1 |

**Items.** One flagged line is one item. Lines that belong together are one item: the dated and model-name lines of one file (from 3 lines), the flagged lines of one MEMORY.md, a run of consecutive copied lines between two files, and a file that copies another file of its stack (a `file_copy` note).
Each item has a `rule`, a `verb` (`DELETE`, `CHECK`, `PICK ONE`, `MOVE`, `REWRITE`, `SYNC?`), a `certainty` (`certain`, `candidate`, `info`), a `scope`, an `ownership`, and `locations` with the whole text, `end_line` and a hash.
A pair item has one location for each line: `side` `a` (the line to act on) first, then `side` `b`. A run of copied lines has one location for each copied line, so a DELETE never names a line between them.
The id is a hash of the rule, the text and the file path. It stays the same when a line moves, and when you scan a folder alone or inside its parent. The same line in two folders gets two ids. The path is the path on this machine: a clone in another place gets other ids.

**Scope.** Only `session_start` items change the verdict and Start here. Items in skill, agent and command bodies (`on_demand`), lines that no stack loads together (`not_together`, for example copies between sibling folders), and Cursor, Copilot, Gemini, Windsurf and Cline files (`other_agent`) stay in folded groups with their counts. Findings outside every stack of the scan (`elsewhere`) are counted in `triage.hidden`.

**Ownership.** `owned` is a file in the git checkout of the folder. `memory` is the MEMORY.md of the folder. `shared` is a global file or a parent file outside the checkout. `files[].ownership` follows the stacks: a file that any stack sees as shared is shared.

**Verdicts.** The budget is 15,000 estimated tokens per stack (`--max-tokens`).
Token counts use the bytes-v2 estimator, which has the same prose rates as `walle context`, so the two commands give one number for one file.
A file that Claude Code loads counts without its HTML comment blocks, as Claude Code drops them. Codex files count whole.
A Codex skill counts as Codex lists it: a skill whose `agents/openai.yaml` sets `allow_implicit_invocation: false` is not listed. The Codex project doc cap is `project_doc_max_bytes` in `~/.codex/config.toml` (32 KiB when unset).
`--estimator v1` counts with bytes-v1 (ASCII at 4 characters per token), and its default budget is 8,000.
On the author's machine, 15,000 bytes-v2 tokens put the same stacks over the budget as 8,000 bytes-v1 tokens, with HTML comment blocks removed (2026-10-20).
The other token thresholds scale the bytes-v1 values by the first bytes-v2 calibration (14,500 / 8,000). They did not move with the budget.
Synced claude.ai skills and plugin skills are not in the budgeted stack; `walle context` shows what they cost.

- `OVER BUDGET`: the stack is over the budget.
- `TRIM`: the stack has certain DELETE lines of 180 estimated tokens or more (100 with `--estimator v1`), or a conflict (two drifted lines that load together), shared files included. Or your own files have 3 or more open candidates.
- `LEAN`: all other stacks.

**Term clashes.** A `topic` or `protocol` pair in one stack shares a term or a message, not the wording. On real files most such pairs agree. walle shows them as `term_clash` CHECK candidates in Everything else. They are not in Start here, and they do not change the verdict. `walle judge` reads them.

**Start here.** At most 3 items from your own files: the git checkout of the folder and its MEMORY.md. The order is conflicts, every-task steps, large layers, duplicates, old-model habits, dates and model names, then vague words. A large layer (MOVE) is one of your files with more than 3,600 estimated tokens (2,000 with `--estimator v1`) and more than 40% of its stack. A stack over the budget puts its large layer first. A scan of several folders also writes `start_here_by_reach`, which includes shared files and ranks by estimated tokens times the number of stacks that load the line.

**DELETE keeps the broader copy.** walle marks a copied line DELETE only when every stack that loads it also loads the copy that stays. It never marks a line in a shared file, such as `~/.claude/CLAUDE.md`, for deletion, because that file also loads in folders outside the scan.

**Run index.** Each scan adds one line to `index.jsonl` in the runs folder (`--out`): id, parent, label, roots, rules hash, estimator, counting revision, stack totals, verdicts, and open and kept counts.
Compare finding counts of two runs only when their `rules_hash` values are the same. Compare token totals only when their `estimator` and `counting_revision` values are the same.
`counting_revision` 2 counts a file that Claude Code loads without its HTML comment blocks. 3 also counts the Codex skill list as Codex writes it. A run without the field counts as 1. That label can be wrong for runs made between commits b0f005e and 7a956ce, which already left the comments out; walle was not released then.

## Limits

- Load rules are the ones observed for Claude Code and Codex. Connector tools and hosted plugin skills are not on disk. `walle eve` measures them as part of the baseline.
- Token counts in `scan` are estimates from bytes (bytes-v2 by default). `eve` and `walle context` measured numbers come from the API usage in transcripts.
- Session baselines vary with model, account and connectors. Compare a folder with itself before and after a change (`--before` / `--since`) more than across folders.
- walle sends nothing off the machine. `walle judge` only writes files for your agent, which already reads these instruction files. The hook never runs it.
- `walle judge` reads only the candidates that `scan` found. A topic pair needs a shared key term and a verb with the opposite sign, or two forms of one message. Two rules that disagree in other words are not candidates.
- Negation is read in Korean and English only. Korean verbs with a one-syllable stem (쓰다, 읽다) are not read, because they match too many words.
- Each load stack keeps its 40 topic pairs with the rarest shared terms. `scan.md` section 4 gives the counts.
- walle does not review code quality. On the code side it reports only what affects agent sessions: worktree copies and copied skills.

## Roadmap

See [ROADMAP.md](ROADMAP.md).

## License

MIT
