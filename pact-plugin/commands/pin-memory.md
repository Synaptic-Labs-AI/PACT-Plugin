---
description: Pin important context permanently to CLAUDE.md, or review the session for pin-worthy context
argument-hint: "[optional: e.g., critical gotcha, key architectural decision]"
---

## Mode

- **With arguments** (`/PACT:pin-memory <content>`): Pin the specified content directly.
- **Without arguments** (`/PACT:pin-memory`): Review the session for pin-worthy context, pin what matters, and prune stale entries.

## Caps (hard rules)

**You MUST NOT bypass these. Run `scripts/check_pin_caps.py` BEFORE any pin add, no exceptions.**

- **Count cap**: 12 pins maximum.
- **Size cap**: 1500 characters per pin body (excludes `<!-- pinned: ... -->` and `<!-- STALE: ... -->` auto-markers).
- **Override**: verbatim load-bearing content (e.g., canonical protocol forms) MAY carry a `pin-size-override` rationale — see [Size Override](#size-override). Curator discretion; no hard override count sub-cap.

## When to Pin

- **Critical gotchas** that would waste hours if forgotten
- **Key architectural decisions** that explain "why" (not "what")
- **Build/deploy commands** needed every session
- **Non-obvious patterns** unique to this codebase

## When NOT to Pin

- Routine session context (auto-memory and pact-memory handle this)
- Things easily found in code or docs
- Temporary information that will become stale

## Process

**Target file**: The project CLAUDE.md may be at either `$CLAUDE_PROJECT_DIR/.claude/CLAUDE.md` (preferred) or `$CLAUDE_PROJECT_DIR/CLAUDE.md` (legacy). Use `.claude/CLAUDE.md` if it exists, otherwise use `./CLAUDE.md`. If neither exists, create at `.claude/CLAUDE.md`.

### Step 1 — Enforce caps (required, both modes)

Before any pin add, MUST invoke the cap check CLI:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_pin_caps.py --new-body "$CANDIDATE_BODY" [--has-override]
```

The CLI emits JSON on stdout:

```json
{
  "allowed": true,
  "violation": null,
  "slot_status": "Pin slots: 11/12 used, 340 chars remaining on largest pin",
  "evictable_pins": [...]
}
```

- `allowed: true` (exit 0) → proceed to Step 2.
- `allowed: false` (exit 1) → run the [Refusal Flow](#refusal-flow). MUST NOT bypass.
- Fail-open: if slot_status starts with `Pin slots: unknown (...)`, the CLI could not parse state — proceed, but report the reason to the user.

### Step 2 — Add the pin

#### With Arguments (targeted pin)

1. Read existing CLAUDE.md
2. Locate or create a `## Pinned Context` section (place it before `## Working Memory`)
3. Add the new entry with a date tag:
   ```markdown
   <!-- pinned: YYYY-MM-DD -->
   ### Entry Title
   Content here (~5-10 lines max)
   ```
4. Run the pruning process (see [Pruning Pinned Entries](#pruning-pinned-entries) below)
5. Commit changes

#### Without Arguments (session review)

1. Read existing CLAUDE.md
2. Review the session for pin-worthy context — scan for significant decisions, architectural changes, gotchas discovered, or patterns established. Apply the "When to Pin" criteria above.
3. For each pin-worthy entry, run Step 1 with its candidate body BEFORE adding.
4. If nothing is pin-worthy, report "No new context to pin."
5. Run the pruning process (see [Pruning Pinned Entries](#pruning-pinned-entries) below)
6. Commit changes if any were made

## Refusal Flow

When `check_pin_caps.py` returns `allowed: false`:

### Count refusal (`violation.kind == "count"`)

You MUST prompt the user to evict before the new pin can be added. Use `AskUserQuestion` with a two-step flow — the flat-list approach fails because 12 eviction candidates + cancel exceeds the 4-option platform cap.

**Step A — choose category**:

```
AskUserQuestion(questions=[{
  header: "Pin evict",
  question: "Pin slots full (12/12). Which category to evict?",
  options: [
    {label: "Evict stale pin",    description: "Evict a pin already marked <!-- STALE: ... -->"},
    {label: "Evict non-stale pin", description: "Evict a load-bearing pin — requires justification"},
    {label: "Cancel add",          description: "Abandon this pin; CLAUDE.md is unchanged"}
  ]
}])
```

**Step B — choose pin** (only after Step A is answered):

Render the filtered `evictable_pins` from the CLI output, 4-at-a-time (pagination). The first 3 are candidate evictions; the 4th is always "Show more".

```
AskUserQuestion(questions=[{
  header: "Pin index",
  question: "Which pin to evict? (<stale|non-stale> pins, page N of M)",
  options: [
    {label: "Pin 0 — <heading>", description: "<chars> chars, <age indicator>"},
    {label: "Pin 1 — <heading>", description: "<chars> chars, <age indicator>"},
    {label: "Pin 2 — <heading>", description: "<chars> chars, <age indicator>"},
    {label: "Show more",         description: "Next page of eviction candidates"}
  ]
}])
```

On eviction:
- Remove the pin block AND its `<!-- pinned: ... -->` comment line entirely.
- Then re-run Step 1 with the new body. MUST re-check — a stale-pin eviction may leave room; a non-stale eviction MUST still respect the size cap.

On "Cancel add": report "Pin add cancelled; CLAUDE.md unchanged." and exit.

### Size refusal (`violation.kind == "size"`)

The new pin body exceeds 1500 chars. Prompt the user:

```
AskUserQuestion(questions=[{
  header: "Size cap",
  question: "New pin is <N> chars (cap: 1500). How to proceed?",
  options: [
    {label: "Compress",         description: "Rewrite content more concisely and retry"},
    {label: "Add override",     description: "Add pin-size-override rationale — use ONLY for verbatim load-bearing content"},
    {label: "Cancel add",       description: "Abandon this pin; CLAUDE.md is unchanged"}
  ]
}])
```

On "Compress": rewrite and re-run Step 1.
On "Add override": re-run Step 1 with `--has-override`. The pin MUST be added with the extended comment form:

```markdown
<!-- pinned: YYYY-MM-DD, pin-size-override: RATIONALE -->
### Entry Title
<verbatim content>
```

Rationale is trimmed whitespace, non-empty, ≤ 120 chars. Strict parser: empty or malformed rationale → treated as no override.

On "Cancel add": report and exit.

## Size Override

Use `pin-size-override` ONLY when the pin body is **verbatim** content whose exact form is load-bearing for downstream LLM readers (canonical dispatch strings, protocol templates, regex literals). Rationale MUST state *why* splitting or compressing would lose correctness — not merely "this is important".

Example (live on CLAUDE.md):
```markdown
<!-- pinned: 2026-04-11, pin-size-override: verbatim dispatch form is load-bearing for LLM readers -->
```

## Pruning Pinned Entries

Run this whenever pin-memory is invoked (both modes). Review each entry in the `## Pinned Context` section.

**Prune when:**
- The entry references files, patterns, or architecture that no longer exists in the codebase
- The entry was pinned for a specific feature or task that has been completed and merged
- The information is now documented elsewhere (CLAUDE.md sections, README, code comments)
- The entry has been superseded by a newer pinned entry covering the same topic

**Keep when:**
- The entry is old but still accurate and actionable (age alone is not a reason to prune)
- The entry documents a gotcha or pitfall that could recur
- You are unsure whether it is still relevant — keep it and flag for user review

**How to prune:**
- Remove the entry AND its `<!-- pinned: YYYY-MM-DD -->` tag entirely
- If unsure about a specific entry, ask the user via `AskUserQuestion` before removing
- Report what was pruned: "Pruned N stale entries: [titles]"
