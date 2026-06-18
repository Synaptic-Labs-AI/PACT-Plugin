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

# Shared non-empty sentinel for the in-hook get_journal_path() return (F2 —
# the single source of truth, imported by every harness site + the
# test-engineer regression test rather than re-literalled). The path is never
# written (append_event is spied), so any truthy value works — it only needs to
# satisfy the #917 marker-claim writability gate
# (`if not get_journal_path(): defer`). Modelling a WRITABLE-journal b1 process
# is the correct default: these tests assert b1's emit / dedup / sanitization
# behaviour GIVEN that the canonical journal is writable (the normal
# lead-or-resolvable-context fire). The journal-UNWRITABLE defer path is
# exercised directly in test_handoff_writability_parity.py.
WRITABLE_TEST_JOURNAL = "/pact-test-session/session-journal.jsonl"


_USE_STDIN_TEAM_NAME = object()  # sentinel: context team_name mirrors stdin (default)


def _run_main(
    stdin_payload,
    task_data,
    append_calls,
    journal_path=WRITABLE_TEST_JOURNAL,
    context_team_name=_USE_STDIN_TEAM_NAME,
):
    """Invoke agent_handoff_emitter.main() with patched IO/deps.

    ``journal_path`` is the value the in-hook ``get_journal_path()`` returns
    inside ``main()``. Defaults to a non-empty sentinel so the helper models a
    WRITABLE-journal b1 process (the #917 writability gate passes and emission
    proceeds as before the gate). Pass ``""`` to exercise the journal-
    UNWRITABLE defer path. The hook imports get_journal_path by value, so the
    patch targets the symbol bound in ``agent_handoff_emitter`` — NOT
    ``session_journal`` — or the gate would read the real resolution.

    The marker ``team_name`` is resolved from the SESSION CONTEXT
    (``pact_context.get_pact_context()``), NOT from the stdin ``team_name``
    field — the b1 emitter reads the SSOT context so all three emit paths
    (b1/b2/b3) converge on one O_EXCL marker key by construction. To model a
    resolvable-context b1 process, this helper patches ``get_pact_context`` to
    return a context whose ``team_name`` is, by default, taken from the stdin
    payload's ``team_name`` (the SAME logical name session_init would have
    persisted to the context). So a payload ``team_name="pact-test"`` still
    scopes the marker to ``teams/pact-test/`` — the marker now travels through
    the context channel instead of stdin. (Production guarantees the context
    team_name is a ``session-<id8>`` value minted by generate_team_name, so a
    degenerate context team_name cannot occur; tests that probe a degenerate
    team_name attack via stdin no longer exercise a live path post-rebind —
    that vector is closed by construction.)

    ``context_team_name`` DECOUPLES the two channels. By default it mirrors the
    stdin ``team_name`` (preserving every existing caller). Pass an explicit
    value to make the context team_name DIFFER from the stdin team_name — the
    only way to demonstrate the post-rebind containment property that the stdin
    ``team_name`` is INERT (a hostile stdin value cannot reach the marker key
    when the authoritative context team_name is path-safe).
    """
    # Lazy import: sys.path is configured in conftest.py before this module loads.
    # Do not hoist to module-level — sys.path coupling depends on conftest load order.
    from agent_handoff_emitter import main

    def _append_spy(event):
        append_calls.append(event)
        return True

    # Mirror the context session_init would have persisted: the marker team_name
    # now flows through get_pact_context(), not stdin. Source it from the stdin
    # payload's team_name by default so existing per-test expectations (marker
    # under teams/<team_name>/) hold under the rebind; an explicit
    # ``context_team_name`` decouples the channels for the stdin-inert containment
    # proof.
    _resolved_ctx_team = (
        stdin_payload.get("team_name", "")
        if context_team_name is _USE_STDIN_TEAM_NAME
        else context_team_name
    )
    _ctx = {
        "team_name": _resolved_ctx_team,
        "session_id": "",
        "project_dir": "",
        "plugin_root": "",
        "started_at": "",
    }

    with patch("agent_handoff_emitter.read_task_json", return_value=task_data), \
         patch("agent_handoff_emitter.append_event", side_effect=_append_spy), \
         patch("agent_handoff_emitter.get_journal_path", return_value=journal_path), \
         patch("agent_handoff_emitter.pact_context.get_pact_context", return_value=_ctx), \
         patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
        with pytest.raises(SystemExit) as exc_info:
            main()
    return exc_info.value.code
