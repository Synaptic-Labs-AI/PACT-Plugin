"""
Location: pact-plugin/hooks/shared/error_output.py
Summary: Shared error output helper for hook exception handlers.
Used by: All fail-open PACT hooks that need structured JSON error output
         on stdout when catching unexpected exceptions.

Provides a standardized JSON format using the `systemMessage` key so that
Claude Code's UI can display hook errors to the user instead of showing
"hook error (No output)" or silently suppressing the error.
"""

import json


def hook_error_json(hook_name: str, error: Exception) -> str:
    """Format a hook error as JSON for stdout output.

    Returns a JSON string with a `systemMessage` key that Claude Code
    will display to the user as a warning. This is for fail-open hooks
    (exit 0) where we want to surface the error without blocking.

    Args:
        hook_name: Name of the hook (e.g., 'validate_handoff')
        error: The caught exception

    Returns:
        JSON string: {"systemMessage": "PACT hook warning (hook_name): error message"}
    """
    return json.dumps(
        {"systemMessage": f"PACT hook warning ({hook_name}): {error}"}
    )
