---
name: telegram-guide
description: Telegram notification integration guide for PACT. Use when telegram_notify is available in your tools — covers when to notify, when to ask, when to check replies, and multi-session behavior.
---

# Telegram Notifications

Telegram is an **optional** add-on. Not all PACT users have it installed. **Before using any telegram tool, check if it exists in your available tools.** If `telegram_notify` is not listed, do NOT attempt to call it — skip all Telegram functionality silently with no errors or warnings to the user.

**How to check**: If `telegram_notify` appears in your tool list, the bridge is active. If not, ignore this entire section.

**Available tools** (only when bridge is active):
| Tool | Purpose | Blocking? |
|------|---------|-----------|
| `telegram_notify` | Send one-way notification (completions, deployments, alerts) | No |
| `telegram_ask` | Ask a blocking question with optional buttons; supports text/voice replies | Yes |
| `telegram_check_replies` | Poll for queued replies to notifications | No |
| `telegram_status` | Health check (connection, mode, queue depth) | No |

**When to notify** (target ~3-5 per session):
- Task or phase completions
- Blockers found or algedonic signals
- PR ready for review or merged
- Deployments pushed

**When to use `telegram_ask`**:
- Blocking decisions where user may be away from terminal
- Scope clarifications that can't proceed without input

**When to check replies** (`telegram_check_replies`):
- Between tasks or phases — check if user reacted to any notification
- After sending important notifications — user may reply with corrections or new instructions
- Replies include context snippet of the original notification

**Multi-session behavior**:
- Messages are prefixed with `[ProjectName]` for session identification
- Multiple sessions coordinate via file-based polling — replies route to the correct session
