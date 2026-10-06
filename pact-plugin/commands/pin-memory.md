---
description: Pin important context permanently to CLAUDE.md, or review the session for pin-worthy context
argument-hint: "[optional: e.g., critical gotcha, key architectural decision]"
---

## Mode

- **With arguments** (`/PACT:pin-memory <content>`): Pin the specified content.
- **Without arguments** (`/PACT:pin-memory`): Review the session for pin-worthy context and pin what matters.

## Caps (enforced mechanically)

Cap violations are denied by `hooks/pin_caps_gate.py` when the `Edit`/`Write` tool call lands. You do NOT need to invoke a CLI check before adding — the hook is authoritative.

- **Count**: 12 pins maximum. Every `### ` line outside a fenced code block, including one in another pin's body, is a pin.
- **Size**: Each pin body is at most 1500 characters unless the pin carries a valid `pin-size-override`. A pin that is already over 1500 characters must not grow. The `<!-- pinned: ... -->` and `<!-- STALE: ... -->` markers do not count toward the size; a fenced code example inside the body does, and trailing spaces and tabs do not.
- **Override**: verbatim load-bearing content MAY carry a `pin-size-override` rationale (≤ 120 chars, single line) — see [Size Override](#size-override). The hook validates the rationale in-band.

At the cap, renaming, moving, reordering or rewriting pins is allowed. The count cap refuses only a change that adds a pin; the size cap applies to each pin the change adds or grows.

Make every pin change with `Edit`. Do not change pins through `Bash`.

If the hook allows an edit with a note that it could not locate the Pinned section, could not read or check the file, stopped the check early, or is not checking pin caps, the cap was NOT enforced for that change. Tell the curator, and repair the file. When the note names a line, the fix is there: usually a code fence or an HTML block left open above or inside the Pinned Context section.

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
2. Locate or create a `## Pinned Context` section (place it before `## Working Memory`). If the file has a `<!-- PACT_MEMORY_START -->` … `<!-- PACT_MEMORY_END -->` block, the section MUST be inside it.
3. **If the file carries a `<!-- PACT_MEMORY_PINNED_END -->` line, the new entry MUST go ABOVE that line.** The pinned region ends there, so a pin placed below it is outside the Pinned Context section. Insert immediately before that line, after the last existing entry. If the file has no such line, append at the end of the section as usual.
4. Add the new entry with a date tag. **Make this write with the `Edit` tool. Do NOT use the `Write` tool.** Step 1 gave you the full file, so a `Write` call looks like the short route to the new content. After a `Write`, a file that had CRLF line endings has LF line endings. The curator then sees a change to a file they did not edit. An `Edit` keeps the line endings of the file. Use the example below:
   ```markdown
   <!-- pinned: YYYY-MM-DD -->
   ### Entry Title
   Content here (~5-10 lines max)
   ```
5. **Commit — then confirm `CLAUDE.md` is actually in the resulting commit.** Where a project git-ignores `CLAUDE.md` (a bare `CLAUDE.md` line in `.gitignore` matches at any depth), the failure is loud in the obvious cases and silent in the one that matters. An explicit `git add CLAUDE.md` errors, and a commit whose only change is `CLAUDE.md` reports "nothing to commit" — both non-zero, both visible. But a blanket `git commit -a` / `-am` that also picks up other changed files **succeeds, exits 0, and simply omits `CLAUDE.md`**. Checking the commit's exit status instead of its contents is what turns that into a pin reported as durable while it has no git backing at all. So verify the file is in the commit, not that the commit succeeded. If it is not, report the pin as **applied to the working file but not version-controlled**, so the curator knows it lives only on this machine. NEVER use `git add -f` to force it past the ignore rule — the rule is the project's decision, not an obstacle.

### Without arguments — session review

1. Read existing CLAUDE.md.
2. Review the session for pin-worthy context. Apply the "When to Pin" criteria above.
3. For each pin-worthy entry, add it as in "Adding a pin." If nothing is pin-worthy, report "No new context to pin."
4. Commit any changes — and apply the same check as **Adding a pin** step 5: confirm `CLAUDE.md` is in the resulting commit rather than trusting the commit's exit status, and report any pin that did not make it in as applied to the working file but not version-controlled.

## Refusal flow (hook-denied edits)

If the pin_caps_gate hook denies the `Edit`/`Write`, the deny reason tells you which cap fired. You MUST NOT bypass.

- **Pin count cap reached (N/12)**: Run `/PACT:prune-memory` to demote an existing pin to long-term memory, then retry the add. Demotion archives the pin to pact-memory before removing it, so the content is preserved rather than lost.
  - A teammate, or a subagent in a PACT team session, gets "Do not change CLAUDE.md yourself, by any route; tell the team-lead." instead. Do not write `CLAUDE.md` by any route, not only an `Edit` or a `Write`: do NOT run `/PACT:prune-memory`, and do NOT retry the edit. Send the pin's text to the team-lead: with `SendMessage`, or in your final report if you are a subagent.
  - As the team-lead, when you receive a pin that way, free a slot with the user via `/PACT:prune-memory`, then add the pin yourself.
- **New pin body is N chars (cap: 1500)**: Compress the body, or add a `pin-size-override` rationale if the content is verbatim load-bearing.
- **A `### ` line in a pin body**: That line is its own pin and counts toward the count cap. For structure inside a body, use `#### `, bold, or a fenced code example. A fenced line is not a pin, but it counts toward the body size.
- **Override rationale malformed**: The rationale is empty, exceeds 120 chars, or contains a line terminator (`\n`, `\r`, or a Unicode line separator). Fix the rationale and retry.

## Size Override

Use `pin-size-override` ONLY when the pin body is **verbatim** content whose exact form is load-bearing for downstream LLM readers (canonical dispatch strings, protocol templates, regex literals). Rationale MUST state *why* splitting or compressing would lose correctness — not merely "this is important". Rationale is single-line, ≤ 120 chars.

Example (live on CLAUDE.md):
```markdown
<!-- pinned: 2026-04-11, pin-size-override: verbatim dispatch form is load-bearing for LLM readers -->
```

## See also

- `/PACT:prune-memory` — interactive pruning of existing pins (paginated `AskUserQuestion` over evictable entries).
