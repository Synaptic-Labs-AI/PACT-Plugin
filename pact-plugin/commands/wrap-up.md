---
description: Perform end-of-session cleanup and documentation synchronization
---
# PACT Wrap-Up Protocol

You are now entering the **Wrap-Up Phase**. Your goal is to ensure the workspace is clean, documentation is synchronized, and the session is properly closed.

> **Cross-reference**: For pausing a session (PR open, not ready to merge), see [pause.md](pause.md). Pause consolidates memory and persists state without worktree cleanup or task deletion.

## 1. Memory Consolidation (Pass 2)

Create a consolidation task for the secretary:
```
TaskCreate(subject="secretary: session consolidation (Pass 2)",
  description="Run Consolidation Harvest for team {team_name}. Follow the Consolidation Harvest workflow in your pact-handoff-harvest skill. Report summary when done.")
TaskUpdate(taskId, owner="secretary")
```

This is the deep-clean pass. Pass 1 (workflow-level HANDOFF review) is the primary mechanism; this consolidation is recommended — skip only for trivial sessions (single comPACT, no variety assessment performed).

> **Track whether this ran**: step 5's journal template requires a `{consolidation_ran}` flag — pass the literal string `true` when the secretary confirms Pass 2 completed, or `false` when you skipped consolidation per the trivial-session rule above. The flag drives the shell-clamped `session_consolidated` emission in step 5.

> **Why this runs first**: Memory consolidation reads task HANDOFFs via `TaskGet`. Task audit (step 7) may delete completed tasks. Running consolidation first ensures HANDOFF data is available.

## 2. Documentation Sync

1. **Run `/PACT:pin-memory`** (no arguments): Reviews the session for pin-worthy context, pins what matters, and prunes stale entries. This handles both CLAUDE.md updates and pinned content maintenance in one invocation.
2. **Verify docs**: Confirm that `docs/<feature>/preparation/` and `docs/<feature>/architecture/` are up-to-date with the implementation. Archive obsolete documentation to `docs/archive/`.

## 3. Workspace Cleanup

- **Identify** any temporary files created during the session (e.g., `temp_test.py`, `debug.log`, `foo.txt`, `test_output.json`).
- **Delete** these files to leave the workspace clean.

## 4. Orchestration Retrospective (Second-Order Cybernetics)

Perform a brief self-assessment. Compare your initial variety assessment and orchestration decisions against actual outcomes. This calibrates future judgment.

**Answer these six questions:**

