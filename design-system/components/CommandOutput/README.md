# CommandOutput

The output panel of one `walle` command that the page ran. The consumer gives `command`, the state (`running`, `refused`, `cancelled`, `code`), and optionally `cwd`, `seconds`, `error`, `lines`, `onCancel` and `onClose`.

- Head: "Output" in the `label` style, the command in mono 600 13px/20px, a status `Badge`, and one ghost button at the row end.
- Status: `refused` is "Refused" (`walle`). `running` is "Running" (`eve`). `code` 0 is "Exit 0" (`leaf`). Any other `code` is "Exit N" (`walle`). With no `code`, it is "Cancelled" when `cancelled`, else "Error" (`walle`).
- The button is Cancel while `running` (with `onCancel`), else Close (with `onClose`). Without the handler there is no button.
- Under the head: "In <cwd> · N.N s" in `caption` and `muted` (not when `refused`), then `error` in `body-sm` and `rust`.
- `lines` show in one `pre` block: mono 12px/18px on `sunk`, at most 360px high, then it scrolls. Lines wrap anywhere. The block takes keyboard focus, so a keyboard user can scroll it.
- One hidden status region gives the state and the error, also when the panel first shows with its final state. A screen reader hears each state change. It does not read each new line.
- It is a `surface` card with one `line` border and `radius-lg`. It has no shadow.
