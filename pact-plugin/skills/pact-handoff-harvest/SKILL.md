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

### Step 0: Resolve the Session Directory (do this once)

Resolve the absolute session directory **before any journal read**, and reuse that one value (`$SESSION_DIR`) for every journal read below (Step 1 `agent_handoff`, Step 3.5 `artifact_paths`, and Step 10 `variety_assessed`). **Every journal read in this skill MUST pass this explicit `--session-dir`** — never a path-less read.

**Why this is load-bearing (not optional):** you run **off-lead** (a `pact-secretary` teammate). The implicit-path read (`read_events(...)` with no `--session-dir`) derives its path via `pact_context.get_session_dir()`, which **false-returns `''` in a teammate frame** (no persisted lead session context) → the read silently returns **0 events**. Off-lead, that would make the entire harvest — HANDOFF discovery, artifact recovery, and calibration — a silent no-op. Passing an explicit `--session-dir` is frame-independent and masked-read-safe.

Resolve the directory with the `pact_harvest.py resolve-session-dir` subcommand, which reads `pact-session-context.json` and routes the reconstruction through the SSOT helper `reconstruct_session_dir` (it sanitizes both the slug and the `session_id` the same way the writer did, so the reconstructed path cannot drift from where the journal was actually written — a hand-built `{slug}/{session_id}` join would land on a DIFFERENT directory whenever the project basename or `session_id` contains a non-`[A-Za-z0-9_-]` character):

```bash
if ! SESSION_DIR=$(python3 "{plugin_root}/hooks/shared/pact_harvest.py" \
       resolve-session-dir --context-file "{context_file}"); then
  # Nonzero exit (2) = unresolvable: context file missing/unreadable/invalid,
  # or reconstruct_session_dir returned ''. Report the gap to the team-lead
  # and STOP — do NOT proceed to any journal read.
  echo "HARVEST GAP: could not resolve session_dir; reporting and stopping." >&2
fi
# On success $SESSION_DIR holds the absolute session dir; reuse it for every read below.
```

**Key the report-gap-and-stop branch on the subcommand's EXIT CODE**, never on parsing stdout for emptiness — a nonzero exit is unambiguous and cannot be defeated by a stray byte. On a nonzero exit, **report the gap to the team-lead and stop** — do NOT fall back to a path-less read (that silently re-introduces the off-lead false-empty bug). An unresolved `session_dir` is a reportable gap, not a degrade-to-implicit case.

### Step 1: Task Discovery

You have two sources for finding completed agent tasks, in priority order:

1. **Session journal** (primary, GC-proof): `$SESSION_DIR/session-journal.jsonl` (the `$SESSION_DIR` resolved in Step 0) — read `agent_handoff` events via the existing `session_journal.py read` subcommand (explicit `--session-dir`, masked-read-safe):

   ```bash
   EVENTS=$(python3 "{plugin_root}/hooks/shared/session_journal.py" read \
              --session-dir "$SESSION_DIR" --type agent_handoff)
   ```

   `read` prints a **JSON ARRAY** to stdout (`[ {...}, {...} ]`), NOT one JSON object per line. So parse the whole stdout once — `json.loads(EVENTS)` → a list of event dicts — then iterate the list (do **not** iterate line-by-line). Each event is `{"type": "agent_handoff", "agent": "...", "task_id": "...", "task_subject": "...", "handoff": {...}, "ts": "..."}` — full HANDOFF content inline, garbage-collection-proof. **Deduplicate**: extract unique task_ids only.
2. **`TaskList`** (supplementary): Read `TaskList` for completed tasks owned by agents. Useful as a cross-reference and for catching tasks where the completion hook didn't fire. Note: the platform garbage-collects older task files during long sessions, so `TaskList` may be incomplete.

If none of these sources have completed agent tasks, report "No pending HANDOFFs to review" and complete — this is normal when HANDOFFs were already processed by an earlier trigger (idempotent).

### Step 2: Dedup Check (Processed Tasks)

Read your processed task list from your team's section in agent memory (`~/.claude/agent-memory/pact-secretary/session_processed_tasks.md`). The file is namespaced by team — read **only** your own `## team={your team_id}` section (file-format contract: see Step 8). Skip any task IDs already processed — only review the delta. This enables incremental passes (e.g., after remediation).

