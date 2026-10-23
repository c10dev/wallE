# Agent rules for wallE

Code: `walle/`. Tests: `tests/`. Commands: README.md. Scope: ROADMAP.md.

## Principles
- walle code makes no network call. Only `walle/app.py` listens, on 127.0.0.1.
- walle code calls no model and reads no API key. Judgments go to `judge.py` batch files.
- walle code never edits an instruction file. It writes patches to `runs/<id>/patches/`.
- walle code never writes into a scanned folder. Check each new output path with `out_in_scan`.
- A new key or op in `apply.check_change` needs a refusal test.
- `walle apply` and `undo` write settings files only with `apply.write_atomic`, after a backup and a sha256.
- A file that a running Claude app rewrites goes in `apply.APP_FILES` and passes `apply._app_guard`.
- If walle cannot list processes, refuse. After a write, check the hash again and report a mismatch as `lost`.
- A command that writes any file outside the run folder goes in `app.REFUSED`.
- Start only `ps`, `zstd` and `app.child_argv`, as argv lists. `ps` and `zstd` get a timeout.
- Signal only process groups that walle started. Stop them when the page closes or the server exits.
- TODO: decide whether `apply` may delete a key. README.md:126 says it never deletes a settings key, but op `remove` deletes one `mcpServers` entry in `~/.claude.json`.

## Unknown values
- A value walle could not observe is `None` in code, `null` in JSON and a labelled unknown on screen, never 0. The caller refuses or says unknown.
- An unknown record kind goes to an `other` source.
- A printed saving names its estimator and says "lower bound", or comes from a measured compare.
- A size cap names its reason beside the constant. Output that a cap cuts says that it was cut, as `app.OUTPUT_CAP` does.
- TODO: mark the cut text in `judge._section` and the trim judge item.

## Contracts
- `scan.json` and `eve.json`: add fields freely. A rename or removal bumps `schema` and adds a README migration line.
- `runs/index.jsonl` rows: add fields only.
- Run records store raw keys. Only report and app code make labels.
- A change to a cached record bumps that module's `CACHE_VERSION` and notes `<n>: <change>` beside it.
- User text, exception messages included, is a `walle/strings.py` key with `en` and `ko` (see `apply.ApplyError`).
- A refusal is one stderr line. Exit 2 is bad usage or a lookup error. No stdin prompts.
- Exit 1 is a refused or failed action. Each command documents what its exit 1 means.
- `walle alarm` exits 0 on every input, within the 5 s hook timeout.
- Keep the version the same in pyproject.toml, walle/__init__.py, .claude-plugin/plugin.json and .claude-plugin/marketplace.json. Change all four in one commit.
- Add a CHANGELOG at the first release.

## Modules
- Only `cli.py` and `app.py` start commands. New code imports nothing from `cli`.
- TODO: move `out_in_scan` and `DEFAULT_OUT` out of `cli.py`. Today compare, eve and rundiff import them inside functions.
- Only `transcript.py`, `rollout.py` and `stack.py` parse agent-written files.

## Tests
- Test: `python -m unittest discover -s tests`.
- On macOS, set TMPDIR to a path with no symlink (for example under /private/tmp). The default /var path fails 7 path tests.
- Resolve both sides of a path comparison with `Path.resolve()`.
- Run walle through `tests/appfixture.py` or patch `walle.*.HOME`. Do not read `~/.claude`, `~/.codex` or `~/.walle`.
- Update `tests/snapshots` only with `WALLE_UPDATE_SNAPSHOTS=1`.
- A parser or apply change tests bad input: a non-JSON line, an unknown kind, a changed target, an undo hash mismatch, few sessions.
- A change to a started process tests its lifecycle: a closed page, a server stop, a second command while one runs, a timeout, and a missing or failed `ps` or `zstd`.
- Synthetic data (`tests/sessions.py`) proves logic only. Host support needs a fixture (`tests/test_formats.py`).
- CI adds one job per gap. TODO: add a macOS job, because the `ps` branch of `apply.claude_processes` runs only off Linux.
- Real-data checks run by hand, read-only, and the report names them. They are not CI jobs.

## Changes
- Fix the producer of a wrong value. Delete replaced code in the same change.
- Say whether a check used fixtures or real data.
- Name changed snapshots and the reason in the commit message.
- A ROADMAP.md `Done:` line names its proof and the agents checked.

## Do not build
- No shared framework, registry or plugin layer until two callers need it.
- Add a check tool only for a defect that occurred.
