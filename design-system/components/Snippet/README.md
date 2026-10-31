# Snippet

One command or id to copy, with a Copy button. The consumer gives `text`, and optionally `face` and `className`.

- Use it for a command that the person runs in a terminal, such as `walle apply` or `walle undo`. One command per snippet.
- The text is mono 400 12px/20px with `tabular-nums`. It is not a text style yet (see "Not in sync yet" in the README). It wraps anywhere.
- The text sits on a `sunk` face. On a ground that is not `surface` (for example `sunk` or `eve-soft`), give `face: "surface"`.
- The button is a small ghost `Button`. Its accessible name is "Copy" and the text.
- In a secure context, a click copies the text, and the label reads "Copied" for 1.5 s. Elsewhere, or when the clipboard refuses, the click selects the text, and the label reads "Selected" for 4 s. The person then copies it with the keyboard.
- A hidden status region says the same to a screen reader, on each press.
