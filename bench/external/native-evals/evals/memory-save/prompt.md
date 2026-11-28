---
name: memory-save
description: Save a project convention through the PACT memory skill.
max_turns: 20
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Bash, Write, Edit, Skill]
---
Save this project convention in PACT memory: "All API handlers must return a JSON envelope with data and error keys." Then search/retrieve it and show the stored entry and its id. Work autonomously; do not ask questions; make reasonable decisions.
