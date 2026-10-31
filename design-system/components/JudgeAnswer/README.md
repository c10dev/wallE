# JudgeAnswer

A card for one verdict that `walle judge --import` read. The consumer gives `refs` (`file`, `line`, optional `text`), `verdict` (`real`, `keep`, `fix`, `why`), and optionally `type`, `pair`, `judge`, `cached`, `id` and `compact`.

- The fields follow the judge answer file and the items of `judge.json` in a run folder. Pair the `refs` (`"path:line"`) and `texts` of a `judge.json` item into `refs`.
- Head: the kind (`conflict · <pair>` or `cost`) in the `label` style on the left, the verdict badge on the right. `real` true is "Real problem" (`walle`), false is "Not a problem" (`leaf`), no `verdict` is "Not judged" (`neutral`).
- **Compact.** Without `compact`, only Not-a-problem verdicts collapse to one line: kind, first location (the last two path parts and the line, `+N` for more), reason, badge. Click the row to open it; "Collapse" closes it again. This keeps real problems from drowning in a long list.
- Target rows: `sunk` rows. The `file:line` location in `eve` mono, the rule text in `body-sm`. Conflicts get A and B.
- **Long rule text.** When any rule text in the card is over 160 characters, all rule texts clamp to 3 lines with a "Show the full rules" link. Real rule texts can pass 1,000 characters.
- **Inline code.** `` `Backtick` `` spans in rule text, fix and why render as mono code. Other markdown stays as plain text.
- A real conflict gets a badge on each row from `keep`: `a`/`b` → Keep (`leaf`) and Remove (`walle`), `both` → Keep on both, `neither` → Rewrite (`trim`) on both, `ask` → You choose (`eve`).
- `fix` follows a `trim` FIX label in `body`. A `fix` of "-" hides the row. `why` sits under it in `muted`.
- Foot: the EVE mark, "Judged by <agent>", cached or not. The id is not shown as text; a "Copy id" button puts it on the clipboard (the id repeats the locations and is long). Before a verdict, the EVE mark blinks and the foot reads "Waiting for the answer file".
- A verdict list puts real problems first and stacks cards `space-3` apart, 680px wide at most.
