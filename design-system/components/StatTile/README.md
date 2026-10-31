# StatTile

One number with its name, for a row of key figures. The consumer gives `label` and `value`, and optionally `badge`, `tag` and `note`.

- `label` is in the `label` style, `muted`. Name the measure, not the screen: "First call", "Trim".
- `value` is mono 600 21px/30px with `tabular-nums`. Write "-" when there is no value yet, and say why in `note`.
- `badge` comes before the value (for example the verdict). `tag` comes after it (for example "median, n=12").
- `note` is one `caption` line in `muted`: the source of the number or the next step.
- Put tiles in a `StatGrid`.
