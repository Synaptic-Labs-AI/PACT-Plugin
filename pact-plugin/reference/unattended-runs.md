# Unattended PACT Runs

> **Purpose**: Keep a hands-off (unattended) PACT session from stalling while the lead is idle.
>
> **Usage**: Read this when you saw the startup notice about in-process teammate mode, or before leaving a PACT run unattended.
>
> **Created**: 2026-05-29

---

## TL;DR — use tmux for unattended runs

Relaunch Claude Code with tmux teammate delivery:

```bash
claude --teammate-mode tmux
```

In tmux mode, teammate wake signals are delivered natively and reliably even
when the lead has been idle for a long time. In **in-process** mode, the lead
can sit idle waiting for a wake that needs a manual nudge — fine when you are
watching the session, a stall risk when you walk away.

You can also set it permanently in `~/.claude/settings.json`:

```json
{ "teammateMode": "tmux" }
```

## If you must stay on in-process mode

Keep a lightweight external heartbeat in another terminal so you periodically
return to nudge the lead:

```bash
while sleep 300; do printf '\a'; done   # bell every 5 min as a "check the session" cue
```

This does not fix delivery — it just reminds you to glance at the run. For
truly hands-off operation, prefer `--teammate-mode tmux`.
