---
name: pact-memory
description: |
  Persistent memory for PACT agents. Save context, goals, lessons learned,
  decisions, and entities. Semantic search across sessions.
  Use when: saving session context, recalling past decisions, searching lessons.
  Triggers: memory, save memory, search memory, lessons learned, remember, recall
---

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
# See Memory Structure table below for all available fields
# including agreements_reached and disagreements_resolved

# Search memories
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" search "rate limiting tokens"

# Search with graph-enhanced boosting for current file
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" search "auth tokens" --current-file src/auth/refresh.ts

# List recent memories
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" list --limit 10

# Get a specific memory by ID
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" get <memory_id>

# Update an existing memory
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" update <memory_id> '{"goal": "Updated goal"}'

# Delete a memory
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" delete <memory_id>

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

Each memory can contain:

| Field | Type | Description |
|-------|------|-------------|
| `context` | string | Current working context description |
| `goal` | string | What you're trying to achieve |
| `active_tasks` | list | Tasks with status and priority |
| `lessons_learned` | list | What worked or didn't work |
| `decisions` | list | Decisions with rationale and alternatives |
| `entities` | list | Referenced components, services, modules |
| `reasoning_chains` | list | How key decisions connect — "X because Y, which required Z" |
| `agreements_reached` | list | What was verified via teachback or agreement check |
| `disagreements_resolved` | list | Where agents disagreed and how it was settled |
| `files` | list | Associated file paths (auto-linked) |
| `project_id` | string | Auto-detected from environment |
| `session_id` | string | Auto-detected from environment |

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
| `save <json>` | Save a memory object | `{"memory_id": "<hex>"}` |
| `save --stdin` | Save from piped JSON | `{"memory_id": "<hex>"}` |
| `search <query>` | Semantic search | `[{"id": "...", "context": "...", ...}, ...]` |
| `search <query> --limit N` | Search with limit | `[...]` (default: 5) |
| `search <query> --current-file <path>` | Search with graph boosting | `[...]` (boosts file-related memories) |
| `list` | List recent memories | `[{"id": "...", "context": "...", ...}, ...]` |
| `list --limit N` | List with limit | `[...]` (default: 20) |
| `get <id>` | Get memory by ID | `{"id": "...", "context": "...", ...}` |
| `update <id> <json>` | Update memory fields | `{"memory_id": "<hex>"}` |
| `update <id> --stdin` | Update from piped JSON | `{"memory_id": "<hex>"}` |
| `delete <id>` | Delete a memory | `{"deleted": true, "memory_id": "<hex>"}` |
| `status` | System status | `{"memory_count": N, "db_path": "...", ...}` |
| `setup` | Initialize system | `{"status": "ready", "message": "..."}` |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | User error (bad args, invalid JSON) |
| 2 | System error (DB failure, missing deps) |

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

# Update an existing memory
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" update abc123 '{"goal": "Updated goal", "lessons_learned": ["New lesson"]}'

# Delete a memory
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" delete abc123
```

## Search Capabilities

### Semantic Search
Uses embeddings to find semantically similar memories. Requires either:
- sqlite-lembed with GGUF model (preferred)
- sentence-transformers (fallback)

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

```bash
# Required for database
pip install sqlite-vec

# For local embeddings (recommended)
pip install sqlite-lembed

# Alternative embedding backend
pip install sentence-transformers
```

### Initialize and Check Status

```bash
# Initialize the memory system (creates directories, database schema)
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" setup

# Check system status (memory count, capabilities, db path)
python3 "${CLAUDE_SKILL_DIR}/scripts/cli.py" status
```

## Storage

Memories are stored in `~/.claude/pact-memory/memory.db` using SQLite with:
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

## Best Practices

1. **Save at Phase Completion**: Save memories after completing PACT phases
2. **Include Lessons**: Always capture what worked and what didn't
3. **Document Decisions**: Record rationale and alternatives considered
4. **Link Entities**: Reference components for better graph connectivity
5. **Search Before Acting**: Check for relevant past context before starting work
6. **Write Complete Sentences**: Context should be a full description, not a fragment
7. **Be Specific in Lessons**: "X didn't work because Y" is better than "X didn't work"

## Memory Layers: pact-memory vs Auto-Memory

The PACT framework operates with multiple memory layers. Understanding their
distinct roles prevents duplication and ensures the right tool is used for the
right purpose.

| Layer | Storage | Content | Who Writes | Auto-Loaded |
|-------|---------|---------|------------|-------------|
| **Auto-memory** (MEMORY.md) | `~/.claude/projects/{hash}/memory/` | Free-form session learnings, user preferences, general patterns | Platform (automatic) | Yes (first 200 lines) |
| **pact-memory** (SQLite) | `~/.claude/pact-memory/memory.db` | Structured institutional knowledge: context, goals, decisions, lessons, entities | Agents via this skill | Via Working Memory sync to CLAUDE.md |
| **Agent persistent memory** | `~/.claude/agent-memory/<name>/` | Per-agent domain expertise accumulated across sessions | Individual agents (automatic) | Yes (first 200 lines, per agent) |

**pact-memory's unique value**: Structured fields (context, goal, decisions,
lessons_learned, entities) enable semantic search, graph-enhanced retrieval,
and cross-agent knowledge sharing -- capabilities that auto-memory's free-form
markdown does not provide.

**Coexistence model**: Auto-memory captures broad session context automatically.
pact-memory captures deliberate, structured knowledge at PACT phase boundaries.
The Working Memory section in CLAUDE.md shows the 3 most recent pact-memory
entries, providing structured context that complements auto-memory's general
learnings.

## Integration with PACT

The memory skill integrates with PACT phases:

- **Prepare**: Search for relevant past context before starting
- **Architect**: Record design decisions with rationale
- **Code**: Save lessons learned during implementation
- **Test**: Document test strategies and findings

See `references/memory-patterns.md` for detailed usage patterns.