1. **Variety accuracy**: Was the initial variety score close to actual complexity? Over/under by how much?
2. **Phase efficiency**: Did any phases need to be re-run (imPACT)? Were any skipped phases needed after all?
3. **Specialist fit**: Were specialists well-matched to tasks? Any that should have been different?
4. **Estimation pattern**: Does this match a recurring pattern from prior sessions? (Search pact-memory for `orchestration_calibration` entries)
5. **Variety divergence**: Was per-dispatch variety distribution materially different from feature variety? Use the pure helper `from shared.variety_divergence import compute_variety_divergence` — pass `feature_variety` (`TaskGet(feature_task_id).metadata.variety.total`) and `dispatch_varieties`. **Source the dispatch varieties from the journal FIRST (GC-immune)** — the task store that holds `metadata.variety` is reaped by the teams/tasks reaper, so a task-store read goes false-empty after GC and wrongly reports "pre-dates stamping." **Arc scope (current feature only)**: in a resumed/multi-feature session the journal holds prior arcs, so FIRST compute the current arc's start timestamp via the pure helper `from shared.variety_divergence import resolve_arc_start`: read ALL `variety_assessed` events (`read --type variety_assessed --session-dir '{session_dir}'`, single-JSON-array parse) and call `arc_start = resolve_arc_start(variety_assessed_events, feature_task_id)`. The helper returns the LATEST `ts` among events matching the current `feature_task_id` — the platform reuses task_ids across arcs, so the current feature's id can also match a PRIOR arc, and the latest-ts match is the current arc (this is why a plain `read-last` of ANY feature is wrong) — or `None` for a legacy/trivial session. Pass `--since '{arc_start}'` on EVERY read below so the aggregation is scoped to the current arc; if `arc_start` is `None`, omit `--since` → whole-journal read (single-arc behavior unchanged). (Scope boundary: only orchestrate features emit `variety_assessed`, so a comPACT-led arc yields `arc_start=None`; that never mis-scopes here — a comPACT workflow does not run this retrospective, and in a resumed session the orchestrate feature's `variety_assessed` anchors `--since`, excluding any prior comPACT arc's events by ts.) Read `dispatch_variety` events: `python3 "{plugin_root}/hooks/shared/session_journal.py" read --type dispatch_variety --since '{arc_start}' --session-dir '{session_dir}'` — **the `read` subcommand prints a SINGLE JSON array**, so `events = json.loads(output)` and iterate the list; do NOT parse line-by-line, and do NOT pipe through `2>/dev/null` / `|| echo` / `head` (they mask a parse crash as emptiness). Then `dispatch_varieties = [e["variety"]["total"] for e in events]`, and the GC-immune denominator via the pure helper `from shared.variety_divergence import count_task_b_dispatch_sites`. Read the three variety-INDEPENDENT dispatch markers — `agent_dispatch`, `review_dispatch`, and `remediation` (each `read --type <T> --since '{arc_start}' --session-dir '{session_dir}'`, same single-JSON-array parse as above) — then `total_pact_dispatch_count = count_task_b_dispatch_sites(agent_dispatch_events, review_dispatch_events, remediation_events)`. Scoping all reads to the current arc is REQUIRED for the helper's remediation/agent_dispatch task_id dedup to be correct — the platform reuses task_ids across arcs, so the dedup is valid only within one arc. The helper counts distinct Task-B dispatch SITES = `len(agent_dispatch) + Σ len(review_dispatch.reviewers) + remediations whose task_id is not already an agent_dispatch task_id` — so peer-review reviewers and remediation fixers are counted (not just orchestrate/comPACT coders), a comPACT/orchestrate-remediation that emits both `remediation` and `agent_dispatch` is counted once, and un-stamped reuse dispatches still count (so coverage can be below 1.0, e.g. 6/7). Pass all three to `compute_variety_divergence(feature_variety, dispatch_varieties, total_pact_dispatch_count)` — which returns `reason="coverage_exceeds_unity"` as a self-reporting tripwire if the denominator ever regresses below the stamped count. **If `reason == "coverage_exceeds_unity"`** (the denominator collapsed or undercounts vs the numerator — `coverage` is unclamped, >= 1.0, and NOT a real ratio), do NOT render coverage as a `stamped/total` ratio and do NOT surface the divergence row (`surfaced` is False); instead emit a one-line DENOMINATOR-REGRESSION advisory — e.g. "Q5 coverage denominator looks broken (stamped dispatches exceed the counted dispatch sites) — investigate the agent_dispatch / review_dispatch / remediation markers" — so the anomaly is visible without a nonsensical ratio. **Coupled pair**: the numerator (`dispatch_variety` events) and the denominator (Task-B dispatch sites from `agent_dispatch` + `review_dispatch.reviewers` + `remediation`) must be sourced over the SAME Task-B dispatch population; the helper counts every dispatch site so the denominator no longer undercounts review/remediation dispatches. If a dispatch emit site changes, keep the helper's site set consistent or coverage skews. **Fallback (exclusive-or, no double-count)**: ONLY when the journal yields zero `dispatch_variety` events, fall back to the legacy task-store read (list of `metadata.variety.total` across all pact-* Task-B work tasks). The returned dict carries `coverage`, `mean`, `max`, `min`, `delta`, `surfaced`, `direction`, `reason`. Surface this question only when `surfaced` is True; when `coverage` is 0.0 from BOTH sources, FIRST apply the masked-empty guard — re-read the raw `session-journal.jsonl` and confirm `dispatch_variety` is genuinely absent (error-suppression or a mis-parse can make a crashed read look identical to absence) — and only then omit with the note "Per-dispatch variety not available — session pre-dates per-dispatch stamping" (now a genuine pre-stamping session, not a GC-reaped one). See [pact-variety.md §Variety Calibration Record](../protocols/pact-variety.md#variety-calibration-record) for the schema; sample output below.
6. **Variety acknowledgment signals**: How many teammates flagged the orchestrator's variety scoring as cargo-culted ("no") or concerning ("concern")? **Source from the journal FIRST (GC-immune)** — read `teachback_ack` events scoped to the current arc (reuse `arc_start` from question 5 — the latest `variety_assessed.ts` matched on `feature_task_id`): `python3 "{plugin_root}/hooks/shared/session_journal.py" read --type teachback_ack --since '{arc_start}' --session-dir '{session_dir}'` — **the `read` subcommand prints a SINGLE JSON array**, so `events = json.loads(output)` and iterate the list; do NOT parse line-by-line, and do NOT pipe through `2>/dev/null` / `|| echo` / `head` (they mask a parse crash as emptiness). Then `flags = [e["rationale_articulates_this_dispatch"] for e in events]`, `total_teachbacks = len(events)`, `cargo_cult_signal_rate = (count "no" + count "concern") / total_teachbacks`; acute-flag text comes from each event's optional `concern`. **Masked-empty guard**: if the `teachback_ack` read appears empty, FIRST re-read the raw `session-journal.jsonl` and confirm the type is genuinely absent before concluding so — error-suppression or a mis-parse can make a crashed read look identical to absence, yielding a false 0% signal rate (the exact Q6 corruption to avoid: reporting 0% when the true rate is non-zero). **Fallback (exclusive-or, no double-count)**: ONLY when the journal genuinely yields zero `teachback_ack` events, fall back to the legacy iteration over teachback Task-A subjects reading `metadata.teachback_submit.variety_acknowledgment.rationale_articulates_this_dispatch`. **Dual-trigger surfacing** (UNCHANGED — only the data source moves): surface this question when EITHER `cargo_cult_signal_rate >= 0.20` (one in five teammates flagged) OR any single `"no"` is present. Pull the `concern` text from acute `"no"`/`"concern"` flags into the output to make the surfaced rationale visible. See [pact-variety.md §Variety Acknowledgment Signal](../protocols/pact-variety.md#variety-acknowledgment-signal-wrap-up-aggregation) for the full aggregation spec.

**Sample output for question 5 (variety divergence)** when `surfaced=True`:
```
**Variety divergence** (question 5):
- Feature variety: 9
- Per-dispatch distribution: 5 dispatches; mean=6, min=4, max=8
- Coverage: 4 of 5 dispatches stamped (1 missing variety)
- Delta (feature vs mean): 3 → SURFACED (>= 2 threshold)
- Direction: feature OVERSHOT — actual dispatch complexity was lower than estimated
- Calibration note: revisit aggregate scoping; sub-dispatches were simpler than feature-level estimate suggested
```

**Sample output for question 6 (variety acknowledgment signals)** when the dual-trigger fires:
```
**Variety acknowledgment signals** (question 6):
- Teachbacks reviewed: 8 total
- Teammate flags: 6 "yes", 1 "no", 1 "concern" — signal rate 25%
- Coverage: 8 of 8 teachbacks acknowledged (100%)
- Acute flags:
  <!-- planning-artifact-exempt: fictional sample-output demonstrating retrospective acute-flag shape; `Task #14` is example data, not a real task ref -->
  - Task #14 (architect: review PR ...) — teammate flagged "no":
    "novelty_rationale repeats feature description verbatim"
- Calibration note: surfaces residual cargo-cult risk in variety scoring;
  inspect per-dispatch rationales for the flagged tasks
```

**Save as pact-memory** (delegate to secretary):
```
context: "Orchestration retrospective for {feature}"
goal: "Calibrate orchestration judgment via second-order observation"
decisions: [
  "Variety scored {X}, actual was {Y}",
  "Specialist {Z} was {well/poorly} matched because {reason}",
  "Per-dispatch variety: feature {N}, mean {M}, delta {D} {SURFACED/within-threshold}",  # only when coverage >= 50%
  "Variety acknowledgment: {ack_yes} yes, {ack_no} no, {ack_concern} concern (signal rate {rate}%)"  # only when question 6 surfaces
]
lessons_learned: ["Pattern: {any recurring observation}"]
entities: ["orchestration_calibration", "{domain}", "variety_acknowledgment", "cargo_cult_signal"]
```

The `Per-dispatch variety` decision row is omitted when `coverage < 0.5`; the `Variety acknowledgment` decision row is appended only when question 6's dual-trigger fired. The `variety_acknowledgment` and `cargo_cult_signal` entities are added only when question 6 surfaces.

**Skip when**: Session was trivial (single comPACT, no variety assessment performed).

## 5. Journal Drain-Before-Close

Before ending the session (step 8), ensure all journal entries have been processed:

1. Confirm the secretary has completed the consolidation harvest (step 1). The secretary should confirm via `SendMessage`: "All journal entries processed to pact-memory."
2. **Only on confirmation**: Proceed to worktree cleanup and session decision.
3. **If secretary cannot confirm**: Warn user — unprocessed journal entries will not be distilled to pact-memory. The journal itself is safe (stored in `~/.claude/pact-sessions/`, not the team directory).

**Journal events**: Write a `session_end` event after confirmation, then emit a `session_consolidated` event (when step 1 actually ran) so the SessionEnd detector (`check_unpaused_pr`) can recognize this session as consolidated regardless of whether the wrap-up took the "PR merged / no PR" branch or the "PR still open" branch. The bash template below is **shell-clamped** via a three-branch `case` statement — `true` emits, `false` is a no-op, and anything else (empty string, `True`, `TRUE`, a stray integer, an accidental unsubstituted placeholder) fails fast with a stderr message and non-zero exit. The orchestrator MUST pass the literal string `true` or `false` for `{consolidation_ran}`; any other value is treated as a template-substitution bug, not a caller convention.

```bash
set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] wrap-up.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type session_end --session-dir '{session_dir}'
# Emit session_consolidated only when consolidation actually ran in step 1.
# Shell-clamped via case/esac (mirrors pause.md step 5) so the prose
# contract is enforced mechanically and an invalid flag value fails
# fast rather than silently taking the false branch.
case '{consolidation_ran}' in
  true)
    python3 "{plugin_root}/hooks/shared/session_journal.py" write \
      --type session_consolidated --session-dir '{session_dir}' --stdin <<'JSON'
{"pass": 2, "task_count": {task_count}, "memories_saved": {memories_saved}}
JSON
    ;;
  false)
    ;;  # intentional no-op — step 1 was skipped per the trivial-session rule
  *)
    echo "[wrap-up.md] invalid {consolidation_ran} flag: '{consolidation_ran}' (expected literal 'true' or 'false')" >&2
    exit 1
    ;;
