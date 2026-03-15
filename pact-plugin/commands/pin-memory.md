---
description: Pin important context permanently to CLAUDE.md, or review the session for pin-worthy context
argument-hint: "[optional: e.g., critical gotcha, key architectural decision]"
---

## Mode

- **With arguments** (`/PACT:pin-memory <content>`): Pin the specified content directly.
- **Without arguments** (`/PACT:pin-memory`): Review the session for pin-worthy context, pin what matters, and prune stale entries.

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

### With Arguments (targeted pin)

1. Read existing CLAUDE.md
2. Locate or create a `## Pinned Context` section (place it before `## Working Memory`)
3. Add the new entry with a date tag:
   ```markdown
   <!-- pinned: YYYY-MM-DD -->
   ### Entry Title
   Content here (~5-10 lines max)
   ```
4. Prune any stale pinned entries while you're there
5. Commit changes

### Without Arguments (session review)

1. Read existing CLAUDE.md
2. Review the session for pin-worthy context — scan for significant decisions, architectural changes, gotchas discovered, or patterns established. Apply the "When to Pin" criteria above.
3. If pin-worthy content is found, add each entry to the `## Pinned Context` section with date tags
4. If nothing is pin-worthy, report "No new context to pin."
5. Run the pruning process (see below)
6. Commit changes if any were made

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
