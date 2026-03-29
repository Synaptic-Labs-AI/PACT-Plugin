---
name: pact-handoff-harvest
description: |
  HANDOFF discovery, review, save, and cleanup workflow for the PACT secretary.
  Use when: processing agent HANDOFFs after workflow phases, running session
  consolidation, or recovering orphaned breadcrumbs from prior sessions.
  Triggers: harvest HANDOFFs, process HANDOFFs, incremental, consolidation, breadcrumb recovery.
---

# PACT Handoff Harvest

This skill provides the complete workflow for discovering, reviewing, and saving agent HANDOFFs as institutional knowledge. It is the single source of truth for HANDOFF processing — the secretary's agent definition describes *what role you play*; this skill describes *how you do the work*.

Three workflow variants:
- **Standard Harvest** — discover, review, save, cleanup. Triggered by workflow commands (orchestrate, comPACT, peer-review) after phases complete.
- **Incremental Harvest** — delta-only pass after remediation. Processes only new completions since last harvest.
- **Consolidation Harvest** — safety-net + deep-clean pass. Triggered by wrap-up/pause at session end.

Determine which variant to run from the task subject/description: "harvest" or "process HANDOFFs" → Standard Harvest. "incremental" or "remediation" → Incremental Harvest. "consolidation" → Consolidation Harvest.

---

## Standard Harvest Workflow

### Read All HANDOFFs Before Saving

When reviewing multiple HANDOFFs, read ALL of them via `TaskGet` before saving any memories. This lets you deduplicate and consolidate across HANDOFFs before committing to pact-memory — producing cleaner entries than saving after each individual HANDOFF.

### Step 1: Task Discovery

You have two complementary sources for finding completed agent tasks:

