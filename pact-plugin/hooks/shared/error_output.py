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


# Bound the error portion of systemMessage. A pathological exception (e.g.,
# huge stdin echoed back into the message, full traceback string, base64 blob)
# could otherwise produce a multi-megabyte JSON line and overwhelm the UI.
# Matches the _ERROR_MAX_CHARS = 200 cap used by failure_log.append_failure.
_ERROR_MAX_CHARS = 200
_HOOK_NAME_MAX_CHARS = 50


def hook_error_json(hook_name: str, error: Exception) -> str:
    """Format a hook error as JSON for stdout output.

    Returns a JSON string with a `systemMessage` key that Claude Code
    will display to the user as a warning. This is for fail-open hooks
    (exit 0) where we want to surface the error without blocking.

    Both hook_name and error are truncated so that a pathological input
    cannot blow up the systemMessage line.

    Args:
        hook_name: Name of the hook (e.g., 'validate_handoff')
        error: The caught exception

    Returns:
        JSON string: {"systemMessage": "PACT hook warning (hook_name): error message"}
    """
    safe_name = str(hook_name)[:_HOOK_NAME_MAX_CHARS]
    error_str = str(error)[:_ERROR_MAX_CHARS]
    return json.dumps(
        {"systemMessage": f"PACT hook warning ({safe_name}): {error_str}"}
    )
