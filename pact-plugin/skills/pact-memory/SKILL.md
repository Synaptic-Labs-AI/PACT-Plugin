---
name: pact-memory
description: |
  Persistent memory for PACT agents. Save context, goals, lessons learned,
  decisions, and entities. Semantic search across sessions.
  Use when: saving session context, recalling past decisions, searching lessons.
  Triggers: memory, save memory, search memory, lessons learned, remember, recall
---

<!-- planning-artifact-exempt-file: skill body demonstrates memory-entry shape via fictional examples that intentionally include PR/issue refs and version markers; these are example data, not real planning artifacts -->

# PACT Memory Skill

Persistent memory system for PACT framework agents. Store and retrieve context,
goals, lessons learned, decisions, and entities across sessions with semantic search.

## Overview

The PACT Memory skill provides:
- **Rich Memory Objects**: Store context, goals, tasks, lessons, decisions, and entities
- **Semantic Search**: Find relevant memories using natural language queries
- **Graph-Enhanced Retrieval**: Memories linked to files are boosted when working on related files
- **Session Tracking**: Automatic file tracking and session context
- **Cross-Session Learning**: Memories persist across sessions for cumulative knowledge

## Store access: the two routes

<!-- PACT_STORE_BAR_BEGIN -->
**STORE ACCESS.** A memory operation (save, search, get, list, update or
delete a record) goes through the pact-memory CLI. YOU DO NOT SELECT A
STORE. Do not name a store by `--db-path`, by an environment variable, or by
one more route somebody adds later. Let the CLI resolve it. A store you
select is not the store the memory of the team lives in, so a save there is
lost rather than shared. STORE INSPECTION is different: a row count, a
column audit, or a schema check on the file. To inspect, do not run a CLI
verb, do not import a module below `skills/pact-memory/scripts/`, and do not
open the store read-write. In ONE command, against ONE resolved path, check
that `memory.db-wal` and `memory.db-shm` are both absent by their full
names, then open with `mode=ro` and `immutable=1`. Without `immutable=1` the
open fails. If a sidecar is present, stop and report. The read does not load
the vector extension, so it cannot answer a question about `vec_memories`.
Stop and report rather than take a barred route.
<!-- PACT_STORE_BAR_END -->

The sections below give the detail behind each sentence above.

MORE ON THE BRIGHT LINE, because an agent will look for an exception. THE RULE
IS A POLICY, AND IT IS NOT A CLAIM ABOUT WHAT THE TOOL DOES. An agent does not
select a store. The CLI can tell you to run `setup --db-path <path>`. That
message is correct for the tool and it does not apply to you. You reach it
only after you pass `--db-path`, which this rule forbids. Do not run it.
Report it and ask the user. The danger is NOT that the flag reaches the live
store. The flag binds the store you name, so the danger is that your save
lands in a store nobody reads. For a memory system, a write that goes nowhere
costs as much as a write that goes incorrectly.

### To inspect the store

1. Check that `memory.db-wal` and `memory.db-shm` are both absent. Name the
   two files in full. Do not use a glob.
2. If one of the two files is present, stop. Report it. Do not read the store.
3. If neither file is present, open the store read-only:
   `sqlite3.connect("file:<path>?mode=ro&immutable=1", uri=True)`

### What you must not do, and why

- Do not run a pact-memory CLI verb to inspect the store. The CLI opens a
  read-write connection and runs `PRAGMA journal_mode=WAL` on it, which
  creates `memory.db-wal` and `memory.db-shm`. A CLI read is thus a write
  to the store directory. A CLI run also reaches the same functions an import
  reaches, so it defeats the next rule.
- Do not import a module below `skills/pact-memory/scripts/`. In that package,
  functions create the live store directory as a side result of a path
  resolution, so an import puts each of them one call away. Do not read an
  absent import as a guarantee, because a subprocess reaches those functions
  with no import at all.
- Do not open the store with a plain `sqlite3.connect`. A read-write open
  creates the two sidecar files. That is a write to the store directory, even
  when no SQL runs.
