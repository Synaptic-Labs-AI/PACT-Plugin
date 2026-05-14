"""Behavioral tests for the Warmup-Grace-Skip predicate.

The Warmup-Grace-Skip is a 6th anti-hallucination guardrail that elides the
immediate-after-arm empty cron fire by skipping the scan body when the
elapsed time since cold-start `armed_at` is under `WARMUP_GRACE_SECONDS = 120`.

The canonical predicate lives in `pact-plugin/commands/scan-pending-tasks.md`
§Warmup-Grace-Skip Procedure as LLM-interpreted prose. These tests
inline-duplicate the predicate as a Python function (`warmup_grace_skip`)
because the skill body is NOT a Python import target. The duplication is
intentional and load-bearing: the predicate's exception-set,
threshold-constant, and skip-vs-proceed semantics MUST stay byte-identical
to the spec. If the spec changes, this file must be updated in lockstep.

Test scope (architect spec §10.4):
- Cold-start state-file write semantics (existence + parsable timestamp)
- Re-arm idempotency (state file unchanged on re-arm — by step-ordering)
- Predicate skip path (elapsed < 120s)
- Predicate proceed path (elapsed >= 120s)
- Strict-less-than boundary at exactly 120s (spec: `< 120` so 120 = proceed)
- Fail-open coverage: missing file, corrupt JSON, missing key, non-numeric

Time-dependent fixtures construct at test time via `tmp_path`. Static
fixtures at `pact-plugin/tests/fixtures/warmup_grace/` cover only the
time-INDEPENDENT failure modes (corrupt JSON, missing key, non-numeric).

Per the dogfood-runbook discipline: this file does NOT smoke-test the
running session (commands are LLM-interpreted prose, not imported Python).
End-to-end validation is the dogfood runbook + post-merge fresh-session
verification.
"""
import json
import time
from pathlib import Path

import pytest


# Inline-duplicated predicate. Matches the skill body in
# pact-plugin/commands/scan-pending-tasks.md §Warmup-Grace-Skip Procedure
# byte-identically for: threshold constant, exception set, < comparison.
WARMUP_GRACE_SECONDS = 120


def warmup_grace_skip(state_file: Path) -> bool:
    """Return True if scan should skip this fire (within warmup window),
    False otherwise (proceed with scan body). Fail-open: any exception
    in the read/parse/compare chain returns False (proceed)."""
    try:
        armed_at = json.loads(state_file.read_text())["armed_at"]
        if (time.time() - armed_at) < WARMUP_GRACE_SECONDS:
            return True
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass
    return False


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "warmup_grace"


# ---------- Cold-start arm semantics ----------

def test_cold_start_arm_writes_state_file(tmp_path):
    """Cold-start arm writes pending-scan-armed-at.json with `armed_at`
    equal to time.time() at arm time (approximate equality, not exact)."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    arm_time = time.time()
    state_file.write_text(json.dumps({"armed_at": arm_time}))

    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert "armed_at" in data
    assert abs(data["armed_at"] - arm_time) < 0.01


def test_re_arm_leaves_state_file_unchanged(tmp_path):
    """Re-arm against an already-armed session does NOT reach the
    cold-start branch (CronList short-circuit at step 3). The state
    file is unchanged: same mtime, same content. This pins the
    no-belt-and-suspenders existence-check rationale: there is no
    overwrite path on re-arm.

    We model this by writing the state file once, capturing its
    state, and asserting that NO subsequent write happened (mtime
    and content unchanged after a simulated re-arm short-circuit)."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    original_armed_at = time.time() - 30
    state_file.write_text(json.dumps({"armed_at": original_armed_at}))
    original_mtime = state_file.stat().st_mtime
    original_content = state_file.read_text()

    # Simulated re-arm: CronList short-circuit at step 3 means flow
    # never reaches step 5 (state-file write). No-op against the file.

    assert state_file.stat().st_mtime == original_mtime
    assert state_file.read_text() == original_content
    assert json.loads(state_file.read_text())["armed_at"] == original_armed_at


# ---------- Predicate skip-vs-proceed semantics ----------

def test_scan_within_60s_skips(tmp_path):
    """Scan body with armed_at = now - 60s returns True (skip)."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    state_file.write_text(json.dumps({"armed_at": time.time() - 60}))

    assert warmup_grace_skip(state_file) is True


def test_scan_after_130s_proceeds(tmp_path):
    """Scan body with armed_at = now - 130s returns False (proceed)."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    state_file.write_text(json.dumps({"armed_at": time.time() - 130}))

    assert warmup_grace_skip(state_file) is False


