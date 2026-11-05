#!/bin/bash
# uninstall.sh — Remove the PACT Meta-Orchestrator launchd service
#
# This script:
#   1. Unloads the launchd service (stops the process)
#   2. Removes the plist from ~/Library/LaunchAgents/
#   3. Optionally removes all data with --purge
#
# Usage:
#   ./uninstall.sh          # Remove service, keep data
#   ./uninstall.sh --purge  # Remove service AND all data/logs

set -euo pipefail

# --- Configuration ---

SERVICE_LABEL="com.pact.meta-orchestrator"
PLIST_NAME="${SERVICE_LABEL}.plist"
WORK_DIR="${HOME}/.claude/meta-orchestrator"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"

PURGE=false
if [ "${1:-}" = "--purge" ]; then
    PURGE=true
fi

echo "=== PACT Meta-Orchestrator Uninstaller ==="
echo ""

# --- Unload Service ---

if launchctl list "${SERVICE_LABEL}" >/dev/null 2>&1; then
    echo "Unloading service '${SERVICE_LABEL}'..."
    launchctl unload "${PLIST_PATH}" 2>/dev/null || true

    # Verify it stopped
    sleep 1
    if launchctl list "${SERVICE_LABEL}" >/dev/null 2>&1; then
        echo "WARNING: Service still appears loaded. Trying bootstrap remove..."
        launchctl bootout "gui/$(id -u)/${SERVICE_LABEL}" 2>/dev/null || true
    fi
    echo "Service unloaded."
else
    echo "Service '${SERVICE_LABEL}' is not currently loaded."
fi

# --- Remove Plist ---

if [ -f "${PLIST_PATH}" ]; then
    echo "Removing plist: ${PLIST_PATH}"
    rm "${PLIST_PATH}"
else
    echo "Plist not found at ${PLIST_PATH} (already removed or never installed)."
fi

# --- Remove Fallback Logs ---

for log_file in "/tmp/${SERVICE_LABEL}.stdout.log" "/tmp/${SERVICE_LABEL}.stderr.log"; do
    if [ -f "${log_file}" ]; then
        echo "Removing fallback log: ${log_file}"
        rm "${log_file}"
    fi
done

# --- Purge Data (Optional) ---

if [ "${PURGE}" = true ]; then
    echo ""
    read -r -p "This will delete all data and logs at ${WORK_DIR}. Continue? [y/N] " response
    if [[ ! "${response}" =~ ^[Yy]$ ]]; then
        echo "Purge cancelled. Data preserved."
        PURGE=false
    else
        echo "Purging all data..."
        if [ -d "${WORK_DIR}" ]; then
            echo "Removing: ${WORK_DIR}"
            rm -rf "${WORK_DIR}"
            echo "All data and logs removed."
        else
            echo "Work directory not found at ${WORK_DIR} (already removed)."
        fi
    fi
else
    echo ""
    echo "Data preserved at: ${WORK_DIR}"
    echo "To remove all data and logs, run: ./uninstall.sh --purge"
fi

# --- Summary ---

echo ""
echo "=== Uninstall Complete ==="
echo ""
echo "Service '${SERVICE_LABEL}' has been removed."
if [ "${PURGE}" = false ]; then
    echo "Logs and data remain at: ${WORK_DIR}"
fi
