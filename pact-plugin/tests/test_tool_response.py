"""Tests for shared.tool_response.extract_tool_response — SSOT helper.

Pins the helper's three behavioral contracts:

1. Canonical-prefer: when `tool_response` is present, return it.
2. Legacy-fallback: when only `tool_output` is present, return it.
3. Dual-envelope warning: when BOTH are present, warn to stderr and
   return the canonical `tool_response` value (not legacy).

Also pins defensive shapes (non-dict input, non-dict field values, empty
fields, missing fields).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.tool_response import extract_tool_response  # noqa: E402


# =============================================================================
# Canonical-prefer behavior
# =============================================================================


def test_canonical_tool_response_returned_when_present():
    payload = {"tool_response": {"task": {"id": "1"}}}
    assert extract_tool_response(payload) == {"task": {"id": "1"}}


def test_canonical_returned_even_when_legacy_missing():
    payload = {"tool_response": {"status": "completed"}}
    assert extract_tool_response(payload) == {"status": "completed"}


# =============================================================================
# Legacy-fallback behavior
# =============================================================================


def test_legacy_tool_output_returned_when_canonical_missing():
    payload = {"tool_output": {"task": {"id": "1"}}}
    assert extract_tool_response(payload) == {"task": {"id": "1"}}


def test_legacy_returned_when_canonical_is_empty():
    # `or` short-circuits on falsy — empty dict triggers fallback to legacy.
    payload = {"tool_response": {}, "tool_output": {"id": "from_legacy"}}
    # NOTE: empty dict is falsy → legacy wins. Document this so a future
    # refactor that uses `is not None` instead of `or` would change the
    # contract and break this test.
    assert extract_tool_response(payload) == {"id": "from_legacy"}


# =============================================================================
# Dual-envelope warning
# =============================================================================


def test_dual_envelope_warns_to_stderr(capsys):
    """Both fields present and truthy → SECURITY warning + canonical wins.

    Pins the envelope-confusion-attack defense: no legitimate platform
    fire emits both fields; a same-payload pair is categorically
    suspicious. Helper emits a stderr warning and returns canonical.
    """
    payload = {
        "tool_response": {"id": "from_canonical"},
        "tool_output": {"id": "from_legacy"},
    }
    result = extract_tool_response(payload)
    assert result == {"id": "from_canonical"}, "canonical must win the precedence"

    captured = capsys.readouterr()
    assert "[security]" in captured.err
    assert "dual-envelope" in captured.err
    assert "envelope-confusion" in captured.err
    assert "tool_response" in captured.err  # warns it's using canonical


def test_dual_envelope_no_warn_when_canonical_is_empty(capsys):
    """Empty canonical + present legacy → fallback fires, no warning.

    Empty dict is falsy → not a dual-envelope race. The warning fires
    only when BOTH are truthy (which is the actual confusion shape).
    """
    payload = {"tool_response": {}, "tool_output": {"id": "1"}}
    result = extract_tool_response(payload)
    assert result == {"id": "1"}
    captured = capsys.readouterr()
    assert captured.err == "", "no warning when canonical is empty"


# =============================================================================
# Defensive shapes
# =============================================================================


def test_neither_field_present_returns_empty_dict():
    assert extract_tool_response({}) == {}
    assert extract_tool_response({"tool_input": {"foo": "bar"}}) == {}


def test_non_dict_input_returns_empty_dict():
    assert extract_tool_response(None) == {}
    assert extract_tool_response("string") == {}
    assert extract_tool_response(42) == {}
    assert extract_tool_response([]) == {}


def test_non_dict_field_values_return_empty_dict():
    """Field present but non-dict → return {} rather than propagate the bad value."""
    assert extract_tool_response({"tool_response": "not a dict"}) == {}
    assert extract_tool_response({"tool_response": 42}) == {}
    assert extract_tool_response({"tool_response": ["list"]}) == {}
    assert extract_tool_response({"tool_output": "not a dict"}) == {}


def test_canonical_takes_precedence_when_present_and_truthy(capsys):
    """Re-pin the precedence semantics from a different angle.

    A refactor that flipped the operands (`tool_output or tool_response`)
    would silently make legacy data shadow canonical data — caught here
    with strict equality on canonical's payload.
    """
    payload = {
        "tool_response": {"canonical_marker": True},
        "tool_output": {"legacy_marker": True},
    }
    result = extract_tool_response(payload)
    assert "canonical_marker" in result
    assert "legacy_marker" not in result
