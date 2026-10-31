# Vendored files of walle app

walle app loads nothing from the network. These files ship in the package.

| File | Source | Change |
| --- | --- | --- |
| `vendor/react.production.min.js` | npm `react@18.3.1`, `umd/react.production.min.js` (tarball shasum 49ab892009c53933625bd16b2533fc754cab2891) | none |
| `vendor/react-dom.production.min.js` | npm `react-dom@18.3.1`, `umd/react-dom.production.min.js` (tarball shasum c2265d79511b57d479b3dd3fdfa51536494c5cb4) | none |
| `vendor/REACT-LICENSE` | the MIT license of `react` and `react-dom` 18.3.1 (the two files are the same) | none |
| `vendor/walle-ds/bundle.js` | walle design system artifact (https://claude.ai/artifact/Tup1jTqb38aTSDaysCp5ke), version `1792564933-752d`, `project/components/bundle.js` | none |
| `vendor/walle-ds/bundle.css` | the same artifact version, `project/components/bundle.css` | line 1 removed: an `@import` of Google Fonts |
| `vendor/walle-ds/tokens.json` | the same artifact version, `project/tokens.json` | the `usage` and `sample` notes removed: the server reads names and values only |

`tokens.css` is not a file: the server makes it from `tokens.json` (light on `:root`, dark under
`prefers-color-scheme: dark` and `[data-theme="dark"]`, each text style as `--type-<name>` and
`--type-<name>-tracking`) and adds `@font-face` rules for the IBM Plex fonts in
`walle/fonts` (SIL Open Font License, `walle/fonts/OFL.txt`). IBM Plex Mono 600 also serves weight 700.

To update the design system, read the artifact files again, copy them here, remove the `@import` line, and remove
the `usage` and `sample` notes of tokens.json again.

The full design system (brand book, component guidelines, previews and types) is in `design-system/` at the
repository root. `design-system/SOURCE.md` gives its artifact version.
