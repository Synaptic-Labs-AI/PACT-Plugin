#!/bin/bash
# launch.sh — Startup script for the PACT Meta-Orchestrator
#
# This script is invoked by launchd to start a persistent Claude Code session
# that acts as a conversational router for Telegram messages across all projects.
#
# It handles:
#   - Environment setup (PATH for claude CLI, node, etc.)
#   - Working directory creation
#   - CLAUDE.md deployment to the working dir
#   - Log rotation (10MB threshold)
#   - Graceful shutdown via SIGTERM
#
# Usage: Called by launchd via com.pact.meta-orchestrator.plist
#        Can also be run manually for debugging: ./launch.sh

set -euo pipefail

# Restrict file permissions for any files we create (logs may contain session content)
umask 077

# --- Configuration ---

WORK_DIR="${HOME}/.claude/meta-orchestrator"
LOG_DIR="${WORK_DIR}/logs"
LOG_FILE="${LOG_DIR}/meta-orchestrator.log"
MAX_LOG_SIZE=$((10 * 1024 * 1024))  # 10MB in bytes

# Source script directory — where CLAUDE.md and other assets live
# Resolve symlinks to find the actual script location
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- PATH Setup ---
# launchd starts with a minimal PATH. We need to ensure claude and node are findable.
# Common install locations for Homebrew (Intel + Apple Silicon), nvm, volta, fnm, and Claude CLI.

add_to_path_if_exists() {
    if [ -d "$1" ] && [[ ":${PATH}:" != *":$1:"* ]]; then
        export PATH="$1:${PATH}"
    fi
}

# Homebrew
add_to_path_if_exists "/opt/homebrew/bin"
add_to_path_if_exists "/usr/local/bin"

# Node version managers
if [ -d "${HOME}/.nvm/versions/node" ]; then
    NVM_NODE=$(ls "${HOME}/.nvm/versions/node/" 2>/dev/null | sort -V | tail -1)
    [ -n "${NVM_NODE}" ] && add_to_path_if_exists "${HOME}/.nvm/versions/node/${NVM_NODE}/bin"
fi
add_to_path_if_exists "${HOME}/.volta/bin"
add_to_path_if_exists "${HOME}/.fnm/aliases/default/bin"

# Claude CLI (npm global installs)
add_to_path_if_exists "${HOME}/.npm-global/bin"
add_to_path_if_exists "${HOME}/.local/bin"

# Source nvm if available (some setups require this)
if [ -s "${HOME}/.nvm/nvm.sh" ]; then
    # shellcheck disable=SC1091
    . "${HOME}/.nvm/nvm.sh" --no-use 2>/dev/null || true
    nvm use default 2>/dev/null || true
fi

# --- Verify Dependencies ---

if ! command -v claude >/dev/null 2>&1; then
    echo "ERROR: 'claude' command not found in PATH: ${PATH}" >&2
    echo "Install Claude Code CLI or add its location to this script's PATH setup." >&2
    exit 1
fi

# --- Directory Setup ---

mkdir -p "${WORK_DIR}"
mkdir -p "${LOG_DIR}"

# Deploy CLAUDE.md to the working directory
# This is the meta-orchestrator's instruction file
if [ -f "${SCRIPT_DIR}/CLAUDE.md" ]; then
    cp "${SCRIPT_DIR}/CLAUDE.md" "${WORK_DIR}/CLAUDE.md"
else
    echo "WARNING: CLAUDE.md not found at ${SCRIPT_DIR}/CLAUDE.md" >&2
    echo "The meta-orchestrator will run without instructions." >&2
fi

# --- Log Rotation ---
# Rotate the log file if it exceeds MAX_LOG_SIZE.
# Keeps one rotated backup (.1) to avoid unbounded disk use.

rotate_log() {
    if [ -f "${LOG_FILE}" ]; then
        local size
        size=$(stat -f%z "${LOG_FILE}" 2>/dev/null || echo 0)
        if [ "${size}" -gt "${MAX_LOG_SIZE}" ]; then
            mv "${LOG_FILE}" "${LOG_FILE}.1"
            echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Log rotated (was ${size} bytes)" > "${LOG_FILE}"
        fi
    fi
}

rotate_log

# --- Graceful Shutdown ---
# Claude Code handles SIGTERM for graceful shutdown.
# We trap it here to log the event and forward to the child process.

CLAUDE_PID=""

cleanup() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Received shutdown signal" >> "${LOG_FILE}"
    if [ -n "${CLAUDE_PID}" ] && kill -0 "${CLAUDE_PID}" 2>/dev/null; then
        echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Sending SIGTERM to claude (PID: ${CLAUDE_PID})" >> "${LOG_FILE}"
        kill -TERM "${CLAUDE_PID}" 2>/dev/null || true
        # Wait up to 10 seconds for graceful shutdown
        local count=0
        while kill -0 "${CLAUDE_PID}" 2>/dev/null && [ ${count} -lt 10 ]; do
            sleep 1
            count=$((count + 1))
        done
        # Force kill if still running
        if kill -0 "${CLAUDE_PID}" 2>/dev/null; then
            echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Force killing claude (PID: ${CLAUDE_PID})" >> "${LOG_FILE}"
            kill -9 "${CLAUDE_PID}" 2>/dev/null || true
        fi
    fi
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Meta-orchestrator shutdown complete" >> "${LOG_FILE}"
    exit 0
}

trap cleanup SIGTERM SIGINT SIGHUP

# --- Launch Claude ---

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Starting PACT Meta-Orchestrator" >> "${LOG_FILE}"
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Working directory: ${WORK_DIR}" >> "${LOG_FILE}"
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Claude path: $(which claude)" >> "${LOG_FILE}"
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] PATH: ${PATH}" >> "${LOG_FILE}"

cd "${WORK_DIR}"

# Launch claude as a background process so we can capture its PID for signal handling.
# --dangerously-skip-permissions: Required for unattended operation
# --dangerously-load-development-channels server:pact-telegram: Enables Telegram channel
# --yes: Auto-accepts any prompts
# -p: Initial prompt to bootstrap the session
claude --dangerously-skip-permissions \
       --dangerously-load-development-channels server:pact-telegram \
       --yes \
       -p "You are the PACT Meta-Orchestrator. Read CLAUDE.md for your instructions. Start by checking the session registry and reporting your status." \
       >> "${LOG_FILE}" 2>&1 &

CLAUDE_PID=$!

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Claude started with PID: ${CLAUDE_PID}" >> "${LOG_FILE}"

# Wait for the claude process. Using 'wait' allows signal handling to work properly.
# If claude exits on its own, launchd's KeepAlive will restart this script.
wait "${CLAUDE_PID}"
EXIT_CODE=$?

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] Claude exited with code: ${EXIT_CODE}" >> "${LOG_FILE}"
exit "${EXIT_CODE}"
