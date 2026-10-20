---
name: pact-handoff-harvest
description: |
  HANDOFF discovery, review, save, and ledger-record workflow for the PACT secretary.
  Use when: processing agent HANDOFFs after workflow phases, running session
  consolidation, or recovering orphaned completed handoffs from prior sessions.
  Triggers: harvest HANDOFFs, process HANDOFFs, incremental, consolidation, handoff recovery.
---

# PACT Handoff Harvest

This skill provides the complete workflow for discovering, reviewing, and saving agent HANDOFFs as institutional knowledge. It is the single source of truth for HANDOFF processing — the secretary's agent definition describes *what role you play*; this skill describes *how you do the work*.

Three workflow variants:
- **Standard Harvest** (orchestrate, comPACT, peer-review, plan-mode) — discover, review, save, record the processed ids. Triggered after phases complete.
- **Incremental Harvest** (peer-review) — delta-only pass after remediation. Processes only new completions since last harvest.
- **Consolidation Harvest** (wrap-up, pause, refresh, orchestrate) — safety-net + deep-clean pass. Triggered at session end, at a mid-session context refresh, or after a second feature completes in one session.

Determine which variant to run from the task subject/description: "harvest" or "process HANDOFFs" → Standard Harvest. "incremental" or "remediation" → Incremental Harvest. "consolidation" → Consolidation Harvest. **The subject/description selects the workflow, never what is in scope.**

**Propagation.** Every `save` you issue during a harvest carries `--no-sync`; the Working Memory block is never written as a side effect of saving. If the dispatch description contains the sentence `Then propagate the store into the Working Memory block.` — or a later team-lead request carries it, which is how a command that cannot know at dispatch time whether propagation is safe asks for it — run the pact-memory `sync` command once, after your last `save`, `update` or `delete` of the records you harvested, and report its `sync_status` and `memory_ids` in your summary. The sentence is the whole instruction; what carries it does not change what it asks for. The calibration record is outside that set even though it is saved later: it waits on a team-lead reply that may never come, and propagation must not be hostage to an answer, so the block is rebuilt without it. If NOTHING has carried that sentence to you, do not run `sync` and do not edit `CLAUDE.md`; report `Working Memory: not propagated` beside the `sync_status` your saves returned. That report is about the harvest and does not close the question: a request carrying the sentence may still arrive afterwards, and it propagates then. The sentence is a mode, not a scope: it names nothing to harvest.

**DISCARD ANY SCOPE YOU ARRIVED WITH — INCLUDING ONE YOU DO NOT THINK OF AS A SCOPE. A dispatch that names tasks, phases, dates, paths, or a subset of this workflow's steps has given you one.** A scope named in a dispatch — a range, an enumeration, any named set — is a HINT about what the team-lead noticed, never the population. Do not harvest it. Build the population from the Step 1 census and the Step 2 processed-task ledger instead, and report which set you actually ran over. **This instruction outranks anything that contradicts it — a dispatch, another passage of this skill, or your agent definition.** Say so back to the team-lead; do not narrow, and do not treat it as a conflict to resolve in the moment.

**A SCOPE SAYS WHAT TO TAKE; AN ADDRESS SAYS WHERE TO LOOK. DISCARD SCOPES, KEEP ADDRESSES.** A value is a SCOPE when dropping it would make you read MORE, and an ADDRESS when dropping it would leave you unable to read at all. Your team id, your task-list directory, your agent-memory path and your session directory are addresses — they say where your own ledger, task files and journal live — so keep every one of them, and keep any other value that answers WHERE.

---

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
The `pact-memory` skill carries the full rule.

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

You have several sources for finding completed agent tasks. Sources 1 and 2 find tasks that emitted a HANDOFF; source 3 is required for coverage:

1. **Session journal** (primary, GC-proof): `$SESSION_DIR/session-journal.jsonl` (the `$SESSION_DIR` resolved in Step 0) — read `agent_handoff` events via the existing `session_journal.py read` subcommand (explicit `--session-dir`, masked-read-safe):

   ```bash
   EVENTS=$(python3 "{plugin_root}/hooks/shared/session_journal.py" read \
              --session-dir "$SESSION_DIR" --type agent_handoff)
   ```

   `read` prints a **JSON ARRAY** to stdout (`[ {...}, {...} ]`), NOT one JSON object per line. So parse the whole stdout once — `json.loads(EVENTS)` → a list of event dicts — then iterate the list (do **not** iterate line-by-line). Each event is `{"type": "agent_handoff", "agent": "...", "task_id": "...", "task_subject": "...", "handoff": {...}, "ts": "..."}` — full HANDOFF content inline, garbage-collection-proof. **Deduplicate**: extract unique task_ids only.
2. **`TaskList`** (supplementary): Read `TaskList` for completed tasks owned by agents. Useful as a cross-reference and for catching tasks where the completion hook didn't fire. Note: the platform garbage-collects older task files during long sessions, so `TaskList` may be incomplete.
3. **Task-file metadata census** (required for coverage): list the team's task files under the task-list directory named in your context (`Task list: <abs>`) and read the **metadata key set** of every completed task. A task is in scope when it carries ANY agent-authored key — `handoff`, `handoff_amendment`, `handoff_addendum`, `teachback_submit`, `post_completion_verification`, `audit_summary`, `audit_summary_authored`, or any key not on this list. **Enumerate the keys each task carries; never test for one key.** A task whose content sits only in a non-`handoff` key emits no `agent_handoff` event, so sources 1 and 2 cannot see it — resolving `handoff` alone finds nothing on that task and reports success. An auditor carries its findings in `audit_summary`, mirrored to `audit_summary_authored`, and its task carries no `handoff` key at all, so a census that resolves `handoff` reports an auditor as having produced nothing.

4. **Earlier team's task store** (required when the journal names one): read `team_realigned` events the same way as source 1 (`--type team_realigned`). For each event, run the source 3 census as well on the directory of team `{from}`, the event's `from` field: the `Task list:` path in your context with its last component replaced by `{from}`. Those task ids belong to team `{from}`, not to your team: the same id can name a different task in each store, so run Step 2 and Step 8 for them against a `## team={from}` section.

Scope the census by the keys a task **carries**, never by a key family you expect. A census scoped to one key family fails the same way as one scoped to a single key.

If none of these sources have completed agent tasks, report "No pending HANDOFFs to review" and complete — this is normal when HANDOFFs were already processed by an earlier trigger (idempotent).

### Step 2: Dedup Check (Processed Tasks)

