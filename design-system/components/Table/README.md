# Table

A data table with one header row. The consumer gives `columns` (`key`, `label`, optional `type` and `stackLabel`) and `rows` (`key`, `cells`, optional `marked` and `unlabeled`), and optionally `caption`, `markedLabel` and `stack`.

- Text is `body-sm`. Header cells are in the `label` style, `muted`, over one `line-strong` rule. Rows are split by `line`.
- `type: "num"` right-aligns the column. Its cells are mono 12px with `tabular-nums`. Its header stays in the `label` style. `type: "time"` sets it in mono 12px/20px with `tabular-nums`. Neither wraps.
- `marked` puts an `eve` bar at the start of the row, for example a run of the current folder. Say what a marked row means in the text above the table. A screen reader hears `markedLabel` (default "marked") after the first cell.
- On a narrow screen (860px or less) each row becomes a block, and each cell starts with its column label. Give `stackLabel: false` to a column whose content names itself, such as an id. A row's `unlabeled` lists the cells of that row that show no label, for example a summary row. An empty cell does not show. Give `stack: false` to keep the table form.
- `caption` names the table for a screen reader.
- Write "-" for a value that does not exist, and "0" only for a real zero.
