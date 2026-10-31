# ContextPanel

A status panel that shows what fills an agent's context, where, and what actually gets used. The consumer passes walle output as it is: `context` takes `walle context --json` (or a run folder's `context.json`), `usage` takes `walle context --usage --json`. Either one alone is fine. Optionally give `window`, `limit` and `note`.

```js
// walle context --last --json > ctx.json ; walle context --usage --since 2026-10-07 --json > usage.json
h(WallE.ContextPanel, { context: ctx, usage: usageRun })
```

- The panel does not recount what walle measured. It only adds up groups and shares. The residual comes from `first_call.residual.tokens`.
- It takes `run.agent` `claude` or `codex`. The two agents have different source kinds; both fold into the same four groups. A source kind not listed here goes to Host and other, under walle's own `label`.
  - Host and other (`chart-host`): system prompt, built-in tools, agent list, hook output, first prompt, other attachments / for Codex: base instructions, app context, permissions, collaboration mode, multi-agent, environment, history, other items.
  - Skill list (`chart-skills`): `skill_listing`, `skill_bodies`.
  - MCP and tools (`chart-mcp`): `mcp_tools`, `deferred_tools`, `mcp_instructions`, `tool_namespaces`, `plugins_apps`.
  - Instruction files (`chart-instr`): `instructions`, `agents_md`, `memory`.
- **Head.** The measured tokens (`first_call.measured.total`) as a large mono number, the other `measured` keys under it (Claude: cache write, cache read, input; Codex: cached, cache write). On the right, from `session`: folder (home as `~`), agent with surface and version, model, start time, estimator. With `window`, the share of the context window is added.
- **First-call bar.** A 100% bar of the four groups, 2px gaps, 4px round ends. Hover or focus a segment to see a tooltip with its sources at that segment. Under the bar: a legend, then a source table (zero sources in `muted`) with the residual as the last row. Color never carries the meaning alone.
- **Loaded against used.** Tabs for "MCP servers" and "Skills". The whole bar is the sessions of the period, the `eve-soft` part the sessions that loaded the item (for skills: listed it), and the `eve` part inside it the sessions that used it.
  - MCP servers: with `tokens_unused_est`, "≈ N tokens loaded, not used" (Claude). Without it, "N sessions loaded, not used" (Codex). The first folder of `used_in` shows as "Mostly in …" on one line; hover shows the full path.
  - Skills: walle gives no token figure, so "N sessions loaded, not used".
  - Rows sort by tokens loaded and not used, then by sessions loaded and not used. Past `limit` (default 6), "Show N more" opens the rest.
- The period is `usage.since`/`before`, else the first and last dates of `usage.window`.
- Without `note`, the foot shows walle's own `note` (measured values do not enter the verdict).
- 960px at most. On a narrow screen the item rows stack.