- Do not copy the store with `cp`. A copy taken while commits sit in an
  uncheckpointed WAL is silently short of data, and it passes
  `PRAGMA integrity_check`. If you must have a copy, use `VACUUM INTO`.

### Why the two flags, and why the check comes first

`mode=ro` alone cannot open this store. sqlite must create `memory.db-shm` to
read a WAL database, and `mode=ro` forbids that creation, so the open fails
with `unable to open database file`. `immutable=1` tells sqlite to skip the
WAL and read the main file directly, so the open succeeds and creates nothing.
`immutable=1` is not extra hardening. It is the only flag that opens the file.

The sidecar check is not a writer detector. It is a state selector. When the
two sidecar files are absent, no WAL data is pending, so the main file is the
whole database. That is the state in which `immutable=1` is correct. If a
sidecar is present, committed data can sit outside the main file, and
`immutable=1` then returns an older image and raises no error. One flag makes
the read possible. The check makes the read correct.

### If the check refuses

A refusal is information. In normal operation the CLI closes its connection
and sqlite removes the two sidecar files on that close, so the check passes. A
sidecar that stays is evidence that a writer did not close cleanly.

If a sidecar is present:

1. Report the sidecar by its full name and its size.
2. Do not read the store with `immutable=1`.
3. Do not delete a sidecar. A sidecar can hold committed data that is not
   in the main file at this time.
4. Ask the user before you go further. A recovery needs a write, and a write
   to this store is the user's decision.

### To save a memory

Run the pact-memory CLI `save` command with no `--db-path`. The CLI resolves
the default store. The section "Store access: the two routes" above gives the
cause, and that cause covers a write.

Do not run `python3 setup_memory.py init`. That script runs as a script, it
writes, and it accepts no `--db-path`, so the write reaches the live store and
you cannot steer it away. The pytest refusal in the CLI is keyed on
`PYTEST_CURRENT_TEST`, so it does not cover a run outside pytest.

Do not import a module below `skills/pact-memory/scripts/` to write. Most of
the modules in that package carry writing SQL.

## Quick Start

All commands use the CLI entry point via `${CLAUDE_SKILL_DIR}`:

```bash
# Save a memory
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" save '{
  "context": "Implementing user authentication",
  "goal": "Add JWT refresh token support",
  "lessons_learned": [
    "Redis INCR is atomic - perfect for rate limiting",
    "Always validate refresh token rotation"
  ],
  "decisions": [
    {
      "decision": "Use Redis for token blacklist",
      "rationale": "Fast TTL support, distributed access"
    }
  ],
  "reasoning_chains": [
    "Redis chosen because TTL support → needed for token expiry → simpler than DB cleanup"
  ],
  "entities": [
    {"name": "AuthService", "type": "component"},
    {"name": "TokenManager", "type": "class"}
  ]
}'
# See the Memory Structure table below for every field a save payload accepts
# (including agreements_reached and disagreements_resolved). Fields marked
# "never" in that table are read-only and are rejected if you send them.

# Search memories
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" search "rate limiting tokens"

# Search with graph-enhanced boosting for current file
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" search "auth tokens" --current-file src/auth/refresh.ts

# List recent memories
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" list --limit 10

# Get a specific memory by full ID or unique prefix (>= 7 chars, case-insensitive)
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" get <memory_id_or_prefix>

# Update an existing memory by full ID or unique prefix (scalar fields replace;
# list fields merge additively). Ambiguous prefix is refused.
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" update <memory_id_or_prefix> '{"goal": "Updated goal"}'

# Delete a memory by full ID or unique prefix. Ambiguous prefix is refused.
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" delete <memory_id_or_prefix>

# Check system status
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" status

# Initialize/verify the memory system
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" setup
```

All commands output JSON to stdout: `{"ok": true, "result": ...}`.
Errors output JSON to stderr: `{"ok": false, "error": "...", "message": "..."}`.

For large JSON payloads (to avoid shell escaping issues), use `--stdin`:
```bash
echo '{"context": "...", "goal": "..."}' | python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" save --stdin
```