**A dispatch does not narrow this list.** The census finds which tasks carry content; only this ledger says where the last pass stopped, so the delta against it is the population — discard any scope the dispatch suggested. Read your processed task list from your team's section of `session_processed_tasks.md`, in the agent-memory directory the platform gave you — use the path you are given, never one built from your agent type. The file is namespaced by team — locate **only** your own `## team={your team_id}` section with the **Step 8 rule's existence decision**: the `grep -c` header count, what each of its outcomes means for whether your section exists, and the `awk` extract on the `1` branch (Step 8 also carries the file-format contract). **The write actions inside those branches — appending, creating a section — belong to the step that writes, not to this one.** Do NOT restate that rule here, and do NOT decide the section exists by searching what you read — a ledger past the read cap hides your section while the file is intact, and a pass that concludes "no processed IDs" reprocesses everything. **Your processed set is the UNION of every `Processed task IDs` line in that section.** A section carries many; taking the newest line alone under-reports the set and reprocesses work already done. Skip any task IDs already processed — review only the delta **against this ledger**. A dispatch that says "harvest the delta" means its own named range; that is a different delta, it is not this one, and it does not narrow this one. This enables incremental passes (e.g., after remediation).

### Step 3: Read All HANDOFFs

For each discovered task, read the HANDOFF from the two copies and combine them. This is **not** a fallback chain. Each copy can hold content the other does not.

1. **Session journal** (GC-proof): If the task was discovered through `agent_handoff` journal events, the event's `handoff` field contains the full HANDOFF content inline. **The journal carries the HANDOFF as it stood at the LAST EMITTING WRITE.** Do not read it as the accepted HANDOFF. A revision that lands before completion reaches the journal. A revision that lands after completion can reach no journal event, so the journal copy can be the superseded one.
2. **Task file** (freshest, and the task-store drain removes it): `task_utils.read_task_json(task_id, team_name)`, the raw task JSON, which carries `owner`, `subject` and `metadata`. The HANDOFF is `metadata.handoff` (`TaskGet` is metadata-blind). ⚠️ **THAT READER RETURNS AN EMPTY DICT ON A MISSING FILE, NOT `None`.** It is fail-open by design, so a drained task file, a malformed JSON file and an IO error all give `{}`. Test the drained case as EMPTINESS (`if not task:`), and do NOT test `task is None`, which is False for every one of those states.
3. **Take the UNION of the two copies, and PREFER THE TASK FILE on a conflict, with ONE narrow exception.** Do not choose one copy. Content moves in the two directions between them, so a union is the safe operation and a choice is not. Keep each field that is in one copy alone. When the two hold different content for one field, keep the task-file content. **THE EXCEPTION HAS THREE STATES, NOT TWO.** When the task-file field is PRESENT AND EMPTY and the journal field holds content, keep the JOURNAL content. When the task-file field is ABSENT, or PRESENT AND NON-EMPTY, keep the task-file preference. A `TaskUpdate` metadata merge REPLACES a nested sub-object, so a partial re-write that carries an empty field makes the present-and-empty state, and an unconditional task-file preference then ERASES the journal content in silence. 🔴 **DO NOT restate this as a blanket prefer-the-non-empty-side rule.** The exception is per-field and applies to the present-and-empty state alone. A blanket rule makes the journal the default winner and the task file is the freshest copy, which INVERTS the precedence this step is built on.
4. **When the task file is ABSENT, report `handoff divergence check unavailable for task N: task file absent`** and use the journal copy alone. Do not skip in silence. A drained task file is a reportable gap and not a clean single-source read.
5. **Report gap**: If no source resolves, report the gap to the lead. Record the task_id, the agent name and the timestamp so that the team-lead has context.

**SELECTION, because `agent_handoff` is a multi-event family.** Do not take the first match. The first match is the FIRST emit, which is the superseded copy. Use the rule Step 3.1 uses for snapshots:

1. Filter the `agent_handoff` events to the matching `task_id`.
2. Group the match by the `(agent, task_subject)` identity the event carries. Compute that identity with the shared `occupant_hash` function and not with a local reimplementation. An `agent_handoff` event carries no `occupant` field, so compute the identity from the event's own fields. The platform reuses task_ids across arcs, and a reused task_id carries one identity for each arc. When the group count is more than one, process each group as a distinct work unit. Do not pick one group.
3. In each group take the **latest `ts`**, and take the **last in journal order on an equal `ts`** (journal events stamp `ts` at second granularity, so a same-second re-emit ties. The later line in journal order is the authoritative one, the same tie-break Step 3.1 uses).
4. The task file belongs to at most one group. Union the task-file copy into the group with the identity `occupant_hash(owner, subject)` computed from the task record, and report each other group through the report-message rule below.

🔴 **THIS STEP IS THE ONE PLACE THE SELECTION RULE IS WRITTEN, AND IT HAS A SECOND IMPLEMENTATION.** `hooks/shared/session_resume.py` applies the same grouping and latest-wins when it renders its Completed Work summary, and it points here rather than repeat the rule. A change to the selection above MUST change that code in the SAME commit. Nothing compares the two.

**DERIVE THE DISK-SIDE IDENTITY THE WAY THE EMIT PATHS DERIVE THEIRS.** The two emit paths SUBSTITUTE before they hash, and the task record carries the value the substitution replaced. Compute the disk identity from the SUBSTITUTED values. Without the substitution the identity of a substituted task cannot equal one group identity, so the union does not run on the very tasks it protects:

1. **Subject term.** When the record subject is missing, empty, or whitespace-only, substitute the literal `(no subject)` before hashing. The two emit paths both apply this.
2. **Agent term.** When the record owner is missing, empty, or whitespace-only, the `TaskCompleted` emit path hashes the platform teammate name for that task in place of the owner. The task record does not carry that name, so this one is NOT reproducible from the record. Report such a task through the report-message rule below and do not read it as a drain.

**HOW TO CALL THE TWO NAMES THIS STEP USES.** The step above names `occupant_hash` and the pseudocode below names `latest_by_ts_then_journal_order`. Neither is callable from a name alone, and the instruction to use the shared function rather than a local one is not followable without a route. **DO NOT reimplement either. Use these.**

1. **`occupant_hash`**, the SHARED identity hash. This is the one runnable copy in this file. Step 3.1 uses the same command and points here.

   ```bash
   python3 -c "import sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.agent_handoff_marker import occupant_hash; print(occupant_hash(sys.argv[1], sys.argv[2]))" "$AGENT" "$TASK_SUBJECT"
   ```

   🔴 **THE AGENT AND THE SUBJECT MUST RIDE AS ARGV. DO NOT INTERPOLATE THEM INTO THE `-c` STRING.** Read `sys.argv[1]` and `sys.argv[2]` as written above, and keep the two values as separate shell arguments after the closing quote. The author of the task writes its subject, so a subject folded into the `-c` body is Python that the subject author chose, executed by you. Nothing about the values makes this safe: the ARGV BOUNDARY is what makes it safe, and rewriting the command is enough to remove it.

