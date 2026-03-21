#!/bin/bash
# install.sh — Install the PACT Meta-Orchestrator as a macOS launchd service
#
# This script:
#   1. Creates the working directory (~/.claude/meta-orchestrator/)
#   2. Copies launch.sh, CLAUDE.md to the working directory
#   3. Installs the launchd plist to ~/Library/LaunchAgents/
#   4. Loads the service
#   5. Verifies it started
#
# Usage: ./install.sh
# Uninstall: ./uninstall.sh

set -euo pipefail

# --- Configuration ---

SERVICE_LABEL="com.pact.meta-orchestrator"
PLIST_NAME="${SERVICE_LABEL}.plist"
WORK_DIR="${HOME}/.claude/meta-orchestrator"
LOG_DIR="${WORK_DIR}/logs"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"

# Source directory — where this script and its assets live
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Pre-flight Checks ---

echo "=== PACT Meta-Orchestrator Installer ==="
echo ""

# Check if already installed
if launchctl list "${SERVICE_LABEL}" >/dev/null 2>&1; then
    echo "WARNING: Service '${SERVICE_LABEL}' is already loaded."
    echo "Run ./uninstall.sh first, or use: launchctl unload ${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"
    echo ""
    read -r -p "Unload existing service and reinstall? [y/N] " response
    if [[ "${response}" =~ ^[Yy]$ ]]; then
        echo "Unloading existing service..."
        launchctl unload "${LAUNCH_AGENTS_DIR}/${PLIST_NAME}" 2>/dev/null || true
    else
        echo "Aborting installation."
        exit 1
    fi
fi

# Verify required source files exist
for file in launch.sh CLAUDE.md "${PLIST_NAME}"; do
    if [ ! -f "${SCRIPT_DIR}/${file}" ]; then
        echo "ERROR: Required file not found: ${SCRIPT_DIR}/${file}" >&2
        exit 1
    fi
done

# --- Create Directories ---

echo "Creating directories..."
mkdir -p "${WORK_DIR}"
mkdir -p "${LOG_DIR}"
mkdir -p "${LAUNCH_AGENTS_DIR}"
echo "  ${WORK_DIR}"
echo "  ${LOG_DIR}"

# --- Copy Files ---

echo "Copying files..."

# Copy launch.sh and make it executable
cp "${SCRIPT_DIR}/launch.sh" "${WORK_DIR}/launch.sh"
chmod +x "${WORK_DIR}/launch.sh"
echo "  launch.sh -> ${WORK_DIR}/launch.sh (executable)"

# Copy CLAUDE.md (the meta-orchestrator's instructions)
cp "${SCRIPT_DIR}/CLAUDE.md" "${WORK_DIR}/CLAUDE.md"
echo "  CLAUDE.md -> ${WORK_DIR}/CLAUDE.md"

# Copy plist to LaunchAgents
cp "${SCRIPT_DIR}/${PLIST_NAME}" "${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"
echo "  ${PLIST_NAME} -> ${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"

# --- Load Service ---

echo ""
echo "Loading launchd service..."
launchctl load "${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"

# --- Verify ---

echo ""
echo "Verifying service status..."
sleep 2  # Brief pause to let launchd start the process

if launchctl list "${SERVICE_LABEL}" >/dev/null 2>&1; then
    echo "Service loaded successfully."

    # Check if the process is actually running
    # launchctl list output format: PID Status Label
    PID=$(launchctl list "${SERVICE_LABEL}" 2>/dev/null | awk '{print $1}')
    if [ -n "${PID}" ] && [ "${PID}" != "-" ]; then
        echo "Process running with PID: ${PID}"
    else
        echo "NOTE: Process not yet running (may be starting up)."
        echo "Check logs for details: tail -f ${LOG_DIR}/meta-orchestrator.log"
    fi
else
    echo "WARNING: Service may not have loaded correctly."
    echo "Check: launchctl list | grep pact"
    echo "Logs:  cat /tmp/${SERVICE_LABEL}.stderr.log"
fi

# --- Summary ---

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Service:    ${SERVICE_LABEL}"
echo "Work dir:   ${WORK_DIR}"
echo "Logs:       ${LOG_DIR}/meta-orchestrator.log"
echo "Plist:      ${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"
echo ""
echo "Useful commands:"
echo "  Status:    ./status.sh"
echo "  Logs:      tail -f ${LOG_DIR}/meta-orchestrator.log"
echo "  Stop:      launchctl unload ${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"
echo "  Start:     launchctl load ${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"
echo "  Uninstall: ./uninstall.sh"
