---
description: Interactively prune pins from CLAUDE.md via paginated `AskUserQuestion`
---

## What this command does

Presents the curator with the current evictable-pin list, asks which
one (or none) to remove, **archives the selected pin to long-term memory
and verifies the archive before removing anything**, then removes it.
Useful after `/PACT:pin-memory` is denied by the count cap: use
`/PACT:prune-memory` to demote an existing pin to long-term memory, then
retry the add.

Demotion is not deletion. A pin leaves `## Pinned Context` only once its
content is provably somewhere else — see [Step 3](#step-3--archive-the-selected-pin).

Only the team-lead runs this command, with the user. A teammate or subagent
whose pin was denied by the count cap asks the team-lead for a free slot
instead.

The `pin_caps_gate` PreToolUse hook ALLOWS the resulting `Edit` because
the change adds no pin. **The hook cannot enforce the archive**: it sees a
change that adds no pin and allows it, whether or not Step 3 ran. Step 3 is
enforced by this file and by nothing else, which is why every exit below
emits a journal event — an invocation that evicts a pin while emitting
nothing is the one state that should never occur.

## Process

### Step 1 — Read the evictable-pin list

Invoke the advisory CLI to get the current state:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check_pin_caps.py" --status
```

The CLI emits a JSON payload with `evictable_pins`:

```json
{
  "allowed": true,
  "violation": null,
  "slot_status": "Pin slots: N/12 used, ...",
  "evictable_pins": [
    {"index": 0, "heading": "First Pin", "chars": 40, "stale": false, "override": false,
     "age_days": 60, "overdue": true},
    {"index": 1, "heading": "Second Pin", "chars": 30, "stale": true, "override": false,
     "age_days": null, "overdue": null},
    ...
  ]
}
```

`age_days` is computed from the pin's `<!-- pinned: ... -->` date comment;
`overdue` is true once that age reaches the staleness threshold. Both are
distinct from `stale`, which reports only that a `<!-- STALE: ... -->` marker
has been written into the body. A pin can be `overdue` without being `stale`.

**`age_days: null` is a third state and is NOT `overdue: false`.** It means the
pin carries no parseable date and its age is unknown. Surface it to the curator
as unknown rather than silently treating it as fresh — an undated pin is the one
most likely to have been sitting there longest.

If `slot_status` starts with `Pin slots: unknown (...)`, the CLI could
not parse CLAUDE.md. Report the reason, emit the skip event with outcome
`unknown_state`, and stop — do NOT attempt to evict from an unknown state.

If `evictable_pins` is empty, report "No pins to prune.", emit the skip
event with outcome `no_candidates`, and stop.

### Step 2 — Ask the curator which pin to prune

Present up to 3 candidate pins per `AskUserQuestion` call (plus a 4th
"Show more" or "Cancel" option). `AskUserQuestion` accepts at most 4
options per question and at most 4 questions per call, so 3 candidates
plus one navigation slot sits exactly at the option cap — this pagination
cannot be widened, only paged.

**Order `overdue` pins first** — they have outlived the horizon they were
pinned for and are the default prune candidates. Within the same overdue
bucket, Prefer STALE pins over fresh ones. Label shape:

```
AskUserQuestion(questions=[{
  header: "Pin prune",
  question: "Which pin to demote to long-term memory? (page N of M)",
  options: [
    {label: "Pin {index} — {heading}", description: "{chars} chars{, OVERDUE {age_days}d if overdue}{, age unknown if age_days is null}{, STALE if stale}{, OVERRIDE if override}"},
    ...
    {label: "Show more",                description: "Next page of candidates"}
       | {label: "Cancel",              description: "Do not prune any pin"}
  ]
}])
```

Pagination rules:
- **≤ 3 evictable pins**: present all + "Cancel".
- **> 3 evictable pins**: present 3 pins + "Show more" per page. The last page shows remaining pins + "Cancel".
- Label format: `Pin {index} — {heading}` (the index is the position in `evictable_pins`, not the line in CLAUDE.md).

If the curator picks "Cancel", report "Prune cancelled; CLAUDE.md unchanged.",
emit the skip event with outcome `cancelled`, and stop.

#### Justify the eviction

Once a pin is selected, ask the curator — as a plain question, not an
`AskUserQuestion` call, because a rationale cannot be enumerated as options
in advance — **what concrete next-session question this pin resolves**.

A justification MUST name a concrete question ("how do parsers scope their
reads in CLAUDE.md?") and cite the pin's answer ("use `_extract_managed_region()`").
It MUST NOT fall back to "load-bearing", "accurate", "important", or similar
non-discriminating generalities. If the answer is one of those, say so and ask
again — a pin nobody can frame a question for is a pin that has stopped earning
its slot, and that is the finding, not an obstacle.

This is a discipline rule you apply by reading the answer. It is deliberately
NOT a banned-word check: a word list is satisfied by any synonym, and a
mechanical-looking gate suppresses the scrutiny a prose rule invites.

#### Re-confirm the other overdue pins

For pins that are `overdue` but were NOT selected, batch re-confirmation into
`AskUserQuestion` calls of up to 4 questions each (with 6 overdue pins that is
2 calls, not 6). Ask whether each is still worth its slot. For each pin the
curator keeps, collect a one-line reason and extend its date comment in place:

```markdown
<!-- pinned: 2026-05-26, reconfirmed: 2026-07-25 because {concrete reason} -->
```

The reason MUST be single-line. Re-confirming resets the clock: age computes
from the `reconfirmed:` date once present. The reason is free text and is
subject to the same concreteness rule above.

Scope note: justification is asked for the pin being evicted and for
age-flagged borderline pins only — **not for every retained pin**. At 12 pins
the exhaustive form serialises into roughly 20 prompts to evict one pin, and a
curator facing 20 prompts answers with exactly the generalities this step
exists to forbid.

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

### Step 3 — Archive the selected pin

Demote the pin into long-term memory and **verify it arrived** before anything
is removed:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/archive_pin.py" --index N
```

The script always exits 0 and reports its verdict inside the JSON payload — a
non-zero exit is a crash, not a verdict, and is treated as `UNEVALUABLE`. It
writes the pin block to pact-memory, re-fetches it by the returned `memory_id`,
and confirms the block is present byte-for-byte:

```json
{"outcome": "ARCHIVED", "memory_id": "<32-hex>", "heading": "...", "claude_md_path": "/abs/path/CLAUDE.md", "chars": 412, "contained": true, "occurrences": 1}
{"outcome": "ARCHIVED_DELETE_UNSAFE", "memory_id": "<32-hex>", "heading": "...", "claude_md_path": "/abs/path/CLAUDE.md", "occurrences": 2, "locations": [1043, 2871], "reason": "<why>"}
{"outcome": "NOT_ARCHIVED", "memory_id": "<32-hex>|null", "reason": "<why>", "heading": "...", "claude_md_path": "/abs/path/CLAUDE.md"}
{"outcome": "UNEVALUABLE", "reason": "<why>", "heading": "...|null", "claude_md_path": "/abs/path/CLAUDE.md|null"}
```

`outcome`, `heading` and `claude_md_path` are present in **every** verdict.
`claude_md_path` names the file this run actually read and is null only when
resolution itself failed — which is also the case where there is nothing to
edit, so a null path is a refusal, never a prompt to guess.

**Check the returned `heading` against the pin the curator chose in Step 2.**
`--index N` is a position in `evictable_pins`, and the script re-resolves that
index against CLAUDE.md as it reads the file. If anything changed the pinned
section in between, the index now denotes a different pin — and the archive
would succeed, and its containment check would pass, for the wrong pin. On any
mismatch, stop and re-run from Step 1. A verification that measures the right
property on the wrong object reports success and proves nothing.

`heading` is always present as a key. It is the pin's real parsed heading, never
an echo of the index you passed — an echo would make this check compare the
index against itself and pass unconditionally. It is `null` only on
`UNEVALUABLE` where no pin could be resolved at all (unreadable CLAUDE.md, no
pinned section, index out of range). **Read `null` as unresolvable and take the
escape hatch below — never as a mismatch**; there is no heading to disagree with.

Then act on the verdict. **Only `ARCHIVED` permits the removal Edit** — the
name determines the disposition, so no row needs a footnote to be actionable:

| Verdict | Meaning | Action |
|---|---|---|
| `ARCHIVED` | the pin's bytes are provably in long-term memory | proceed to Step 4 |
| `ARCHIVED_DELETE_UNSAFE` | the archive **succeeded**; only the automatic removal is unsafe | do NOT Edit; hand the curator `memory_id`, `occurrences` and `locations` for manual removal; emit `delete_unsafe`; stop |
| `NOT_ARCHIVED` | the archive definitively failed | **refuse**; report `reason`; emit `archive_refused`; stop |
| `UNEVALUABLE` | could not tell — CLI absent, crash, timeout, unreadable file | **refuse**; report `reason`; offer the escape hatch below |

#### `ARCHIVED_DELETE_UNSAFE` — a redirect, not a refusal

**The archive worked.** The pin's content is in long-term memory and the
verdict carries the `memory_id` that proves it. What failed is only the
automatic removal: the emitted handle did not occur exactly once in the file,
so an `Edit` keyed on it would match the wrong text or none at all.

So the command does not remove the pin, and tells the curator plainly: *your
content is safe under this `memory_id`; the block was found `occurrences`
times; remove the pin by hand.* **The curator is not stuck at the cap** — the
eviction can still be completed manually, and the safety guarantee this command
exists to provide already held. Say that explicitly; a curator meeting an
unfamiliar refusal at 12/12 will otherwise read it as a dead end and start
hand-editing without the memory_id in view.

`locations` are character offsets and are **diagnostic — where to look, never
what to delete.** An offset computed at verdict time is silently wrong if the
file changed afterwards, which is the whole reason the removal is keyed on
content rather than position.

There is **no escape hatch** on this verdict, and that is deliberate rather
than an omission. The hatch exists for *cannot tell*; this is a known-bad
precondition for the delete. Offering proceed-anyway would hand the curator an
ambiguous target to remove by hand, which is worse than declining to remove it
automatically.

**Do NOT warn-and-proceed on a failed archive.** Elsewhere in PACT a failed
journal write degrades to a warning because the journal survives independently
of the thing being written about. Here nothing survives: proceeding past a
failed archive destroys the only copy of the pin, and CLAUDE.md is frequently
git-ignored, so there may be no commit to recover it from. The degradation is
to keep the pin, not to keep going.

#### Escape hatch — `UNEVALUABLE` only

A curator at the cap with a broken memory CLI must not be trapped between a
command that will not add and a command that will not remove. So on
`UNEVALUABLE` — and never on `NOT_ARCHIVED`, where the failure is definite:

1. **Print `delete_string` verbatim into the conversation**, under a heading
   stating it is the only remaining copy. The verdict carries it whenever the
   pin was located, so this path needs no separate reading of the file — the
   hatch consumes the same checked handle as Step 4 rather than working out
   its own boundary. (If the verdict carries no handle, the pin was never
   located: there is nothing to print and nothing to evict — refuse outright
   and stop.)
2. Ask the curator to confirm they have captured it elsewhere. This is an
   explicit acknowledgement — never a default, never inferred from silence.
3. Only on that acknowledgement, proceed to Step 4, which removes that same
   string.
4. Emit the skip event with outcome `unverified_eviction`, so the unverified
   eviction is auditable.

The hatch exists to make **the pin survive**, not to make the eviction proceed.

### Step 4 — Remove the selected pin

**Proceed only if Step 3 returned `ARCHIVED` with a matching `heading`, or if
the `UNEVALUABLE` escape hatch was explicitly acknowledged.** If neither holds,
this step does not run — go back and report the refusal. Do not remove a pin
you cannot show is somewhere else.

Read the CLAUDE.md at **`claude_md_path`, the absolute path the Step 3 verdict
reports** — not "the project's CLAUDE.md". That field names the file the
archival run actually read, and editing anything else breaks the one link that
makes the verdict mean something about the file you are about to change.

The two are not reliably the same file. Resolution falls back through
`CLAUDE_PROJECT_DIR`, then the git common dir's parent, then the working
directory, so the file that gets read is not always the one the invocation
seems to name. **If `claude_md_path` is not the project you expected, STOP and
report it; do not edit.**

The script refuses one case for you and cannot refuse the other. A fall-through
that lands **outside** the starting directory's repository is refused outright —
that verdict arrives as `UNEVALUABLE` with a reason naming both the directory
and the file it would otherwise have used. A fall-through **within the same
repository is allowed**, deliberately: PACT's own worktrees have no CLAUDE.md
of their own, so every normal invocation depends on reaching the main
checkout's file. That residual is the one you have to catch by reading
`claude_md_path` — which is the whole reason this step names it.

Step 3's heading cross-check cannot save you here. Step 1 uses the *same*
resolver, so both agree on the same wrong file and the headings match — it
detects an index shift *within* one file and is structurally blind to a
wrong-*file* resolution. A cross-check only catches disagreement between two
things that can disagree, and these two cannot.

**Remove exactly the string the Step 3 verdict emitted as `delete_string`, at
`claude_md_path`:**

    Edit(file_path=<claude_md_path>, old_string=<delete_string>, new_string="")

**Use the emitted string exactly as given. Do not trim, pad, reformat or tidy
it, and do not add, remove or adjust blank lines around the Edit** — the string
spans exactly what must be removed. A curator who "cleans up" the value breaks
the match, and the Edit then fails rather than removing the wrong bytes. That
failure is the safe direction, but it is a confusing dead end, so do not walk
into it: if the Edit does not match, something changed the file after the
verdict was produced. **Stop and re-run from Step 1 — do not search for a
near-match and do not hand-edit.**

`delete_string` is a **checked handle, not simply the pin's text.** The script
emits it only once it has established the string can be used — that it is
present and occurs exactly once. Where that could not be established the field
is absent, and the verdict says so through its outcome rather than handing you
something unusable. **A field is present exactly when the fact it names was
established**, which is why a missing handle is a refusal and never an
invitation to reconstruct one.

The `pin_caps_gate` hook ALLOWS the edit because it adds no pin.

### Step 5 — Report

- Report: "Pruned pin {index}: {heading} — archived as {memory_id}." Name the
  `memory_id` so the curator can retrieve the demoted content later.
- Emit `pin_pruned` carrying that `memory_id`, the pin's `heading`, and
  `pin_count`. It has no `outcome` field — the event's existence IS the
  outcome. An escape-hatch eviction never reaches this event; it already
  emitted `pin_prune_skipped` with `unverified_eviction` back at Step 3.
- Commit the change — then **confirm `CLAUDE.md` is actually in the resulting
  commit.** Where a project git-ignores `CLAUDE.md` (a bare `CLAUDE.md` line in
  `.gitignore` matches at any depth), an explicit `git add CLAUDE.md` errors and
  a commit whose only change is `CLAUDE.md` reports "nothing to commit" — both
  loud. The silent case is a blanket `git commit -a` / `-am` that also picks up
  other changed files: it succeeds, exits 0, and omits `CLAUDE.md`. Verify the
  file is in the commit rather than trusting the commit's exit status, and if it
  is not, report the prune as applied to the working file but not
  version-controlled. NEVER use `git add -f`.

## Telemetry

Every exit emits exactly one journal event, so that an invocation which removed
a pin while emitting nothing is detectable after the fact. That is the only
check on Step 3 there is — the hook cannot enforce the archive, so the audit
trail is what makes a skipped archive visible at all.

| Exit | Event | `outcome` |
|---|---|---|
| Curator picks Cancel | `pin_prune_skipped` | `cancelled` |
| `evictable_pins` empty | `pin_prune_skipped` | `no_candidates` |
| `slot_status` unknown | `pin_prune_skipped` | `unknown_state` |
| Archive refused (Step 3) | `pin_prune_skipped` | `archive_refused` |
| Archived but removal unsafe (Step 3) | `pin_prune_skipped` | `delete_unsafe` |
| Escape-hatch eviction | `pin_prune_skipped` | `unverified_eviction` |
| Pin archived and removed | `pin_pruned` | — (no `outcome` field) |

`delete_unsafe` is a **distinct value and must not be folded into
`archive_refused`.** That value means the archive definitively failed, and a run
of them is the signal that the archival mechanism itself is broken. On this exit
the archive SUCCEEDED, so reusing it would make a run of successful archives
read as a failing archiver — poisoning the one diagnostic that row exists for.

`pin_prune_skipped` carries `outcome`, `pin_count` at the time of the skip, and
`age_distribution` (oldest / newest / median `age_days`, over pins whose age is
known). An **empty `age_distribution` means the measured set was empty** — no
pins with a known age existed at that moment. It is never a placeholder and
never means not-applicable: the field is computed identically on every exit and
is not conditional on the outcome. In particular `no_candidates` means
`evictable_pins` is empty, which is NOT the same as no pins existing, so where
dated pins are present the real distribution goes in even though none are
evictable. `reason` is optional — a bare Cancel does not elicit one, and
emitting a field with nothing behind it is a report with no measurement in it.

`pin_pruned` carries `heading`, the `memory_id` of the archive, and `pin_count`
— the count BEFORE the removal, matching `pin_prune_skipped`'s "count at the
time of the decision" so the two events share one referent and stay directly
comparable. The post-eviction count is derivable, since this command never
evicts more than one pin per invocation. It carries no `outcome` field: only
required fields are validated, so an extra one would be written into the audit
trail looking authoritative while guaranteed by nothing. The `memory_id` is the
point of the event — it makes the claim checkable by fetching it, rather than
merely asserted.

**Reading the trail: an eviction is a UNION, not a single event.** An
escape-hatch eviction removed a pin but is filed under the skip event, because
it has no `memory_id` to carry and `pin_pruned` requires one:

    evictions = pin_pruned  UNION  (pin_prune_skipped WHERE outcome = "unverified_eviction")

The second arm is exactly the set of evictions with no archive record. An audit
that queries only `pin_pruned` under-counts evictions and, worse, misses the
ones that went unverified — the population it most needs to see.

Substitute `{session_dir}` from the `- Session dir:` line in CLAUDE.md's
`## Current Session` block, falling back to `pact-session-context.json` for that
one field. **If neither resolves, skip the emit, note it in the report, and
carry on** — telemetry never blocks a prune. A failed audit write that refused a
legitimate eviction would be a data-loss bug arriving from the observability
layer, which is the one place nobody is watching for it.

```bash
set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] prune-memory.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/shared/session_journal.py" write \
  --type pin_prune_skipped --session-dir '{session_dir}' --stdin <<'JSON'
{"outcome": "cancelled", "pin_count": 12, "age_distribution": {"oldest": 60, "newest": 3, "median": 33}}
JSON
```

The heredoc delimiter is quoted (`<<'JSON'`) so bash performs no expansion
inside it — a curator's reason containing an apostrophe or a backtick passes
through verbatim instead of closing the quote and aborting the write under
`set -e`. Construct JSON-valid string content (escape `\"`, `\\`, and control
characters).

The remaining skip outcomes take the same form; the `set -e` and `trap` lines
above apply to each and are not repeated. **One emit per fence** — do not
combine them.

`no_candidates`, where the empty `age_distribution` records that the measured
set was empty rather than standing in for a value:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/shared/session_journal.py" write \
  --type pin_prune_skipped --session-dir '{session_dir}' --stdin <<'JSON'
{"outcome": "no_candidates", "pin_count": 0, "age_distribution": {}}
JSON
```

`unknown_state`, where the CLI could not parse CLAUDE.md, so no age is known:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/shared/session_journal.py" write \
  --type pin_prune_skipped --session-dir '{session_dir}' --stdin <<'JSON'
{"outcome": "unknown_state", "pin_count": 12, "age_distribution": {}, "reason": "Pin slots: unknown (CLAUDE.md unparseable)"}
JSON
```

`archive_refused`, where the archive definitively failed and the pin survives:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/shared/session_journal.py" write \
  --type pin_prune_skipped --session-dir '{session_dir}' --stdin <<'JSON'
{"outcome": "archive_refused", "pin_count": 12, "age_distribution": {"oldest": 60, "newest": 3, "median": 33}, "reason": "memory CLI timed out"}
JSON
```

`delete_unsafe`, where the archive SUCCEEDED and only the automatic removal was
declined — note it still carries a `reason`, because unlike a bare Cancel this
exit always has one:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/shared/session_journal.py" write \
  --type pin_prune_skipped --session-dir '{session_dir}' --stdin <<'JSON'
{"outcome": "delete_unsafe", "pin_count": 12, "age_distribution": {"oldest": 60, "newest": 3, "median": 33}, "reason": "block occurs 2 times; removal would be ambiguous"}
JSON
```

`unverified_eviction` is emitted on the escape-hatch path, where the pin was
removed without a verified archive:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/shared/session_journal.py" write \
  --type pin_prune_skipped --session-dir '{session_dir}' --stdin <<'JSON'
{"outcome": "unverified_eviction", "pin_count": 12, "age_distribution": {"oldest": 60, "newest": 3, "median": 33}, "reason": "curator acknowledged the pin block was captured elsewhere"}
JSON
```

The success event is emitted at Step 5, once the removal Edit has landed:

```bash
set -e
trap 'rc=$?; echo "[JOURNAL WRITE FAILED] prune-memory.md (bash line $LINENO): \"${BASH_COMMAND%%$'\''\n'\''*}\" exit=$rc" >&2; exit $rc' ERR
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/shared/session_journal.py" write \
  --type pin_pruned --session-dir '{session_dir}' --stdin <<'JSON'
{"heading": "First Pin", "memory_id": "0f3a9c1d7e2b48f6a5c04d8e1b7f2a93", "pin_count": 12}
JSON
```

Two things in that payload are deliberate and are the ones most likely to be
"corrected" by a later editor. There is **no `outcome` field** — only required
fields are validated, so an extra one would land in the audit trail looking
authoritative while guaranteed by nothing. And `pin_count` is **12, the count
BEFORE the removal**, matching the skip event above so both carry the same
referent; the post-eviction count is derivable, since this command never evicts
more than one pin per invocation.

## Notes

- This command NEVER evicts more than one pin per invocation. Run it
  multiple times to prune multiple pins — this keeps each evict/retry
  cycle auditable.
- Stale pins (marked with `<!-- STALE: Last relevant YYYY-MM-DD -->`)
  are surfaced with a `STALE` tag in the option description. Prefer
  pruning stale pins before non-stale.
- Overdue pins (`age_days` past the threshold) are surfaced with an
  `OVERDUE` tag and sorted first. `stale` and `overdue` are independent
  signals — a pin can be either, both, or neither.
- Override-carrying pins (with `pin-size-override` rationale) are
  surfaced with an `OVERRIDE` tag. These are load-bearing verbatim
  content; prune only with deliberate intent.

## See also

- `/PACT:pin-memory` — add a new pin (hook enforces caps).
- `hooks/pin_caps_gate.py` — the authoritative cap enforcer.