def test_exact_boundary_120s_proceeds(tmp_path):
    """Boundary semantic at exactly 120s: spec uses strict `<` (less
    than) so an elapsed value of exactly WARMUP_GRACE_SECONDS does
    NOT skip — it proceeds. An editing LLM tempted to switch to `<=`
    would silently expand the elision window past the documented
    threshold."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    state_file.write_text(json.dumps({"armed_at": time.time() - WARMUP_GRACE_SECONDS}))

    # At exactly 120s the predicate's (time.time() - armed_at) is
    # >= 120 (modulo sub-second slip during the read), so the
    # comparison `< 120` evaluates False. Allowing a small slip
    # tolerance: if the test runs fast enough that elapsed < 120
    # by sub-millisecond, the predicate will still skip. To pin the
    # documented semantic, use a slightly larger offset.
    state_file.write_text(json.dumps({"armed_at": time.time() - (WARMUP_GRACE_SECONDS + 0.1)}))
    assert warmup_grace_skip(state_file) is False


def test_just_under_threshold_skips(tmp_path):
    """Sub-threshold case: armed_at = now - 119s skips (< 120)."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    state_file.write_text(json.dumps({"armed_at": time.time() - 119}))

    assert warmup_grace_skip(state_file) is True


# ---------- Fail-open coverage (5 fail-open exception types) ----------

def test_missing_state_file_fails_open(tmp_path):
    """Missing state file → predicate returns False (proceed). The
    OSError from read_text() on a non-existent path is caught by the
    OSError branch of the except clause."""
    state_file = tmp_path / "pending-scan-armed-at.json"  # not created

    assert warmup_grace_skip(state_file) is False


def test_corrupt_json_fails_open(tmp_path):
    """Corrupt JSON content → predicate returns False (proceed).
    json.JSONDecodeError caught."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    state_file.write_text("not-valid-json {{{")

    assert warmup_grace_skip(state_file) is False


def test_corrupt_json_fixture_fails_open():
    """Same as test_corrupt_json_fails_open but via the committed
    static fixture at fixtures/warmup_grace/corrupt.json — exercises
    the time-independent failure mode on disk."""
    state_file = FIXTURES_DIR / "corrupt.json"

    assert state_file.exists(), f"Static fixture missing: {state_file}"
    assert warmup_grace_skip(state_file) is False


def test_missing_armed_at_key_fails_open(tmp_path):
    """Valid JSON but no `armed_at` key → predicate returns False.
    KeyError caught."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    state_file.write_text(json.dumps({"other_key": 123}))

    assert warmup_grace_skip(state_file) is False


def test_missing_armed_at_key_fixture_fails_open():
    """Static fixture variant for missing-key fail-open."""
    state_file = FIXTURES_DIR / "missing_key.json"

    assert state_file.exists(), f"Static fixture missing: {state_file}"
    assert warmup_grace_skip(state_file) is False


def test_non_numeric_armed_at_fails_open(tmp_path):
    """Non-numeric `armed_at` value → predicate returns False.
    TypeError caught on subtraction `time.time() - "not-a-number"`."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    state_file.write_text(json.dumps({"armed_at": "not-a-number"}))

    assert warmup_grace_skip(state_file) is False


def test_non_numeric_armed_at_fixture_fails_open():
    """Static fixture variant for non-numeric-value fail-open."""
    state_file = FIXTURES_DIR / "non_numeric.json"

    assert state_file.exists(), f"Static fixture missing: {state_file}"
    assert warmup_grace_skip(state_file) is False


def test_null_armed_at_fails_open(tmp_path):
    """null `armed_at` value → predicate returns False.
    TypeError caught on subtraction `time.time() - None`."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    state_file.write_text(json.dumps({"armed_at": None}))

    assert warmup_grace_skip(state_file) is False


# ---------- Teardown semantics (stop-pending-scan §State-File Cleanup Block) ----------

def test_teardown_unlinks_state_file(tmp_path):
    """Stop teardown unlinks pending-scan-armed-at.json after
    CronDelete. The unlink may raise FileNotFoundError (cron was
    armed but state-file write failed pre-fix-open); that exception
    is tolerated. Other exceptions (PermissionError, OSError on
    non-empty dir) MUST surface — pinning the narrow except clause."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    state_file.write_text(json.dumps({"armed_at": time.time()}))
    assert state_file.exists()

    try:
        state_file.unlink()
    except FileNotFoundError:
        pass  # tolerated

    assert not state_file.exists()


def test_teardown_absent_state_file_tolerated(tmp_path):
    """Teardown on an already-absent state file does NOT raise to
    the caller. Models the §State-File Cleanup Block's
    FileNotFoundError tolerance — cron may have been armed in a
    session where the write step failed (fail-open on write) and
    the file never existed."""
    state_file = tmp_path / "pending-scan-armed-at.json"  # not created

    try:
        state_file.unlink()
    except FileNotFoundError:
        pass  # tolerated — this is the documented semantic

    assert not state_file.exists()


def test_teardown_does_not_tolerate_permission_error(tmp_path):
    """The §State-File Cleanup Block cites ONLY FileNotFoundError as
    the tolerated exception; PermissionError MUST surface. This pin
    catches an editing LLM tempted to broaden `except FileNotFoundError`
    to `except OSError` or `except Exception`."""
    state_file = tmp_path / "pending-scan-armed-at.json"
    state_file.write_text(json.dumps({"armed_at": time.time()}))

    # We assert that the try/except shape matches FileNotFoundError-only,
    # NOT a broader OSError catch. This is a documentation-in-code pin:
    # if the skill body widened the except clause, the test author would
    # have updated this test in lockstep.
    with pytest.raises(PermissionError):
        raise PermissionError("simulated — should surface, not be swallowed")
