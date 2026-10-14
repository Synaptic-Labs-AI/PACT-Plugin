#!/bin/bash
# status.sh — Quick status check for the PACT Meta-Orchestrator
#
# Reports:
#   - launchd service state
#   - Claude process info (PID, uptime)
#   - Active sessions from the registry
#   - Recent log entries
#
# Usage: ./status.sh

set -euo pipefail

# --- Configuration ---

SERVICE_LABEL="com.pact.meta-orchestrator"
PLIST_NAME="${SERVICE_LABEL}.plist"
WORK_DIR="${HOME}/.claude/meta-orchestrator"
LOG_FILE="${WORK_DIR}/logs/meta-orchestrator.log"
SESSION_DIR="${HOME}/.claude/pact-telegram/coordinator/sessions"

echo "=== PACT Meta-Orchestrator Status ==="
echo ""

# --- Service Status ---

echo "--- Service ---"

if launchctl list "${SERVICE_LABEL}" >/dev/null 2>&1; then
    # Parse launchctl list output for PID and exit status
    LAUNCHCTL_INFO=$(launchctl list "${SERVICE_LABEL}" 2>/dev/null)
    PID=$(echo "${LAUNCHCTL_INFO}" | awk '{print $1}')
    LAST_EXIT=$(echo "${LAUNCHCTL_INFO}" | awk '{print $2}')

    echo "Loaded:     yes"

    if [ -n "${PID}" ] && [ "${PID}" != "-" ]; then
        echo "Running:    yes (PID: ${PID})"

        # Get process uptime using ps
        # etime format: [[dd-]hh:]mm:ss
        UPTIME=$(ps -p "${PID}" -o etime= 2>/dev/null | xargs)
        if [ -n "${UPTIME}" ]; then
            echo "Uptime:     ${UPTIME}"
        fi

        # Memory usage
        MEM=$(ps -p "${PID}" -o rss= 2>/dev/null | xargs)
        if [ -n "${MEM}" ]; then
            MEM_MB=$(( MEM / 1024 ))
            echo "Memory:     ${MEM_MB} MB"
        fi
    else
        echo "Running:    no (process not started)"
        if [ "${LAST_EXIT}" != "0" ] && [ "${LAST_EXIT}" != "-" ]; then
            echo "Last exit:  ${LAST_EXIT}"
        fi
    fi
else
    echo "Loaded:     no"
    echo "Running:    no"
    echo ""
    echo "The service is not installed. Run ./install.sh to set it up."
fi

# --- Active Sessions ---

echo ""
echo "--- Sessions ---"

if [ -d "${SESSION_DIR}" ]; then
    SESSION_FILES=$(find "${SESSION_DIR}" -name "*.json" -type f 2>/dev/null)
    if [ -n "${SESSION_FILES}" ]; then
        ACTIVE=0
        STALE=0

        while IFS= read -r session_file; do
            [ -z "${session_file}" ] && continue

            # Parse session JSON — use python for reliable JSON parsing if available,
            # fall back to basic grep/sed
            if command -v python3 >/dev/null 2>&1; then
                SESSION_PID=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('pid','?'))" "${session_file}" 2>/dev/null || echo "?")
                SESSION_PROJECT=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('project','unknown'))" "${session_file}" 2>/dev/null || echo "unknown")
            else
                # Fallback: basic parsing (less robust but works for simple JSON)
                SESSION_PID=$(grep -o '"pid"[[:space:]]*:[[:space:]]*[0-9]*' "${session_file}" | grep -o '[0-9]*' || echo "?")
                SESSION_PROJECT=$(grep -o '"project"[[:space:]]*:[[:space:]]*"[^"]*"' "${session_file}" | sed 's/.*"project"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/' || echo "unknown")
            fi

            # Check if PID is alive
            if [ "${SESSION_PID}" != "?" ] && kill -0 "${SESSION_PID}" 2>/dev/null; then
                UPTIME=$(ps -p "${SESSION_PID}" -o etime= 2>/dev/null | xargs)
                echo "  [ACTIVE] ${SESSION_PROJECT} (PID: ${SESSION_PID}, uptime: ${UPTIME:-unknown})"
                ACTIVE=$((ACTIVE + 1))
            else
                echo "  [STALE]  ${SESSION_PROJECT} (PID: ${SESSION_PID} — not running)"
                STALE=$((STALE + 1))
            fi
        done <<< "${SESSION_FILES}"

        echo ""
        echo "Active: ${ACTIVE}  |  Stale: ${STALE}"
        if [ ${STALE} -gt 0 ]; then
            echo "NOTE: Stale sessions have dead PIDs. The meta-orchestrator cleans these up periodically."
        fi
    else
        echo "No sessions registered."
    fi
else
    echo "Session registry not found at: ${SESSION_DIR}"
    echo "This is normal if no sessions have been started yet."
fi

# --- Recent Logs ---

echo ""
echo "--- Recent Logs ---"

if [ -f "${LOG_FILE}" ]; then
    LOG_SIZE=$(stat -f%z "${LOG_FILE}" 2>/dev/null || echo "?")
    echo "(${LOG_FILE} — ${LOG_SIZE} bytes)"
    echo ""
    tail -15 "${LOG_FILE}"
else
    echo "No log file found at: ${LOG_FILE}"
    echo ""
    # Check fallback logs
    if [ -f "/tmp/${SERVICE_LABEL}.stderr.log" ]; then
        echo "Fallback stderr log:"
        tail -10 "/tmp/${SERVICE_LABEL}.stderr.log"
    fi
fi

echo ""
echo "=== End Status ==="
