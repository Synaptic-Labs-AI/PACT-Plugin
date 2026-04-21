---
description: Pin important context permanently to CLAUDE.md, or review the session for pin-worthy context
argument-hint: "[optional: e.g., critical gotcha, key architectural decision]"
---

## Mode

- **With arguments** (`/PACT:pin-memory <content>`): Pin the specified content.
- **Without arguments** (`/PACT:pin-memory`): Review the session for pin-worthy context and pin what matters.

## Caps (enforced mechanically)

Cap violations are denied by `hooks/pin_caps_gate.py` when the Edit/Write tool call lands. You do NOT need to invoke a CLI check before adding — the hook is authoritative.

- **Count**: 12 pins maximum.
- **Size**: 1500 characters per pin body (excludes `<!-- pinned: ... -->` and `<!-- STALE: ... -->` auto-markers).
- **Override**: verbatim load-bearing content MAY carry a `pin-size-override` rationale (≤ 120 chars, single line) — see [Size Override](#size-override). The hook validates the rationale in-band.

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

**Target file**: The project CLAUDE.md may be at either `$CLAUDE_PROJECT_DIR/.claude/CLAUDE.md` (preferred) or `$CLAUDE_PROJECT_DIR/CLAUDE.md` (legacy). Use `.claude/CLAUDE.md` if it exists, otherwise `./CLAUDE.md`. If neither exists, create at `.claude/CLAUDE.md`.

### Adding a pin

1. Read existing CLAUDE.md.
2. Locate or create a `## Pinned Context` section (place it before `## Working Memory`).
3. Add the new entry with a date tag:
   ```markdown
   <!-- pinned: YYYY-MM-DD -->
   ### Entry Title
   Content here (~5-10 lines max)
   ```
4. Commit.

### Without arguments — session review

1. Read existing CLAUDE.md.
2. Review the session for pin-worthy context. Apply the "When to Pin" criteria above.
3. For each pin-worthy entry, add it as in "Adding a pin." If nothing is pin-worthy, report "No new context to pin."
4. Commit changes if any were made.

## Refusal flow (hook-denied edits)

If the pin_caps_gate hook denies the Edit/Write, the deny reason tells you which cap fired. You MUST NOT bypass.

- **Pin count cap reached (12/12)**: Run `/PACT:prune-memory` to evict an existing pin, then retry the add.
- **New pin body is N chars (cap: 1500)**: Compress the body, or add a `pin-size-override` rationale if the content is verbatim load-bearing.
- **Embedded pin structure in body**: Your new pin body contains a `### ` heading, which would be counted as an additional pin on reload. Use `#### ` or bold for in-body structure instead.
- **Override rationale malformed**: The rationale is empty, exceeds 120 chars, or contains a line terminator (`\n`, `\r`, or a Unicode line separator). Fix the rationale and retry.

## Size Override

Use `pin-size-override` ONLY when the pin body is **verbatim** content whose exact form is load-bearing for downstream LLM readers (canonical dispatch strings, protocol templates, regex literals). Rationale MUST state *why* splitting or compressing would lose correctness — not merely "this is important". Rationale is single-line, ≤ 120 chars.

Example (live on CLAUDE.md):
```markdown
<!-- pinned: 2026-04-11, pin-size-override: verbatim dispatch form is load-bearing for LLM readers -->
```

## See also

- `/PACT:prune-memory` — interactive pruning of existing pins (paginated AskUserQuestion over evictable entries).