1. **TaskList** (primary): Read TaskList for all completed tasks owned by agents. This is authoritative — it reflects the current state of every task regardless of hook behavior.
2. **Breadcrumb file** (supplementary): `~/.claude/teams/{team_name}/completed_handoffs.jsonl` — appended by the `handoff_gate.py` hook each time an agent passes completion gates. Each line contains `{"task_id": "...", "teammate_name": "...", "timestamp": "..."}`. Provides temporal ordering and serves as a cross-reference. May not exist (already processed or hooks didn't fire). **Deduplicate**: extract unique task_ids only (the file may contain duplicates from prior cascade behavior).

If neither TaskList has completed agent tasks nor the breadcrumb file exists, report "No pending HANDOFFs to review" and complete — this is normal when HANDOFFs were already processed by an earlier trigger (idempotent).

### Step 2: Dedup Check (Processed Tasks)

Read your processed task list from agent memory (`~/.claude/agent-memory/pact-secretary/session_processed_tasks.md`). Skip any task IDs already processed — only review the delta. This enables incremental passes (e.g., after remediation).

### Step 3: Read All HANDOFFs

Read each HANDOFF via `TaskGet(taskId).metadata.handoff` for every discovered task. Read all before proceeding to extraction.

### Step 4: Extract Institutional Knowledge

Focus on:
- Architectural decisions with rationale
- Cross-cutting concerns that affect multiple components
- Stakeholder decisions (user-specified constraints or preferences)
- Patterns established that future work should follow
- Integration points between components
- Risks and uncertainties that warrant tracking

### Step 5: Capture Organizational State

Alongside institutional knowledge, snapshot the current workflow state for session recovery. Read TaskList and extract:
- Current phase statuses (which phases are completed, in-progress, pending)
- Active agents and their roles/task assignments
- Key decisions extracted from the HANDOFFs being processed (the "why" behind implementation choices)
- Any scope changes, blockers, or unresolved items discovered during the phase

Save this state snapshot to pact-memory alongside the institutional knowledge entries. This makes you the organizational note-taker — capturing not just *what was learned* but *where the project stands* at each phase boundary.

### Step 6: Save-vs-Update Dedup Protocol

**Before every `save` call**, apply this standard operating procedure:

1. Search pact-memory for the same entities and topic: `search --query "{topic}" --limit 5`
2. If a match is found with high topical overlap (same entities + same decision area + same or superseded conclusion):
   - **Update** the existing memory (`update` CLI command) rather than creating a new one
   - Note in summary: "Updated memory {id} (was: {old summary})"
3. If no match or low overlap: Proceed with `save`

This applies to ALL save operations — HANDOFF review, ad-hoc saves, and consolidation.

### Step 7: Save to pact-memory

Save using the CLI with proper structure:
- `context`: What was being done and why
- `goal`: What was achieved
- `decisions`: Key decisions with rationale and alternatives considered
- `lessons_learned`: Actionable insights
- `entities`: Components, files, services involved (enables graph search)

### Step 8: Update Processed Task Tracking

Save the list of all processed task IDs to agent memory (overwrite, not append — this sets the baseline for subsequent incremental passes):

File: `~/.claude/agent-memory/pact-secretary/session_processed_tasks.md`
```markdown
---
name: session_processed_tasks
description: Task IDs processed in current session for dedup on incremental passes
type: reference
---

Processed task IDs: 6, 7, 12, 15
Last processed: {timestamp}
```

### Step 9: Breadcrumb Cleanup

Delete the breadcrumb file after all entries are processed (simple cleanup; the file is session-scoped and also cleaned up with TeamDelete). Use `python3 -c "from pathlib import Path; Path('~/.claude/teams/{team_name}/completed_handoffs.jsonl').expanduser().unlink(missing_ok=True)"` — not shell `rm`, because the file is inside `~/.claude/teams/` which Claude Code treats as sensitive, and `rm` via Bash triggers a permission prompt.

### Step 10: Report Summary

Report to the lead:

```
SendMessage(to="team-lead",
  message="[secretary→lead] HANDOFF review complete. Saved N memories from M HANDOFFs.
- {memory summary 1}
- {memory summary 2}
Gaps: {any HANDOFFs that were thin or missing}",
  summary="HANDOFF review complete: N memories from M HANDOFFs")
```

### Step 11: Gather Calibration Data

After processing HANDOFFs, gather calibration metrics for the orchestrator's variety scoring feedback loop:
- Read the feature task metadata for `initial_variety_score` (stored during variety assessment)
- Scan TaskList for blocker count (tasks with "BLOCKER:" in subject)
- Scan TaskList for phase rerun count (retry/redo phase tasks)
- Note domain from feature task description
- Infer specialist fit from HANDOFF content (scope mismatch signals, blocker patterns)
- Send a calibration check to the lead:
  ```
  SendMessage(to="team-lead",
    message="[secretary→lead] Calibration: variety was scored {X}. Blockers: {N}, reruns: {N}. Was actual difficulty higher, lower, or about the same? Any dimensions that surprised you?",
    summary="Calibration check: variety {X}")
  ```
- On lead's response, compute the full CalibrationRecord and save to pact-memory with entities `['orchestration_calibration', '{domain}']`

---

## Incremental Harvest Workflow

Triggered after remediation completes — processes only the delta since the last harvest pass. Fires only when remediation occurred and produced new completed tasks.

1. **Check processed task tracking**: Read `~/.claude/agent-memory/pact-secretary/session_processed_tasks.md` for already-processed task IDs
2. **Read TaskList** for completed tasks not in the processed set — these are new completions from remediation
3. **If no new completions**: Report "No new HANDOFFs since last harvest" and complete
4. **Read new HANDOFFs** via `TaskGet(taskId).metadata.handoff`
5. **Extract and save** using Steps 4-7 from Standard Harvest (extract knowledge, organizational state, dedup protocol, save)
6. **Update processed task tracking** — append new task IDs to the processed set (do NOT overwrite — preserves the full session history)
7. **Do NOT delete the breadcrumb file** — it may still be accumulating entries from ongoing work
8. **Update existing memories** if remediation superseded prior decisions (use `update` CLI command, not `save`)
9. **Report delta summary** to lead — only report what changed in this incremental pass

---

## Consolidation Harvest Workflow

Triggered during `/PACT:wrap-up` or `/PACT:pause`. This is the deep-clean pass — it extends the standard workflow with memory consolidation and pruning.

### Step 1: Safety Net (Unprocessed HANDOFFs)

If the breadcrumb file at `~/.claude/teams/{team_name}/completed_handoffs.jsonl` still exists, process remaining HANDOFFs first using the Standard Harvest workflow above (Layer 2 may have been missed). Then continue with consolidation.

### Step 2: Review Session Memories

Review all memories saved during this session by listing recent pact-memory entries.

### Step 3: Consolidate and Prune

- Merge overlapping memories (same topic, same entities, compatible conclusions)
- Prune superseded memories (update or delete entries replaced by newer information)

### Step 4: Sync Working Memory

Sync Working Memory to CLAUDE.md. The auto-sync mechanism handles individual saves, but consolidation may have merged/pruned entries that require a refresh.

### Step 5: Save Orchestration Retrospective

Save orchestration retrospective as calibration data (see Standard Harvest Step 11 for CalibrationRecord schema). This captures the session-level view: overall workflow effectiveness, recurring patterns, and calibration for future variety scoring.

### Step 6: Report Summary

Report consolidation results to the lead, including:
- Memories consolidated (merged count)
- Memories pruned (deleted/superseded count)
- Calibration data saved
- Any gaps or concerns

---

## Knowledge Extraction Guide

### What to Save vs Skip

| Include | Skip |
|---------|------|
| Architectural decisions with rationale | File locations (agent memory handles this) |
| Cross-agent integration points | Framework conventions (agent memory) |
| Stakeholder decisions and constraints | Debugging techniques (agent memory) |
| Patterns established for this project | Implementation details without broader impact |
| Risks, uncertainties, and known issues | Routine changes following existing patterns |

### Three-Layer Guidance

When deciding where knowledge belongs:

```
Is this knowledge specific to ONE agent's craft/domain?
  -> YES -> Agent persistent memory (the agent saves it themselves)
  -> NO |

Is this knowledge about the project that other agents/sessions need?
  -> YES -> pact-memory (you save via Knowledge Distiller)
  -> NO |

Is this a broad session observation or user preference?
  -> YES -> Auto-memory (platform handles automatically)
  -> NO -> Probably doesn't need saving
```

---

## Investigation Protocol

HANDOFFs are agent-written summaries — they may omit implicit learnings (failed approaches, nuanced trade-offs). When HANDOFFs are thin, you compensate with investigation.

### When to Investigate

- HANDOFF seems thin relative to scope of work
- Key decisions lack rationale ("chose X" without "because Y")
- Uncertainty areas flagged as HIGH but lack detail
- Work touches areas where prior memories indicate recurring problems

### Investigation Techniques

**Direct teammate communication**: Message implementing agents **directly** — not through the lead. The lead does not need to be in the loop for these exchanges.

```
SendMessage(to="{agent-name}",
  message="[secretary→{agent-name}] Your HANDOFF mentions {decision}. What alternatives did you consider and why were they rejected?",
  summary="Elaboration request: {topic}")
```

**File and git analysis**: Independently examine source materials:
- Read actual files created/modified (from HANDOFF's "produced" field)
- Examine git diffs and commit history for ground truth
- Cross-reference file changes with HANDOFF claims

**Lead communication**: Only when broader context is needed that neither the HANDOFF nor the implementing agent can provide (e.g., "Why was this feature prioritized?").

### Investigation Boundaries

- Keep investigations focused — ask 1-2 targeted questions, not open-ended interviews
- Do not block workflow completion — investigation happens in parallel
- If an agent has been shut down, fall back to file/git analysis
- Report investigation findings in your review summary

---

## Ad-Hoc Save Requests

For direct save requests from the lead outside of workflow HANDOFF review (ad-hoc saves), apply the same institutional knowledge criteria and save-vs-update dedup — save decisions, lessons, and cross-cutting concerns to pact-memory.

---

## Orphaned Breadcrumb Recovery

This is the Layer 4 fallback for breadcrumbs left behind by sessions that ended without wrap-up or where Layer 2 triggers were missed.

1. Look for `completed_handoffs.jsonl` in `~/.claude/teams/*/` directories. **Exclude the current session's team** (available via the `CLAUDE_CODE_TEAM_NAME` environment variable or the team name provided in your dispatch prompt) — that team's breadcrumbs are active, not orphaned.
2. If found: report to lead "Found N orphaned HANDOFFs from prior session {team_name}"
3. Attempt to process them (TaskGet may fail for old tasks — extract what's available from breadcrumb metadata)
4. Delete the breadcrumb file after processing (use `python3 -c "from pathlib import Path; Path(...).unlink(missing_ok=True)"` — not shell `rm`, to avoid sensitive-file permission prompts)
5. Report summary of recovered knowledge (or gaps where TaskGet failed)
