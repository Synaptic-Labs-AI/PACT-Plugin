---
name: pact-handoff-harvest
description: |
  HANDOFF discovery, review, save, and cleanup workflow for the PACT secretary.
  Use when: processing agent HANDOFFs after workflow phases, running session
  consolidation, or recovering orphaned completed handoffs from prior sessions.
  Triggers: harvest HANDOFFs, process HANDOFFs, incremental, consolidation, handoff recovery.
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

When reviewing multiple HANDOFFs, read ALL of them before saving any memories. This lets you deduplicate and consolidate across HANDOFFs before committing to pact-memory — producing cleaner entries than saving after each individual HANDOFF.

### Step 1: Task Discovery

You have two sources for finding completed agent tasks, in priority order:

1. **Session journal** (primary, GC-proof): `~/.claude/pact-sessions/{slug}/{session_id}/session-journal.jsonl` — read `agent_handoff` events via `python3 -c "import sys; sys.path.insert(0, '{hooks_dir}'); from shared.session_journal import read_events; import json; [print(json.dumps(e)) for e in read_events('agent_handoff')]"`. Each event contains `{"type": "agent_handoff", "agent": "...", "task_id": "...", "task_subject": "...", "handoff": {...}, "ts": "..."}` — full HANDOFF content inline, garbage-collection-proof. **Deduplicate**: extract unique task_ids only.
2. **`TaskList`** (supplementary): Read `TaskList` for completed tasks owned by agents. Useful as a cross-reference and for catching tasks where the completion hook didn't fire. Note: the platform garbage-collects older task files during long sessions, so `TaskList` may be incomplete.

If none of these sources have completed agent tasks, report "No pending HANDOFFs to review" and complete — this is normal when HANDOFFs were already processed by an earlier trigger (idempotent).

### Step 2: Dedup Check (Processed Tasks)

Read your processed task list from agent memory (`~/.claude/agent-memory/pact-secretary/session_processed_tasks.md`). Skip any task IDs already processed — only review the delta. This enables incremental passes (e.g., after remediation).

### Step 3: Read All HANDOFFs

For each discovered task, read the HANDOFF using this revision-aware fallback:

1. **Revision-aware metadata read**: Read the task's `metadata` (raw JSON; `TaskGet` is metadata-blind for `handoff` content). If `metadata.revision_number` is set and `> 1`, prefer `metadata.handoff` over the journal event. The journal `agent_handoff` event captured the FIRST (rejected) submission only — `agent_handoff_emitter.py` writes one journal event per task lifetime via an O_EXCL marker. On revision, the team-lead-completion of the revised task does NOT emit a second journal event, so the revised content lives in `metadata.handoff` only.
2. **Session journal** (preferred for revision_number == 1 or unset, GC-proof): If the task was discovered via `agent_handoff` journal events and `revision_number` is unset or 1, the journal event's `handoff` field contains the full HANDOFF content inline — use it directly. This is the most reliable source for first-pass acceptance flows.
3. **`TaskGet` fallback**: If both above fail (no journal event AND no `metadata.handoff`), fall back to `TaskGet(taskId).metadata.handoff`. May fail for garbage-collected tasks.
4. **Report gap**: If all sources fail, report the gap to lead — note the task_id, agent name, and timestamp so the team-lead has context.

Pseudocode for the revision-aware branch:

```python
for task_id in unprocessed:
    journal_event = next((e for e in journal_events if e.task_id == task_id), None)
    task_meta = read_task_metadata(task_id) or {}  # raw JSON read; TaskGet is metadata-blind
    revision_n = task_meta.get("revision_number", 1)
    if revision_n > 1:
        # Revised HANDOFF; journal event captured only the first (rejected) submission.
        handoff = task_meta.get("handoff")
    elif journal_event:
        handoff = journal_event.handoff
    else:
        handoff = task_meta.get("handoff")
    # ...process handoff...
```

Read all HANDOFFs before proceeding to extraction.

### Step 4: Extract Institutional Knowledge

Focus on:
- Architectural decisions with rationale
- Cross-cutting concerns that affect multiple components
- Stakeholder decisions (user-specified constraints or preferences)
- Patterns established that future work should follow
- Integration points between components
- Risks and uncertainties that warrant tracking

### Step 5: Capture Organizational State

Alongside institutional knowledge, snapshot the current workflow state for session recovery. Read `TaskList` (`TaskList` is authoritative for current workflow state; session journal is primary for HANDOFF content) and extract:
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
   - `update` merges list fields additively with content-hash dedup, so passing just the new lessons/decisions/entities appends them without clobbering what's already there. Repeated calls are idempotent.
   - Use `update --replace` only when a prior conclusion has been **superseded** and you need to remove the old items from the list. Default `update` is append-only semantically — it will never delete an existing item.
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

### Step 9: Report Summary

Report to the team-lead:

```
SendMessage(to="team-lead",
  message="[secretary→team-lead] HANDOFF review complete. Saved N memories from M HANDOFFs.
- {memory summary 1}
- {memory summary 2}
Gaps: {any HANDOFFs that were thin or missing}",
  summary="HANDOFF review complete: N memories from M HANDOFFs")
```

