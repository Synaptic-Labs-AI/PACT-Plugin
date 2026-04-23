"""
LOW-priority coverage for pin_caps_gate.py addressing backend-coder-7's
HANDOFF items and auditor-2's residual sweep.

Items covered:
  1. failure_log classification assertions for all 4 gate error paths
     (_FAIL_BASELINE_READ, _FAIL_BASELINE_PARSE, _FAIL_SIMULATE,
     _FAIL_UNEXPECTED).
  2. file_lock adversarial concurrent access — two near-simultaneous
     hook invocations must serialize correctly without corruption or
     deadlock.

Item 3 (CLI parse_known_args silent-accept) is already covered in
test_pin_caps_phantom_green.py::TestPhantomGreen_CliBypass.

Item 4 (prune-memory.md end-to-end mock-curator integration) is already
covered in test_prune_memory_integration.py.
"""

import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


# ---------------------------------------------------------------------------
# failure_log classification assertions
# ---------------------------------------------------------------------------


@pytest.fixture
def gate_with_captured_failures(tmp_path, monkeypatch, pact_context):
    """Capture append_failure calls without writing to the real ring buffer.

    Returns dict with:
        claude_md — Path to on-disk CLAUDE.md
        failures  — list appended to on every append_failure call
                    ({"classification", "error", "source"})
    """
    claude_md = tmp_path / "CLAUDE.md"
    pact_context(
        team_name="test-team",
        session_id="session-fail-log",
        project_dir=str(tmp_path),
    )

    import staleness
    monkeypatch.setattr(
        staleness, "get_project_claude_md_path", lambda: claude_md
    )

    failures = []

    def _capture(classification, error=None, cwd=None, source=None):
        failures.append({
            "classification": classification,
            "error": error,
            "source": source,
        })

    # Patch in the gate module where append_failure is called.
    import pin_caps_gate
    monkeypatch.setattr(pin_caps_gate, "append_failure", _capture)

    return {"claude_md": claude_md, "failures": failures}


def _call_gate(input_data):
    from pin_caps_gate import _check_tool_allowed
    return _check_tool_allowed(input_data)


def _build_over_cap():
    entries = [make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(13)]
    return make_claude_md_with_pins(entries)