## Memory Structure

A memory record has more fields than a `save`/`update` payload accepts. The
**On save** column is the one that governs what you may put in the JSON you
pass to the CLI — a payload key outside that set is REJECTED with exit 2 and
the memory is NOT written.

| Field | Type | On save | Description |
|-------|------|---------|-------------|
| `context` | string | you supply | Current working context description |
| `goal` | string | you supply | What you're trying to achieve |
| `active_tasks` | list | you supply | Tasks with status and priority |
| `lessons_learned` | list | you supply | What worked or didn't work |
| `decisions` | list | you supply | Decisions with rationale and alternatives |
| `entities` | list | you supply | Referenced components, services, modules |
| `reasoning_chains` | list | you supply | How key decisions connect — "X because Y, which required Z" |
| `agreements_reached` | list | you supply | What was verified via teachback or agreement check |
| `disagreements_resolved` | list | you supply | Where agents disagreed and how it was settled |
| `project_id` | string | optional | Accepted in the payload; auto-detected from the environment when you omit it |
| `session_id` | string | optional | Accepted in the payload; auto-detected from the environment when you omit it |
| `files` | list | **never** | Read-only. Returned by `get`/`search`/`list`; REJECTED as a payload key — see below |

### `files` is read-only — do not put it in a save payload

`files` is part of a memory RECORD, not of a save PAYLOAD. It is stored in a
separate link table and re-attached when a memory is read back, so you will
see it in `get` / `search` / `list` output. Passing it to `save` or `update`
fails with exit 2 and writes nothing.

The CLI has no way to set it: there is no `--files` flag, and the automatic
session-file linking runs off in-process state that a fresh CLI invocation
never has. **For a CLI-created memory, `files` is always empty.** Only a
long-lived in-process caller of the Python API can populate it, via the
separate `files=` parameter of `MemoryAPI.save()` — never a payload key.

To record which files a memory concerns, name them in an entity's `notes`
field instead.

### Task Format
```python
{"task": "Implement token refresh", "status": "in_progress", "priority": "high"}
```

### Decision Format
```python
{
    "decision": "Use Redis for caching",
    "rationale": "Fast, supports TTL natively",
    "alternatives": ["Memcached", "In-memory LRU"]
}
```

### Entity Format
```python
{"name": "AuthService", "type": "component", "notes": "Handles all auth flows"}
```

## CLI Reference

### Commands

| Command | Description | Output |
|---------|-------------|--------|
| `save <json>` | Save a memory object; `--no-sync` leaves CLAUDE.md's Working Memory untouched | `{"memory_id": "<hex>", "sync_status": "..."}` |
| `save --stdin` | Save from piped JSON | `{"memory_id": "<hex>", "sync_status": "..."}` |
| `search <query>` | Semantic search | `[{"id": "...", "context": "...", ...}, ...]` |
| `search <query> --limit N` | Search with limit | `[...]` (default: 5) |
| `search <query> --current-file <path>` | Search with graph boosting | `[...]` (boosts file-related memories) |
| `list` | List recent memories | `[{"id": "...", "context": "...", ...}, ...]` |
| `list --limit N` | List with limit | `[...]` (default: 20) |
| `get <id\|prefix>` | Get memory by full ID or unique prefix (>= 7 chars, case-insensitive). Ambiguous prefix returns `AMBIGUOUS_PREFIX` with `matches: [...]`, `matches_capped`, `total_matches`; too-short returns `PREFIX_TOO_SHORT` | `{"id": "...", "context": "...", ...}` |
| `update <id\|prefix> <json>` | Update memory fields (list fields merge additively). Same prefix-resolution rules as `get`; ambiguous prefix is refused | `{"memory_id": "<hex>"}` |
| `update <id\|prefix> --stdin` | Update from piped JSON | `{"memory_id": "<hex>"}` |
| `update <id\|prefix> <json> --replace` | Replace list fields wholesale instead of merging | `{"memory_id": "<hex>"}` |
| `delete <id\|prefix>` | Delete a memory. Same prefix-resolution rules as `get`; ambiguous prefix is refused | `{"deleted": true, "memory_id": "<hex>"}` |
| `sync` | Rebuild CLAUDE.md's Working Memory from this project's newest 3 memories (replaces the section; `--claude-md-root` bounds the write) | `{"sync_status": "wrote", "projected": 3, "memory_ids": [...], "project_id": "..."}` |
| `status` | System status | `{"memory_count": N, "db_path": "...", ...}` |
| `setup` | Initialize system | `{"status": "ready", "message": "..."}` |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | User error (bad args, invalid JSON, not found) |
| 2 | Validation error (unknown field name, unknown sub-object key) — the error envelope includes an `allowed_fields` list |