### Step 10: Gather Calibration Data

After processing HANDOFFs, gather calibration metrics for the orchestrator's variety scoring feedback loop:
- Read the feature task metadata for `initial_variety_score` (stored during variety assessment). If `TaskGet` fails (garbage-collected), ask the team-lead for the variety score instead.
- Scan `TaskList` for blocker count (tasks with "BLOCKER:" in subject). Note: `TaskList` may be incomplete in long sessions due to garbage collection — report what's available.
- Scan `TaskList` for phase rerun count (retry/redo phase tasks)
- Note domain from feature task description
- Infer specialist fit from HANDOFF content (scope mismatch signals, blocker patterns)
- Send a calibration check to the team-lead:
  ```
  SendMessage(to="team-lead",
    message="[secretary→team-lead] Calibration: variety was scored {X}. Blockers: {N}, reruns: {N}. Was actual difficulty higher, lower, or about the same? Any dimensions that surprised you?",
    summary="Calibration check: variety {X}")
  ```
- On team-lead's response, compute the full CalibrationRecord and save to pact-memory with entities `['orchestration_calibration', '{domain}']`

---

## Incremental Harvest Workflow

Triggered after remediation completes — processes only the delta since the last harvest pass. Fires only when remediation occurred and produced new completed tasks.

1. **Check processed task tracking**: Read `~/.claude/agent-memory/pact-secretary/session_processed_tasks.md` for already-processed task IDs
2. **Discover new completions**: Check session journal `agent_handoff` events (primary) and `TaskList` (supplementary) for completed tasks not in the processed set — these are new completions from remediation.
3. **If no new completions**: Report "No new HANDOFFs since last harvest" and complete
4. **Read new HANDOFFs** using the Standard Harvest Step 3 two-tier fallback: prefer journal inline content, fall back to `TaskGet`
5. **Extract and save** using Steps 4-7 from Standard Harvest (extract knowledge, organizational state, dedup protocol, save)
6. **Update processed task tracking** — append new task IDs to the processed set (do NOT overwrite — preserves the full session history)
7. **Do NOT delete the session journal** — it may still be accumulating entries from ongoing work
8. **Update existing memories** if remediation superseded prior decisions (use `update` CLI command, not `save`). Remember: default `update` is additive merge — pass `--replace` only when the prior list items need to be discarded, not amended.
9. **Report delta summary** to team-lead — only report what changed in this incremental pass

---

## Consolidation Harvest Workflow

Triggered during `/PACT:wrap-up` or `/PACT:pause`. This is the deep-clean pass — it extends the standard workflow with memory consolidation and pruning.

### Step 1: Safety Net (Unprocessed HANDOFFs)

Check the session journal for `agent_handoff` events not yet in the processed task set. If unprocessed entries exist, run the Standard Harvest workflow above first (earlier harvest triggers may have been missed). Then continue with consolidation.

### Step 2: Review Session Memories

Review all memories saved during this session by listing recent pact-memory entries.

### Step 3: Consolidate and Prune

- Merge overlapping memories (same topic, same entities, compatible conclusions)
- Prune superseded memories (update or delete entries replaced by newer information)

### Step 4: Sync Working Memory

Sync Working Memory to CLAUDE.md. The auto-sync mechanism handles individual saves, but consolidation may have merged/pruned entries that require a refresh.

### Step 5: Save Orchestration Retrospective

Save orchestration retrospective as calibration data (see Standard Harvest Step 10 for CalibrationRecord schema). This captures the session-level view: overall workflow effectiveness, recurring patterns, and calibration for future variety scoring.

### Step 6: Report Summary

Report consolidation results to the team-lead, including:
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

**Direct teammate communication**: Message implementing agents **directly** — not through the team-lead. The team-lead does not need to be in the loop for these exchanges.

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

For direct save requests from the team-lead outside of workflow HANDOFF review (ad-hoc saves), apply the same institutional knowledge criteria and save-vs-update dedup — save decisions, lessons, and cross-cutting concerns to pact-memory.

---

## Orphaned Handoff Recovery

This is the Layer 4 fallback for completed handoffs left behind by sessions that ended without wrap-up or where Layer 2 triggers were missed.

1. Look for `session-journal.jsonl` in `~/.claude/pact-sessions/*/*/` directories. **Exclude the current session's directory** (available from the session context file at `~/.claude/pact-sessions/{slug}/{session_id}/pact-session-context.json`, or the session dir provided in your dispatch prompt) — that session's data is active, not orphaned.
2. If found: report to team-lead "Found N orphaned HANDOFFs from prior session {session_dir}"
3. Attempt to process them — prefer `agent_handoff` events from the session journal (full HANDOFF inline, read via `read_events_from(session_dir, 'agent_handoff')`); fall back to `TaskGet` (may fail for garbage-collected tasks)
4. Delete processed files after recovery (use `python3 -c "from pathlib import Path; Path(...).unlink(missing_ok=True)"` — not shell `rm`, to avoid sensitive-file permission prompts)
5. Report summary of recovered knowledge (or gaps where all sources failed)