class TestFailureLogClassification:
    """Every fail-open bypass MUST record a failure_log entry with a
    correct classification (invariant #4). Without this, post-hoc
    diagnostics cannot distinguish between the 4 gate error paths.
    """

    def test_baseline_read_failure_records_classification(
        self, gate_with_captured_failures, monkeypatch
    ):
        """_FAIL_BASELINE_READ fires when the baseline read raises IOError."""
        env = gate_with_captured_failures
        # Write a legitimate file, then force _read_baseline to return
        # the error branch by patching it.
        env["claude_md"].write_text(
            make_claude_md_with_pins([
                make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(3)
            ]),
            encoding="utf-8",
        )
        import pin_caps_gate
        monkeypatch.setattr(
            pin_caps_gate,
            "_read_baseline",
            lambda _: (None, pin_caps_gate._FAIL_BASELINE_READ),
        )

        # Edit with unreadable baseline → fail-open (Edit is asymmetric-
        # exempt) AND classification recorded.
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "x",
                "new_string": "y",
                "replace_all": False,
            },
        })
        assert result is None
        assert len(env["failures"]) == 1
        assert env["failures"][0]["classification"] == "pin_caps_gate_baseline_read"
        assert env["failures"][0]["source"] == "Edit"

    def test_baseline_parse_failure_records_classification(
        self, gate_with_captured_failures, monkeypatch
    ):
        """_FAIL_BASELINE_PARSE fires when _parse_baseline raises."""
        env = gate_with_captured_failures
        env["claude_md"].write_text(
            make_claude_md_with_pins([
                make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(3)
            ]),
            encoding="utf-8",
        )

        import pin_caps_gate

        def raising_parse(_):
            raise RuntimeError("synthetic parse failure")

        monkeypatch.setattr(pin_caps_gate, "_parse_baseline", raising_parse)

        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "x",
                "new_string": "y",
                "replace_all": False,
            },
        })
        assert result is None
        # Exactly one baseline_parse classification.
        parse_failures = [
            f for f in env["failures"]
            if f["classification"] == "pin_caps_gate_baseline_parse"
        ]
        assert len(parse_failures) == 1

    def test_simulate_failure_records_classification(
        self, gate_with_captured_failures, monkeypatch
    ):
        """_FAIL_SIMULATE fires when apply_edit_and_parse raises."""
        env = gate_with_captured_failures
        env["claude_md"].write_text(
            make_claude_md_with_pins([
                make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(3)
            ]),
            encoding="utf-8",
        )

        import pin_caps_gate

        def raising_simulate(*args, **kwargs):
            raise RuntimeError("synthetic simulate failure")

        # apply_edit_and_parse is used in the gate via `from pin_caps import
        # apply_edit_and_parse` — freeze-bound. Patch the gate-local name.
        monkeypatch.setattr(
            pin_caps_gate, "apply_edit_and_parse", raising_simulate
        )

        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "x",
                "new_string": "y",
                "replace_all": False,
            },
        })
        assert result is None
        simulate_failures = [
            f for f in env["failures"]
            if f["classification"] == "pin_caps_gate_simulate"
        ]
        assert len(simulate_failures) == 1

    def test_unexpected_failure_records_classification(
        self, tmp_path, monkeypatch, pact_context
    ):
        """_FAIL_UNEXPECTED fires from main()'s outer except.

        This path is exercised by forcing _check_tool_allowed to raise.
        main() catches, appends failure_log with _FAIL_UNEXPECTED, and
        fail-opens with suppressOutput + exit 0.
        """
        pact_context(
            team_name="test-team",
            session_id="session-unexpected",
            project_dir=str(tmp_path),
        )

        import pin_caps_gate

        failures = []

        def _capture(classification, error=None, cwd=None, source=None):
            failures.append({
                "classification": classification,
                "error": error,
                "source": source,
            })

        monkeypatch.setattr(pin_caps_gate, "append_failure", _capture)
        monkeypatch.setattr(
            pin_caps_gate,
            "_check_tool_allowed",
            lambda _: (_ for _ in ()).throw(
                RuntimeError("synthetic unexpected failure")
            ),
        )

        import io
        stdin_payload = json.dumps({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(tmp_path / "CLAUDE.md"),
                "old_string": "",
                "new_string": "",
            },
        })
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_payload))

        with pytest.raises(SystemExit) as exc_info:
            pin_caps_gate.main()
        assert exc_info.value.code == 0
        unexpected_failures = [
            f for f in failures
            if f["classification"] == "pin_caps_gate_unexpected"
        ]
        assert len(unexpected_failures) == 1
        # Source carries the tool_name for post-hoc correlation.
        assert unexpected_failures[0]["source"] == "Edit"
        # Error carries the exception type + message.
        assert "RuntimeError" in unexpected_failures[0]["error"]
        assert "synthetic unexpected failure" in unexpected_failures[0]["error"]

    def test_write_baseline_failclosed_records_classification(
        self, gate_with_captured_failures
    ):
        """Write fail-CLOSED path still records a failure_log entry for
        the baseline read. The closure is an asymmetric exception that
        DENIES the tool; it does NOT skip observability.
        """
        env = gate_with_captured_failures
        # Baseline NOT created → IOError on read → _FAIL_BASELINE_READ.
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": _build_over_cap(),
            },
        })
        assert result is not None
        assert "Refusing Write" in result
        # failure_log still captured the read failure (observability is
        # NOT skipped just because the outer decision is DENY).
        read_failures = [
            f for f in env["failures"]
            if f["classification"] == "pin_caps_gate_baseline_read"
        ]
        assert len(read_failures) == 1
        assert read_failures[0]["source"] == "Write"

    def test_write_baseline_parse_error_over_cap_denies(
        self, gate_with_captured_failures, monkeypatch
    ):
        """Write + baseline readable-but-parse-raises + over-cap content → DENY.

        Sibling to test_write_baseline_failclosed_records_classification: covers
        the second trigger branch of the fail-CLOSED matrix (parse-exception
        at pin_caps_gate.py:263, is_write=True path at line 269). Without this
        guard a regression fail-OPENing the parse-error Write branch would
        ship green through the full suite (empirically verified via counter-
        test-by-revert during task #3 review).
        """
        env = gate_with_captured_failures
        env["claude_md"].write_text(
            make_claude_md_with_pins([
                make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(3)
            ]),
            encoding="utf-8",
        )

        import pin_caps_gate

        def raising_parse(_):
            raise RuntimeError("synthetic parse failure")

        monkeypatch.setattr(pin_caps_gate, "_parse_baseline", raising_parse)

        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": _build_over_cap(),
            },
        })
        assert result is not None
        assert "Refusing Write" in result
        # Observability: classification recorded even though decision is DENY.
        parse_failures = [
            f for f in env["failures"]
            if f["classification"] == "pin_caps_gate_baseline_parse"
        ]
        assert len(parse_failures) == 1
        assert parse_failures[0]["source"] == "Write"

    def test_write_baseline_parse_error_under_cap_allows(
        self, gate_with_captured_failures, monkeypatch
    ):
        """Write + baseline readable-but-parse-raises + under-cap content → ALLOW.

        The fail-CLOSED gate treats an unparseable baseline as empty and then
        evaluates the Write's own content against the caps. Under-cap content
        is clean → allow (no spurious denial). Pinning this invariant prevents
        an over-eager fix from flipping the parse-error branch to unconditional
        DENY, which would block legitimate CLAUDE.md repair Writes.
        """
        env = gate_with_captured_failures
        env["claude_md"].write_text(
            make_claude_md_with_pins([
                make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(3)
            ]),
            encoding="utf-8",
        )

        import pin_caps_gate

        def raising_parse(_):
            raise RuntimeError("synthetic parse failure")

        monkeypatch.setattr(pin_caps_gate, "_parse_baseline", raising_parse)

        under_cap_content = make_claude_md_with_pins([
            make_pin_entry(title=f"P{i}", body_chars=4) for i in range(3)
        ])
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": under_cap_content,
            },
        })
        assert result is None


