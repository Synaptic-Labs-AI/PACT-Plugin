# Integration Test Scenario: Memory Layer Interaction

**Priority**: P0
**Type**: Integration (runbook)
**Estimated Time**: 20-30 minutes

## Scenario Description

Verify that the three memory layers (auto-memory, pact-memory, agent persistent memory) coexist correctly: no token duplication causes context overflow, Working Memory in CLAUDE.md syncs from pact-memory SQLite, and agent persistent memory loads for specialist agents.

## Prerequisites

1. PACT plugin installed with memory hooks active (`pact-plugin/hooks/memory_enforce.py`, `staleness.py`, `session_init.py`)
2. pact-memory database exists at `~/.claude/pact-memory/memory.db`
3. Project `CLAUDE.md` contains a Working Memory section (with up to 3 entries)
4. Auto-memory file exists at `~/.claude/projects/{hash}/memory/MEMORY.md` (created by platform after at least one prior session)
5. Agent definitions include `memory: user` in frontmatter (verifiable via `grep -l "memory: user" pact-plugin/agents/*.md`)

## Steps

### Step 1: Verify Auto-Memory Loading

Start a new Claude Code session in the project directory.

**What happens**: The platform automatically loads the first 200 lines of `~/.claude/projects/{hash}/memory/MEMORY.md` into the system prompt.

**Expected outcome**: Session starts with auto-memory content available. The orchestrator can reference session learnings from previous sessions without invoking any skill.

**Verification**: Ask the orchestrator "What do you remember from previous sessions?" It should reference content from MEMORY.md without needing to search pact-memory.

### Step 2: Verify Working Memory Loading

**What happens**: The `session_init.py` hook runs at session start. Working Memory entries in CLAUDE.md (up to 3 most recent) are loaded as part of the system prompt.

**Expected outcome**: CLAUDE.md's Working Memory section is visible to the orchestrator and contains structured entries with timestamps, context, and memory IDs.

**Verification**:
```bash
# Check CLAUDE.md has Working Memory entries
grep -A 5 "Working Memory" CLAUDE.md
```
Should show up to 3 timestamped entries with structured fields (Context, Goal, Lessons, Decisions, Memory ID).

### Step 3: Verify No Duplication Between Layers

**What happens**: Auto-memory (MEMORY.md) and Working Memory (CLAUDE.md section) both load into the system prompt. They should contain complementary, not duplicated, content.

**Expected outcome**:
- Auto-memory: Free-form session learnings, user preferences, general patterns
- Working Memory: Structured PACT context (context, goal, lessons, decisions, entities)
- No significant overlap in content

**Verification**: Compare content of both:
```bash
# Auto-memory content
cat ~/.claude/projects/*/memory/MEMORY.md | head -50

# Working Memory content
grep -A 20 "Working Memory" CLAUDE.md
```
Content should be complementary. Auto-memory captures broad patterns; Working Memory captures specific PACT decisions and lessons.

### Step 4: Trigger pact-memory Save via Agent

Run a task that completes successfully, triggering the memory enforcement hook.

```
/PACT:comPACT backend Fix a typo in the README introduction
```

**What happens**: After the agent completes, `memory_enforce.py` hook evaluates whether a memory save is warranted. The orchestrator may also delegate to `pact-memory-agent` to save context.

**Expected outcome**: If the hook triggers, it invokes the pact-memory skill to save a structured memory object to SQLite. The memory includes context, goal, lessons_learned, and entities fields.

**Verification**:
```bash
# Check recent pact-memory entries
python3 -c "
from pact_memory.scripts import PACTMemory
m = PACTMemory()
recent = m.list(limit=3)
for r in recent:
    print(f'{r.created_at}: {r.context[:80]}...')
"
```
Should show a recent entry related to the task.

### Step 5: Verify Working Memory Sync

**What happens**: After a pact-memory save, the `working_memory.py` script syncs the 3 most recent memories to the Working Memory section in CLAUDE.md.

