# Source

This folder is a copy of the walle design system.
The design system is a claude.ai artifact of the type "Design System":
https://claude.ai/artifact/Tup1jTqb38aTSDaysCp5ke

- Copied version: `1793472507-06e6` (2026-10-31).
- Copied files: everything under the artifact's `project/` folder, at the same paths here.
- Not copied: `index.html`, `SKILL.md` and `artifact-type/`. These files belong to the artifact type, not to this design system.
- Changed: two previews hold real walle output. In `components/ContextPanel/preview.html` and
  `components/ScanProgress/preview.html`, the home folder is `/home/user`, one folder is `budget`, one skill is
  `exec-log`, and each UUID (session id, MCP server ids) is `00000000-0000-4000-8000-0000000000NN`.
  All other files are the same as in the artifact.
- The design system has no asset uploads and no font files. The previews load IBM Plex Sans and IBM Plex Mono from Google Fonts (line 1 of `components/bundle.css`).
- `components/bundle.js` is the built bundle. The artifact holds no other source for the components.

## Where it is used

`walle app` uses `components/bundle.js`, `components/bundle.css` and `tokens.json` from its own copy in
`walle/appui/vendor/walle-ds/`. That copy has changes, so the app loads nothing from the network.
`walle/appui/VENDOR.md` lists them. This folder is not in the package.

## Update this copy

1. Read the artifact's `project/` files at the new version.
2. Replace the files here with them.
3. Make the changes in the "Changed" item above again. Search the new files for other real paths and ids.
4. Change the copied version above.
5. When the bundle or the tokens changed, update `walle/appui/vendor/walle-ds/` as `walle/appui/VENDOR.md` says.
