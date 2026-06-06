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

# Non-empty sentinel for the in-hook get_journal_path() return. The path is
# never written (append_event is spied), so any truthy value works — it only
# needs to satisfy the #917 marker-claim writability gate
# (`if not get_journal_path(): defer`). Modelling a WRITABLE-journal b1 process
# is the correct default for this helper: these tests assert b1's emit /
# dedup / sanitization behaviour GIVEN that the canonical journal is writable
# (the normal lead-or-resolvable-context fire). The journal-UNWRITABLE defer
# path is exercised directly in test_handoff_writability_parity.py.
_WRITABLE_JOURNAL_SENTINEL = "/pact-test-session/session-journal.jsonl"


def _run_main(stdin_payload, task_data, append_calls, journal_path=_WRITABLE_JOURNAL_SENTINEL):
    """Invoke agent_handoff_emitter.main() with patched IO/deps.

    ``journal_path`` is the value the in-hook ``get_journal_path()`` returns
    inside ``main()``. Defaults to a non-empty sentinel so the helper models a
    WRITABLE-journal b1 process (the #917 writability gate passes and emission
    proceeds as before the gate). Pass ``""`` to exercise the journal-
    UNWRITABLE defer path. The hook imports get_journal_path by value, so the
    patch targets the symbol bound in ``agent_handoff_emitter`` — NOT
    ``session_journal`` — or the gate would read the real resolution.
    """
    # Lazy import: sys.path is configured in conftest.py before this module loads.
    # Do not hoist to module-level — sys.path coupling depends on conftest load order.
    from agent_handoff_emitter import main

    def _append_spy(event):
        append_calls.append(event)
        return True

    with patch("agent_handoff_emitter.read_task_json", return_value=task_data), \
         patch("agent_handoff_emitter.append_event", side_effect=_append_spy), \
         patch("agent_handoff_emitter.get_journal_path", return_value=journal_path), \
         patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
        with pytest.raises(SystemExit) as exc_info:
            main()
    return exc_info.value.code
