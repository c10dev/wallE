# CommandBar

The status strip and `walle` command input at the top of the main area. It is not a chat input. The consumer gives `project` (`path`, optional `branch`), `device` (`name`, `status`, optional `detail`), `agent`, `onRun(command)`, and optionally `defaultValue`, `placeholder`, `running`, `clearOnRun` and `hint`.

- Upper strip: Project, Device and Agent cells split by vertical rules. Cell names in the `label` style, values in bold `body-sm`, paths in mono.
- Device status is a lamp plus a word: `online` → Online (`leaf`), `busy` → Running (`eve`, blinking), `offline` → Offline (`line-strong`). `detail` holds the load or the last contact.
- Lower row: type only the subcommand after the `$ walle` prefix. The input is a mono field on `sunk` with a 2px `line-strong` border and `radius-sm`.
- One primary Run button. It is disabled when the input is empty, while `running`, or when the device is offline.
- `onRun` gets the full command with `walle ` in front. The input keeps its text by default (`clearOnRun` empties it).
- It is fixed to the screen, so it has no shadow. On a narrow screen the cells stack one per line.
