# `dev/` — development-only instruments

> # ⚠️ DEVELOPMENT USE ONLY
>
> **Nothing in this directory is part of the PACT plugin, and nothing here is
> safe to run casually.** These scripts modify live plugin hook files on your
> machine, capture data from Claude Code sessions that are not yours, and do
> not clean up after themselves. Read this entire file before running any of
> them.

## What is in here

Capture-campaign instruments. When PACT needs to know the *actual* shape of a
hook payload — rather than the shape the documentation claims — these scripts
wrap a live hook with a logging tee and record the raw stdin it receives.

| Script | Wraps | Captures to |
|---|---|---|
| `install_session_start_logging_shim.sh` | `hooks/session_init.py` (SessionStart) | `/tmp/pact-hook-stdin-captures/sessionstart/session_init/` |
| `install_taskcompleted_logging_shim.sh` | `hooks/agent_handoff_emitter.py` (TaskCompleted) | `/tmp/pact-hook-stdin-captures/taskcompleted/agent_handoff_emitter/` |

Each shim is a side-effect tee: it reads stdin, writes it to disk, then
replays the identical bytes to the original hook. Hook behavior is preserved
and any shim error falls through to the unmodified hook. That is the *only*
part of this design that is defensive — everything below is not.

## Why these are dangerous

### They patch plugin hook files globally

The installers resolve the highest-versioned plugin directory under
`~/.claude/plugins/cache/pact-plugin/PACT/` and rewrite a hook file **in
place**. The edit is not scoped to a project, a session, or a terminal. It is
a machine-wide change to an executable that every Claude Code session loads.

### They capture from every session on the machine, including other users'

The shim fires on **every** invocation of the wrapped hook, from **every**
concurrent Claude Code session sharing that plugin cache. There is no
session-id filter. If you install the shim to debug one session and another
session is running — yours or, on a shared machine, **another user's** — that
session's payloads land in your capture directory too. This is not
theoretical; it is the observed behavior that motivated moving these scripts
out of the distributed plugin.

Captured payloads routinely contain free-text task descriptions, working-
directory paths, transcript paths, session identifiers correlatable across
captures, and full tool I/O. Treat the capture directory as sensitive.
**Inspect it and purge it before sharing anything from a capture session:**

```bash
rm -rf /tmp/pact-hook-stdin-captures/
```

### They do not uninstall themselves

There is no `trap`, no TTL, no expiry, and no session-end cleanup. A shim you
forget about keeps capturing indefinitely, for every future session, until you
remove it by hand. The installer prints uninstall instructions and then trusts
you to follow them.

## Uninstalling

Each installer backs the hook up to a `.preshim.bak` sibling before editing.
Reverting is a manual copy back over the modified file:

```bash
cp <hook>.preshim.bak <hook>
```

Uninstall as soon as the capture session ends — not "later". The intended
lifecycle is: install, run one capture session, **immediately uninstall**,
then promote captures to fixtures.

**The version trap:** the installer targets whichever plugin version was
highest *at install time*. If the plugin updates before you revert, the
shimmed file is left behind in the **older** version directory, where you are
unlikely to look for it. Audit the whole cache rather than just the current
version:

```bash
grep -rl 'PACT-PREPARER-LOGGING-SHIM-INSTALLED' ~/.claude/plugins/cache/pact-plugin/PACT/
find ~/.claude/plugins/cache/pact-plugin/PACT -name '*.preshim.bak'
```

Either command returning results means a shim is still installed somewhere.

## Never move these back under `pact-plugin/`

`pact-plugin/` is the **distributed tree**. `.claude-plugin/marketplace.json`
declares `"source": "./pact-plugin"`, and that one subdirectory is mirrored
wholesale into every consumer's plugin cache.

There is no packaging filter anywhere in that chain — `plugin.json` has no
`files`/`include`/`exclude` key and there is no `.pluginignore`. A file's
**position relative to `pact-plugin/`** is therefore the only control over
whether it ships. Any path under `pact-plugin/` reaches every PACT user;
`dev/` reaches nobody but someone working in this repository.

That is why these scripts live here. Moving one back — even into a test or
runbook subdirectory — puts an executable that globally patches hooks and
captures other sessions' data onto the disk of every person who installs the
plugin. Add new instruments of this kind to `dev/`, never to `pact-plugin/`.

## Why `dev/` is separate from `scripts/` and `testing/`

Three repo-root directories hold things that are not application code. They
are separated by *who runs them* and *what happens if they go wrong*:

| Directory | Runs how | Blast radius |
|---|---|---|
| `scripts/` | Automated, CI-adjacent | Safe. Read-only verification gates (`verify-*.sh`) that inspect the repo and report. Failure means a red check. |
| `testing/` | Manual, by a human | Inert. Scenario checklists and canary lists — Markdown a person follows. Nothing executes. |
| `dev/` | Manual, by a human | **Dangerous.** Executables that mutate live machine state outside this repository and leave it mutated. |

`testing/` is the directory most likely to be mistaken for the right home,
since these scripts are also manual and also serve verification. The
distinction is that `testing/` documents a procedure for a human to carry out,
while `dev/` *is* the machine-altering procedure, already loaded. Something
that patches files outside the repo belongs here regardless of what it is for.