esac
```

The `session_consolidated` write fires under the `true` branch regardless of whether step 6 takes the "PR still open" branch (which ALSO writes `session_paused`) or the "PR merged / no PR" branch (which previously wrote nothing and caused the false-positive warning). `{task_count}` and `{memories_saved}` come from the secretary's consolidation summary (step 1); when the secretary cannot produce exact counts, emit the event with `0` for either field rather than skipping the write — the event's EXISTENCE is the detector signal and the payload is advisory audit trail.

**Recovery note**: The journal lives in `~/.claude/pact-sessions/{slug}/{session_id}/`, independent of the team directory — it survives both natural TTL cleanup and explicit team teardown. Old session directories are cleaned automatically after 30 days (with paused-session preservation). See [pact-state-recovery.md](../protocols/pact-state-recovery.md) for the full State Recovery Protocol.

## 6. Worktree Cleanup

Check for open PRs associated with the current worktree branch:
- **PR merged or no PR**: Invoke `/PACT:worktree-cleanup` to remove the worktree cleanly.
- **PR still open**: Skip worktree cleanup. Write a `session_paused` event to the journal (see the `session_paused` field table in [pause.md step 5](pause.md#5-write-paused-state-to-session-journal) for the event schema — wrap-up writes only the `session_paused` event here; the `session_consolidated` event was already emitted in step 5 above). Set `consolidation_completed: true` because wrap-up steps 1-4 already performed memory consolidation. Report: "Worktree preserved — PR still open. Use `/PACT:pause` to consolidate and pause, or `/PACT:peer-review` to continue review."

> **Non-mocked seam-integration-test gate (projects with runtime hooks).** If this PR adds or changes a runtime hook whose observable value depends on an integration seam (task-dir resolution, the real session journal/inbox, an env-keyed path, or the platform task store), it MUST include at least one test that exercises that *real* seam rather than mocking it — a mocked-only suite can stay green while the one broken seam is the one every test stubs. See the non-mocked seam-test pattern in the pact-testing-strategies skill; the seam-dependent hook set is the SSOT in `hooks/shared/hook_infra_classifier.py`. Not applicable to projects without runtime hooks.

## 7. Task Audit

Audit and optionally clean up Task state:

```
1. `TaskList`: Review all session tasks
2. For abandoned in_progress tasks: complete or document reason
3. Verify Feature task reflects final state
4. Report task summary: "Session has N tasks (X completed, Y pending)"
5. IF multi-session mode (CLAUDE_CODE_TASK_LIST_ID set):
   - Offer: "Clean up completed workflows? (Context archived to memory)"
   - User confirms → delete completed feature hierarchies
   - User declines → leave as-is
```

**Cleanup rules**:

| Task State | Cleanup Action |
|------------|----------------|
| `completed` Feature task | Archive summary, then delete with children |
| `in_progress` Feature task | Do NOT delete (workflow still active) |
| Orphaned `in_progress` | Document abandonment reason, then delete |
| `pending` blocked forever | Delete with note |

**Why conservative:** Tasks are session-scoped by default. Cleanup only matters for multi-session work via `CLAUDE_CODE_TASK_LIST_ID`.

## 8. Session Decision

Use `AskUserQuestion` with these exact options:
- **"Yes, continue"** (description: "Keep team alive, ready for next task") → On selection: Report "Ready for next task."
- **"Pause work for now"** (description: "Save session knowledge and pause — resume later") → On selection: invoke `/PACT:pause`
- **"No, end session"** (description: "Natural cleanup — platform reaps processes, 30-day TTL cleans directories (recommended)") → On selection: Report "Session complete. Teammate processes will be terminated when this session ends. Team and task directories (`~/.claude/teams/`, `~/.claude/tasks/`) are reaped automatically after 30 days by TTL cleanup."
