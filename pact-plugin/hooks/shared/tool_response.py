"""
Location: pact-plugin/hooks/shared/tool_response.py
Summary: SSOT helper for extracting the platform's tool-response payload from
         hook stdin, with a defensive fallback for the legacy `tool_output`
         envelope name and a security warning when both fields are present.
Used by: hooks that consume PostToolUse / SubagentStop / TaskCompleted stdin
         (task_lifecycle_gate.py, wake_lifecycle_emitter.py, merge_guard_post.py).

The platform's canonical field is `tool_response`. Pre-rename payloads (and
captured-from-production test fixtures from that era) carry `tool_output`.
This helper centralizes the "prefer canonical, fall back to legacy" pattern
so that all consumer hooks share one defensive read — preventing the kind
of asymmetry that existed pre-#677-PR-#1 where only one hook had the
fallback while siblings did not.

When BOTH fields are present in the same payload, that is a categorically
suspicious shape (no legitimate platform fire emits both). The helper logs
a SECURITY warning to stderr identifying it as a possible
envelope-confusion attack, and returns the canonical `tool_response` value.
"""

import sys


def extract_tool_response(input_data: dict) -> dict:
    """Return the tool-response payload, preferring canonical over legacy.

    Args:
        input_data: The hook stdin dict.

    Returns:
        The tool-response payload as a dict. Returns `{}` when neither
        field is present, when the value is non-dict, or when the field
        is present but empty.

    Side effect:
        Emits a stderr warning when BOTH `tool_response` AND `tool_output`
        are present in the same payload (envelope-confusion shape).
    """
    if not isinstance(input_data, dict):
        return {}

    canonical = input_data.get("tool_response")
    legacy = input_data.get("tool_output")

    if canonical and legacy:
        # Categorically suspicious: no legitimate platform fire emits both.
        # Warn for forensic visibility; return canonical (the trustworthy
        # field per current platform contract).
        print(
            "[security] dual-envelope payload detected: both tool_response "
            "AND tool_output present — possible envelope-confusion attack. "
            "Using tool_response (canonical).",
            file=sys.stderr,
        )

    chosen = canonical or legacy or {}
    if not isinstance(chosen, dict):
        return {}
    return chosen
