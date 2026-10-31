# EmptyState

The screen for a folder with no scan or measure yet. The consumer optionally gives `title`, `body`, `suggestions` (`kicker`, `text`, `command`), `onPick(s)`, `note`, and children (the main action button).

- From the top: mark, title, body, children, suggestions, note. Centered, 680px at most, `space-12` above.
- The mark is three `walle-yellow` cubes and one `eve-glow` pill. No character drawings.
- The title is one `display` line that states the state: "This folder has no scan yet."
- Children hold one primary `Button` (for example "Start scan").
- Suggestions are cards in two columns (one on a narrow screen). `kicker` is an `eve` `label`, `text` is `body-sm`, `command` is the CLI command for that action. Four at most.
- `note` is a `caption` line at the bottom.