2. **`latest_by_ts_then_journal_order`**, defined HERE because it is defined at no site in the repo. It takes the events of ONE group, in journal order, and returns the authoritative one:

   ```python
   def latest_by_ts_then_journal_order(events):
       """Last write wins. `events` MUST arrive in journal order."""
       winner = events[0]
       for event in events[1:]:
           # `>=` and not `>`: an equal ts keeps the LATER journal line.
           if event["ts"] >= winner["ts"]:
               winner = event
       return winner
   ```

   🔴 **THE STRING COMPARE ON `ts` IS CORRECT BY A CALLER PROPERTY, NOT BY A SCHEMA PROPERTY, AND THE DIFFERENCE IS THE WHOLE OF THE RISK.** `make_event` stamps `%Y-%m-%dT%H:%M:%SZ`, and neither `agent_handoff` emit path passes a `ts` of its own, so each event of this family carries ONE format and a string compare orders them correctly TODAY. `make_event` HONOURS a caller-supplied `ts` through `setdefault`, so the one-format property belongs to the CALLERS and the schema does not enforce it. A second format defeats the compare in silence, because a MIXED SET IS ORDERED BY BYTES AND NOT BY TIME.

   🔴 **SO IF ANY CALLER EVER PASSES A `ts`, PARSE THE TWO VALUES BEFORE YOU COMPARE THEM. Do not compare them as strings, and do not reach for a rule about which byte decides.** THIS IS THE LOAD-BEARING SENTENCE OF THIS WARNING AND IT IS THE ONE PART THAT HAS NEEDED NO CORRECTION. Three rounds of review moved the text below it: a claim, then a rule replacing the claim, then a precondition replacing the rule. Each was complete across its own list of spellings and each was broken by the next probe. This sentence survived all of them. A sibling resolver in this repo carries the same warning for the same cause.

   **WHAT FOLLOWS EXPLAINS THE CONSEQUENCE IF THAT PROPERTY IS LOST. IT IS AN EXPLANATION AND NOT A LAW, AND IT CARRIES A PRECONDITION.** A string compare walks left to right, so the DATETIME PREFIX decides first, and AT AN EQUAL DATETIME PREFIX THE COMPARE IS DECIDED BY THE FIRST BYTE AFTER THE SECONDS. MEASURED at one prefix, against the `Z` form at 0x5A: `+` at 0x2B, `-` at 0x2D and `.` at 0x2E each read as OLDER, a lowercase `z` at 0x7A reads as NEWER, and a bare form with NO suffix reads oldest of all. So `2026-08-19T20:00:00.123456Z` sorts BEFORE `2026-08-19T20:00:00Z` while naming the LATER instant. THE FRACTION IS THE MOST REACHABLE OF THESE AND IT TAKES NO DELIBERATE CHOICE: a bare `datetime.now(timezone.utc).isoformat()` emits microseconds by default, where an offset takes a decision.

   🔴 **THE PRECONDITION: THAT RULE HOLDS WHERE THE DATETIME PREFIX IS FIXED-WIDTH AND THE SECONDS ARE PRESENT. OUTSIDE THAT, THE DECIDING BYTE IS NOT THE ONE THE RULE NAMES.** MEASURED, two forms that break it. `2026-08-19 20:00:00+00:00` against the `Z` form decides at INDEX 10, a SPACE at 0x20 against `T` at 0x54, and `str(datetime.now(timezone.utc))` produces that form. `2026-08-19T20:00:00.123Z` against `2026-08-19T20:00:00.123456Z` carries `.` as the byte after the seconds for BOTH, so the rule answers nothing, and the compare decides at INDEX 23 with the LATER instant reading as older. A `timespec='minutes'` form has no seconds field at all, so the named position does not exist.
   THE BOUND ON THAT LIST, AND IT IS THE CAUSE FOR PUTTING THE PARSE INSTRUCTION FIRST: it comes from a probe of the stdlib producers and one hand-built form, so a producer outside those is outside the check. NO RULE ABOUT WHICH BYTE DECIDES CAN BE MADE COMPLETE, because a new spelling can move the deciding byte to a position no rule anticipated.

🔴 **THIS RULE MIRRORS CODE, AND NOTHING ENFORCES THE MIRROR.** The two substitutions above are implemented in `agent_handoff_emitter` and in `task_lifecycle_gate._emit_lead_side_agent_handoff`. One rule thus lives at THREE sites: two emit paths IMPLEMENT it and this step DESCRIBES it. **A change to the identity derivation in either emit path MUST change this step in the SAME commit.** The coupling is stated because it cannot be removed here, and one rule at several sites with nothing to say the sites are coupled is what let a repaired rule and a stale rule stand together in this same file.

**A NON-MATCHING GROUP IS NOT ALWAYS A DRAIN, AND THE TWO CASES MUST NOT SHARE ONE MESSAGE.** Report an ABSENT task file as `handoff divergence check unavailable for task N: task file absent`. When the task file is PRESENT and no group carries its identity, report `handoff identity mismatch for task N: task file present, no event group carries its identity`. One message for the two cases makes a present file read as a drained one, which is the strongest signal this workflow emits.

**A SUBSTITUTION IS NOT THE ONLY CAUSE OF A MISMATCH, WHICH IS WHY THE SECOND MESSAGE IS NEEDED AND NOT MERELY TIDY.** The identity terms are the agent and the subject AS THEY STOOD AT EMIT TIME. An edit to either term after an emit leaves the record holding a value that no group carries. The subject term also reaches the `TaskCompleted` emit path from the platform payload rather than from the task record. So a mismatch on a present task file is an ordinary state, it is not evidence of a drain, and it is not always closable by the substitution rule above. Report it as itself, and process each group from the journal copy alone.

**THIS WORKFLOW DOES NOT RE-SWEEP A TASK IT HAS PROCESSED BEFORE.** Step 2 skips each task_id in the processed list, the Incremental Harvest Workflow discovers the delta against that same list, and the Consolidation Harvest Workflow safety net reads only tasks that are not in it. **A COMPLETED TASK IS NOT A CLOSED TASK.** An agent writes content to a task after the pass that recorded that task as processed, and no trigger in this file collects it.