# ---------------------------------------------------------------------------
# file_lock adversarial concurrency
# ---------------------------------------------------------------------------


class TestFileLockContention:
    """file_lock serializes the baseline-read section. Two near-
    simultaneous hook invocations must complete without corruption
    or deadlock.

    Uses threading.Barrier(N+1) — the standard pattern from MEMORY.md
    (feedback_threading_barrier_race_detection). Each reader thread
    calls the gate; we assert every thread returns a consistent result.
    """

    def test_two_concurrent_gate_calls_both_complete(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Two concurrent Edit gate calls against the same CLAUDE.md
        both terminate with sensible results (no deadlock).

        Both calls target an under-cap state → both ALLOW. The point is
        that `file_lock` serializes them without blocking indefinitely.
        """
        claude_md = tmp_path / "CLAUDE.md"
        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(3)
        ]
        claude_md.write_text(
            make_claude_md_with_pins(entries), encoding="utf-8"
        )
        pact_context(
            team_name="test-team",
            session_id="session-concurrent",
            project_dir=str(tmp_path),
        )

        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: claude_md
        )

        N_THREADS = 4
        barrier = threading.Barrier(N_THREADS)
        results = [None] * N_THREADS
        errors = [None] * N_THREADS

        def worker(idx):
            try:
                barrier.wait(timeout=5.0)  # Release all threads in sync.
                results[idx] = _call_gate({
                    "tool_name": "Edit",
                    "tool_input": {
                        "file_path": str(claude_md),
                        "old_string": "x",
                        "new_string": "y",
                        "replace_all": False,
                    },
                })
            except Exception as e:  # noqa: BLE001 — test-scope capture
                errors[idx] = e

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive(), (
                "file_lock deadlock suspected — worker did not terminate"
            )

        # No thread raised.
        assert all(e is None for e in errors), f"errors: {errors}"
        # All N threads got the same (None — under-cap ALLOW) result.
        assert all(r is None for r in results), f"results: {results}"

    def test_concurrent_denies_all_see_consistent_deny_reason(
        self, tmp_path, monkeypatch, pact_context
    ):
        """All concurrent over-cap Writes deny with the SAME reason —
        no observable torn state.
        """
        claude_md = tmp_path / "CLAUDE.md"
        # Pre-load at-cap baseline.
        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(12)
        ]
        claude_md.write_text(
            make_claude_md_with_pins(entries), encoding="utf-8"
        )
        pact_context(
            team_name="test-team",
            session_id="session-concurrent-deny",
            project_dir=str(tmp_path),
        )

        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: claude_md
        )

        N_THREADS = 4
        barrier = threading.Barrier(N_THREADS)
        results = [None] * N_THREADS

        def worker(idx):
            barrier.wait(timeout=5.0)
            results[idx] = _call_gate({
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(claude_md),
                    "content": _build_over_cap(),
                },
            })

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive()

        # All threads denied with the same rendered reason.
        assert all(r is not None for r in results)
        assert all("Pin count cap" in r for r in results)
        # Identical reason strings — no torn state produced a divergent
        # count value in the rendered text.
        assert len(set(results)) == 1, (
            f"concurrent gate calls produced divergent deny reasons: {set(results)}"
        )
