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
- **Standard Harvest** (orchestrate, comPACT, peer-review, plan-mode) — discover, review, save, record the processed unit tokens. Triggered after phases complete.
- **Incremental Harvest** (peer-review) — a pass after remediation. Processes only the units whose tokens the ledger lacks.
- **Consolidation Harvest** (wrap-up, pause, refresh) — safety-net + deep-clean pass. Triggered at session end, at a session pause, or at a mid-session context refresh.

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

Resolve the absolute session directory **before any journal read**, and reuse that one value (`$SESSION_DIR`) for every journal read below (Step 1's one unfiltered read, Step 3.5 `artifact_paths`, and Step 10 `variety_assessed`). **Every journal read in this skill MUST pass this explicit `--session-dir`** — never a path-less read.

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

1. **Session journal** (primary, GC-proof): `$SESSION_DIR/session-journal.jsonl` (the `$SESSION_DIR` resolved in Step 0). Read it ONCE, with no `--type` filter, via the existing `session_journal.py read` subcommand (explicit `--session-dir`, masked-read-safe):

   ```bash
   JOURNAL=$(python3 "{plugin_root}/hooks/shared/session_journal.py" read \
               --session-dir "$SESSION_DIR")
   ```

   `read` prints a **JSON ARRAY** to stdout (`[ {...}, {...} ]`), NOT one JSON object per line. So parse the whole stdout once — `json.loads(JOURNAL)` → a list of event dicts — then iterate the list (do **not** iterate line-by-line). **KEEP THAT LIST IN JOURNAL ORDER: it is the `journal` input of the Step 3 block, and Step 3 reads its `agent_handoff`, `task_created` and `task_metadata_snapshot` events from it.** Assign arcs from this one list's order; never from `ts`, which ties within a second. An `agent_handoff` event is `{"type": "agent_handoff", "agent": "...", "task_id": "...", "task_subject": "...", "handoff": {...}, "team": "<task-store name>", "ts": "..."}` — full HANDOFF content inline, garbage-collection-proof. `team` names the task-store directory the emitting path read its task file from. It is ABSENT on an event written before that field existed; such an event is OLD-FORMAT, and the SELECTION rule below decides it. A `task_created` event records one creation of a task: `{"type": "task_created", "task_id": "...", "team": "...", "subject": "..."}`.

   **Skip an `agent_handoff` event whose `task_id` is not a non-empty string, and report it once: `handoff event skipped: task id <value> is not a non-empty string`.** **Route each remaining `agent_handoff` event at discovery with `partition_destination`, and collect `(section, task_id)` pairs, never bare ids.** `PARTITION_THIS` and `PARTITION_OLD_FORMAT` add `(this team, id)`; `PARTITION_FROM` adds `({team}, id)` for the event's own team; `PARTITION_FOREIGN` and any unknown label add nothing — report `handoff identity mismatch for task N: journal event names team <team>; this store is team <name>` once per id whose ONLY events are foreign. When `this_team` is empty, every event is OLD-FORMAT and adds `(this team, id)`.
2. **`TaskList`** (supplementary): Read `TaskList` for completed tasks owned by agents. Useful as a cross-reference and for catching tasks where the completion hook didn't fire. Note: the platform garbage-collects older task files during long sessions, so `TaskList` may be incomplete.
3. **Task-file metadata census** (required for coverage): list the team's task files under the task-list directory named in your context (`Task list: <abs>`) and read the **metadata key set** of every completed task. A task is in scope when it carries ANY agent-authored key — `handoff`, `handoff_amendment`, `handoff_addendum`, `teachback_submit`, `post_completion_verification`, `audit_summary`, `audit_summary_authored`, or any key not on this list. **Enumerate the keys each task carries; never test for one key.** A task whose content sits only in a non-`handoff` key emits no `agent_handoff` event, so sources 1 and 2 cannot see it — resolving `handoff` alone finds nothing on that task and reports success. An auditor carries its findings in `audit_summary`, mirrored to `audit_summary_authored`, and its task carries no `handoff` key at all, so a census that resolves `handoff` reports an auditor as having produced nothing.

4. **Earlier team's task store** (required when the journal names one): read `team_realigned` events the same way as source 1 (`--type team_realigned`). Build the from-team list with the `safe_from_teams` route: it keeps each `from` that is a valid team name and is not this team, once. Never join a `from` that route rejected into a path, and report each rejection it returns. For each kept team `{from}`, run the source 3 census on its directory — the `Task list:` path in your context with its last component replaced by `{from}` — and add `({from}, id)` for each id that store's census admits. Those task ids belong to team `{from}`, not to your team: the same id can name a different task in each store. **Admission is per store: an id admitted in one from store never admits another store's file of the same number.** Synthesize each of their work units under Steps 4-7, and name team `{from}` as the source in the entry's context so the record is not read as this team's. Step 3 records each of their units under a `{from}/` token, so a later pass skips them and never mistakes them for this team's task of the same number.

Scope the census by the keys a task **carries**, never by a key family you expect. A census scoped to one key family fails the same way as one scoped to a single key.

If none of these sources have completed agent tasks, report "No pending HANDOFFs to review" and complete — this is normal when HANDOFFs were already processed by an earlier trigger (idempotent).

### Step 2: Dedup Check (Processed Tasks)

**A dispatch does not narrow this list.** The census finds which tasks carry content; only this ledger says where the last pass stopped, so the units whose tokens this ledger lacks are the population — discard any scope the dispatch suggested. Read your processed set from your team's section of `session_processed_tasks.md`, in the agent-memory directory the platform gave you — use the path you are given, never one built from your agent type. The file is namespaced by team — locate **only** your own `## team={your team_id}` section with the **Step 8 rule's existence decision**: the `grep -c` header count, what each of its outcomes means for whether your section exists, and the `awk` extract on the `1` branch (Step 8 also carries the file-format contract). **The write actions inside those branches — appending, creating a section — belong to the step that writes, not to this one.** Do NOT restate that rule here, and do NOT decide the section exists by searching what you read — a ledger past the read cap hides your section while the file is intact, and a pass that concludes "no processed IDs" reprocesses everything. Your processed set is the set of UNIT TOKENS on every `Processed task IDs` line in that section. Read them with the `parse_ledger_tokens` route over the `awk` extract. **Your processed set is the UNION of every `Processed task IDs` line in that section.** A section carries many; taking the newest line alone under-reports the set and reprocesses work already done. A token is `{id}#{key}` for this team's unit or `{from}/{id}#{key}` for a realigned team's unit, where `key` is the content key of the unit's selected copy. The parser drops an item with no `#`; that task is read again, which is safe. **Do not subtract anything from the discovered pairs here.** Step 3 checks each unit's token after selection and skips a unit whose token is already present. A dispatch that says "harvest the delta" means its own named range; that is a different delta, it is not this one, and it does not narrow this one. This enables incremental passes (e.g., after remediation).

### Step 3: Read All HANDOFFs

For each discovered task, read the HANDOFF from the two copies and combine them. This is **not** a fallback chain. Each copy can hold content the other does not.

1. **Session journal** (GC-proof): If the task was discovered through `agent_handoff` journal events, the event's `handoff` field contains the full HANDOFF content inline. **The journal carries the HANDOFF as it stood at the LAST EMITTING WRITE.** Do not read it as the accepted HANDOFF. A revision that lands before completion reaches the journal. A revision that lands after completion can reach no journal event, so the journal copy can be the superseded one.
2. **Task file** (freshest, and the task-store drain removes it): the `read_task_json` route in HOW TO CALL, `read_task_json(task_id, team_name, base_fallback=False)`, the raw task JSON, which carries `owner`, `subject` and `metadata`. The HANDOFF is `metadata.handoff` (`TaskGet` is metadata-blind). ⚠️ **THAT READER RETURNS AN EMPTY DICT ON A MISSING FILE, NOT `None`.** It is fail-open by design, so a drained task file, a malformed JSON file and an IO error all give `{}`. Test the drained case as EMPTINESS (`if not task:`), and do NOT test `task is None`, which is False for every one of those states. Read a named store's task file with this route only; it never reads a file outside that store.
3. **Take the UNION of the two copies, and PREFER THE TASK FILE on a conflict, apart from the two states below. Both states are decided by the two copies' REQUIRED key sets, both are reported, and neither is decided by an empty or missing value alone.** Keep each field that is in one copy alone, except a REQUIRED field the task file lacks in the incomparable state, which is dropped and reported. When the two hold different content for one field, keep the task-file content.

**SCOPE THE COMPARISON TO THE REQUIRED FIELD SET.** `reasoning_chain` is recommended, so a legal revision may omit it, and a journal copy may carry keys the task file never had; a comparison over every key calls a complete revision partial and restores the cleared field. **NORMALIZE LEGACY SPELLINGS IN BOTH COPIES BEFORE THE COMPARISON** — derive each raw key's canonical identity with the shared `HANDOFF_LEGACY_ALIASES` map (`key_decisions`→`decisions`, `areas_of_uncertainty`→`uncertainty`, `integration_points`→`integration`, and every other key is its own identity) — because a field renamed to a legacy spelling would otherwise leave the required set entirely. **`resolve_handoff_field` IS NOT A KEY NORMALISER:** it resolves a canonical NAME to a VALUE and returns `None` for both an absent key and a present `None`, so using it for this step collapses present-and-empty into absent — the distinction States A and B rest on. **VALUE SELECTION FOR A DUAL-SPELLED COPY IS A SEPARATE STEP:** when one copy carries both spellings, the canonical spelling wins when it holds content, and a FALSY canonical falls through to the FIRST TRUTHY alias spelling — the same rule `resolve_handoff_field` applies on read. When the canonical spelling is falsy AND no alias spelling is truthy, the CANONICAL spelling wins. The selected raw key is the one the output carries WHEN THE TASK FILE WINS that field; when the JOURNAL value wins a dual-spelled field (a restore or a journal-only keep), the merged output starts from the journal mapping and BOTH spellings survive. Normalize both sides, never one: the journal is append-only and carries legacy spellings, and the task file is mostly canonical but not guaranteed. **EQUALITY COUNTS AS COVERING:** a task-file required set equal to the journal's is a complete revision.

Let `J` be the journal copy's required key set and `D` the task file's.

**`EMPTY` MEANS MEMBERSHIP IN `(None, '', [], {})`, NOT FALSINESS, ON BOTH SIDES.** A task-file `0` or `False` is content and wins its field; a journal `0` or `False` beside an empty task-file field is content and the State A rules decide it.

**WHEN THE TASK FILE WINS A FIELD, NO JOURNAL SPELLING OF IT SURVIVES.** A journal copy may carry BOTH the canonical and a legacy spelling of one field; the output keeps the winner's spelling and removes EVERY raw journal key that maps to that identity, so a stale alias cannot defeat the decision. The same applies to a REQUIRED field dropped in the incomparable state: every spelling of it goes.

**A FIELD PRESENT AND EMPTY ON THE TASK FILE, with content in the journal:**
- `D` covers `J` (equality included): keep the EMPTY value; report `handoff field kept empty for task N: field <name> present and empty; the task-file required key set covers the journal copy`.
- `D` is a proper subset of `J`: keep the JOURNAL value; report `handoff field restored from the journal for task N: field <name> present and empty; the task-file required key set is a proper subset of the journal copy`.
- Neither set contains the other: keep the EMPTY value; report `handoff field undecidable for task N: field <name> present and empty; the required key sets are incomparable`.

**A FIELD PRESENT IN THE JOURNAL AND ABSENT FROM THE TASK FILE:**
- A REQUIRED field, `D` a proper subset of `J`: keep the JOURNAL value; report `handoff field restored from the journal for task N: required field <name> absent from the task file; the task-file required key set is a proper subset of the journal copy`.
- A REQUIRED field, incomparable sets: DROP the field; report `handoff field undecidable for task N: required field <name> absent from the task file; the required key sets are incomparable`.
- NOT a required field (recommended or non-canonical): keep it; report `handoff field kept from the journal for task N: field <name> absent from the task file and not a required field; the required key sets do not decide it`.

**THE RESIDUAL:** a deliberate complete removal of a REQUIRED key and an accidental partial re-write both leave a proper subset, so the proper-subset branch restores in both and reports that it did. A `TaskUpdate` metadata merge REPLACES a nested sub-object, which is why a partial re-write leaves a present-and-empty or missing field; the required-key-set test separates a partial copy from a complete one.

**A CONDITION UNDER WHICH THIS RULE STILL RESTORES A CLEARED FIELD, STATED AS A FAILURE AND NOT AS AN UNDECIDABLE BRANCH.** Where `D` IS A PROPER SUBSET of `J`, the proper-subset branch restores a REQUIRED field the task file lacks; a partial re-write that carries no field the journal lacks therefore revives the cleared field and the revival persists. Do not code an exception, and do not file that proper-subset restore under "undecidable". **A MISSING REQUIRED FIELD DOES NOT IMPLY A PROPER SUBSET.** A task file that lacks one required field while CARRYING a required field the journal copy lacks leaves the sets INCOMPARABLE: the absent field is DROPPED under the State B rule above and reported undecidable, the restore branch does not run, and nothing revives. Example: journal `{produced, decisions, open_questions}` against disk `{produced, uncertainty, open_questions}` — the disk copy lacks `decisions`, yet the sets are incomparable, so `decisions` is dropped.

🔴 **DO NOT restate this as a blanket prefer-the-non-empty-side rule.** The rule is per-field and per-state, and a blanket rule makes the journal the default winner while the task file is the freshest copy, which inverts the precedence this step is built on.
4. **When the task file is ABSENT, report `handoff divergence check unavailable for task N: task file absent`** and use the journal copy alone. Do not skip in silence. A drained task file is a reportable gap and not a clean single-source read.
5. **When the task carries NO `agent_handoff` event in a SECTION and that SECTION's task file EXISTS**, process that task-file HANDOFF alone: there is no second copy to union and no divergence to report. This is a normal population — the census exists because a completion hook may not fire — and it is not a gap. **THIS IS ONE RULE WITH TWO APPLICATIONS, NOT TWO RULES:** apply it per store to THIS team's store and to each `team={from}` store, labelling a from-team application `## team={from}` in the output. A from-team task with a task file and no journal group is processed exactly as this team's is — whatever the file carries, a `handoff` or a sibling key Step 3.1 resolves — and it produces NEITHER the absent-file report NOR the no-match report. **THE FROM-TEAM DISK SHAPE IS ADMITTED BY PROVENANCE, NEVER BY A NUMBER MATCH:** a from store's file is in scope only when the id came from the from-store CENSUS; a same-numbered file in a from store is NOT evidence about THIS team's task, so it must never be processed as one and must never make this team's gap report silent. **ADMISSION IS PER STORE:** a from store's file is read as a disk-only unit only for a `({from}, id)` pair that store's census produced. Read that whole task file, never a per-file content test, which would drop a sibling-key-only task the census admitted.
6. **Report gap**: If no source resolves, report the gap to the lead. Record the task_id, the timestamp, and the agent name WHEN A SOURCE STILL IN SCOPE CARRIES ONE — a task can reach this rule with no journal event and no task file, and a task file drained between discovery and Step 3 had an owner that is no longer recoverable; record the agent as `unknown` rather than omitting the field.

**THE THREE CONDITIONS ARE EXCLUSIVE — PICK BY THE EVIDENCE, NEVER BY THE ABSENCE OF A VALUE.** The ABSENT-FILE report requires a journal group for the task in that section and NO task file there. The NO-MATCH report requires a task file and NO group carrying its identity. The GAP line requires NO source at all: no journal group in this section, no task file, and no from-team JOURNAL PARTITION processed. **The disk-only from-team application does NOT count as a source for this line** — it was admitted by census provenance, so it is not evidence that this team's copy was handled. Never emit one as a fallback for another.

**A TASK WHOSE WORK UNITS WERE ALL FROM-TEAM PARTITIONS HAS NO TASK FILE IN THIS SECTION'S STORE, AND THE ABSENCE IS NOT A GAP.** The from-team section processed it against THAT team's store; do not emit the report-gap line for it. That line fires only when NO source resolves — no journal group in this section, no task file here, and no from-team JOURNAL PARTITION processed (the disk-only from-team application does not count).

**SELECTION, because `agent_handoff` is a multi-event family.** Do not take the first match. The first match is the FIRST emit, which is the superseded copy. Use the rule Step 3.1 uses for snapshots:

1. Take the `agent_handoff` events with the matching `task_id`, then PARTITION the match by the event's `team` field. **DECIDE EACH ROUTE WITH THE SHARED `partition_destination`, NEVER WITH AN INLINE TEAM COMPARISON:** it returns the exported `PARTITION_*` labels, the same one-rule-one-place shape as `old_format_destination` below. **THE FUNCTION IS AUTHORITATIVE; THIS PROSE NAMES THE OUTCOMES FOR THE READER** — a change to the mapping belongs in `partition_destination`, never here. **THIS IS A PARTITION: A PARTITION IS NEVER DISCARDED FOR NAMING ANOTHER TEAM.** The partition naming THIS team's name — the LAST PATH COMPONENT of the `Task list:` directory in your context — is this team's work unit. Each partition naming a team the journal realigned FROM (the `from` teams Step 1 source 4 harvests) is THAT team's work unit: synthesize it under that team's `team={from}` label and against THAT team's task store, and never union it with this team's task file. Record its unit token, `{from}/{id}#{key}`, in this team's section at Step 8; that token is what lets a later pass skip it. **UNION IT WITH THAT TEAM'S TASK FILE**: read that store's `{task_id}.json` and apply the same union, because each copy can hold content the other lacks. A partition naming neither is FOREIGN: do not process it, do not union it, and report `handoff identity mismatch for task N: journal event names team <team>; this store is team <name>`. Events carrying NO `team` form the OLD-FORMAT partition and the rule below decides them; do not discard them. When the `Task list:` directory is ABSENT, or its last path component cannot be derived (the value is empty), the team term cannot be evaluated: treat every partition as OLD-FORMAT and report `handoff team identity underivable for task N: no Task list: directory in context`. **THE REPORT FIRES ONCE PER TASK AND ONLY WHEN A JOURNAL MATCH EXISTS** — a census-only id has no partition to be underivable about.
2. Group the match with the `group_events` route: the key is the `(agent, task_subject)` identity plus the event's ARC, the creation stamp of the `task_created` event that opened that instance of the task. Two instances of one task id that share agent and subject are two groups when the journal records their creations. When it does not, they form one group and only the latest is processed. The arc also carries the store epoch: a `task_created` whose number is at or below a number the journal already holds for that team marks a recreated store, so an instance created after it is a separate group even when its own creation wrote no event. A group whose events carry two or more distinct contents with no creation boundary between them: process the latest and report `handoff group for task N holds K distinct contents with no creation boundary; the latest was processed; the earlier contents remain in the journal`. When the group count is more than one, process each group as a distinct work unit. Do not pick one group. **DO NOT FOLD THE TEAM INTO `occupant_hash`.** The marker filename and the `task_metadata_snapshot` occupant are `occupant_hash(agent, subject)` alone, so a team inside the hash re-keys every existing marker and breaks every existing snapshot join. The team term routes partitions and is never a term of the group key.
3. In each group take the **latest `ts`**, and take the **last in journal order on an equal `ts`**, with the `latest_by_ts_then_journal_order` route, which compares parsed instants (journal events stamp `ts` at second granularity, so a same-second re-emit ties. The later line in journal order is the authoritative one, the same tie-break Step 3.1 uses).
4. The task file belongs to at most one group, and the `owning_group` route picks it. Compute the record's identity with `disk_identity(owner, subject)` — the SUBSTITUTED form the emit paths hashed, never a recomposed `occupant_hash` — and the store's arc with `current_arc`. The owner is the group with that identity and that arc; when no such group exists, it is the group with that identity and no arc, whose events carry no `team`. Union the task-file copy into the owner alone, and pass the task file's metadata to the owner alone. **A group that does not carry that identity is a distinct work unit and gets NO per-group report** — the other group owns the file, so this one has no copy of its own to disagree with, and the divergence report is decided ONCE for the whole group set (see the report rule below).

**AN OLD-FORMAT EVENT CARRIES NO `team` FIELD, AND ITS IDENTITY ALONE DOES NOT PROVE IT IS THIS TEAM'S.** Decide it by content against the stores whose names you hold: this team's store, and each team a `team_realigned` event names in its `from` field. Read `{that store}/{task_id}.json` directly and compare its `metadata.handoff` content key with the event's, using the shared `handoff_content_key` function — never a local equality test. Mark the event MIXED when the content keys are EQUAL and the event's `(agent, task_subject)` identity is NOT that file's `(owner, subject)` identity; discard a mixed event and report `handoff identity mismatch for task N: journal content equals team <X> task N, whose identity it does not carry`. An event whose content AND identity both match one named store is that store's work unit: process it under that team. **CHECK EVERY NAMED CANDIDATE BEFORE REPORTING A MIXED EVENT** — an identity match in ANY candidate wins over an earlier candidate's content-only match, so a mixed result is returned only after all candidates are checked. When no other named store matches, keep the identity match and report `handoff cross-team check unavailable for task N: no other named store carries the identity` — a store you cannot name is out of reach, and the rule reports that bound instead of guessing. **DO NOT ENUMERATE THE TASKS ROOT.** THE CHECK IS REQUIRED FOR OLD-FORMAT EVENTS: every event already in a journal predates the `team` field, and nothing else protects them. **A CONTENT-MATCHING CANDIDATE WHOSE OWN RECORD CARRIES NO `owner` IS SKIPPED, NOT TREATED AS A MISMATCH.** The emit path substitutes the platform teammate name for an empty owner, and the record cannot reproduce that name, so comparing identities here would MANUFACTURE a mismatch and report this team's own event as MIXED, discarding content that was never foreign. **SKIPPING REMOVES THIS CANDIDATE FROM CONSIDERATION; IT DOES NOT SETTLE THE EVENT** — another named candidate may still return OWNED_BY that team or MIXED, and the event is routed or discarded accordingly; it is UNMATCHED — kept as this team's, with the cross-team-check-unavailable report above — only when no other candidate decides it.

**DERIVE THE DISK-SIDE IDENTITY THE WAY THE EMIT PATHS DERIVE THEIRS.** The two emit paths SUBSTITUTE before they hash, and the task record carries the value the substitution replaced. Compute the disk identity from the SUBSTITUTED values. Without the substitution the identity of a substituted task cannot equal one group identity, so the union does not run on the very tasks it protects:

1. **Subject term.** When the record subject is missing, empty, or whitespace-only, substitute the literal `(no subject)` before hashing. The two emit paths both apply this.
2. **Agent term.** When the record owner is missing, empty, or whitespace-only, the `TaskCompleted` emit path hashes the platform teammate name for that task in place of the owner. The task record does not carry that name, so this one is NOT reproducible from the record. Report such a task through the report-message rule below and do not read it as a drain.

**HOW TO CALL THE NAMES THIS STEP USES.** This step and the pseudocode below name these shared functions. None is callable from a name alone, and the instruction to use the shared function rather than a local one is not followable without a route. **DO NOT reimplement any of them. Use these.** Every route imports from `shared.handoff_merge`, except `read_task_json` (`shared.task_utils`) and `HANDOFF_LEGACY_ALIASES` (`shared.handoff_schema`). The block also names `positioned` and `store_epochs`, which the `group_events` and `current_arc` routes apply for you, and `task_metadata` and `disk_handoff`: a task's `metadata` when it is a mapping, else `{}`, and that mapping's `handoff` when it is a non-empty mapping, else `{}`.

🔴 **EVERY VALUE CROSSES AS DATA, NEVER AS COMMAND TEXT.** Pass a value as a separate ARGV entry read through `sys.argv`, or carry it in a JSON payload on STDIN to a command whose text is a FIXED LITERAL — and never interpolate a value into the `-c` string or into an unquoted heredoc body. The author of a task writes its subject, so a subject folded into command text is Python that the subject author chose, executed by you. Nothing about the values makes this safe: the DATA BOUNDARY is what makes it safe, and rewriting the command is enough to remove it.

1. **`report_safe`**, the SHARED report sanitiser. Every `report(...)` value that comes from the journal, the task file or the context is interpolated through it:

   ```bash
   python3 -c "import sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import report_safe; print(report_safe(sys.argv[1]))" "$VALUE"
   ```

   It strips control, format, separator and surrogate characters (so a crafted team name, field name or task id cannot forge a report line) and caps the length. The helper-produced strings — `outcome.report`, the union's `reports` and the `safe_from_teams` reports — are already sanitised; this applies to the values THIS step interpolates.

2. **`disk_identity`**, the SHARED identity of an `(agent or owner, subject)` pair, with the emit paths' `(no subject)` substitution applied. It is the one identity derivation for events, task records and snapshot occupants:

   ```bash
   python3 -c "import sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import disk_identity; print(disk_identity(sys.argv[1], sys.argv[2]))" "$OWNER" "$SUBJECT"
   ```

3. **`handoff_content_key`**, the SHARED handoff content digest the OLD-FORMAT classifier compares. Never compare two handoffs with a local equality test:

   ```bash
   python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import handoff_content_key; print(handoff_content_key(json.load(sys.stdin)))" < "$HANDOFF_JSON"
   ```

   `$HANDOFF_JSON` names a file holding one handoff mapping.

4. **`union_preferring_task_file`**, the SHIPPED union predicate. It takes the two HANDOFF mappings and the task id, and returns the merged mapping plus its per-field report lines:

   ```bash
   python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import union_preferring_task_file; p = json.load(sys.stdin); merged, reports = union_preferring_task_file(p['journal'], p['disk'], p['task_id']); print(json.dumps({'handoff': merged, 'reports': reports}))" < "$PAYLOAD_JSON"
   ```

   `$PAYLOAD_JSON` names a file holding `{"journal": {...}, "disk": {...}, "task_id": "..."}`.

5. **`latest_by_ts_then_journal_order`**, the group's authoritative event: the latest parsed instant, and the later journal line on an equal instant. It takes the events of ONE group, in journal order:

   ```bash
   python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import latest_by_ts_then_journal_order; print(json.dumps(latest_by_ts_then_journal_order(json.load(sys.stdin))))" < "$EVENTS_JSON"
   ```

   `$EVENTS_JSON` names a file holding the group's events as a JSON list. Never compare `ts` values as strings.

6. **`group_events`**, the group key: `(disk_identity(agent, task_subject), arc)`. It takes the whole journal list from Step 1 and the journal positions of the events to group:

   ```bash
   python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import group_events, positioned, store_epochs; p = json.load(sys.stdin); j = positioned(p['journal']); g = group_events([j[i] for i in p['positions']], j, store_epochs(j)); print(json.dumps([[list(k), [e['_pos'] for e in v]] for k, v in g.items()]))" < "$PAYLOAD_JSON"
   ```

   `$PAYLOAD_JSON` holds `{"journal": [...], "positions": [...]}`. It prints one `[key, positions]` pair per group, each group's positions in journal order.

7. **`current_arc`**, the arc of the task a store holds now, for the task-file join:

   ```bash
   python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import current_arc, positioned, store_epochs; p = json.load(sys.stdin); j = positioned(p['journal']); print(json.dumps(current_arc(p['team'], p['task_id'], j, store_epochs(j))))" < "$PAYLOAD_JSON"
   ```

   `$PAYLOAD_JSON` holds `{"journal": [...], "team": "...", "task_id": "..."}`.

8. **`arc_of`**, the arc of ONE journal event, for the Step 3.1 snapshot selection. It takes the whole journal list from Step 1 and the event's position in it:

   ```bash
   python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import arc_of, positioned, store_epochs; p = json.load(sys.stdin); j = positioned(p['journal']); print(json.dumps(arc_of(j[p['position']], j, store_epochs(j))))" < "$PAYLOAD_JSON"
   ```

   `$PAYLOAD_JSON` holds `{"journal": [...], "position": <index>}`. It prints the arc, or `null` for an event with no `team`.

9. **`select_snapshot`**, the ONE snapshot a work unit may take its siblings from (Step 3.1): matching task id and occupant, no other team, and an arc equal to the unit's group arc; the latest instant wins, the later line on a tie:

   ```bash
   python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import select_snapshot; p = json.load(sys.stdin); s, reports = select_snapshot(p['journal'], p['task_id'], p['occupant'], p['team'], p['arc']); print(json.dumps({'snapshot': s, 'reports': reports}))" < "$PAYLOAD_JSON"
   ```

   `$PAYLOAD_JSON` holds `{"journal": [...], "task_id": "...", "occupant": "<the unit's group identity>", "team": "<this section's team>", "arc": <the unit's group arc, or null>}`. It prints the chosen snapshot, or `null`, and the report lines.

10. **`owning_group`**, the ONE group that owns a store's task file: the group with the record's identity and the store's current arc, else the group with that identity and no arc, else none:

   ```bash
   python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import owning_group; p = json.load(sys.stdin); g = {(k[0], tuple(k[1]) if isinstance(k[1], list) else k[1]): None for k in p['groups']}; a = p['file_arc']; o = owning_group(g, p['disk_id'], tuple(a) if isinstance(a, list) else a); print(json.dumps(list(o) if o else None))" < "$PAYLOAD_JSON"
   ```

   `$PAYLOAD_JSON` holds `{"groups": [<keys the group_events route printed>], "disk_id": "...", "file_arc": <the current_arc route's output>}`. It prints the owning key, or `null`.

11. **`undivided_contents`**, the count of distinct contents in ONE group, for the no-boundary report:

   ```bash
   python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import undivided_contents; print(undivided_contents(json.load(sys.stdin)))" < "$EVENTS_JSON"
   ```

12. **`partition_destination(event_team, this_team, from_teams)`**, the NEW-FORMAT route. Returns one of the exported `PARTITION_THIS`, `PARTITION_FROM`, `PARTITION_FOREIGN` and `PARTITION_OLD_FORMAT` labels; compare against those constants, never bare literals. An absent or empty `event_team` returns `PARTITION_OLD_FORMAT`, and callers keep the underivable-`this_team` guard ABOVE it (when `this_team` is empty, every event is OLD-FORMAT before this runs). Pure; never raises.

   ```bash
   python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import partition_destination; p = json.load(sys.stdin); print(partition_destination(p['event_team'], p['this_team'], p['from_teams']))" < "$PAYLOAD_JSON"
   ```

   `$PAYLOAD_JSON` holds `{"event_team": ..., "this_team": "...", "from_teams": ["..."]}`.

13. **`old_format_destination(outcome, this_team)`**, the OLD-FORMAT route. Takes the `OldFormatOutcome` from `mixed_old_format_event` and returns one of `OLD_FORMAT_DEST_MIXED`, `OLD_FORMAT_DEST_FROM` and `OLD_FORMAT_DEST_THIS`: MIXED means discard the event and emit `outcome.report`; FROM means process it under `outcome.team`; THIS covers OWNED_BY this team (no report) and UNMATCHED (emit `outcome.report`). Pure; never raises.

    ```bash
    python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import OldFormatOutcome, old_format_destination; p = json.load(sys.stdin); o = OldFormatOutcome(**p['outcome']); print(old_format_destination(o, p['this_team']))" < "$PAYLOAD_JSON"
    ```

    `$PAYLOAD_JSON` holds `{"outcome": {"state": "...", "team": "...", "report": "..."}, "this_team": "..."}`.

14. **`mixed_old_format_event(task_id, event_agent, event_subject, event_handoff, candidate_tasks)`**, the OLD-FORMAT classifier. Returns an `OldFormatOutcome(state, team, report)`: `state` is one of `OLD_FORMAT_MIXED`, `OLD_FORMAT_OWNED` and `OLD_FORMAT_UNMATCHED`; `report` is the line to emit for MIXED and for UNMATCHED, and `None` for OWNED. `candidate_tasks` is the list of `{"team": "...", "task": <parsed task JSON>}` for the named stores. Pure; never raises.

    ```bash
    python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import mixed_old_format_event; p = json.load(sys.stdin); o = mixed_old_format_event(p['task_id'], p['agent'], p['subject'], p['handoff'], p['candidates']); print(json.dumps({'state': o.state, 'team': o.team, 'report': o.report}))" < "$PAYLOAD_JSON"
    ```

    `$PAYLOAD_JSON` holds `{"task_id": "...", "agent": "...", "subject": "...", "handoff": {...}, "candidates": [...]}`.

15. **`safe_from_teams`**, the from-team list: each `team_realigned` event's `from` that is a valid team name and is not this team, once, plus one report line per rejected value:

    ```bash
    python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import safe_from_teams; p = json.load(sys.stdin); teams, reports = safe_from_teams(p['realign_events'], p['this_team']); print(json.dumps({'teams': teams, 'reports': reports}))" < "$PAYLOAD_JSON"
    ```

    `$PAYLOAD_JSON` holds `{"realign_events": [...], "this_team": "..."}`.

16. **`unit_token`**, a work unit's ledger token: `{id}#{key}`, or `{from}/{id}#{key}` when the third argument names a realigned team. The selection key crosses on STDIN: the selected journal handoff for a journal group, or the task's whole `metadata` for a disk-only unit. An empty line means a part was not a valid name; process the unit, do not record it, and report it as the pseudocode does:

    ```bash
    python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import unit_token; print(unit_token(sys.argv[1], json.load(sys.stdin), sys.argv[2] or None) or '')" "$TASK_ID" "$FROM_TEAM" < "$SELECTION_KEY_JSON"
    ```

    Pass an empty `$FROM_TEAM` for this team's units.

17. **`parse_ledger_tokens`**, the processed token set of one ledger section:

    ```bash
    python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_merge import parse_ledger_tokens; print(json.dumps(sorted(parse_ledger_tokens(sys.stdin.read()))))" < "$SECTION_TEXT"
    ```

    `$SECTION_TEXT` names a file holding the `awk` extract of your section.

18. **`read_task_json`**, a named store's task file. Read a named store's task file with this route only; it never reads a file outside that store:

    ```bash
    python3 -c "import json, sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.task_utils import read_task_json; print(json.dumps(read_task_json(sys.argv[1], sys.argv[2], base_fallback=False)))" "$TASK_ID" "$TEAM"
    ```

19. **`HANDOFF_LEGACY_ALIASES`**, the SHARED legacy-spelling map. It is a DICT, not a call: derive a raw key's canonical identity with `HANDOFF_LEGACY_ALIASES.get(raw_key, raw_key)`. It carries exactly three entries — `key_decisions`→`decisions`, `areas_of_uncertainty`→`uncertainty`, `integration_points`→`integration`.

    ```bash
    python3 -c "import sys; sys.path.insert(0, '{plugin_root}/hooks'); from shared.handoff_schema import HANDOFF_LEGACY_ALIASES; print(HANDOFF_LEGACY_ALIASES)"
    ```

🔴 **THE SUBSTITUTION IS CALLED, NEVER RESTATED.** `disk_identity` applies it; do not write a local definition or an inline hash composition.

**A NON-MATCHING GROUP IS NOT ALWAYS A DRAIN, AND THE TWO REPORTS MUST NOT SHARE ONE MESSAGE.** The check compares ONE task file against the WHOLE group set, so decide its report AFTER the group loop and emit it ONCE per task — never inside the loop, where it fires once per work unit and, for a multi-arc task_id, `no event group carries its identity` is false whenever another group carries it. Report an ABSENT task file as `handoff divergence check unavailable for task N: task file absent`. When the task file is PRESENT and NO group carries its identity, report `handoff identity mismatch for task N: task file present, no event group carries its identity`. A from-team section applies the SAME rule to THAT team's store — both the absent-file and the no-matching-group report — with the SAME wording. A group that does not carry the task file's identity while ANOTHER group does is a distinct work unit (a reused task_id across arcs): process it from the journal copy alone and report NOTHING — the other group owns that file, so this one has no file of its own to disagree with. One message for a drain and a mismatch makes a present file read as a drained one, which is the strongest signal this workflow emits.

**A SUBSTITUTION IS NOT THE ONLY CAUSE OF A MISMATCH, WHICH IS WHY THE SECOND MESSAGE IS NEEDED AND NOT MERELY TIDY.** The identity terms are the agent and the subject AS THEY STOOD AT EMIT TIME. An edit to either term after an emit leaves the record holding a value that no group carries. The subject term also reaches the `TaskCompleted` emit path from the platform payload rather than from the task record. So a mismatch on a present task file is an ordinary state, it is not evidence of a drain, and it is not always closable by the substitution rule above. Report it as itself, and process each group from the journal copy alone.

**EVERY REPORT LINE THIS STEP EMITS IS SYNTHESIS INPUT, NOT A TRANSCRIPT DIAGNOSTIC, AND THE MEMORY ENTRY IS WHERE IT LANDS.** Each report records a decision the union or the identity check made — a field kept, restored, dropped or undecidable, and a group or section processed from one copy alone. **THE HARVESTED MEMORY ENTRY'S `context` MUST CARRY EVERY ONE OF THEM** — both partitions' reports and the union's per-field lines alike — because the journal preserves the raw content and cannot preserve the DECISION that combined it, so the entry is the only record of why the copies were merged as they were. **A SYNTHESIS THAT DROPS A REPORT LINE IS A DEFECT, NOT AN OMISSION OF OPTIONAL COLOUR:** otherwise it reproduces the gap the report exists to close, one layer up.

**THIS WORKFLOW DOES NOT RE-SWEEP A TASK IT HAS PROCESSED BEFORE.** Step 3 skips each unit whose token is in the processed set, the Incremental Harvest Workflow processes only the units whose tokens that same set lacks, and the Consolidation Harvest Workflow safety net processes only the units whose tokens it lacks. **A COMPLETED TASK IS NOT A CLOSED TASK.** A revision after completion that re-emits `agent_handoff` carries new content, so its unit has a NEW token and the next pass collects it. A write to the task file alone — a sibling key, or a handoff edit that fires no new emit — leaves the recorded token unchanged, and no trigger in this file collects it.

**THE RE-SWEEP IS NOT AN OPTIONAL REFINEMENT, AND HERE IS THE FAILURE IT PREVENTS.** A sibling key written after completion has no trigger of its own. A journal snapshot carries such a key only when something else fires a snapshot on that task afterwards, and it carries it as a side effect rather than by design. **So on a task that keeps receiving other writes, the journal picks the key up, and on a task that goes quiet, the key is on the task file alone and the task-store drain removes it.** The quiet task is the one that looks finished, so the loss is silent and this pass reports success while it happens. **On a quiescent completed task, this harvest is the carrier and the journal is not.** To collect that content, run an incremental pass **before the session ends** and name that task_id as FORCED: process its units even when their tokens are present, and record the tokens again. **Do not edit the ledger to achieve this**: writes there are append-only, so a token cannot be taken back out, and it does not need to be — the ledger is a dedup record, not a work queue, so nothing reads it to decide what work remains. The cost is that a re-opened task leaves no durable trace, and that trace has no reader.

Pseudocode for the read. It carries all the selection steps, because a task_id reused across arcs resolves to the wrong arc when any one of them is dropped:

```python
# INPUTS, built at Steps 1-2:
#   task_list_dir    the `Task list:` directory in your context ("" when absent)
#   realign_events   `team_realigned` events (Step 1 source 4)
#   journal          the ONE whole-journal read, in order (Step 1 source 1)
#   tasklist_ids     completed task ids from `TaskList` (Step 1 source 2)
#   census           {store name: set of task ids} for THIS store and each
#                    from store (Step 1 sources 3 and 4)
#   processed_tokens the `parse_ledger_tokens` result (Step 2)
#   forced_ids       task ids the quiescent-task rule sent back (else empty)
# ACTIONS: report(line); process(handoff, metadata=None, section=None) runs
#   Steps 4-7 for one work unit; read_task_json(task_id, team) is the route
#   below, which never falls back to the bare tasks directory.
# OUTPUT: recorded_tokens, appended at Step 8.

this_team = Path(task_list_dir).name if task_list_dir else ""
journal = positioned(journal)
journal_events = []
for e in journal:
    if e.get("type") != "agent_handoff":
        continue
    # Ids are sorted below: a non-str id beside a str one would raise.
    if not isinstance(e.get("task_id"), str) or not e.get("task_id"):
        report(f"handoff event skipped: task id "
               f"{report_safe(e.get('task_id'))!r} is not a non-empty string")
        continue
    journal_events.append(e)
epochs = store_epochs(journal)
from_teams, from_reports = safe_from_teams(realign_events, this_team)
for line in from_reports:
    report(line)
recorded_tokens = []


def route(e):
    # An underivable this_team makes EVERY event OLD-FORMAT; without this
    # guard each team-carrying event fails both team tests and is reported
    # FOREIGN, the opposite of the rule.
    if not this_team:
        return PARTITION_OLD_FORMAT
    return partition_destination(e.get("team"), this_team, from_teams)


def handle(section, task_id, handoff, metadata, selection_key):
    """One work unit: skip it when its token is already recorded."""
    token = unit_token(task_id, selection_key, section)
    if token is not None and token in processed_tokens and task_id not in forced_ids:
        return
    process(handoff, metadata=metadata,
            section=f"team={section}" if section else None)
    if token is None:
        report(f"handoff ledger token unavailable for task "
               f"{report_safe(task_id)}: unit processed, not recorded")
    else:
        recorded_tokens.append(token)


def select(group, task_id):
    """The group's authoritative event; report when contents are undivided."""
    count = undivided_contents(group)
    if count > 1:
        report(f"handoff group for task {report_safe(task_id)} holds {count} "
               f"distinct contents with no creation boundary; the latest was "
               f"processed; the earlier contents remain in the journal")
    return latest_by_ts_then_journal_order(group)


def report_gap(task_id, agent, ts):
    report(f"handoff gap for task {report_safe(task_id)}: no source "
           f"resolved; agent {report_safe(agent)}; recorded {report_safe(ts)}")


# DISCOVERY: (section, task_id) pairs; section None is THIS team.
candidates, foreign_only = set(), set()
for e in journal_events:
    destination = route(e)
    if destination in (PARTITION_THIS, PARTITION_OLD_FORMAT):
        candidates.add((None, e.get("task_id")))
    elif destination == PARTITION_FROM:
        candidates.add((e.get("team"), e.get("task_id")))
    else:
        foreign_only.add(e.get("task_id"))
for task_id in set(tasklist_ids) | census.get(this_team, set()):
    candidates.add((None, task_id))
for team in from_teams:
    for task_id in census.get(team, set()):
        candidates.add((team, task_id))
ids = {task_id for _, task_id in candidates}
for task_id in sorted(foreign_only - ids):
    for e in journal_events:
        if e.get("task_id") == task_id:
            report(f"handoff identity mismatch for task {report_safe(task_id)}: "
                   f"journal event names team {report_safe(e.get('team'))}; this "
                   f"store is team {report_safe(this_team)}")

for task_id in sorted(ids):
    matches = [e for e in journal_events if e.get("task_id") == task_id]
    # Report ONCE per task and ONLY when a partition exists: a population id
    # with no journal match has no partition to be underivable about.
    if matches and not this_team:
        report(f"handoff team identity underivable for task "
               f"{report_safe(task_id)}: no Task list: directory in context")
    this_events, from_events, foreign, old_format = [], {}, [], []
    for e in matches:
        destination = route(e)
        if destination == PARTITION_OLD_FORMAT:
            old_format.append(e)
        elif destination == PARTITION_THIS:
            this_events.append(e)
        elif destination == PARTITION_FROM:
            from_events.setdefault(e.get("team"), []).append(e)
        else:
            # An UNKNOWN label fails CLOSED — routed to FOREIGN, which
            # reports and never processes. The OLD-FORMAT chain defaults
            # the other way on purpose (its UNMATCHED case is this team's
            # by identity); a NEW-format label nobody recognizes must not
            # fall into this team.
            foreign.append(e)
    for e in foreign:
        team_value = e.get("team")
        if not isinstance(team_value, str):
            # A NON-STR team is a SCHEMA violation, not a routing fact: the
            # writer (or a hand-crafted journal) is at fault, and a reader
            # must not mistake it for a real team name to route away from.
            report(f"handoff team value invalid for task "
                   f"{report_safe(task_id)}: event team value "
                   f"{report_safe(team_value)!r} is not a valid team name; "
                   f"event not processed")
        else:
            report(f"handoff identity mismatch for task {report_safe(task_id)}: "
                   f"journal event names team {report_safe(team_value)}; this "
                   f"store is team {report_safe(this_team)}")
    if old_format:
        candidate_tasks = []
        for team in [this_team, *from_teams]:
            task = read_task_json(task_id, team) if team else {}
            if task:
                candidate_tasks.append({"team": team, "task": task})
        for e in old_format:
            outcome = mixed_old_format_event(
                task_id, e.get("agent", ""), e.get("task_subject", ""),
                e.get("handoff") or {}, candidate_tasks)
            destination = old_format_destination(outcome, this_team)
            if destination == OLD_FORMAT_DEST_MIXED:
                report(outcome.report)      # discard: another store's content
            elif destination == OLD_FORMAT_DEST_FROM:
                from_events.setdefault(outcome.team, []).append(e)
            else:
                # OWNED_BY(this_team) carries no report; UNMATCHED carries
                # the unavailable-check line. Never choose silently.
                if outcome.report:
                    report(outcome.report)
                this_events.append(e)

    # FROM SECTIONS: each against ITS OWN store, never this team's.
    from_handled_any = False
    for team in sorted(set(from_events) | {t for t in from_teams if (t, task_id) in candidates}):
        from_task = read_task_json(task_id, team)
        meta = task_metadata(from_task)
        disk = disk_handoff(from_task)
        groups = group_events(from_events.get(team, []), journal, epochs)
        owner = owning_group(
            groups,
            disk_identity(from_task.get("owner", ""), from_task.get("subject", "")),
            current_arc(team, task_id, journal, epochs)) if from_task else None
        for key, group in groups.items():
            event = select(group, task_id)
            handoff = event.get("handoff") if isinstance(event.get("handoff"), dict) else {}
            if key == owner:
                merged, reports = union_preferring_task_file(handoff, disk, task_id)
                for line in reports:
                    report(line)
                handle(team, task_id, merged, meta, handoff)
            else:
                handle(team, task_id, handoff, None, handoff)
            # A journal event's `team` names the store EXPLICITLY, so this is
            # evidence the work unit is real; the disk-only branch below has
            # no such claim and never sets it.
            from_handled_any = True
        if not groups:
            if from_task and (team, task_id) in candidates:
                handle(team, task_id, disk, meta, meta)   # rule 5, from store
            elif not from_task:
                report(f"handoff divergence check unavailable for task "
                       f"{report_safe(task_id)}: task file absent")
        elif not from_task:
            report(f"handoff divergence check unavailable for task "
                   f"{report_safe(task_id)}: task file absent")
        elif owner is None:
            report(f"handoff identity mismatch for task {report_safe(task_id)}: "
                   f"task file present, no event group carries its identity")

    # THIS SECTION.
    if (None, task_id) not in candidates:
        continue
    task = read_task_json(task_id, this_team) if this_team else {}
    meta = task_metadata(task)
    disk = disk_handoff(task)
    groups = group_events(this_events, journal, epochs)
    owner = owning_group(
        groups,
        disk_identity(task.get("owner", ""), task.get("subject", "")),
        current_arc(this_team, task_id, journal, epochs)) if task else None
    if not groups:
        if task:
            handle(None, task_id, disk, meta, meta)       # rule 5
        elif not from_handled_any:
            report_gap(task_id, "unknown",
                       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        continue
    for key, group in groups.items():
        event = select(group, task_id)
        handoff = event.get("handoff") if isinstance(event.get("handoff"), dict) else {}
        if key == owner:
            merged, reports = union_preferring_task_file(handoff, disk, task_id)
            for line in reports:
                report(line)
            handle(None, task_id, merged, meta, handoff)
        else:
            handle(None, task_id, handoff, None, handoff)
    # ONE divergence report for the WHOLE group set, decided AFTER the loop:
    # inside it the line fires once per work unit, and "no event group
    # carries its identity" is false whenever another group carries it.
    if not task:
        report(f"handoff divergence check unavailable for task "
               f"{report_safe(task_id)}: task file absent")
    elif owner is None:
        report(f"handoff identity mismatch for task {report_safe(task_id)}: "
               f"task file present, no event group carries its identity")
```

Read all HANDOFFs before proceeding to extraction.

### Step 3.1: Resolve Sibling Metadata (snapshot fallback)

A HANDOFF may reference sibling metadata keys on its task (verification records, parked analyses, teachback history, variety rationales). Those siblings die with the task file when the task store drains — but every completed task's non-handoff metadata is also mirrored into the journal as a `task_metadata_snapshot` event. Resolve sibling keys through this fallback:

1. **Task file** (freshest, and it can be drained): use ONLY the `metadata` Step 3 passed for this unit. When Step 3 passed none, skip this item and use the snapshot fallback; do not read the task file. Only the group that owns the task file ever reads its siblings: a same-numbered task file of another occupant, or of another instance of this task, is never this unit's sibling source.
2. **Snapshot fallback** (GC-proof): read the mirrored snapshots via the existing subcommand (explicit `--session-dir`, masked-read-safe):

   ```bash
   SNAPSHOTS=$(python3 "{plugin_root}/hooks/shared/session_journal.py" read \
                 --session-dir "$SESSION_DIR" --type task_metadata_snapshot)
   ```

   As in Step 1, `read` prints a **JSON ARRAY** — parse the whole stdout once, then iterate. Each event carries `task_id`, `metadata` (the size-bounded sibling-key payload), `subject`, `occupant`, and optionally `owner` / `task_type` / `truncated` / `team`. **Selection**: take the events with the matching `task_id`; because the platform reuses task_ids across arcs, when you are resolving siblings FOR an `agent_handoff` event, additionally require that `occupant` equals `disk_identity(agent, task_subject)` computed from that handoff event's own fields with the SAME shared function — never a local reimplementation. Also require that `team` EQUALS the team of the SECTION the handoff is processed under — this team's name, or `{from}` for a realigned partition. A snapshot that CARRIES a `team` and names another team is DROPPED and reported `handoff snapshot identity mismatch for task N: snapshot names team <team>; this section is team <expected>`, even when the handoff side is OLD-FORMAT. **A missing `team` on the handoff side does NOT excuse a snapshot that names another team.** **THE SNAPSHOT'S ARC MUST EQUAL THE UNIT'S GROUP ARC.** Compute the snapshot's arc with the `arc_of` route at its position in the Step 1 journal list, and drop every snapshot whose arc is not the unit's. A snapshot with NO `team` has a null arc, so it reaches only a team-less unit; a DATED unit, one whose group arc is not null, accepts only a snapshot that carries its team and its arc. One instance of a task never takes another instance's siblings. **Select with the `select_snapshot` route in Step 3, under HOW TO CALL THE NAMES THIS STEP USES, and never by hand:** it applies the occupant, team and arc rules above and the tie-break below, and returns the report lines to emit. Aggregate (whole-arc) reads apply the arc-scoped `--since` bound first, exactly as Step 10 does for `variety_assessed`. Take the **latest-`ts`** event within the match, **last-wins on an equal `ts`** (journal events stamp `ts` at second granularity, so a same-second re-emit ties; the later line in journal order is the authoritative one — the same tie-break the artifact-paths supersede uses) — a task may legally carry multiple snapshots (a changed payload after completion re-emits; the latest is the authoritative end-state). A value of shape `{"_truncated": true, ...}` or a top-level `_dropped_keys` list means the full value lived only in the task file — note the truncation in your synthesis, don't fake the missing content.
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
3. **Synthesize ONE entry from BOTH sources together** (NOT verbatim, NOT a second entry). For each work unit, produce a SINGLE pact-memory entry synthesized from the HANDOFF **and** its artifact: the artifact is the fuller substance, the HANDOFF is the distilled frame. **A work unit is an `agent_handoff` group from Step 3, not a feature** — one feature carries many work units, so the artifacts a unit draws on are the ones you read under THAT UNIT'S feature. A ~19 KB artifact becomes a **richer-but-bounded** entry (a few hundred tokens of decisions/lessons informed by the full substance) — do NOT store the raw artifact. Substance flows into the entry's `context`/`decisions`; put the artifact's path in an entity `notes` field (NOT a `files` field — that field is rejected on save). **Every Step 3 report line for that unit lands in `context` too, and a synthesis that drops one is a defect** — see the report rule in Step 3.
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

1. Search pact-memory for the same entities and topic: `search "{topic}"`
2. If a match is found with high topical overlap (same entities + same decision area + same or superseded conclusion):
   - **Update** the existing memory (`update` CLI command) rather than creating a new one
   - `update` merges list fields additively with content-hash dedup, so passing just the new lessons/decisions/entities appends them without clobbering what's already there. Repeated calls are idempotent.
   - Use `update <id> --replace` only when a prior conclusion has been **superseded** and you need to remove the old items from the list. Default `update` is append-only semantically — it will never delete an existing item.
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

**Save the processed unit tokens to your team's section in agent memory.** Multiple secretary instances (one per concurrent team) share this single file; each owns exactly its own `## team={your team_id}` section, and **whether yours already exists is decided by a count, never by searching what you read** — see **Section semantics** below for that count and for the boundary rule on other teams' sections. Append this pass's unit tokens to your section in `session_processed_tasks.md`, in the agent-memory directory the platform gave you, as a new `Processed task IDs` line, never rewriting the lines already there. **Write the unit tokens Step 3 recorded, from both sections, on this team's line.** A from-team token carries its `{from}/` prefix, so it never matches a token of this team's task of the same number.

This file is **namespaced by team** so that concurrent secretary instances (one per active team, all sharing this single user-scope file) never clobber each other's processed-task baselines. The file-format contract:

File: `session_processed_tasks.md`, in the agent-memory directory the platform gave you. Use the path you are given; never build one from your agent type.
```markdown
---
name: session_processed_tasks
description: Work-unit tokens processed per team for dedup on incremental passes. NAMESPACED by team to avoid cross-secretary collision (single-file user-scope).
type: reference
---

# Per-team processed-tasks log (NAMESPACED — multiple concurrent secretary instances write here)

## team={team_id} ({project}, session {session_id})

{optional one-line session note}

Processed task IDs (this team): {comma-separated unit tokens}
Last processed (this team): {timestamp}

## team={other_team_id} ({project}, session {session_id})

...
```

Write each `Processed task IDs` line at column 0, exactly as the template shows; never place tokens inside a note. `Last processed` is for a human reader; no step compares it.

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

Triggered after remediation completes — processes only the units whose tokens the ledger lacks. Fires only when remediation occurred and produced new completed tasks.

1. **Check processed task tracking**: Locate your `## team={your team_id}` section of `session_processed_tasks.md` with the **Standard Harvest Step 8 rule's existence decision**: the `grep -c` header count, what each of its outcomes means for whether your section exists, and the `awk` extract on the `1` branch. **The write actions inside those branches — appending, creating a section — belong to step 6, not to this one.** Do NOT restate that rule here, and do NOT decide the section exists by searching what you read — a ledger past the read cap hides your section while the file is intact, and a pass that concludes "no processed IDs" reprocesses everything. From the extract, take the processed token set as the **UNION of every `Processed task IDs` line** in that section, never the newest line alone, with the `parse_ledger_tokens` route
2. **Discover new completions**: Run Standard Harvest Step 1 discovery, PLUS any task the Standard Harvest Step 3 quiescent-task rule sent you back for, which runs as FORCED. Do not narrow to the journal: a new completion whose content sits only in a non-`handoff` key emits no `agent_handoff` event.
3. **If Step 3 selects no unit whose token is absent (and no FORCED task)**: Report "No new HANDOFFs since last harvest" and complete
4. **Read new HANDOFFs** with the Standard Harvest Step 3 rule, in full: the identity derivation, the group selection, the union predicate and the report messages. Do NOT restate that rule here. A summary of it here is a second statement that goes stale on its own
5. **Extract and save** using Steps 4-7 from Standard Harvest (extract knowledge, organizational state, dedup protocol, save) (every save with --no-sync; an Incremental dispatch never carries the propagate sentence)
6. **Update processed task tracking** — **append** the recorded tokens to **your team's** `## team={your team_id}` section of `session_processed_tasks.md` with the **Standard Harvest Step 8 rule, in full**: the `grep -c` header count and its branches decide WHERE to append, and the write is append-only. Do NOT restate that rule here, and do not append to a section you located by reading the file — that is how a second `## team=` section for one team gets created
7. **Do NOT delete the session journal** — it may still be accumulating entries from ongoing work
8. **Update existing memories** if remediation superseded prior decisions (use `update` CLI command, not `save`). Remember: default `update` is additive merge — pass `--replace` only when the prior list items need to be discarded, not amended.
9. **Report delta summary** to team-lead — only report what changed in this incremental pass

---

## Consolidation Harvest Workflow

**Build the population as the Standard Harvest population rule above requires — a dispatch does not narrow it.**

Triggered during `/PACT:wrap-up`, `/PACT:pause` or `/PACT:refresh`. This is the deep-clean pass — it extends the standard workflow with memory consolidation and pruning.

### Step 1: Safety Net (Unprocessed HANDOFFs)

Run the Standard Harvest Step 1 discovery in full — every source, including the task-file metadata census — for units whose token is not in the processed set. Checking `agent_handoff` events alone leaves any task whose content sits in a non-`handoff` key undiscovered, and this is the last pass that will look. If unprocessed entries exist, run the Standard Harvest workflow above first (earlier harvest triggers may have been missed). Then continue with consolidation. That nested run does not propagate: Step 4 does, after the Step 3 prune.

### Step 2: Review Session Memories

Review all memories saved during this session by listing recent pact-memory entries.

### Step 3: Consolidate and Prune

- Merge overlapping memories (same topic, same entities, compatible conclusions)
- Prune superseded memories (update or delete entries replaced by newer information)
- **Prune complete `## team=` sections** in `session_processed_tasks.md`, in the agent-memory directory the platform gave you. The file is shared by every secretary instance across every project, so a section may be removed on exactly one ground and no other: you have VERIFIED that its team is complete by reading that session's journal. Age never prunes: a section's `Last processed` timestamp, however old, is not a ground for removal, and a section with no `Last processed` line is unjudgeable, not stale — a criterion that cannot be evaluated never does. **Enumerate the sections with `grep -n '^## team=' "{ledger path}"` rather than by reading the file**: a section past a read's line cap is invisible to a search of what you received, and item 6 would then under-report what this pass considered. A section may hold `{from}/` tokens of the same session's realigned teams; its completion ground covers them because the realign keeps one session directory. Do not extend tokens to any team outside that session. Verify each section in this order:
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
4. **Record the recovered units' tokens. Do NOT remove the files you read them from.** Add the recovered units' tokens, each prefixed `{prior team}/`, to your team's `## team={your team_id}` section of `session_processed_tasks.md` with the **Standard Harvest Step 8 rule, in full**: the `grep -c` header count and its branches decide WHERE to append, and the write is append-only. Do NOT restate that rule here. That ledger is what stops a later pass reprocessing this session, and Step 2 reads it. No removal is needed to reach that outcome.

   **Two file classes carry this session's content, and either one can be the only carrier of a given HANDOFF.** The journal at `{session_dir}/session-journal.jsonl` carries the tasks that emitted an `agent_handoff` event. The task files under `{config_dir}/tasks/{team_id}/` carry a quiescent completed task whose key never reached the journal. Neither class is a copy of the other, so neither is safe to remove because the other was read.

   **Run the carrier test and report its result at Step 5.**

   1. Read `{config_dir}/tasks/{team_id}/`. Directory absent, or present with no task files → the journal is the ONLY carrier of what you recovered. Task files present → a second carrier exists.
   2. Read raised, or the path did not resolve → record ONE carrier. An unreadable directory is not an empty one, and the error does not tell you which you have.
   3. Re-fetch what you saved in Step 7 by its returned `memory_id` and compare it against what you recovered. Present and in agreement → a durable second copy exists. Save reported success but the re-fetch does not find it, or either step raised → NO durable second copy exists.

   **You have processed these files. It does not follow that you may remove them.** A file processed twice costs a duplicate paragraph, and the ledger's token skip pays even that cost only once. A journal removed once costs every HANDOFF, lifecycle event and snapshot that only it held, and nothing regenerates them. The cleanup this recovery needs is the ledger entry above, and that entry takes no bytes with it. A later reader who wants this workspace tidy is looking at the wrong step.
5. Report summary of recovered knowledge (or gaps where all sources failed). State the carrier test result from step 4 with it: how many carriers held the recovered content, and whether a durable second copy was verified.