**THE RE-SWEEP IS NOT AN OPTIONAL REFINEMENT, AND HERE IS THE FAILURE IT PREVENTS.** A sibling key written after completion has no trigger of its own. A journal snapshot carries such a key only when something else fires a snapshot on that task afterwards, and it carries it as a side effect rather than by design. **So on a task that keeps receiving other writes, the journal picks the key up, and on a task that goes quiet, the key is on the task file alone and the task-store drain removes it.** The quiet task is the one that looks finished, so the loss is silent and this pass reports success while it happens. **On a quiescent completed task, this harvest is the carrier and the journal is not.** To collect that content, run an incremental pass **before the session ends** and put that task_id in its population even though the ledger lists it as processed. **Do not edit the ledger to achieve this**: writes there are append-only, so an id cannot be taken back out, and it does not need to be — the ledger is a dedup record, not a work queue, so nothing reads it to decide what work remains. The cost is that a re-opened task leaves no durable trace, and that trace has no reader.

Pseudocode for the read. It carries all the selection steps, because a task_id reused across arcs resolves to the wrong arc when any one of them is dropped:

```python
def emit_side_subject(subject):
    """Mirror of the sentinel BOTH emit paths apply before they hash.
    Change this when either emit path changes, in the SAME commit."""
    return subject if (subject and str(subject).strip()) else "(no subject)"


# The values that count as EMPTY for the exception below. Test MEMBERSHIP,
# not truthiness: `not disk_value` is also TRUE for `0` and for `False`, so a
# task-file field that deliberately holds one of those would keep the journal
# value and discard the disk value.
# KEEP `None` IN THIS SET. Drop it and the narrowing itself opens the erasure
# it was written to close: a field explicitly holding null would count as
# CONTENT and overwrite the journal copy with null.
_EMPTY_VALUES = (None, "", [], {})


def union_preferring_task_file(journal_handoff, disk_handoff):
    """Task file wins each conflict, apart from present-and-empty. Three states."""
    merged = {**journal_handoff}
    for field, disk_value in disk_handoff.items():
        # PRESENT AND EMPTY on the task file, with content in the journal:
        # keep the journal value. Each other state: the task file wins.
        # A field ABSENT from the task file does not reach this loop, so the
        # journal value survives by construction.
        if disk_value in _EMPTY_VALUES and journal_handoff.get(field):
            continue
        merged[field] = disk_value
    return merged


for task_id in unprocessed:
    matches = [e for e in journal_events if e.task_id == task_id]

    # STEP 2: group by identity. An agent_handoff event carries no `occupant`
    # field, so compute the identity from the event's own (agent, task_subject)
    # with the shared function.
    groups = {}
    for e in matches:
        groups.setdefault(occupant_hash(e.agent, e.task_subject), []).append(e)

    # EMPTY DICT once the drain removed it, and NOT None. read_task_json is
    # fail-open: a missing file, a malformed file and an IO error all give {}.
    task = read_task_json(task_id, team_name)
    # Hash the SUBSTITUTED subject, because that is what the emit paths
    # hashed. The owner substitution (platform teammate name on an empty
    # owner) is NOT reproducible from the record, so a task carrying it
    # falls through to the identity-mismatch report below.
    # `.get` AND NOT A SUBSCRIPT, AT BOTH KEYS. The guard above tests
    # EMPTINESS, which covers a DRAINED record and does NOT cover a PRESENT
    # BUT PARTIAL one. MEASURED across the task store: 143 of 1421 records
    # carry no `owner` key at all, and THREE of those carry
    # `metadata.handoff`, so they are in this harvest target population.
    # A subscript raises KeyError on the accurate case rule 2 above tells
    # you to route to the identity-mismatch report, because an absent owner
    # is what the emit path replaces with the platform name.
    # `subject` is missing in 0 of 1421 today, and it takes `.get` anyway:
    # read_task_json is FAIL-OPEN, so it returns a SHAPE and not a SCHEMA,
    # and a subscript asserts a guarantee that reader does not make.
    disk_identity = (
        occupant_hash(
            task.get("owner", ""), emit_side_subject(task.get("subject", ""))
        )
        if task else None
    )
    disk_handoff = (task.get("metadata", {}).get("handoff") if task else None) or {}

    if not groups:
        if not task:
            report_gap(task_id)
        else:
            process(disk_handoff)  # task file only, no journal copy to union
        continue

    # More than one group means the task_id was reused across arcs. Each group
    # is a distinct work unit. Do NOT pick one.
    for identity, events in groups.items():
        # STEP 3: latest ts, and the line that comes after it on an equal ts.
        journal_event = latest_by_ts_then_journal_order(events)
        handoff = journal_event.handoff  # content at the last emitting write
        if identity == disk_identity:
            # UNION, task file first, apart from present-and-empty.
            handoff = union_preferring_task_file(handoff, disk_handoff)
        elif not task:
            report(f"handoff divergence check unavailable for task "
                   f"{task_id}: task file absent")
        else:
            # The task file is PRESENT and belongs to at most one group.
            # This is an identity mismatch, NOT a drain. Do not reuse the
            # drain wording for it.
            report(f"handoff identity mismatch for task {task_id}: task "
                   f"file present, no event group carries its identity")
        process(handoff)
```

Read all HANDOFFs before proceeding to extraction.

### Step 3.1: Resolve Sibling Metadata (snapshot fallback)

A HANDOFF may reference sibling metadata keys on its task (verification records, parked analyses, teachback history, variety rationales). Those siblings die with the task file when the task store drains — but every completed task's non-handoff metadata is also mirrored into the journal as a `task_metadata_snapshot` event. Resolve sibling keys through this fallback:

1. **Task file** (freshest, and it can be drained): `task_utils.read_task_json(task_id, team_name).get("metadata", {})`, the raw task JSON's `metadata` object, the same accessor the Step 3 pseudocode uses. That reader returns an EMPTY DICT on a missing file, so a drained task gives `{}` rather than a raise, and the fallback below is what covers it.
2. **Snapshot fallback** (GC-proof): read the mirrored snapshots via the existing subcommand (explicit `--session-dir`, masked-read-safe):

   ```bash
   SNAPSHOTS=$(python3 "{plugin_root}/hooks/shared/session_journal.py" read \
                 --session-dir "$SESSION_DIR" --type task_metadata_snapshot)
   ```

   As in Step 1, `read` prints a **JSON ARRAY** — parse the whole stdout once, then iterate. Each event carries `task_id`, `metadata` (the size-bounded sibling-key payload), `subject`, `occupant`, and optionally `owner` / `task_type` / `truncated`. **Selection**: filter to events with the matching `task_id`; because the platform reuses task_ids across arcs, when you are resolving siblings FOR an `agent_handoff` event, additionally filter to events whose `occupant` equals `occupant_hash(agent, task_subject)` computed from that handoff event's own fields with the SAME shared function — never a local reimplementation. **The runnable command is in Step 3, under HOW TO CALL THE TWO NAMES THIS STEP USES.** It is stated once, there, and referenced here. Aggregate (whole-arc) reads apply the arc-scoped `--since` bound first, exactly as Step 10 does for `variety_assessed`. Take the **latest-`ts`** event within the match, **last-wins on an equal `ts`** (journal events stamp `ts` at second granularity, so a same-second re-emit ties; the later line in journal order is the authoritative one — the same tie-break the artifact-paths supersede uses) — a task may legally carry multiple snapshots (a changed payload after completion re-emits; the latest is the authoritative end-state). A value of shape `{"_truncated": true, ...}` or a top-level `_dropped_keys` list means the full value lived only in the task file — note the truncation in your synthesis, don't fake the missing content.
