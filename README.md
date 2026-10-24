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
| `walle scan [folder ...] [--max-tokens N] [--estimator v1\|v2] [--label L] [--progress json]` | For each folder, the stack a session loads before the first prompt: global file, parent files, `@imports`, MEMORY.md, and skill descriptions. It gives each stack a verdict (OVER BUDGET, TRIM or LEAN) and at most 3 items to read first in your own files. It also reports per-file size and rule count, flags by cause, duplicate and drifted rules, and worktree copies that diverged. See [scan.json](#scanjson). `--progress json` writes one JSON object per line on stderr for each step (`collect`, `stack`, `classify`, `pairs`, `triage`) as it starts and ends: `{"event":"step","step":"collect","state":"end","t":1.07,"files":552}`. `t` is the seconds since the start. The end events give `files` (collect), `pairs` (pairs) and `items` (triage). A last `done` event gives the three counts and the path of `scan.json`. The `keep released` line becomes a `note` event, so stderr holds only JSON lines. The option replaces the progress line. It does not change stdout or `scan.json`. |
| `walle eve [folder] [--around APPLY_ID\|DATE] [--agent claude\|codex] [--across-folders]` | Real sessions of one folder (default: the current folder), one model family per row: the measured first-call input, the same input billed with caching counted, tool calls per prompt and calls before the first edit, as median [p25-p75] with n. `--around` splits the sessions at an apply or a date into before and since. `walle measure` is the same command. See [Measure and compare](#measure-and-compare). |
| `walle context [--last \| --session ID \| --compare A B \| --compare APPLY_ID] [--agent claude\|codex] [--usage] [--json]` | Measured, not estimated: the first API call of one session, by source (system prompt, tool lists, skill list, instruction files, first prompt), with a residual row for what no record explains. `--usage` counts, over all sessions, what each MCP server, tool and skill cost and whether a session used it. `--agent codex` reads Codex rollouts in `~/.codex` (zstd files need the `zstd` program). `--compare` compares two first calls source by source. `--json` prints the run's `context.json` to stdout instead of the path of `context.md`; the run is still written. It does not work with `--compare`. See [Measure and compare](#measure-and-compare). |
| `walle diff [last \| A B] [--root F]` | What changed after you trimmed. No argument: the baseline (the pinned run, else the previous scan) against the newest scan of the folder. `last`: the newest two scans. `A B`: two run ids or scan.json paths. It shows the stack tokens, the items closed, new, moved, edited and kept, and the applies between the two scans. A rules change hides the item counts; an estimator or counting change hides the token change. The Sessions lines give the measured first-call input of one folder, one model family per block, and name that folder. It is the folder that `walle diff` compares (`--root`, else the current folder) when a root of the newest scan holds it. Else it is the first root of the newest scan. The lines split the Claude Code sessions at the newest apply between the two scans, else at the time of the newest scan. The n rule of `walle eve --around` applies. When the since side has fewer than 10 sessions and its window is still open, a `Next` line gives the `walle measure --around` command for later. The command names the folder, `--out` and `--transcripts` when they are not the defaults. `--transcripts` sets where the sessions are. Writes `diff.md` and `diff.json` in a new run folder. |
| `walle runs [--root F] [--html]`, `walle pin <run>`, `walle pin --clear` | The run index: scans, trim plans, applies, undos, diffs, context runs, measures and compares. Every run also writes the history page `index.html` in the runs folder. `walle pin` fixes the baseline of a folder for `walle diff`. |
| `walle check [folder] [--max-tokens N] [--estimator v1\|v2]` | Exit 1 when a stack is over budget. Use it in CI. |
| `walle trim --plan [--since D] [--before D]` | A plan to take unused sources out of every session's context: claude.ai connectors, MCP servers, plugins, skills, hook output, and lines that a scan found loaded twice. One row per action: the change (a settings key, a patch or a place in an app), who makes it, the expected saving as a lower bound in bytes-v2 tokens per session, the sessions that loaded and used the source, the risk, the undo and the check. Writes `plan.json`, `plan.md`, `patches/` and judge batch files in a new run folder. `walle trim --import` reads the judge answers into the plan. It changes nothing. |
| `walle apply <run> --only ID[,ID] [--dry-run] [--app-closed]`, `walle undo <apply-id>` | Change the settings keys of the plan rows you pick, after a backup, and put them back. See [Apply and undo](#apply-and-undo). |
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
`walle scan`, `walle check` and `walle diff` stop with exit code 2 when `--out` or its sibling `cache/` is inside a folder they scan, except under `~/.walle`.
`walle eve` and `walle context --compare <apply-id|date>` do the same for the folder they measure.

### Claude Code plugin

```bash
claude plugin marketplace add <repository url or local path>
claude plugin install walle@walle
```

The plugin adds:

- a `walle` skill. Ask "audit my CLAUDE.md" and the agent runs the scan and reads the report.
- an `eve` subagent (`walle:eve`). After `walle apply`, it compares the sessions before and after the change and reports, per surface, which sources left the first call. It is read-only and runs on the `haiku` model. Its description adds about 200 bytes to the agent list of every session.
- an optional session-start alarm, off by default. Turn on `session_alarm` and set `budget_tokens` (default 15,000) to get one line of context when a folder's stack is over budget.
  The alarm counts bytes-v2 tokens. If you saved `budget_tokens` as 8000 for the earlier bytes-v1 counts, change it to 15000.
  The hook runs the `python3` on your PATH. If it is older than 3.11, the alarm prints nothing and exits 0. Other `bin/walle` commands exit 1 with one line.

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

- **walle** rows change one settings key: `enabledPlugins`, `skillOverrides`, one `deniedMcpServers` entry or an `mcpServers` key of `~/.claude.json`. `walle apply` changes them (see below).
  - A claude.ai connector reaches a session in one of two ways ([docs](https://code.claude.com/docs/en/mcp#disable-claude-ai-connectors)). In `cli`, IDE and `sdk-cli` sessions Claude Code fetches it itself, and a `deniedMcpServers` entry `{"serverName": "claude.ai <name>"}` in `~/.claude/settings.json` blocks it there. In `claude-desktop` sessions the desktop app registers it in-process, and no setting reaches it: only disconnecting it at claude.ai/customize/connectors takes it out, and that also takes it out of claude.ai on the web and phone. So a connector gets two rows: a walle row with the `deniedMcpServers` entry, whose saving counts every surface but `claude-desktop`, and a manual row, whose saving counts `claude-desktop`. `walle keep` on one keeps both.
  - A claude.ai plugin that claude.ai syncs to Claude Code (a folder in `~/.claude/plugins/synced/`) is `enabledPlugins["<name>@synced"] = false`, the key that `claude plugin disable <name>@synced` writes ([docs](https://code.claude.com/docs/en/plugins/loading#control-which-synced-plugins-load)). A plugin that your organization requires ignores it, and the docs do not say that it survives the next sync. The key acts on `cli` and `sdk-cli` sessions. It does not reach `claude-desktop` (next item).
  - The desktop host passes each claude.ai plugin to a `claude-desktop` session with `--plugin-dir`, from its copy in `~/.claude/remote/plugins`. That copy loads as `<manifest name>@inline`, and only `enabledPlugins["<name>@inline"] = false` turns it off ([docs](https://code.claude.com/docs/en/plugins/loading#keep-a-session-only-plugin-from-loading)). Measured on 2026-10-21 with `claude plugin list` on Claude Code 2.1.299 and the real desktop copy: the `@inline` key turned the plugin off, the `@synced` key did not. walle gives an `@inline` row to each plugin that `claude-desktop` sessions of the period loaded (a skill `<name>:*` or an MCP server `plugin:<name>:*`) and that a desktop copy names in its `plugin.json`. The copy gives only the skill list. Its saving counts `claude-desktop` sessions. A plugin that is also synced gets both rows. The key also turns off a plugin of that name that any other session loads with `--plugin-dir`. Sessions that are open keep the plugin; the next session drops it. walle offers no `@inline` row for a safety plugin, for a plugin that your organization requires (the key would go around that policy), or for a plugin that none of the newest 10 `claude-desktop` sessions loaded (a past plugin). `walle keep` on one row of a plugin keeps its other row too. Undo it with `walle undo`, not with `claude plugin enable <name>@inline`: that command writes `"<name>@synced": false`. walle never offers `anthropic-skills@inline`: it takes every claude.ai skill out of `claude-desktop` at once.
  - walle offers no `skillOverrides` key for a skill of a real plugin (`<plugin>:*` of a plugin that is not the claude.ai skill sync): Claude Code does not apply it to plugin skills ([docs](https://code.claude.com/docs/en/skills#override-skill-visibility-from-settings)). The plugin row takes the whole plugin out.
  - A synced claude.ai skill (`anthropic-skills:*`) gets a `skillOverrides` row. The `cli` and `sdk-cli` surfaces load these skills as user skills, where `skillOverrides` applies. This comes from reading the Claude Code 2.1.303 code, and no `cli` or `sdk-cli` session has confirmed it yet. The `claude-desktop` surface loads them as a plugin. Measured on `claude-desktop` 2.1.299 on 2026-10-21: skills were still listed after their keys were set.
  - A surface is the `entrypoint` of a session (`cli`, `sdk-cli`, `claude-desktop`). For synced skills and `@synced` plugin keys, the saving counts only `cli` and `sdk-cli` sessions. For `@inline` plugin keys, it counts only `claude-desktop` sessions. The other shares are a separate, unconfirmed number. The plan.md totals are split the same way.
  - A key that is already set gets a status for each surface, from the sessions that started after it was set: `already set`, `set, still listed on <surface>`, `set, the source is gone on <surface>` (a synced skill that is not in the current bucket), `set, no baseline on <surface>` (no session of that surface listed the source before the key was set), or `set, not measured on <surface>`. The set time is the earliest `walle apply` that set the key since its last `walle undo`. Else it is the last change of the settings file, and the key was set at or before that time. Its saving is not counted. No row deletes a key.
  - A synced skill that is not in the current sync bucket is marked "not in the current bucket". Its loaded count is from past sessions.
- **manual** rows are in an app: claude.ai connectors (Customize > Connectors, claude.ai/customize/connectors), duplicate claude.ai skills, plugins that your organization requires, and past desktop plugins (their saving is not counted). On claude.ai, a member can turn off an "Installed by default" plugin. A "Required" plugin needs the organization Owner ([docs](https://claude.com/docs/plugins/admin)).
- **patch** rows are patch files in `patches/`, or a change your agent writes (hook output, a section to move). walle never edits an instruction file.

Rows that need "does this user's work need it?" go to judge batch files with their own rubric; `walle trim --import` reads the answers. The judge cache is `cache/trim-judge.json`.

### Apply and undo

`walle apply <run> --only <id>[,<id>]` changes only the rows that you pick. There is no default choice.

- It changes only `enabledPlugins`, `skillOverrides` and `deniedMcpServers` in `~/.claude/settings.json` and one `mcpServers` entry in `~/.claude.json`. It refuses all other files and keys. It never edits an instruction file, never writes into a folder that walle scans, and never deletes data.
- It sets an `enabledPlugins` key only to `false` (`<name>@synced` and `<name>@inline` too). It sets a `skillOverrides` key only to `user-invocable-only`. It adds to `deniedMcpServers` only one `{"serverName": "claude.ai <name>"}` entry and keeps the entries already there. It never deletes a settings key: it refuses every other operation.
- The plan records the sha256 of each settings file. walle refuses a file that changed after the plan. Then run `walle trim --plan` again.
- Before it writes, walle copies each file to `backups/<apply-id>/` next to the runs folder (`~/.walle/backups/` by default), with `undo.json`. The backup folder is private (0700), because `~/.claude.json` holds tokens.
- The Claude app and its sessions rewrite `~/.claude.json` while they run. walle writes it only with `--app-closed` and when no Claude process runs. After the write, walle reads the file again. A different hash is reported as `apply lost`.
- Manual rows print a checklist with the place to change. `[unconfirmed]` marks a menu path that walle could not confirm. Patch rows print the command for your agent.
- `--dry-run` prints each change and writes nothing.

`walle undo <apply-id>` restores the backup only when each file still has the hash that it had after the apply. If not, walle lists the key paths that changed, without their values, and stops. Each apply and undo adds a line to the run index, linked to its plan and its apply.

### Measure and compare

These commands close the loop after `walle apply`: did the context of new sessions really shrink? They show measured values. The values never enter the verdict, Start here, the alarm or a PR comment.

`walle context --compare A B` compares the first calls of two sessions source by source. A and B are context run ids, their folders or `context.json` files, or session ids (the start of an id is enough).

- Each source shows its recorded bytes on both sides and the change in estimated tokens. The largest changes inside a source (MCP servers, tools, instruction files) follow the table.
- Bytes compare only when both sides split the first call in the same way (the same `counting_revision`). Estimated tokens compare only when both sides also used the same estimator.
- The measured first-call tokens compare when both sessions have the same model family. One session on each side is not a median.
- Below them, `Billed as` gives the same calls with prompt caching counted (see Billed below), and their dollars at list price.
- The first prompt and hook output change with every session, and so do a Codex thread's environment context and the skill bodies its prompt invoked. The table marks them with `*`, and the changes inside a source leave them out.
- The MCP servers and skills that only one side loaded follow, with their calls.

`walle context --compare <apply-id|date>` takes the newest session before the split and the newest session since it. It takes them from `--root` (default: the current folder) when that folder has a session on both sides. Then it lists each row that the apply changed, with the sessions that loaded and used it before and since, in all folders. A plugin row (`enabledPlugins`) counts a session that lists a skill `<plugin>:...` or loads an MCP server `plugin:<plugin>:...`. A skill row (`skillOverrides`) counts the listed skill name. When the sessions come from more than one surface (entrypoint, such as `cli` or `claude-desktop`), the row also gives the counts per surface. A key can act on one surface and not on another. The last line of the row tells if the two newest sessions loaded the source, and gives the surface of each: `still loaded`, `gone`, `loaded only since` or `not loaded`. Last, it gives the measured first-call medians of the folder, as `walle eve --around` does.

`walle eve --around <apply-id|date>` (also `walle measure`) splits the sessions of one folder at the apply or the date.

- The before window starts 30 days before the split (`--days`). The since window ends 30 days after it. A nearer apply or undo ends a window, so each window holds one state of the settings.
- One model family per row. The family keeps the version and drops a date: `claude-opus-5-5` is `opus 5.5`, `claude-haiku-4-5-20251001` is `haiku 4.5`.
- Each side shows the median and p25-p75 (`statistics.quantiles`, inclusive method) and n. A side with fewer than 5 sessions has no value. A side with 5 to 9 sessions is marked `(few sessions)`.
- The change shows only when both sides have 10 sessions or more. It is `within noise` when the two p25-p75 intervals overlap. Else it is `lower` or `higher` by the difference of the medians. `eve.json` also records the half-IQR rule of the design brief.
- A session that resumes or forks another session starts with that history. walle leaves it out and counts it.
- `--across-folders` adds every folder for reference. Folders differ in model, connectors and task mix, so the table ranks nothing.
- The same transcripts give the same output. `eve.json` keeps the all-folder `groups` list that `scan.html` reads.
- `--agent codex` reads Codex threads and gives first-call input only. Subagent, resumed and forked threads are left out.

A deleted transcript still counts. `walle context --usage`, `walle trim` and `walle eve` keep what they counted of each session in `context-usage.json` and `eve.json` in the cache folder. When a transcript file is deleted, the session still counts from those rows, also when the transcripts folder has no file left. A kept row holds counts; tool, MCP server, skill, slash command and hook names; the session's folder, transcript path, start time, surface and model. It holds no prompt text and no hook output text: a hook output label becomes a digest, which adds to the same label when a session on disk shows it. Else `walle context --usage` shows it as `(output label not kept)` with the digest, and `walle trim` makes no hook row for it. The rows count only for the transcripts folder that walle read them from, and once when a transcript moved to another folder. Delete the two files to forget them. A walle version that changes the cache format forgets them too, until 0.4.0 migrates the rows. Claude Code sessions only.

**Billed.** The first-call input adds three counts that cost different amounts: uncached input, cache writes and cache reads. A session that read its prefix from the cache costs a fraction of one that wrote it. The `billed` column of `walle eve`, the `Billed as` line of `walle context` and of `--compare` weight each count by its price relative to the model's base input price: cache reads at the model's read rate (0.1x for most models, 0.05x for Opus 5.5 and Sonnet 5.5, 0.025x for Fable 5.1 and Mythos 5.1), cache writes at 1.25x for the 5-minute duration and 2x for the 1-hour duration, from the split the transcript records. The unit stays tokens, so a trim that removes 10,000 cached tokens shows as 1,000 or less here when later calls read them. `walle context` also gives dollars at Anthropic's list prices of the date in `walle/cost.py`; batch, fast mode and partner prices (Bedrock, Vertex) are not counted. A model missing from that table gets the 0.1x read rate and no dollars. Codex sessions are not priced.

The history page `index.html` in the runs folder lists every run, newest first, with its estimator and counting revision. A labeled divider sits above a scan whose rules, estimator or counting revision changed. It lists the pins, and each apply id with its plan, its undo, the two commands that measure it and the runs made around it. walle writes it again after each run. `walle runs --html` writes it now.

`walle keep` writes `keep.json` next to the runs folder (`~/.walle/keep.json` by default). A team keeps lines in `.wallekeep` at the repository root: one team key per line, then `# ` and a note. The team key uses the remote URL and the path in the repository, so every clone has the same key. walle reads `.wallekeep` and never writes it. A repository without a remote has no team key.

### Web app

`walle app` shows your runs in a browser and runs walle read commands. It serves a local page on 127.0.0.1 only.

```bash
walle app --no-open
walle app --port 8760
```

- walle prints a URL with a random token. A request without the token gets 403. The token changes each time that `walle app` starts.
- The server accepts only the Host names 127.0.0.1, localhost and [::1] with its own port. It refuses a request from another origin or site.
- The page loads nothing from the network. React 18, the walle design system and the IBM Plex fonts are in the package. The Content-Security-Policy allows only the app itself.
- The left list has the actions (Efficiency, Scan, Measure, Trim, Runs, Keep) and the folders of your scans. Each folder shows its verdict and its estimated tokens. The page opens on Efficiency for the folder that `walle app` started in.
- Efficiency puts the loop of one folder on one page: the instruction stack, the measured first call (median [p25-p75] and n, billed tokens with prompt caching counted, and list-price dollars when the sessions used one priced model), the trim rows to apply, and each apply with its `walle undo` command and its `walle context --compare` result. A Next step line names the command to run next. The page runs only read commands: it shows `walle apply` and `walle undo` to copy into a terminal. A row that an apply after the plan changed is not offered again.
- Scan shows the newest scan of the folder: the verdict, Start here, the other items and the judge answers. A folder without a scan shows the commands to start with.
- Measure shows the `walle eve` rows of the folder (median [p25-p75] and n, and the change after a split) and the context panel of the newest context runs.
- Trim shows the newest plan. It is read-only: each row has its `walle apply` command to copy into a terminal. A row whose key is already set shows its status per surface, with no saving and no command. Runs shows the run index, and Keep shows `keep.json`.
- The command bar takes one walle command. The command runs in the folder that the page shows, as a list of arguments without a shell. `walle scan` shows its steps while it runs. When you close the page or stop the server, walle stops the command.
- This version runs read commands only: `scan`, `context`, `eve`, `runs`, `diff`, `show`, `trim --plan` and `judge --dry-run`. It refuses `apply`, `undo`, `keep`, `unkeep`, `pin`, `judge --import` and `trim --import` and runs nothing for them. Run them in a terminal. A scan from the app leaves `keep.json` as it is: when a kept line changed, the scan says so, and the next `walle scan` in a terminal removes the entry.
- `--out` sets the runs folder, as for the other commands. A command from the page cannot change it.

To open the app from another computer, forward the port over SSH. Then open the printed URL on that computer:

```bash
ssh -N -L 8760:127.0.0.1:8760 <server>
```

Use the same port on the two sides: the server refuses a Host header with another port.

`--allow-host NAME` (repeatable) adds a host name that forwards to the app port, for example a tailnet name that `tailscale serve` publishes. The server then also accepts the Host `NAME:<port>` and the origins `https://NAME:<port>` and `http://NAME:<port>`. It still listens on 127.0.0.1 only, and every path still needs the token.

```bash
walle app --port 8760 --no-open --allow-host vocado-dev.tail82e74d.ts.net
```

Without `--no-open`, walle opens the browser through a private temporary file (mode 0600) that redirects to the URL. The token is not in the command line of the browser, where other users can see it with `ps`.

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
Other runs add their own line: trim, apply and undo, diff, context, measure and compare. A measure or compare run around an apply has that apply as its `parent_id`.
Compare finding counts of two runs only when their `rules_hash` values are the same. Compare token totals only when their `estimator` and `counting_revision` values are the same.
A context run has its own `counting_revision`: 1 is the source split of `walle context`. It does not compare with the revision of a scan.
`counting_revision` 2 counts a file that Claude Code loads without its HTML comment blocks. 3 also counts the Codex skill list as Codex writes it. A run without the field counts as 1. That label can be wrong for runs made between commits b0f005e and 7a956ce, which already left the comments out; walle was not released then.

## Limits

- Load rules are the ones observed for Claude Code and Codex. Connector tools and hosted plugin skills are not on disk. `walle eve` measures them as part of the baseline.
- A change in a measured median is not proof of cause: task mix and connectors move it. Compare one folder and one model family at a time.
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
