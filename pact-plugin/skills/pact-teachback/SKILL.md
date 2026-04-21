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
    "Proceeding unless corrected."
  ),
  summary="Teachback: <1-line summary>"
)
```

After sending, record the teachback as metadata on your task:

```
TaskUpdate(taskId, metadata={"teachback_sent": true})
```

If you will idle-wait for the lead's correction, SET `intentional_wait`
(reason `awaiting_teachback_approved`, resolver `lead`) before going idle and
CLEAR it on resume. See "Protocol Waits" in `pact-agent-teams` for the
SET/CLEAR snippets and full contract.

## Ordering rule

You must send the teachback before any Edit/Write/Bash call used for
implementation work. Reading files to understand the task (via Read, Glob,
Grep) is permitted before teachback; those are understanding actions, not
implementation actions.

## Post-send behavior

After sending the teachback, proceed with your work immediately. Do not
wait for the lead to confirm — the protocol is non-blocking by design.
If the lead sends a correction via SendMessage, adjust your approach
as soon as you see it.

## Exception

Consultant questions (a peer asks you something) do not require a teachback.
You only teachback on task dispatches.