3. **Graceful degrade**: neither source resolves → record the gap (task_id, key, timestamp) exactly as Step 3's report-gap tier does; never invent content.

**SIBLING KEY NAMES CARRY NO ORDER.** A task commonly carries a family of related sibling keys, and their names do not record which one is newest. A key that names itself `final` can be the earlier of two. Do not sort sibling keys by name, and do not read a name as a position. **ONE NAME DOES CARRY A RELATION, and it is not a position: a sibling key that amends, corrects or withdraws HANDOFF content SUPERSEDES what it addresses, whatever its date. Synthesize from the amended state, and never bank a claim an amendment withdrew.** Resolve the order of the rest in this priority:

1. **Use a write-time field the key itself carries**, for example `written_at_utc`. This is the only self-dating source, and it is present only when the agent that wrote the key chose to record it.
2. **Take no other date in the payload as the write time.** A date in the body commonly dates a different event, for example a read of some other file or a measurement of a document. A wrong timestamp is worse than an absent one, because it is executable and produces a confident wrong order.
3. **When no write-time field is present, the set is unordered.** Read every member of the family, synthesize from all of them, and report the ambiguity. Do not present one member as the end state, and do not drop the members you cannot date.

### Step 3.5: Resolve and Read Phase Artifacts (always)

Each phase's HANDOFF is the **distilled frame**; the phase's disk artifact (e.g. `docs/preparation/{feature}.md`, `docs/architecture/{feature}.md`, `docs/plans/{slug}-plan.md`, `docs/review/…`) is the **fuller substance**. The lead writes a path-only `artifact_paths` journal event pointing at each phase's artifact(s); that event lives in the journal (outside any worktree), so it survives `git worktree remove` even though the pointed-at file is worktree-ephemeral. **Always** resolve these events and fold the artifact substance into the same synthesis the HANDOFF drives.

1. **Build the feature set from the journal, the same way Step 1 builds the task population.** `resolve-artifacts` requires a `--feature` value and nothing in your context carries one. Do not take it from your dispatch: each `artifact_paths` event names its own `feature`, so the distinct values across those events ARE the set, and reading them is one command:

   ```bash
   FEATURES=$(python3 "{plugin_root}/hooks/shared/session_journal.py" read \
                --session-dir "$SESSION_DIR" --type artifact_paths \
              | python3 -c "import json,sys; print('\n'.join(sorted({e['feature'] for e in json.load(sys.stdin) if e.get('feature')})))")
   ```

   No events, or none carrying a `feature` → the set is empty, there is nothing to resolve, and that is a normal result rather than a gap.
2. **Resolve AND READ, one feature at a time.** Both happen INSIDE the loop (masked-read-safe — uses the Step 0 `$SESSION_DIR`). The `resolve-artifacts` subcommand reads the `artifact_paths` events and applies the supersede-by-`(workflow, feature)`-latest-`ts` dedup for you:

   ```bash
   while IFS= read -r FEATURE; do
     [ -n "$FEATURE" ] || continue
     ARTIFACTS=$(python3 "{plugin_root}/hooks/shared/pact_harvest.py" resolve-artifacts \
                   --session-dir "$SESSION_DIR" --feature "$FEATURE")
     # stdout is a single-line JSON object {workflow: [abs_path, ...]}, e.g.:
     # {"prepare":["/abs/docs/preparation/$FEATURE.md"],"architect":["/abs/docs/architecture/$FEATURE.md"]}
     # Empty (no artifacts for this feature) -> {}. Parse with json.loads, iterate keys.
     # READ the paths HERE, and keep what you read UNDER THIS FEATURE.
   done <<< "$FEATURES"
   ```

   🔴 **`$ARTIFACTS` IS OVERWRITTEN ON EVERY ITERATION AND NOTHING WARNS YOU.** Read its paths before the next iteration begins. After `done` it holds the LAST feature alone, so a read placed after the loop harvests one feature, discards every other, and reports success.

   🔴 **DO NOT MERGE THE PER-FEATURE OBJECTS INTO ONE.** The object is keyed by `workflow` ALONE: the feature is consumed by the `--feature` filter and never appears in the result. Every feature runs the same phases, so two features collide on MOST of their keys, and a merge keeps one path-list per workflow while looking perfectly well-formed. Keep the results separate, keyed by feature.

   **Read the loop variable, never a word-split.** A feature slug can carry a space, and `for FEATURE in $FEATURES` would split one such slug into two names that resolve to nothing.

   Paths are full-absolute; read them **while the worktree is live** (the `worktree-cleanup` harvest-before-teardown guard guarantees this ordering at the single teardown chokepoint). If a path no longer resolves (file already gone — the accepted abnormal-teardown edge), skip it, note the gap, and degrade to HANDOFF-only for that artifact.

   The subcommand already filters to this feature, groups by `workflow`, takes the **latest-`ts`** event per `(workflow, feature)`, and returns only the resolved set. Each `artifact_paths` event carries the **COMPLETE** path-list for its `(workflow, feature)` (a full enumeration per emit, not a delta), so the latest event is self-sufficient — the supersede never merges across events. Result: one path-list per `workflow`, FOR THIS ONE FEATURE.
3. **Synthesize ONE entry from BOTH sources together** (NOT verbatim, NOT a second entry). For each work unit, produce a SINGLE pact-memory entry synthesized from the HANDOFF **and** its artifact: the artifact is the fuller substance, the HANDOFF is the distilled frame. **A work unit is an `agent_handoff` group from Step 3, not a feature** — one feature carries many work units, so the artifacts a unit draws on are the ones you read under THAT UNIT'S feature. A ~19 KB artifact becomes a **richer-but-bounded** entry (a few hundred tokens of decisions/lessons informed by the full substance) — do NOT store the raw artifact. Substance flows into the entry's `context`/`decisions`; put the artifact's path in an entity `notes` field (NOT a `files` field — that field is rejected on save).
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
- Pass `--no-sync` on every `save`. Record the `sync_status` each envelope returns for Step 9. `suppressed` is the expected value; any other value means that save wrote the Working Memory block mid-harvest, and Step 9 reports the value you saw rather than the value you expected.

### Step 7.5: Propagate (only when dispatched)