### Step 3: Read All HANDOFFs

For each discovered task, read the HANDOFF using this journal-first fallback:

1. **Session journal** (preferred, GC-proof): If the task was discovered via `agent_handoff` journal events, the journal event's `handoff` field contains the full HANDOFF content inline — use it directly. The journal carries the **accepted** HANDOFF for every completed task: the team-lead's single completion (acceptance) emits whatever `metadata.handoff` holds at that moment, so on a reject→revise→accept flow the journal event holds the revised content the lead accepted. This is the most reliable source for all completed tasks, first-pass and revised alike.
2. **`TaskGet` fallback**: If there is no journal event for the task, fall back to `TaskGet(taskId).metadata.handoff` (read the raw `metadata` JSON; `TaskGet` is metadata-blind for `handoff` content). May fail for garbage-collected tasks.
3. **Report gap**: If all sources fail, report the gap to lead — note the task_id, agent name, and timestamp so the team-lead has context.

Pseudocode for the journal-first read:

```python
for task_id in unprocessed:
    journal_event = next((e for e in journal_events if e.task_id == task_id), None)
    if journal_event:
        handoff = journal_event.handoff  # accepted content, GC-proof
    else:
        # No journal event — last-resort metadata read (may be GC'd).
        task_meta = read_task_metadata(task_id) or {}  # raw JSON; TaskGet is metadata-blind
        handoff = task_meta.get("handoff")
    # ...process handoff...
```

Read all HANDOFFs before proceeding to extraction.

### Step 3.1: Resolve Sibling Metadata (three-tier snapshot fallback)

A HANDOFF may reference sibling metadata keys on its task (verification records, parked analyses, teachback history, variety rationales). Those siblings die with the task file when the task store drains — but every completed task's non-handoff metadata is also mirrored into the journal as a `task_metadata_snapshot` event. Resolve sibling keys through this three-tier fallback:

1. **Task file** (freshest; may be drained): `read_task_metadata(task_id)` — the raw task JSON's `metadata` object, exactly as in the Step 3 pseudocode.
2. **Snapshot fallback** (GC-proof): read the mirrored snapshots via the existing subcommand (explicit `--session-dir`, masked-read-safe):

   ```bash
   SNAPSHOTS=$(python3 "{plugin_root}/hooks/shared/session_journal.py" read \
                 --session-dir "$SESSION_DIR" --type task_metadata_snapshot)
   ```

   As in Step 1, `read` prints a **JSON ARRAY** — parse the whole stdout once, then iterate. Each event carries `task_id`, `metadata` (the size-bounded sibling-key payload), `subject`, `occupant`, and optionally `owner` / `task_type` / `truncated`. **Selection**: filter to events with the matching `task_id`; because the platform reuses task_ids across arcs, when you are resolving siblings FOR an `agent_handoff` event, additionally filter to events whose `occupant` equals `occupant_hash(agent, task_subject)` computed from that handoff event's own fields with the SAME shared function (`python3 -c "import sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.agent_handoff_marker import occupant_hash; print(occupant_hash(sys.argv[1], sys.argv[2]))" "$AGENT" "$TASK_SUBJECT"`) — never a local reimplementation. Aggregate (whole-arc) reads apply the arc-scoped `--since` bound first, exactly as Step 10 does for `variety_assessed`. Take the **latest-`ts`** event within the match, **last-wins on an equal `ts`** (journal events stamp `ts` at second granularity, so a same-second re-emit ties; the later line in journal order is the authoritative one — the same tie-break the artifact-paths supersede uses) — a task may legally carry multiple snapshots (a changed payload after completion re-emits; the latest is the authoritative end-state). A value of shape `{"_truncated": true, ...}` or a top-level `_dropped_keys` list means the full value lived only in the task file — note the truncation in your synthesis, don't fake the missing content.
3. **Graceful degrade**: neither source resolves → record the gap (task_id, key, timestamp) exactly as Step 3's report-gap tier does; never invent content.

### Step 3.5: Resolve and Read Phase Artifacts (always)

