walle is a maintenance tool for coding agents such as Claude Code and Codex. It collects, compacts and sorts the instruction files an agent reads (CLAUDE.md, AGENTS.md, skills, rules), and it measures what those files cost in real sessions. It is not a chat service. The look follows the design conventions of Claude, ChatGPT and Grok: neutral grounds, little color, generous space, one column of content. When color appears, it is one of two characters. WALL·E (yellow, orange, rust) does the collecting and trimming. EVE (white body, blue eyes) does the reading and measuring.

## Principles

- Grounds come first. Do 90% of a screen with `bg`, `surface`, `sunk`, `fg` and `muted`.
- Use WALL·E colors for actions that change things: Apply, Run, items to trim, Real problem.
- Use EVE colors for reading and measuring: links, measured values, file:line locations, selection, focus, running marks.
- Use the seedling (`leaf`, `lean`) only for what is kept, what is done, and devices that are online.
- One `walle-orange` fill per screen.

## Voice

- Lead with the result. One fact per sentence.
- Use the active voice: "Merged 3 lines", "Kept 2 rules". Do not hedge with "seems to".
- Give each number its source: "41 sessions (last 14 days)".
- No greetings, sign-offs, exclamation marks or emoji.
- Write the product name in lowercase: `walle`. Set commands in `code`: `walle eve --since 2026-10-01`.
- Sentence case for labels and buttons: "New scan", "Show the full rules".

## Color

- Page and sidebar on `bg`, main area and cards on `surface`. Pressed faces, bar tracks and inline code on `sunk`.
- Body text in `fg`, secondary text in `muted`. Both hold 4.5:1 or more on all three grounds in both themes.
- The primary button (Apply, Run, Scan) is a `walle-orange` fill with `on-walle` text. Never white text.
- Use `walle-yellow` only for large fills (empty-state mark, scan cubes, cover). Yellow-family text is `trim`.
- Links and focus are `eve` (the `link` and `focus` aliases). The running or measuring eye is `eve-glow`.
- Tint pairs: `rust`/`rust-soft`, `eve`/`eve-soft`, `lean`/`lean-soft`, `trim`/`trim-soft`.
- `rust` and `lean` are close in lightness. A status never speaks by color alone; it always carries a word.
- The dark theme uses the same token names. Accents get lighter, and text on fills moves to the dark `on-…` tokens.
- Chart categories are `chart-host`, `chart-skills`, `chart-mcp` and `chart-instr`, in that fixed order. They are not the brand colors: they were picked to pass the chart validator (lightness band, chroma, color-vision separation). A one-hue magnitude bar (loaded against used) uses three steps: `eve`, `eve-soft`, `sunk`.

## Typography

- Body in IBM Plex Sans (`sans`). Numbers, commands and labels in IBM Plex Mono (`mono`). Korean text falls back to Apple SD Gothic Neo or Noto Sans KR in each stack.
- Screen body is `body` (15/24). Long text an agent wrote is `chat` (16/26). Tables, lists and secondary notes are `body-sm`.
- One `display` line on an empty or waiting screen. Screen titles are `title`.
- Section heads and key labels are `label`: mono 11px, 700, uppercase, 0.12em tracking.
- Token counts and times are `code` with `font-variant-numeric: tabular-nums`.

## Spacing, radii, shadows

- Spacing runs from `space-1` (4) to `space-12` (48). `space-4` inside cards, `space-6` between sections, `space-8` screen margins.
- The content column is 680px at most. Tables and charts widen to 960px.
- Controls stay square at `radius-sm` (3px): WALL·E's machine feel. Cards are `radius-lg` (10px).
- Only EVE things are round: status lamps and the EVE mark use `radius-pill`, EVE's smooth body. Everything else stays square at `radius-sm` or `radius-lg`.
- There are two shadows. `shadow-card` for floating menus and tooltips, `shadow-key` for the bottom face of key-style buttons. Surfaces fixed to the screen (CommandBar, cards) get no shadow.

## States and motion

- Focus: `focus`, 2px solid, 2px offset, 3:1 or more on every ground.
- Disabled: `sunk` fill, `muted` text, no shadow.
- Running or measuring: the device lamp (`eve`) and the EVE eye blink. They stop under `prefers-reduced-motion`.
- Device status pairs a lamp with a word: Online (`leaf`), Running (`eve`), Offline (`line-strong`).
- Transitions take 120ms. No bounce.

## Logo and icons

- There is no logo file. Write the product name as `walle` in the `wordmark` style.
- Do not draw WALL·E or EVE: no pictures, faces or silhouettes. The two characters appear only as color and shape (square cubes, smooth pills).
- There is no icon set yet. Icons inside components are inline SVG on a 16px grid, 2px strokes, `currentColor`.

## Layout

- `Sidebar` on the left (260px, `bg`), the main area on the right (`surface`), split by one `line`.
- The sidebar's upper group is actions (Scan, Measure, Trim, Runs, Keep); the lower group is folders. Numbers such as token counts sit at the right of a row in mono.
- The selected row is EVE (`eve-soft` fill, `eve` text). The "New scan" button at the top is key-style.
- `CommandBar` sits at the top of the main area. It is not a chat input: it shows the current project (path, branch), device status and agent, and takes one `walle` command below.
- The result of `walle judge --import` is a vertical stack of `JudgeAnswer` cards, one card per verdict. Not-a-problem verdicts collapse to one line; Real problem and Not judged stay open.
- `ContextPanel` shows the context status: what filled the first call (a bar and table by source) and what loaded and got used over a period (a session bar per item). Pass the output of `walle context --json` and `walle context --usage --json` as it is.
- While `walle scan` runs, the main area shows `ScanProgress`: five steps, the counts so far, and one cube per finished step. A real scan ends in seconds (2.1 s for 550 files in `~/projects` on 2026-10-21), so there is no progress bar and no time-left estimate. `walle scan --progress json` gives the step events it follows.
- A folder with no scan shows `EmptyState` in the main area. Each suggestion card shows the command it runs in `code`.

## Components

`components/bundle.js` puts `Button`, `Badge`, `JudgeAnswer`, `CommandBar`, `Sidebar`, `EmptyState`, `ContextPanel` and `ScanProgress` on `window.WallE`. The page needs React 18. Class names start with `we-`.
