# Button

A button that runs one action. The consumer gives the label (children), `variant` and `onClick`.

- **primary** (`walle-orange` fill, `on-walle` text): one per screen, for the thing the screen is for (Apply, Run, Scan).
- **secondary** (the default): key-style, with a `line-strong` border and a `shadow-key` bottom face. It moves down 2px when pressed.
- **ghost**: toolbars and minor actions inside cards (Copy, Show command).
- Labels start with a verb and stay short, in sentence case: "Apply", "Show plan". No exclamation marks.
- Disabled is a `sunk` fill with `muted` text. Say why it is blocked in text next to the button.