Each phase's HANDOFF is the **distilled frame**; the phase's disk artifact (e.g. `docs/preparation/{feature}.md`, `docs/architecture/{feature}.md`, `docs/plans/{slug}-plan.md`, `docs/review/…`) is the **fuller substance**. The lead writes a path-only `artifact_paths` journal event pointing at each phase's artifact(s); that event lives in the journal (outside any worktree), so it survives `git worktree remove` even though the pointed-at file is worktree-ephemeral. **Always** resolve these events and fold the artifact substance into the same synthesis the HANDOFF drives.

1. **Resolve** (masked-read-safe — uses the Step 0 `$SESSION_DIR`): call the `pact_harvest.py resolve-artifacts` subcommand, which reads the `artifact_paths` events and applies the supersede-by-`(workflow, feature)`-latest-`ts` dedup for you:

   ```bash
   ARTIFACTS=$(python3 "{plugin_root}/hooks/shared/pact_harvest.py" resolve-artifacts \
                 --session-dir "$SESSION_DIR" --feature "{feature}")
   # stdout is a single-line JSON object {workflow: [abs_path, ...]}, e.g.:
   # {"prepare":["/abs/docs/preparation/{feature}.md"],"architect":["/abs/docs/architecture/{feature}.md"]}
   # Empty (no artifacts for this feature) -> {}. Parse with json.loads, iterate keys.
   ```

   The subcommand already filters to this feature, groups by `workflow`, takes the **latest-`ts`** event per `(workflow, feature)`, and returns only the resolved set. Each `artifact_paths` event carries the **COMPLETE** path-list for its `(workflow, feature)` (a full enumeration per emit, not a delta), so the latest event is self-sufficient — the supersede never merges across events. Result (the JSON object): one path-list per `(workflow, feature)`.
2. **Read** each path in the surviving events' `paths` lists off disk. Paths are full-absolute; read them **while the worktree is live** (the `worktree-cleanup` harvest-before-teardown guard guarantees this ordering at the single teardown chokepoint). If a path no longer resolves (file already gone — the accepted abnormal-teardown edge), skip it, note the gap, and degrade to HANDOFF-only for that artifact.
3. **Synthesize ONE entry from BOTH sources together** (NOT verbatim, NOT a second entry). For each work unit, produce a SINGLE pact-memory entry synthesized from the HANDOFF **and** its artifact: the artifact is the fuller substance, the HANDOFF is the distilled frame. A ~19 KB artifact becomes a **richer-but-bounded** entry (a few hundred tokens of decisions/lessons informed by the full substance) — do NOT store the raw artifact. Substance flows into the entry's `context`/`decisions`; put the artifact's path in an entity `notes` field (NOT a `files` field — that field is rejected on save).
4. **Dedup** — reuse the existing mechanism; do NOT invent a content-diff. Against existing memory: the Step 6 save-vs-update entity+topic protocol, unchanged — the synthesized HANDOFF+artifact entry enriches an existing entry exactly as a HANDOFF-only entry does. Against the HANDOFF's own content: the only new rule is **sequencing** — because step 3 synthesizes the HANDOFF and artifact into ONE entry, there is no separate artifact-entry to dedup; the single synthesis IS the dedup. (Idempotency: the existing processed-task ledger of Step 2/Step 8 extends to mark a `(workflow, feature)` artifact as read, so an incremental or consolidation re-harvest does not re-read and re-distill the same artifact.)

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

**Save the processed task IDs to your team's section in agent memory.** Locate (or create) the `## team={your team_id}` section in `~/.claude/agent-memory/pact-secretary/session_processed_tasks.md` and overwrite **that section's** task-ID list to set the baseline for subsequent incremental passes. Overwrite only your own team's section — never modify, overwrite, or remove another team's `## team=` section. Multiple secretary instances (one per concurrent team) share this single file; each owns exactly its own section.

This file is **namespaced by team** so that concurrent secretary instances (one per active team, all sharing this single user-scope file) never clobber each other's processed-task baselines. The file-format contract:

File: `~/.claude/agent-memory/pact-secretary/session_processed_tasks.md`
```markdown
---
name: session_processed_tasks
description: Task IDs processed per team for dedup on incremental passes. NAMESPACED by team to avoid cross-secretary collision (single-file user-scope).
type: reference
---

# Per-team processed-tasks log (NAMESPACED — multiple concurrent secretary instances write here)

## team={team_id} ({project}, session {session_id})

{optional one-line session note}

Processed task IDs (this team): {comma-separated IDs}
Last processed (this team): {timestamp}

## team={other_team_id} ({project}, session {session_id})

...
```