If the dispatch carries the propagate sentence (see Propagation above), run `sync` now, once. It rebuilds the Working Memory section from the store: the newest three records of this project, each under its own date. Skip it when another workflow's step sent you into this one: that workflow propagates after its own last mutation, and a rebuild taken before that shows the store as it was, not as it ends. A later `save` of your own, such as Step 10's calibration record, is not that and does not skip this step. Record the returned `sync_status` and `memory_ids` for Step 9. On `empty`, record the envelope's `project_id` beside `sync_status`; a `project_id` that is not the project you expected is a misconfiguration to report to the team-lead, not an empty project. If the dispatch does not carry the sentence, skip this step.

### Step 8: Update Processed Task Tracking

**Save the processed task IDs to your team's section in agent memory.** Multiple secretary instances (one per concurrent team) share this single file; each owns exactly its own `## team={your team_id}` section, and **whether yours already exists is decided by a count, never by searching what you read** — see **Section semantics** below for that count and for the boundary rule on other teams' sections. Append this pass's task IDs to your section in `session_processed_tasks.md`, in the agent-memory directory the platform gave you, as a new `Processed task IDs` line, never rewriting the lines already there.

This file is **namespaced by team** so that concurrent secretary instances (one per active team, all sharing this single user-scope file) never clobber each other's processed-task baselines. The file-format contract:

File: `session_processed_tasks.md`, in the agent-memory directory the platform gave you. Use the path you are given; never build one from your agent type.
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
- The section key is your own `{team_id}` (your spawn `session-XXXXXXXX`). The parenthetical `({project}, session {session_id})` carries the exact on-disk names of the `pact-sessions/{project}/{session_id}` directory, taken from your own `pact-session-context.json` through the `pact_harvest.py resolve-session-dir` subcommand named at Step 0; Consolidation Step 3 reads them as a filesystem path, so write them verbatim, never a paraphrase or a display name.
- Touch only your own team's section — never modify, overwrite, or remove another team's `## team=` section, and on a Standard or Incremental pass do not read one either; only the Consolidation Step 3 prune touches other teams' sections. **Within your own section every write is still APPEND-ONLY — see the next bullet.**
- Within your own `## team=` section every write is **APPEND-ONLY**, on Standard and Incremental passes alike: add a new `Processed task IDs` line and a new `Last processed` line, and never rewrite the existing ones. **Readers of this ledger take the UNION of every `Processed task IDs` line in the section and the NEWEST `Last processed`.** So you never need to have read the section you are writing to — which is what makes the write safe, because a truncated read looks exactly like a complete one.
- **Decide whether your section exists with `grep -c '^## team={your team_id}' "{ledger path}"`, never by searching what you read.** The file can exceed a read's line cap, and a section past the cut is invisible while the file is intact — so a search of what you received answers "not found" for a section that is there. `1`: it exists — extract it with `awk 'f&&/^## team=/{exit} /^## team={your team_id}/{f=1} f' "{ledger path}"`, which reads **the FIRST matching section only and stops before the next `## team=` header**, then append. `0`: it genuinely does not exist — create it (append a new section at the end of the file); never recreate the file from scratch. `2` or more: refuse and report — duplicates mean an earlier pass already created a second section from a truncated read, so do not add a third. **Anything else — no count at all, or a count that is not a digit — is NOT `0`: refuse and report.** A `grep` that errors prints no count, and the `0` branch needs a digit to read. **Decide on the presence of a digit, never on the exit status**: the status varies by `grep` implementation — a directory given as the path exits `1` on one and `2` on another — while a true zero count exits `1` on both. No exit code separates the two; only the digit does. **Do not substitute a `sed` range for the extraction above.** `sed -n '/^## team={id}/,/^## team=/p'` fails twice over: it RESTARTS after its end pattern, so a duplicated team returns both its sections, and its end line is INCLUSIVE, so the next team's header lands inside your extract — and appending after that extract writes under another team's header.

### Step 9: Report Summary

Report to the team-lead:

```
SendMessage(to="team-lead",
  message="[secretary→team-lead] HANDOFF review complete. Saved N memories from M HANDOFFs.
- {memory summary 1}
- {memory summary 2}
Working Memory: {sync_status | empty, project {project_id}}, {N} entries projected | not propagated, saves reported {save sync_status}
Gaps: {any HANDOFFs that were thin or missing}",
  summary="HANDOFF review complete: N memories from M HANDOFFs")
```

### Step 10: Gather Calibration Data

