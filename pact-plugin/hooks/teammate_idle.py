#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teammate_idle.py
Summary: TeammateIdle hook for PACT v3.0 Agent Teams.
Used by: hooks.json TeammateIdle event registration

Fires when a teammate goes idle. Validates that the teammate
has stored a handoff in their Task metadata before allowing idle.
Exit code 2 keeps the teammate working with feedback.

Input (env vars): TEAMMATE_NAME, TEAM_NAME, SESSION_ID, CWD, TRANSCRIPT_PATH

Note: This hook needs empirical validation. The TeammateIdle event
behavior (env vars available, exit code semantics) is based on
platform documentation but has not been tested in production.
"""
import os
import sys


def main():
    teammate_name = os.environ.get("TEAMMATE_NAME", "")
    team_name = os.environ.get("TEAM_NAME", "")

    if not teammate_name or not team_name:
        # Not in an Agent Teams context — allow idle
        sys.exit(0)

    # Only validate PACT specialists (name starts with "pact-")
    if not teammate_name.startswith("pact-"):
        sys.exit(0)

    # TODO: Empirical validation needed before relying on this hook.
    # The design doc notes TeammateIdle hook behavior needs testing.
    # For now, provide feedback but don't hard-block.
    print("You haven't completed your handoff yet. Please store your "
          "handoff in Task metadata via TaskUpdate and send a completion "
          "signal via SendMessage before going idle.")
    sys.exit(2)  # Exit code 2: keep teammate working


if __name__ == "__main__":
    main()