**Section semantics:**
- The section key is your own `{team_id}` (your spawn `pact-XXXXXXXX`). The parenthetical `({project}, session {session_id})` is human-readable context.
- Read/write **only your own** `## team={team_id}` section. You MUST NOT read, edit, or remove any other team's section.
- Within your own section: **overwrite** to set the clean baseline on this Standard Harvest pass; **append** task IDs on incremental passes (the intra-team overwrite-then-append semantics are preserved exactly — they just operate on your team's section instead of the whole file).
- If your `## team={team_id}` section does not yet exist, create it (append a new section at the end of the file); never recreate the file from scratch.

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
- Read `initial_variety_score` from the journal's `variety_assessed` event (GC-proof, survives the task-store drain), using the Step 0 `$SESSION_DIR` via the existing `session_journal.py read` subcommand: `python3 "{plugin_root}/hooks/shared/session_journal.py" read --session-dir "$SESSION_DIR" --type variety_assessed`. As in Step 1, `read` prints a **JSON ARRAY** — `json.loads` the whole stdout into a list, then iterate (not line-by-line). **Select the event for THIS feature** — `variety_assessed` events carry a `task_id`, and a resumed/multi-feature session holds one per feature (plus, because the platform reuses task_ids across arcs, the current feature's id can match a PRIOR arc too). So do NOT take the first event: filter to events whose `task_id` matches the feature task being harvested and take the **latest-`ts`** match — the `resolve_arc_start(events, feature_task_id)` semantics the wrap-up retrospective uses (`shared/variety_divergence.resolve_arc_start` is the canonical implementation). Then resolve the scalar total from that event's `variety` dict via the pure `resolve_variety_total(variety)` helper (`shared/teachback_schema.py`) rather than indexing `variety['total']` directly — it prefers the canonical `total` key, falls through a documented fallback chain, and returns `None` instead of raising `KeyError` if the dict is malformed or `total` is missing. If no `variety_assessed` event matches this feature (e.g., a feature dispatched without a variety emit), or `resolve_variety_total` returns `None`, ask the team-lead for the variety score instead.
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
- **Consumer exclusivity guard**: the calibration aggregation reads `dispatch_variety` events ONLY. `task_metadata_snapshot` events also carry `variety` payloads, but the snapshot is a recovery/breadth source for sibling-key CONTENT (Step 3.1) and must NEVER become an additive second numerator for the coverage ratio — counting both would double-count dispatches.

---

## Incremental Harvest Workflow

Triggered after remediation completes — processes only the delta since the last harvest pass. Fires only when remediation occurred and produced new completed tasks.

1. **Check processed task tracking**: Read **only your own** `## team={your team_id}` section of `~/.claude/agent-memory/pact-secretary/session_processed_tasks.md` for already-processed task IDs
2. **Discover new completions**: Check session journal `agent_handoff` events (primary) and `TaskList` (supplementary) for completed tasks not in the processed set — these are new completions from remediation.
3. **If no new completions**: Report "No new HANDOFFs since last harvest" and complete
4. **Read new HANDOFFs** using the Standard Harvest Step 3 two-tier fallback: prefer journal inline content, fall back to `TaskGet`
5. **Extract and save** using Steps 4-7 from Standard Harvest (extract knowledge, organizational state, dedup protocol, save)
6. **Update processed task tracking** — **append** the new task IDs to **your team's** `## team={your team_id}` section (do NOT overwrite — preserves the full session history for your team)
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
- **Prune stale `## team=` sections** in `~/.claude/agent-memory/pact-secretary/session_processed_tasks.md`: drop any `## team=` section older than ~30 days (judge by the section's `Last processed` timestamp) or whose team is known-complete (the session has wrapped/paused and will not resume). This is safe — the session journal's `agent_handoff` events are the authoritative dedup source, so a pruned-then-resurrected team re-derives its processed set from its own journal. Prune only stale/complete sections; never touch an active team's section. (Pruning happens only in this deep-clean Consolidation pass — the Standard/Incremental hot paths leave the file untouched apart from your own section.)

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