After processing HANDOFFs, gather calibration metrics for the orchestrator's variety scoring feedback loop:
- Read `initial_variety_score` from the journal's `variety_assessed` event (GC-proof, survives the task-store drain), using the Step 0 `$SESSION_DIR` via the existing `session_journal.py read` subcommand: `python3 "{plugin_root}/hooks/shared/session_journal.py" read --session-dir "$SESSION_DIR" --type variety_assessed`. As in Step 1, `read` prints a **JSON ARRAY** — `json.loads` the whole stdout into a list, then iterate (not line-by-line). **Select the event for THIS feature** — `variety_assessed` events carry a `task_id`, and a resumed/multi-feature session holds one per feature (plus, because the platform reuses task_ids across arcs, the current feature's id can match a PRIOR arc too). So do NOT take the first event: filter to events whose `task_id` matches the feature task being harvested AND whose top-level `scope` is not `"dispatch"` (per-dispatch mirrors carry the dispatch task's id, so they never match a feature id by design — the exclusion makes that structural rather than incidental, and `resolve_arc_start` applies it too), and take the **latest-`ts`** match — the `resolve_arc_start(events, feature_task_id)` semantics the wrap-up retrospective uses (`shared/variety_divergence.resolve_arc_start` is the canonical implementation). Then resolve the scalar total from that event's `variety` dict via the pure `resolve_variety_total(variety)` helper (`shared/teachback_schema.py`) rather than indexing `variety['total']` directly — it prefers the canonical `total` key, falls through a documented fallback chain, and returns `None` instead of raising `KeyError` if the dict is malformed or `total` is missing. If no `variety_assessed` event matches this feature (e.g., a feature dispatched without a variety emit), or `resolve_variety_total` returns `None`, ask the team-lead for the variety score instead.
  **THE FEATURE TASK ID IS THE ONE VALUE YOU MAY TAKE FROM YOUR DISPATCH, AND IT IS A BOUNDED EXCEPTION.** Nothing in the journal says which task is the feature task, so unlike the feature set in Step 3.5 this one cannot be derived. Take that id and take nothing else with it: it selects which `variety_assessed` event to read, and it does not narrow the HANDOFF population Step 1 and Step 2 built. If your dispatch names no feature task, ask the team-lead for it rather than inferring one from a task set.
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
- **Consumer exclusivity guard**: the calibration figures read the `variety_assessed` stream ONLY (the Step 10 selection above). `task_metadata_snapshot` events also carry `variety` payloads, but the snapshot is a recovery/breadth source for sibling-key CONTENT (Step 3.1) and must NEVER become an additive second source for the calibration figures — counting both would double-count the feature score.

---

## Incremental Harvest Workflow

**Build the population as the Standard Harvest population rule above requires — a dispatch does not narrow it.**

Triggered after remediation completes — processes only the delta since the last harvest pass. Fires only when remediation occurred and produced new completed tasks.

1. **Check processed task tracking**: Locate your `## team={your team_id}` section of `session_processed_tasks.md` with the **Standard Harvest Step 8 rule's existence decision**: the `grep -c` header count, what each of its outcomes means for whether your section exists, and the `awk` extract on the `1` branch. **The write actions inside those branches — appending, creating a section — belong to step 6, not to this one.** Do NOT restate that rule here, and do NOT decide the section exists by searching what you read — a ledger past the read cap hides your section while the file is intact, and a pass that concludes "no processed IDs" reprocesses everything. From the extract, take already-processed task IDs as the **UNION of every `Processed task IDs` line** in that section, never the newest line alone
2. **Discover new completions**: Run the Standard Harvest Step 1 sources — session journal `agent_handoff` events, `TaskList`, and the task-file metadata census — for completed tasks not in the processed set, **PLUS any task the Standard Harvest Step 3 quiescent-task rule sent you back for**. That task IS in the processed set — the rule deliberately does not edit the ledger — so a filter that only subtracts the processed set drops the very task the re-sweep exists to collect, and step 3 then reports "no new completions" and completes. Including it widens the population, which is always permitted; only narrowing is forbidden. Do not narrow to the journal either: a new completion whose content sits only in a non-`handoff` key emits no `agent_handoff` event.
3. **If no new completions**: Report "No new HANDOFFs since last harvest" and complete
4. **Read new HANDOFFs** with the Standard Harvest Step 3 rule, in full: the identity derivation, the group selection, the union predicate and the report messages. Do NOT restate that rule here. A summary of it here is a second statement that goes stale on its own
5. **Extract and save** using Steps 4-7 from Standard Harvest (extract knowledge, organizational state, dedup protocol, save) (every save with --no-sync; an Incremental dispatch never carries the propagate sentence)
6. **Update processed task tracking** — **append** the new task IDs to **your team's** `## team={your team_id}` section of `session_processed_tasks.md` with the **Standard Harvest Step 8 rule, in full**: the `grep -c` header count and its branches decide WHERE to append, and the write is append-only. Do NOT restate that rule here, and do not append to a section you located by reading the file — that is how a second `## team=` section for one team gets created
7. **Do NOT delete the session journal** — it may still be accumulating entries from ongoing work
8. **Update existing memories** if remediation superseded prior decisions (use `update` CLI command, not `save`). Remember: default `update` is additive merge — pass `--replace` only when the prior list items need to be discarded, not amended.
9. **Report delta summary** to team-lead — only report what changed in this incremental pass

---

## Consolidation Harvest Workflow

**Build the population as the Standard Harvest population rule above requires — a dispatch does not narrow it.**

Triggered during `/PACT:wrap-up`, `/PACT:pause`, `/PACT:refresh`, or `/PACT:orchestrate` once a second or subsequent feature completes in one session. This is the deep-clean pass — it extends the standard workflow with memory consolidation and pruning.

### Step 1: Safety Net (Unprocessed HANDOFFs)

Run the Standard Harvest Step 1 discovery in full — every source, including the task-file metadata census — for tasks not yet in the processed task set. Checking `agent_handoff` events alone leaves any task whose content sits in a non-`handoff` key undiscovered, and this is the last pass that will look. If unprocessed entries exist, run the Standard Harvest workflow above first (earlier harvest triggers may have been missed). Then continue with consolidation. That nested run does not propagate: Step 4 does, after the Step 3 prune.

### Step 2: Review Session Memories

Review all memories saved during this session by listing recent pact-memory entries.

### Step 3: Consolidate and Prune

- Merge overlapping memories (same topic, same entities, compatible conclusions)
- Prune superseded memories (update or delete entries replaced by newer information)
- **Prune complete `## team=` sections** in `session_processed_tasks.md`, in the agent-memory directory the platform gave you. The file is shared by every secretary instance across every project, so a section may be removed on exactly one ground and no other: you have VERIFIED that its team is complete by reading that session's journal. Age never prunes: a section's `Last processed` timestamp, however old, is not a ground for removal, and a section with no `Last processed` line is unjudgeable, not stale — a criterion that cannot be evaluated never does. **Enumerate the sections with `grep -n '^## team=' "{ledger path}"` rather than by reading the file**: a section past a read's line cap is invisible to a search of what you received, and item 6 would then under-report what this pass considered. Verify each section in this order:
  1. **Address.** Take `{project}` and `{session_id}` from the section's header line, which carries them as `## team={team_id} ({project}, session {session_id})` and may carry other text after that on the same line: read the ids wherever they sit on that line. A session id is five lowercase-hex groups of 8-4-4-4-12 characters joined by hyphens, the shape the session-directory reaper accepts; any other shape counts as no session id. The session directory is `{config_dir}/pact-sessions/{project}/{session_id}` (`{config_dir}` is `$CLAUDE_CONFIG_DIR` when set and non-empty, otherwise `$HOME/.claude`). If the header carries no project or no session id and `{team_id}` has the shape `session-XXXXXXXX`, glob `{config_dir}/pact-sessions/*/XXXXXXXX-*`: exactly one match is the session directory, continue with it as if the header had named it; no match means the session cannot be located from this config root: it stays; two or more matches mean the section cannot be verified: it stays. A `{team_id}` of any other shape with no project or session id cannot be resolved: the section stays.
  2. **Existence.** If the session directory does not exist under `{config_dir}`, the section cannot be verified from this root: it stays. Absence is not evidence of reaping — PACT's own `session_end.py::cleanup_old_sessions` reaps only under the ending session's OWN config root, and no section records which root its session ran under, so absence here and alive-under-another-root produce the same bytes: none. If the directory exists but `{that dir}/session-journal.jsonl` does not exist, the team cannot be verified from that section: it stays.
  3. **Reads.** Run `python3 "{plugin_root}/hooks/shared/session_journal.py" read-last --session-dir '{that dir}' --type session_end`, then the same command with `--type session_paused`, then with `--type session_start` (`read-last` prints the newest event of that type as one JSON object, or `null`).
  4. **Ordering.** The team is verified complete only when the `session_start` read is non-null AND at least one of `session_end` / `session_paused` is non-null AND the later of their `ts` values is later than the `ts` of the `session_start` event. Equal `ts` values are not later: a tie means not complete, the section stays. A `session_start` later than the newest completion event means the session came back after closing, whatever its `source`: the team is live and the section stays. `null` from both completion reads means not complete; `null` from the `session_start` read, a non-zero exit, or non-JSON output on any read means the check could not verify. In every other verification outcome, the section stays. Removing a paused team's section is safe — a pruned-then-resurrected team re-derives its processed set by re-running Step 1 discovery over its own session — so speculation that the session might resume never keeps a section; only an observed `session_start` later than the completion event does.
  5. **Team-wide removal.** Completion is a property of the team, not of one section: once a team is verified complete from any one of its sections, every `## team={that team_id}` section is marked for removal, including siblings whose header carries no project or session id.
  6. **Report.** Before removing anything, report what you would remove: each section header with its byte size and its ground (verified by journal, or team-wide sibling), then the section count and byte total.
  7. **Replace with a tombstone.** Then REPLACE each of those sections with its tombstone — the section's header line unchanged, then one line `PRUNED {today} — {ground}, {byte size} recovered` — and change nothing else. **A team with more than one section gets exactly ONE tombstone: keep the first section's header, and delete the sibling sections AND their headers outright.** Leaving a second `## team=` header behind would hard-stop that team forever at Step 8's `2 or more` arm, and this prune is the only thing that clears a duplicate. The record IS the removal: there is no separate write step to skip, so a section cannot be removed without leaving what it was and why. (Pruning happens only in this deep-clean Consolidation pass — the Standard/Incremental hot paths leave the file untouched apart from your own section.)

### Step 4: Reconcile Working Memory

The Working Memory section is a view of the store, rebuilt by the pact-memory `sync` command and by nothing else you run: `save --no-sync`, `update` and `delete` change the store and leave the section as it was. Fix the record, then rebuild the view: if an entry the section shows is wrong or superseded, correct or delete its record in Step 3, never the section text. If the dispatch carries the propagate sentence (see Propagation above for what else can carry it), run `sync` now, after the Step 3 merges and prunes, so the section shows the store as consolidated: the newest three records of this project, each under its own date, deleted records gone. Record `sync_status` and `memory_ids` for Step 6. On `empty`, record the envelope's `project_id` beside `sync_status`; a `project_id` that is not the project you expected is a misconfiguration to report to the team-lead, not an empty project. If the dispatch does not carry the sentence, skip this step and report it in Step 6.

### Step 5: Save Orchestration Retrospective

Save orchestration retrospective as calibration data (see Standard Harvest Step 10 for CalibrationRecord schema). This captures the session-level view: overall workflow effectiveness, recurring patterns, and calibration for future variety scoring.

### Step 6: Report Summary

Report consolidation results to the team-lead, including:
- Memories consolidated (merged count)
- Memories pruned (deleted/superseded count)
- Working Memory: {sync_status | empty, project {project_id}}, {N} entries projected
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

### Layered Guidance

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

1. Look for `session-journal.jsonl` in `{config_dir}/pact-sessions/*/*/` directories. **Exclude the current session's directory** (the session dir provided in your dispatch prompt, also named in `{session_dir}/pact-session-context.json`) — that session's data is active, not orphaned.

   `{config_dir}` is this session's Claude config root — the value of `$CLAUDE_CONFIG_DIR` when set and non-empty, otherwise `$HOME/.claude`. Read it off an absolute path the platform already injected into your context — your plugin root is `{config_dir}/plugins/…` — rather than shelling out for the variable. Substitute it before running any command; never assume `~/.claude`.
2. If found: report to team-lead "Found N orphaned HANDOFFs from prior session {session_dir}"
3. Read the HANDOFFs with the **Standard Harvest Step 3 rule, in full and without exception**: the identity derivation, the grouping, the latest-`ts` selection with its journal-order tie-break, the union predicate and the report messages. **This path is NOT a fallback chain and MUST NOT become one.** Do not prefer one source and fall back to the other, and do not restate the Step 3 rule here. Read the prior session's events with `read_events_from(session_dir, 'agent_handoff')`, and resolve the task-file copy in `{config_dir}/tasks/{team_id}/` where that session's task files survive. Run the Step 1 metadata census on those files also. The journal carries only the tasks that emitted a HANDOFF, so a journal-only pass reports a count it cannot support.
   🔴 **THIS PATH NEEDS THE SELECTION RULE MORE THAN THE LIVE PATH DOES.** It runs across PRIOR sessions, where a task_id reused across arcs and several accumulated events for one task are most likely. Without the selection rule an implementer takes the first match. The first match is the FIRST emit, and the content-keyed marker makes the first emit the SUPERSEDED copy. The one path that exists to catch what each other layer missed then reports recovered knowledge that a later revision had replaced.
4. **Record the recovered task ids. Do NOT remove the files you read them from.** Add the ids to your team's `## team={your team_id}` section of `session_processed_tasks.md` with the **Standard Harvest Step 8 rule, in full**: the `grep -c` header count and its branches decide WHERE to append, and the write is append-only. Do NOT restate that rule here. That ledger is what stops a later pass reprocessing this session, and Step 2 reads it. No removal is needed to reach that outcome.

   **Two file classes carry this session's content, and either one can be the only carrier of a given HANDOFF.** The journal at `{session_dir}/session-journal.jsonl` carries the tasks that emitted an `agent_handoff` event. The task files under `{config_dir}/tasks/{team_id}/` carry a quiescent completed task whose key never reached the journal. Neither class is a copy of the other, so neither is safe to remove because the other was read.

   **Run the carrier test and report its result at Step 5.**

   1. Read `{config_dir}/tasks/{team_id}/`. Directory absent, or present with no task files → the journal is the ONLY carrier of what you recovered. Task files present → a second carrier exists.
   2. Read raised, or the path did not resolve → record ONE carrier. An unreadable directory is not an empty one, and the error does not tell you which you have.
   3. Re-fetch what you saved in Step 7 by its returned `memory_id` and compare it against what you recovered. Present and in agreement → a durable second copy exists. Save reported success but the re-fetch does not find it, or either step raised → NO durable second copy exists.

   **You have processed these files. It does not follow that you may remove them.** A file processed twice costs a duplicate paragraph, and the Step 2 ledger skip pays even that cost only once. A journal removed once costs every HANDOFF, lifecycle event and snapshot that only it held, and nothing regenerates them. The cleanup this recovery needs is the ledger entry above, and that entry takes no bytes with it. A later reader who wants this workspace tidy is looking at the wrong step.
5. Report summary of recovered knowledge (or gaps where all sources failed). State the carrier test result from step 4 with it: how many carriers held the recovered content, and whether a durable second copy was verified.
