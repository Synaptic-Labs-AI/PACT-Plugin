---
description: Set up Telegram notifications for PACT
argument-hint:
---
# pact-telegram Setup

Walk the user through configuring the pact-telegram bridge. This is an interactive setup -- use AskUserQuestion at each step and Bash for automation.

**Security**: NEVER echo, log, or display bot tokens or API keys in any tool output. Store values in variables only. All curl commands must use `-s` (silent mode).

---

## Step 1: Check Existing Configuration

Check if `~/.claude/pact-telegram/.env` already exists:

```bash
test -f ~/.claude/pact-telegram/.env && echo "EXISTS" || echo "MISSING"
```

- If **EXISTS**: Tell the user "pact-telegram is already configured." Use AskUserQuestion to ask: "Would you like to (A) reconfigure from scratch, (B) test the existing setup, or (C) cancel?"
  - A: Continue to Step 2 (will overwrite existing config)
  - B: Skip to Step 8 (send test notification)
  - C: Stop -- tell user setup cancelled
- If **MISSING**: Continue to Step 2.

## Step 2: Create a Telegram Bot

1. Tell the user: "Open Telegram and message **@BotFather**. Send `/newbot`, follow the prompts to name your bot, then paste the **bot token** here. It looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz_0123456`."
2. Use AskUserQuestion to collect the bot token.
3. Store the token -- do NOT echo or log it.

## Step 3: Validate Token Format

Validate the token matches the pattern `\d+:[A-Za-z0-9_-]{35}`:

```bash
echo "$TOKEN" | grep -qE '^\d+:[A-Za-z0-9_-]{35}$'
```

- If **valid**: Continue to Step 4.
- If **invalid**: Tell the user the format looks wrong and ask them to paste it again (AskUserQuestion). Retry up to 2 more times, then give up with an error message.

## Step 4: Detect Chat ID

1. Tell the user: "Now send `/start` (or any message) to your new bot in Telegram. I will detect your chat ID automatically."
2. Use AskUserQuestion to confirm the user has sent the message.
3. Call the Telegram getUpdates API:
   ```bash
   curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates"
   ```
4. Parse the JSON response to extract `result[0].message.chat.id`.
5. If no updates found, ask the user to send another message and retry (up to 3 attempts with a 3-second wait between each).
6. Once detected, show the chat ID and ask the user to confirm: "Detected chat ID: **{chat_id}**. Is this correct?" (AskUserQuestion)

## Step 5: Optional Voice Transcription

1. Tell the user: "**Optional**: To enable voice note transcription, paste your OpenAI API key. This uses the Whisper API (~$0.006/min). Leave blank to skip."
2. Use AskUserQuestion to collect the key (or empty to skip).
3. If provided, validate it starts with `sk-`. If invalid format, warn and offer to re-enter or skip.
4. Store the key if provided -- do NOT echo or log it.

## Step 6: Write Configuration

Create the config file with secure permissions:

1. Create directory:
   ```bash
   mkdir -p ~/.claude/pact-telegram
   ```

2. Write `~/.claude/pact-telegram/.env` with the collected values:
   ```
   TELEGRAM_BOT_TOKEN=<token from step 2>
   TELEGRAM_CHAT_ID=<chat_id from step 4>
   OPENAI_API_KEY=<key from step 5, or omit this line entirely if skipped>
   PACT_TELEGRAM_MODE=passive
   ```

3. Set permissions:
   ```bash
   chmod 600 ~/.claude/pact-telegram/.env
   ```

4. Verify the file is NOT inside a git repository:
   ```bash
   git -C ~/.claude/pact-telegram rev-parse 2>/dev/null
   ```
   If exit code is 0 (inside a git repo), warn: "Your pact-telegram config directory is inside a git repository. Credentials could be accidentally committed. Consider moving it or adding it to .gitignore."

## Step 7: Install Python Dependencies

Install the MCP server dependencies:

```bash
python3 -c "
import sys; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}')
from telegram.deps import check_dependencies, install_dependencies
missing = check_dependencies()
if not missing:
    print('All dependencies already installed.')
    sys.exit(0)
print(f'Installing missing packages: {missing}')
ok, msg = install_dependencies(quiet=True)
print(msg)
sys.exit(0 if ok else 1)
"
```

- If installation fails, show the error and suggest manual installation: `pip install mcp httpx`

## Step 8: Register MCP Server

Register the pact-telegram MCP server so it starts automatically on every Claude Code session.

1. Check if the server is already registered:
   ```bash
   claude mcp list 2>/dev/null | grep -q "pact-telegram" && echo "REGISTERED" || echo "NOT_REGISTERED"
   ```

2. If **NOT_REGISTERED**, detect the plugin cache path and register:
   ```bash
   # Detect plugin cache path
   if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
     PLUGIN_CACHE_PATH="$(dirname "$CLAUDE_PLUGIN_ROOT")"
   else
     PLUGIN_CACHE_PATH="$(ls -d ~/.claude/plugins/cache/pact-marketplace/PACT/*/telegram 2>/dev/null | sort -V | tail -1 | xargs dirname)"
   fi

   if [ -z "$PLUGIN_CACHE_PATH" ]; then
     echo "ERROR: Could not detect plugin cache path"
   else
     claude mcp add -s user -e "PYTHONPATH=$PLUGIN_CACHE_PATH" pact-telegram -- python3 -m telegram
     echo "MCP server registered successfully"
   fi
   ```

3. If registration fails, tell the user the manual command:
   `claude mcp add -s user -e PYTHONPATH=<path-to-plugin-cache> pact-telegram -- python3 -m telegram`

4. If **REGISTERED**: Tell the user "pact-telegram MCP server is already registered." and continue.

This ensures the MCP server starts on every Claude Code session without relying on `.mcp.json` auto-start.

## Step 9: Send Test Notification

Verify the setup works by sending a test message:

```bash
curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  -H "Content-Type: application/json" \
  -d '{"chat_id": "${CHAT_ID}", "text": "pact-telegram setup complete! You will receive session notifications here.", "parse_mode": "Markdown"}'
```

1. Check the API response for `"ok": true`.
2. Ask the user: "Did you receive the test message in Telegram?" (AskUserQuestion)
3. If **yes**: Continue to Step 9.
4. If **no**: Troubleshoot -- check token, chat ID, internet connectivity. Offer to retry or reconfigure.

## Step 10: Finalize

Tell the user:

> **Setup complete!** The pact-telegram MCP server is registered and will start automatically on every Claude Code session.
>
> - **Passive mode** (default): You will receive a Telegram notification when a Claude Code session ends, summarizing what was accomplished.
> - **Active mode**: The orchestrator can send you questions via Telegram and wait for your reply. Enable by changing `PACT_TELEGRAM_MODE=active` in `~/.claude/pact-telegram/.env`.
> - **MCP tools**: The tools `telegram_notify`, `telegram_ask`, and `telegram_status` are available to agents.
> - **Multi-session support**: Each message includes the project name so you can tell which Claude Code instance sent it. When replying to questions, use swipe-reply (mobile) or click-reply (desktop) to route your answer to the correct session.
>
> **Restart Claude Code now** to activate the pact-telegram MCP server.

Configuration file: `~/.claude/pact-telegram/.env`
To reconfigure later: `/PACT:telegram-setup`
