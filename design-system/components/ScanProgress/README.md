# ScanProgress

The screen in the main area while `walle scan` runs. The consumer gives `step` (a step id or 0–4), and optionally `done`, `roots`, `counts` (`files`, `pairs`, `items`), `elapsed` (seconds), `onCancel`, and children to show when done (usually a "Show results" button).

- The steps are the work of `walle/scan.py` in five groups: Collect instruction files (collect) → Build load stacks (stack) → Split and classify lines (segment, classify) → Find copy and conflict pairs (rule pairs, topic, protocol) → Sort Start here (triage).
- The mark is five cubes. Each finished step fills a `walle-yellow` cube, three on the floor, then two on top. The cube of the current step presses down and back up, over and over: WALL·E compacting. Places not reached yet are dashed `sunk` outlines.
- Step list: done (`leaf` dot, `lean` text, the count), now (blinking `eve` dot, bold name, "Running" or the count so far…), waiting (`muted`). Each color comes with a word.
- No progress bar and no time-left estimate. A real scan is short: on 2026-10-21, `~/projects` (550 files, 453 pairs, 360 items) took 2.12 s, and collecting took 1.01 s of that.
- Foot: "Step N of 5 · elapsed s", a ghost Cancel button while running (`onCancel`), the children when done.
- Under `prefers-reduced-motion` the cubes and dots stop.
- To follow a real scan, run `walle scan <folder> --progress json`. It writes one JSON object per line to stderr; stdout stays as it is.
  - `{"event": "step", "step": "<id>", "state": "start", "t": <s>}`: set `step` to that id and `elapsed` to `t`.
  - `{"event": "step", "step": "<id>", "state": "end", ...}`: copy `files` (collect), `pairs` (pairs) or `items` (triage) into `counts`.
  - `{"event": "done", "files", "pairs", "items", "scan", "t"}`: set `done`, and point the "Show results" button at `scan` (the path of scan.json).
  - The step ids of the events are the step ids of this component.