### Update Semantics

`update` uses **additive merge with content-hash dedup** for list-valued fields
(`lessons_learned`, `reasoning_chains`, `agreements_reached`, `disagreements_resolved`,
`active_tasks`, `decisions`, `entities`). Scalar fields (`context`, `goal`, etc.) still
replace on update.

- Passing `{"lessons_learned": ["new lesson"]}` **appends** to the existing list;
  duplicate items (by content hash) are silently deduplicated, so repeated saves are
  idempotent.
- Pass `--replace` when you intentionally want to remove items from a list by
  overwriting it wholesale.
- Unknown top-level fields (e.g. `{"foo": 1}`) raise `ValueError` with exit code 2
  instead of silently disappearing. Likewise, unknown sub-object keys (e.g.
  `{"entities": [{"description": "…"}]}` — the field is `notes`, not `description`)
  raise `ValueError`.

This prevents partial-list updates from silently clobbering the entire column.

### Examples

```bash
# Save a memory
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" save '{"context": "Bug fix", "lessons_learned": ["Check null values first"]}'

# Search memories
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" search "authentication"

# Search with file context for graph-enhanced results
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" search "auth patterns" --current-file src/auth/service.py

# List recent memories
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" list --limit 5

# Update an existing memory — additive list merge (default)
# This APPENDS "New lesson" to the existing lessons_learned list; any
# existing lessons are preserved. Scalar fields like "goal" still replace.
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" update abc123 \
  '{"goal": "Updated goal", "lessons_learned": ["New lesson"]}'

# Update with wholesale list replacement (--replace)
# Use this ONLY when you intentionally want to remove items from a list.
# After this call, lessons_learned contains exactly ["Only lesson that matters"]
# and nothing else.
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" update abc123 \
  '{"lessons_learned": ["Only lesson that matters"]}' --replace

# Delete a memory
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" delete abc123
```

## Search Capabilities

### Semantic Search
Uses embeddings to find semantically similar memories. Requires both:
- `pysqlite3` — enables SQLite extension loading, which `sqlite-vec` needs
- `model2vec` — the embedding backend (model `minishlab/potion-base-8M`, 256 dimensions)

`model2vec` is the only embedding backend the code implements.

### Graph-Enhanced Search
When searching while working on a file, memories linked to:
- The current file
- Files imported by/importing the current file
- Files modified in the same session

...are boosted in ranking.

### Keyword Fallback
If embeddings are unavailable, falls back to substring matching across
context, goal, lessons_learned, and decisions fields.

## Setup

### Dependencies

These are the three packages the memory system installs and imports:

```bash
# Enables SQLite extension loading — required before sqlite-vec can load
pip install pysqlite3

# Vector storage and similarity search
pip install sqlite-vec

# Embedding backend for semantic search
pip install model2vec
```

Install `pysqlite3`, not `pysqlite3-binary`. The two are different
distributions and only `pysqlite3` publishes artifacts for macOS on arm64.

`setup` installs all three automatically. Run it rather than installing by
hand:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" setup
```

If any of the three is missing, semantic search is unavailable and search
falls back to keyword mode. `status` reports which mode is active.

### Initialize and Check Status

```bash
# Initialize the memory system (creates directories, database schema)
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" setup

