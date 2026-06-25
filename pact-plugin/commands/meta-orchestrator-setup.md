---
description: Set up the PACT Meta-Orchestrator — a persistent Claude Code session that acts as a conversational Telegram concierge
argument-hint:
---
# Meta-Orchestrator Setup

Walk the user through installing the PACT Meta-Orchestrator as a macOS launchd service. This is an interactive setup — use AskUserQuestion at each step and Bash for automation.

**Prerequisites**: The pact-telegram bridge must be configured first (`/PACT:telegram-setup`).

**Security**: NEVER echo, log, or display bot tokens or API keys in any tool output.

---

## Step 1: Check Prerequisites

### 1a: Check Telegram bridge is configured

```bash
test -f ~/.claude/pact-telegram/.env && echo "CONFIGURED" || echo "MISSING"
```

- If **MISSING**: Tell the user "The pact-telegram bridge must be configured first. Run `/PACT:telegram-setup` to set it up, then come back here." Stop.
- If **CONFIGURED**: Continue.

### 1b: Check for existing installation

```bash
launchctl list 2>/dev/null | grep -q "com.pact.meta-orchestrator" && echo "INSTALLED" || echo "NOT_INSTALLED"
```

- If **INSTALLED**: Tell the user "The Meta-Orchestrator is already installed." Use AskUserQuestion to ask: "Would you like to (A) reinstall from scratch, (B) check status, or (C) uninstall?"
  - A: Continue to Step 2 (unload first, then reinstall)
  - B: Run `~/.claude/meta-orchestrator/status.sh` and display the output. Stop.
  - C: Run `~/.claude/meta-orchestrator/uninstall.sh` and confirm. Stop.
- If **NOT_INSTALLED**: Continue to Step 2.

### 1c: Check platform

```bash
uname -s
```

- If **not Darwin**: Tell the user "The Meta-Orchestrator currently supports macOS only (uses launchd). Linux systemd support is planned." Stop.

## Step 2: Check Claude Code CLI

Verify the `claude` CLI is available:

```bash
which claude 2>/dev/null || echo "NOT_FOUND"
```

- If **NOT_FOUND**: Tell the user "The `claude` CLI was not found in PATH. Please install Claude Code first: `npm install -g @anthropic-ai/claude-code`" Stop.
- If found: Note the path for the launch script.

## Step 3: Configure Channels Mode

Tell the user:

> The Meta-Orchestrator uses **Claude Code Channels** to receive your Telegram messages as a two-way conversation. This requires the `--dangerously-load-development-channels` flag (the Channels feature is in research preview).
>
> The orchestrator will also run with `--dangerously-skip-permissions` so it can spawn new sessions and manage files without manual approval.
>
> **Security note**: The meta-orchestrator only accepts messages from your authorized Telegram chat (configured in your pact-telegram bridge). No external access.

Use AskUserQuestion: "Proceed with installation? (Yes / No)"

- If **No**: Stop — tell user setup cancelled.
- If **Yes**: Continue.

## Step 4: Optional Sender Allowlist

Tell the user:

> **Optional security**: You can restrict which Telegram users can send messages to the Meta-Orchestrator. This is useful if your Telegram chat is a group.
>
> Enter your Telegram user ID to restrict to only you, or leave blank to allow all messages from the authorized chat.
>
> (To find your user ID, send `/myid` to @userinfobot on Telegram)

Use AskUserQuestion to collect the user ID (or empty to skip).

If provided, update `~/.claude/pact-telegram/.env` to add:
```
PACT_TELEGRAM_ALLOWED_SENDERS=<user_id>
```

## Step 5: Install

Locate the meta-orchestrator files within the PACT plugin:

```bash
PLUGIN_ROOT=$(find ~/.claude/plugins -path "*/pact-plugin/meta-orchestrator" -type d 2>/dev/null | head -1)
echo "Found: $PLUGIN_ROOT"
```

If not found, check the marketplace cache:
```bash
PLUGIN_ROOT=$(find ~/.claude/plugins/marketplaces -path "*/pact-plugin/meta-orchestrator" -type d 2>/dev/null | head -1)
echo "Found: $PLUGIN_ROOT"
```

Run the install script:
```bash
bash "$PLUGIN_ROOT/install.sh"
```

Check the exit code:
- If **0**: Continue to Step 6.
- If **non-zero**: Show the error output, troubleshoot, and offer to retry.

## Step 6: Verify

Wait 5 seconds for the service to start, then check status:

```bash
sleep 5
bash ~/.claude/meta-orchestrator/status.sh
```

Also verify the Telegram bot can send a message:

```bash
# Read config
source ~/.claude/pact-telegram/.env
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -H "Content-Type: application/json" \
  -d "{\"chat_id\": \"${TELEGRAM_CHAT_ID}\", \"text\": \"PACT Meta-Orchestrator is online! Send me a message to get started.\", \"parse_mode\": \"Markdown\"}"
```

Ask the user: "Did you receive the message in Telegram?" (AskUserQuestion)

- If **yes**: Continue to Step 7.
- If **no**: Troubleshoot — check logs at `~/.claude/meta-orchestrator/logs/`, verify service is running.

## Step 7: Finalize

Tell the user:

> **Meta-Orchestrator installed!**
>
> Your always-on Telegram concierge is now running. Here's what you can do:
>
> **Talk to it naturally on Telegram:**
> - "What sessions are running?" — status report
> - "Start a new project for X at ~/Projects/my-app" — spawns a new Claude Code session
> - "Stop the landing page project" — gracefully stops a session
> - Just chat — it's Claude, so it can answer questions too
>
> **Management commands:**
> - Check status: `~/.claude/meta-orchestrator/status.sh`
> - View logs: `tail -f ~/.claude/meta-orchestrator/logs/meta-orchestrator.log`
> - Stop: `launchctl unload ~/Library/LaunchAgents/com.pact.meta-orchestrator.plist`
> - Start: `launchctl load ~/Library/LaunchAgents/com.pact.meta-orchestrator.plist`
> - Uninstall: `~/.claude/meta-orchestrator/uninstall.sh`
>
> **How it works with your other sessions:**
> - Messages you send (not replies) go to the Meta-Orchestrator
> - Replies to specific session notifications still route to that session
> - One bot, one chat, all sessions
>
> **Recommended alias** (add to your shell profile):
> ```bash
> alias cc='claude --dangerously-skip-permissions --dangerously-load-development-channels server:pact-telegram'
> ```
> This starts new project sessions with Channels + permissions pre-configured.
