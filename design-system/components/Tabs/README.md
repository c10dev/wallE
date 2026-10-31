# Tabs

A row of tabs that picks one view of the content under it. The consumer gives `items` (`id`, `label`, optional `count`), `value`, `onChange(id)` and `label`, and optionally `panelId`.

- The row is a `sunk` track. The selected tab is a `surface` face with `fg` text. The others are `muted`.
- Labels are short nouns in sentence case: "All rows", "This folder". A `count` sits after the label in mono.
- Keyboard: the selected tab is the one tab stop. Left and Right move to the previous and next tab, Home and End to the first and last. The selection follows the focus.
- Give `panelId`, and give the content under the tabs `id` = `panelId`, `role="tabpanel"` and `aria-labelledby` = `panelId` + "-" + the selected `id`. Each tab then controls that panel. Without `panelId`, the tabs get ids that are unique on the page, and they control no panel.
- Two to five tabs. For more, use a list.
