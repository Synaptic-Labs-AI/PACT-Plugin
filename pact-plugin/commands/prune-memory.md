---
description: Interactively prune pins from CLAUDE.md via paginated AskUserQuestion
---

## What this command does

Presents the curator with the current evictable-pin list and asks which
one (or none) to remove. Useful after `/PACT:pin-memory` is denied by
the count cap: use `/PACT:prune-memory` to evict an existing pin, then
retry the add.

The `pin_caps_gate` PreToolUse hook ALLOWS the resulting Edit because
the pin count strictly decreases (net-worse predicate: pre has ≥N
pins, post has N-1 — not worse, so allow).

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
    {"index": 0, "heading": "First Pin", "chars": 40, "stale": false, "override": false},
    {"index": 1, "heading": "Second Pin", "chars": 30, "stale": true, "override": false},
    ...
  ]
}
```

If `slot_status` starts with `Pin slots: unknown (...)`, the CLI could
not parse CLAUDE.md. Report the reason and stop — do NOT attempt to
evict from an unknown state.

If `evictable_pins` is empty, report "No pins to prune." and stop.

### Step 2 — Ask the curator which pin to prune

Present up to 3 candidate pins per `AskUserQuestion` call (plus a 4th
"Show more" or "Cancel" option). Prefer STALE pins first — they are
the safest to evict. Label shape:

```
AskUserQuestion(questions=[{
  header: "Pin prune",
  question: "Which pin to evict? (page N of M)",
  options: [
    {label: "Pin {index} — {heading}", description: "{chars} chars{, STALE if stale}{, OVERRIDE if override}"},
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

If the curator picks "Cancel", report "Prune cancelled; CLAUDE.md unchanged." and stop.

### Step 3 — Remove the selected pin

Read the current CLAUDE.md. Locate the pin block for the selected
`{heading}`:

- The date comment immediately preceding `### {heading}` (if any).
- The `### {heading}` line itself.
- The body up to (but not including) the next `### ` heading OR the
  end of the `## Pinned Context` section.

Use the `Edit` tool to remove the full block, preserving surrounding
blank lines (one blank line between remaining pins). The `pin_caps_gate`
hook ALLOWS the edit because `len(post_pins) < len(pre_pins)` — strictly
better, not worse.

### Step 4 — Report + commit

- Report: "Pruned pin {index}: {heading}".
- Commit the change with a concise message, e.g. `chore: prune pin "{heading}"`.

## Notes

- This command NEVER evicts more than one pin per invocation. Run it
  multiple times to prune multiple pins — this keeps each evict/retry
  cycle auditable.
- Stale pins (marked with `<!-- STALE: Last relevant YYYY-MM-DD -->`)
  are surfaced with a `STALE` tag in the option description. Prefer
  pruning stale pins before non-stale.
- Override-carrying pins (with `pin-size-override` rationale) are
  surfaced with an `OVERRIDE` tag. These are load-bearing verbatim
  content; prune only with deliberate intent.

## See also

- `/PACT:pin-memory` — add a new pin (hook enforces caps).
- `hooks/pin_caps_gate.py` — the authoritative cap enforcer.