# Check system status (memory count, capabilities, db path)
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" status
```

## Storage

Memories are stored in `~/.claude/pact-memory/memory.db` (matches a default-root pin in code — do not migrate) using SQLite with:
- WAL mode for crash safety
- Vector extensions for semantic search
- Graph tables for file relationships

## Command Line Usage

When invoked via `/pact-memory <command> "<args>"`:

### Save Command
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" save '<json>'
```

**IMPORTANT**: The argument is just a hint. You MUST construct a comprehensive memory object with ALL relevant fields. Never just save the raw string. Think of each memory as a detailed journal entry that your future self (or another agent) needs to fully understand what happened, why it mattered, and what was learned.

**Required fields for every save:**

| Field | Minimum Length | What to Include |
|-------|----------------|-----------------|
| `context` | 3-5 sentences (paragraph) | Full background: what you were working on, why, what led to this point, relevant history, the state of things when this memory was created |
| `goal` | 1-2 sentences | The specific objective, including success criteria if applicable |
| `lessons_learned` | 3-5 items | Specific, actionable insights with enough detail to be useful months later. Each lesson should explain the "why" not just the "what" |

**Recommended fields:**
- `decisions`: Key decisions made with full rationale, alternatives considered, and why they were rejected
- `entities`: Components, files, services, APIs involved (enables graph-based retrieval)

**Writing comprehensive context:**

BAD (too sparse):
> "Debugging auth bug"

STILL BAD (single sentence):
> "Debugging authentication failure in the login flow where users were getting 401 errors."

GOOD (comprehensive):
> "Working on the fix/auth-refresh branch to resolve issue #234 where users reported intermittent 401 errors after being logged in for extended periods. The bug was reported by 3 enterprise customers last week and is blocking the v2.1 release. Initial investigation pointed to the token refresh mechanism, specifically a race condition between concurrent API requests. The authentication system uses JWT tokens with 15-minute expiry and a refresh token rotation pattern. This session focused on reproducing the bug locally by simulating high-latency conditions."

**Example transformation:**
```
# Agent is asked to save "figured out the auth bug"

# Construct the full memory object and save:
{
    "context": "Working on the fix/auth-refresh branch to resolve issue #234 where users reported intermittent 401 errors after being logged in for extended periods. The bug was reported by 3 enterprise customers last week and is blocking the v2.1 release. Initial investigation pointed to the token refresh mechanism, specifically a race condition between concurrent API requests. The authentication system uses JWT tokens with 15-minute expiry and a refresh token rotation pattern. This session focused on reproducing the bug locally by simulating high-latency conditions and tracing through the token refresh flow.",
    "goal": "Identify and fix the root cause of intermittent authentication failures that occur after extended user sessions, ensuring the fix doesn't introduce performance regressions.",
    "lessons_learned": [
        "The token refresh mechanism had a race condition: when multiple API requests detected an expired token simultaneously, each would trigger its own refresh, causing token rotation conflicts where subsequent requests used invalidated tokens",
        "Adding a mutex/lock around the token refresh operation prevents concurrent refresh attempts - the first request refreshes while others wait and then use the new token",
        "The bug only manifests under high latency conditions (>500ms API response time) because faster responses complete before the token expiry window, making it hard to reproduce in development",
        "Our existing retry logic actually made the problem worse by immediately retrying with the same stale token instead of waiting for the refresh to complete",
        "Integration tests should include latency simulation to catch timing-dependent bugs like this"
    ],
    "decisions": [
        {
            "decision": "Use mutex pattern for token refresh instead of request queuing",
            "rationale": "Simpler implementation with less state to manage. A mutex ensures only one refresh happens at a time while other requests wait. Our concurrency level (typically <10 concurrent requests) doesn't warrant the complexity of a full request queue.",
            "alternatives": ["Request queue with single refresh - more complex, better for high concurrency", "Optimistic token prefetch - would require predicting refresh timing", "Retry with backoff - doesn't solve the root cause, just masks it"]
        }
    ],
    "entities": [
        {"name": "AuthService", "type": "service", "notes": "Central authentication service handling login, logout, and token management"},
        {"name": "TokenManager", "type": "class", "notes": "Manages JWT token lifecycle including refresh logic"},
        {"name": "src/auth/refresh.ts", "type": "file", "notes": "Contains the token refresh implementation where the bug was fixed"}
    ]
}
```

