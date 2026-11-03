---
description: Open PACT Mission Control — a local, read-only browser view of the current or a past PACT run
argument-hint: [demo|live|<project>|stop]
---
Open PACT Mission Control for: $ARGUMENTS

Mission Control renders a PACT run from its session journal: phases, the specialist crew, dispatch and handoff flow, gates, findings, reviews, and the wrap-up debrief. It is read-only, binds loopback only, makes no external requests, never writes to the config root's journals, teams, tasks, or memory directories, and uses no model tokens while it runs.

## Parse the argument

- Empty or `live` — the newest live run in the default config root; if no run is live, the newest run. Use `<project>` to target another project.
- `demo` — a deterministic built-in training run; available even with no journals on disk.
- `<project>` — a project slug from the session list; open its newest run.
- `stop` — stop the Mission Control server this session launched.

## Launch

Run exactly one of these, from any directory:

| Argument | Command |
|---|---|
| empty / `live` | `"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz" --detach --open` |
| `demo` | `"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz" --detach --open --demo` |
| `<project>` | `"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz" --detach --open --project <slug>` |
| `stop` | `"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz" --stop` |

Pass `--open` when the user is at a local GUI session. Omit it when the session is headless — over SSH (`$SSH_CONNECTION` or `$SSH_TTY` is set) or with no display server available — and report the printed `URL:` line so the user can open it themselves.

Report the tool's output back to the user: the `URL:` line verbatim, and the `Stop:` line when stopping. Do not run the server in the foreground in this turn, do not poll the URL, and do not launch a second instance — the tool reuses a healthy running server and prints its existing URL.

## Direct launch is an equal option

Tell the user the direct launch is an equal path, not a fallback: running `"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz" --open` in their own shell starts the same server with no Claude turn involved, so it costs nothing in tokens. Prefer the command form when the user wants launch and stop handled from the session; prefer the direct form when they want the viewer outside the session entirely, such as replaying a long historical run.
