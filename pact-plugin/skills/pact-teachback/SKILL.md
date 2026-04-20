---
name: pact-teachback
description: Command-style teachback protocol for PACT teammates. Invoking this skill directly instructs you to construct and send a teachback SendMessage in the canonical format before any implementation work.
---

# Teachback — Send Now

Invoking this skill means you are about to send a teachback SendMessage to
the lead. Do not proceed to implementation work until you have sent it.

## What a teachback is

A teachback is a Pask Conversation Theory verification gate. Before you
start implementing, you restate your understanding of the task so the
orchestrator can catch misunderstandings early, before you burn context on
a wrong implementation.

## Action: send this SendMessage now

Construct the following SendMessage call with your understanding of the
current task substituted into the placeholders. Send it now, before any
Edit, Write, or Bash tool call:

```
SendMessage(
  to="team-lead",
  message=(
    "[<your-agent-name>→lead] Teachback:\n"
    "- Building: <what you understand you're building>\n"
    "- Key constraints: <constraints you're working within>\n"
    "- Interfaces: <interfaces you'll produce or consume>\n"
    "- Approach: <your intended approach, briefly>\n"
    "I will NOT begin implementation work until you respond with `teachback_approved`."
  ),
  summary="Teachback: <1-line summary>"
)
```

After sending, record the teachback as metadata on your task:

```
TaskUpdate(taskId, metadata={"teachback_sent": true})
```

## Ordering rule

You must send the teachback before any Edit/Write/Bash call used for
implementation work. Reading files to understand the task (via Read, Glob,
Grep) is permitted before teachback; those are understanding actions, not
implementation actions.

## Post-send behavior

**Teachback blocks work start.** After sending, halt and wait for the
lead's structured `teachback_approved` to land on your task metadata via
`TaskUpdate`. Do NOT begin work until the lead sends `teachback_approved`.
No `Edit`, no `Write`, no `Bash`, no implementation tool calls until
approval arrives. Reading files for understanding (`Read`, `Glob`, `Grep`)
stays permitted. If the lead writes `teachback_corrections`, revise your
`teachback_submit` and wait again. If the lead writes `teachback_approved`,
you are cleared to begin.

## Exception

Consultant questions (a peer asks you something) do not require a teachback.
You only teachback on task dispatches.
