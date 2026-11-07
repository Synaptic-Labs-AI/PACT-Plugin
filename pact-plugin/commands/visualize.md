---
description: Open PACT Mission Control — a local, read-only browser view of the current or a past PACT run
argument-hint: [demo|live|<project>|stop]
---
Open PACT Mission Control for: $ARGUMENTS

## Parse the argument

- Empty or `live` — this session's project and, when known, this session's run.
- `demo` — the built-in training run; it needs no journals.
- `<project>` — a PROJECT label as the session list shows it. Use it only when it fully matches `^[A-Za-z0-9._-]+$`. Otherwise do not launch anything: tell the user the label is invalid, and run `"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz" --list` to show the valid labels.
- `stop` — stop the Mission Control server recorded for this config root. Another session or a shell may have started it.

## Launch

Run exactly one of these, from any directory. Append `--open` only when the user is at a local GUI session. Leave it off when the session is headless: over SSH (`$SSH_CONNECTION` or `$SSH_TTY` is set) or with no display server.

| Argument | Command |
|---|---|
| empty / `live` | `"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz" --detach --project-dir "$CLAUDE_PROJECT_DIR"` |
| `demo` | `"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz" --detach --demo` |
| `<project>` | `"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz" --detach --project '<label>'` |
| `stop` | `"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz" --stop` |

For the empty / `live` row:
- When this session's id is known (the `Resume:` line of the project's CLAUDE.md names it), append `--session '<session id>'`.
- When `$CLAUDE_PROJECT_DIR` is empty, drop `--project-dir "$CLAUDE_PROJECT_DIR"`, and tell the user the newest run on the config root opened, not necessarily this project's.

## Report

- Report the `URL:` line verbatim, including everything after `#`. That part is the page's access key, and the page refuses to load data without it.
- Report the `Stop:` line verbatim. It names the absolute command that stops this server.
- If the output has a `pact-viz:` line saying to stop the running instance first, report that line and the `Stop:` command, and do not stop the server yourself.
- Do not run the server in the foreground, do not poll the URL, and do not launch a second instance. A healthy running server is reused and its existing URL is printed.

## Direct launch is an equal option

Tell the user the direct launch is an equal path, not a fallback. Running the launcher with `--open` in their own shell starts or reuses the same server with no Claude turn involved, so it costs no tokens. Give them the launcher's absolute path: the path in front of `--stop` on the `Stop:` line. `${CLAUDE_PLUGIN_ROOT}` is not set in their shell. Prefer the command form when the user wants launch and stop handled from the session. Prefer the direct form when they want the viewer outside the session entirely, such as replaying a long historical run.