**Expected outcome**: CLAUDE.md's Working Memory section updates to include the newly saved memory entry. Older entries beyond the 3-entry limit are dropped.

**Verification**:
```bash
# Check Working Memory section updated
grep -c "###" CLAUDE.md | head -1  # Count section headers in Working Memory area

# Verify max 3 entries
grep -c "Memory ID" CLAUDE.md  # Should be <= 3
```

### Step 6: Verify Agent Persistent Memory

Invoke a specialist agent (e.g., backend coder) and observe whether agent persistent memory is loaded.

**What happens**: When an agent with `memory: user` frontmatter is spawned, Claude Code automatically loads the first 200 lines of `~/.claude/agent-memory/{agent-name}/MEMORY.md` into that agent's context.

**Expected outcome**: The specialist agent has access to domain expertise from previous sessions. This is separate from and complementary to pact-memory.

**Verification**: After the agent runs, check that the agent memory directory exists:
```bash
ls ~/.claude/agent-memory/
```
Should contain directories for agents that have accumulated persistent memory (e.g., `pact-backend-coder/`, `pact-test-engineer/`).

### Step 7: Verify Staleness Detection

**What happens**: The `staleness.py` hook checks Pinned Context entries in CLAUDE.md for staleness (entries older than 30 days).

**Expected outcome**: Pinned Context entries older than 30 days are marked with a staleness indicator. Entries within 30 days are left unmarked.

**Verification**:
```bash
# Check for staleness markers in Pinned Context
grep -A 2 "Pinned Context" CLAUDE.md | grep -i "stale"
```

### Step 8: Verify pact-memory Search

Test semantic search across the memory database to confirm structured retrieval works.

Ask the orchestrator:
```
Search pact-memory for lessons about "verification scripts"
```

**What happens**: The orchestrator delegates to `pact-memory-agent` which uses the pact-memory skill's semantic search.

**Expected outcome**: Returns relevant memories with structured fields, ranked by semantic similarity. Graph-enhanced retrieval boosts memories linked to related files.

## Verification Checks

| Check | How to Verify | Pass Criteria |
|-------|---------------|---------------|
| Auto-memory loads | Orchestrator knows session history without search | Content from MEMORY.md referenced |
| Working Memory loads | CLAUDE.md Working Memory section visible | Up to 3 structured entries present |
| No duplication | Compare MEMORY.md vs Working Memory | Complementary content, not overlapping |
| pact-memory saves work | Query SQLite after task completion | New entry appears with structured fields |
| Working Memory sync | Check CLAUDE.md after save | Section updated with latest entries, max 3 |
| Agent memory frontmatter | `grep "memory: user" pact-plugin/agents/*.md` | All 8 agent definition files have the setting |
| Agent memory directory | `ls ~/.claude/agent-memory/` | Directories exist after agents have run |
| Staleness detection | Check Pinned Context for markers | Old entries flagged, recent entries clean |
| Semantic search | Search returns relevant results | Structured memories returned, ranked by relevance |

## Failure Modes

| Failure | Symptom | Diagnosis |
|---------|---------|-----------|
| Working Memory exceeds 3 entries | More than 3 `### {date}` entries in Working Memory section | `MAX_WORKING_MEMORIES` not set to 3 in `working_memory.py` |
| pact-memory save fails silently | No new entries in SQLite after task | Check `memory_enforce.py` for errors; verify SQLite extensions installed |
| Token budget exceeded | CLAUDE.md loads slowly or context appears truncated | Combined auto-memory + Working Memory + Pinned Context too large; check token budget in `working_memory.py` |
| Agent memory not loading | Specialist lacks domain context from prior sessions | `memory: user` missing from agent frontmatter; check with `grep` |
| project_id is None | Memories saved without project tag | Project ID fallback chain broken in pact-memory scripts (env var, git rev-parse, cwd basename) |
| Cross-package import error | Python ImportError in hooks or scripts | `hooks/` and `skills/pact-memory/scripts/` cannot import from each other; shared logic is intentionally duplicated |
