---
name: impact-triage
description: S3 blocker triage through /PACT:imPACT.
max_turns: 20
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, TaskGet, TaskUpdate, TaskCreate, TaskList, SendMessage]
---
/PACT:imPACT The backend coder reports that the JWT library rejects the signing key. Triage the blocker: diagnose the likely cause, decide whether to redo a phase or delegate to a specialist, and state the next action. Do not implement the fix yourself. Work autonomously; do not ask questions.
