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
| `walle scan [folder ...]` | For each folder, the stack a session loads before the first prompt: global file, parent files, `@imports`, MEMORY.md, and skill descriptions. It also reports per-file size and rule count, flags by cause, duplicate and drifted rules, and worktree copies that diverged. |
| `walle eve [--since D] [--before D]` | Real sessions from `~/.claude/projects` transcripts. For each folder group: baseline input tokens, tool calls, check calls, and calls before the first edit. Each value is compared with sessions that have no project CLAUDE.md. |
| `walle diff old/scan.json new/scan.json` | What changed after you trimmed: stack tokens, rule counts, flags. |
| `walle check [folder] --max-tokens N` | Exit 1 when a stack is over budget. Use it in CI. |
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

Reports go to `~/.walle/runs/<time>/` (`scan.md`, `scan.json`, `scan.html`). walle never writes into the folders it reads.

### Claude Code plugin

```bash
claude plugin marketplace add <repository url or local path>
claude plugin install walle@walle
```

The plugin adds:

- a `walle` skill. Ask "audit my CLAUDE.md" and the agent runs the scan and reads the report.
- an optional session-start alarm, off by default. Turn on `session_alarm` and set `budget_tokens` to get one line of context when a folder's stack is over budget.

### Codex and other agents

Install the CLI with pipx, then copy `skills/walle` into your agent's skills folder, for example `~/.codex/skills/walle`.

### CI

```yaml
- run: pipx install walle-cli
- run: walle check . --max-tokens 8000
```

### Judge

walle does not call a model and needs no API key. Your agent does the judging, with the session and account it already has.

1. `walle judge` writes the uncached candidates into batch files of about 20 items under `<run>/judge/`. Each file holds the rubric, the items with file:line and the path of its answer file. With `--deep`, the judge may also open the instruction files; it finds more and costs much more.
2. The agent judges each batch, or starts one subagent per batch, and writes the answer files. The `walle` skill tells it how (`skills/walle/judging.md`).
3. `walle judge --import` checks the answers, caches them in `~/.walle/cache/judge.json` and writes `judge.md`.

The cache key is a hash of the rule text and the rubric version. The next export holds only rules whose text changed.
Caches go to the `cache/` folder next to the runs folder (`--out`), by default `~/.walle/cache/`. A rule that only moved to another line keeps its judgment.

## Limits

- Load rules are the ones observed for Claude Code and Codex. Connector tools and hosted plugin skills are not on disk. `walle eve` measures them as part of the baseline.
- Token counts in `scan` are estimates from bytes. `eve` numbers come from the API usage in transcripts.
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
