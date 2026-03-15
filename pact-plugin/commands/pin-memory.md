---
description: Pin important context permanently to CLAUDE.md
argument-hint: [e.g., critical gotcha, key architectural decision]
---
Pin this to CLAUDE.md permanently: $ARGUMENTS

## When to Pin

- **Critical gotchas** that would waste hours if forgotten
- **Key architectural decisions** that explain "why" (not "what")
- **Build/deploy commands** needed every session
- **Non-obvious patterns** unique to this codebase

## When NOT to Pin

- Routine session context (pact-memory handles this automatically)
- Things easily found in code or docs
- Temporary information that will become stale

## Process

1. Read existing CLAUDE.md
2. Locate or create a `## Pinned Context` section (place it before `## Working Memory`)
3. Add the new entry with a date tag for machine identification:
   ```markdown
   ## Pinned Context

   <!-- pinned: 2026-03-15 -->
   ### Entry Title
   Content here (~5-10 lines max)
   ```
4. Review ALL existing pinned entries — prune any that are stale or outdated
5. Keep entries concise (~5-10 lines max each)

**Remember**: Pinned context is for permanent CLAUDE.md context that should survive across all sessions. Working Memory syncs automatically — only pin what's truly permanent and critical.
