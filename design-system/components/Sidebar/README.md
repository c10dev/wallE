# Sidebar

The navigation list on the left of a walle screen. The consumer gives `items` (`id`, `title`, optional `group` and `meta`), `activeId`, `onSelect(id)`, and optionally `actionLabel`, `onAction()` and `footer`.

- 260px wide, full height of its parent. `bg` fill with one `line` on the right.
- At the top: `walle` in the `wordmark` style and a key-style button. The button label defaults to "New scan".
- Group names in the `label` style, `muted`. Usually two groups: Actions (Efficiency, Scan, Measure, Trim, Runs, Keep) and Folders. Groups follow the order in which they first appear in `items`.
- A row is one line of `body-sm` and ends in an ellipsis when it overflows. `meta` sits on the right in mono with `tabular-nums` (token counts, run counts).
- Hover is `sunk`; the selected row is an `eve-soft` fill with `eve` text.
- `footer` holds the last scan time or the version in mono.
- Collapsing on a narrow screen is up to the consumer.