### Search Command
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" search "<query>"
```
Returns semantically similar memories. Use natural language queries.

### List Command
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" list --limit 10
```
Shows recent memories (default: 20).

### Sync Command
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" sync
```
The Working Memory section of CLAUDE.md is a derived view of the store. `sync` replaces it with this project's newest 3 memories, each under its own date; to change what the section shows, `update` or `delete` the record, then run `sync` again. A `sync_status` of `empty` means the project has no memories and the file was not touched; check the envelope's `project_id` against the project you expected.

## Best Practices

1. **Save at Phase Completion**: Save memories after completing PACT phases
2. **Include Lessons**: Always capture what worked and what didn't
3. **Document Decisions**: Record rationale and alternatives considered
4. **Link Entities**: Reference components for better graph connectivity
5. **Search Before Acting**: Check for relevant past context before starting work
6. **Write Complete Sentences**: Context should be a full description, not a fragment
7. **Be Specific in Lessons**: "X didn't work because Y" is better than "X didn't work"
8. **Check Save Results**: The `save` command verifies persistence by reading back the saved memory. If verification fails (exit code 2, error type `SYSTEM_ERROR`), the save silently failed — retry or check system status

## Memory Layers: pact-memory vs Auto-Memory

The PACT framework operates with multiple memory layers. Understanding their
distinct roles prevents duplication and ensures the right tool is used for the
right purpose.

| Layer | Storage | Content | Who Writes | Auto-Loaded |
|-------|---------|---------|------------|-------------|
| **Auto-memory** (MEMORY.md) | `{config_dir}/projects/{hash}/memory/` | Free-form session learnings, user preferences, general patterns | Platform (automatic) | Yes — head of the index only, under the same limits as agent memory; see the index-upkeep rule in `pact-agent-teams` |
| **pact-memory** (SQLite) | `~/.claude/pact-memory/memory.db` (matches a default-root pin in code — do not migrate) | Structured institutional knowledge: context, goals, decisions, lessons, entities | Agents via this skill | Partially — newest entries only, via Working Memory sync to CLAUDE.md |
| **Agent persistent memory** | Platform-delivered absolute path — the leaf is given, never derived from the type name | Per-agent domain expertise accumulated across sessions | Individual agents (automatic) | Yes — head of the index only; see the index-upkeep rule in `pact-agent-teams` for the enforced limits (per memory directory) |

> `{config_dir}` is this session's Claude config root — the value of `$CLAUDE_CONFIG_DIR` when set and non-empty, otherwise `$HOME/.claude`. Read it off an absolute path the platform already injected into your context — your plugin root is `{config_dir}/plugins/…` — rather than shelling out for the variable. Substitute it before running any command; never assume `~/.claude`.

**pact-memory's unique value**: Structured fields (context, goal, decisions,
lessons_learned, entities) enable semantic search, graph-enhanced retrieval,
and cross-agent knowledge sharing -- capabilities that auto-memory's free-form
markdown does not provide.

**Coexistence model**: Auto-memory captures broad session context automatically.
pact-memory captures deliberate, structured knowledge at PACT phase boundaries.
The Working Memory section in CLAUDE.md shows the most recent pact-memory
entries, capped at 3, then reduced further by a token budget, so it can show
fewer. The newest entry is not compressed and not dropped. Older entries are
compressed to a one-line summary and keep their Memory ID. An entry carries no
Memory ID when the record was saved without one, or when the identifier could
not be written safely. What survives provides structured context that
complements auto-memory's general learnings, and the full history stays
searchable via the `search` command.

## Integration with PACT

The memory skill integrates with PACT phases:

- **Prepare**: Search for relevant past context before starting
- **Architect**: Record design decisions with rationale
- **Code**: Save lessons learned during implementation
- **Test**: Document test strategies and findings

See `references/memory-patterns.md` for detailed usage patterns.
