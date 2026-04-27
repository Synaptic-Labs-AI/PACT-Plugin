"""
Emitter test helpers for the agent_handoff_emitter test suite.

Consumed by all test_emitter_*.py files (7 modules):
test_emitter_happy_and_gates, test_emitter_idempotency,
test_emitter_path_sanitization, test_emitter_race_regression,
test_emitter_real_disk, test_emitter_resolution, test_emitter_robustness.

Per-file co-located helpers (NOT lifted here):
- ``PLATFORM_STDIN_SHAPE`` and ``_write_task_json`` live in
  ``test_emitter_real_disk.py`` — single-file consumers.
"""

import io
import json
from unittest.mock import patch

import pytest

VALID_HANDOFF = {
    "produced": ["src/auth.ts"],
    "decisions": ["Used JWT"],
    "uncertainty": [],
    "integration": ["UserService"],
    "open_questions": [],
}


def _run_main(stdin_payload, task_data, append_calls):
    """Invoke agent_handoff_emitter.main() with patched IO/deps."""
    # Lazy import: sys.path is configured in conftest.py before this module loads.
    # Do not hoist to module-level — sys.path coupling depends on conftest load order.
    from agent_handoff_emitter import main

    def _append_spy(event):
        append_calls.append(event)
        return True

    with patch("agent_handoff_emitter.read_task_json", return_value=task_data), \
         patch("agent_handoff_emitter.append_event", side_effect=_append_spy), \
         patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
        with pytest.raises(SystemExit) as exc_info:
            main()
    return exc_info.value.code
