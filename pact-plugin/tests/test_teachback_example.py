"""Tests for shared/teachback_example.py (#401 Commit #3).

Covers: template formatting, imperative-first framing, banned-word absence,
Phase 2 consequence mention, simplified/full variant selection, graceful
fail-open on template errors.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Ensure hooks dir is on sys.path so `shared.*` imports resolve when pytest
# runs from pact-plugin/.
_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from shared import teachback_example  # noqa: E402
from shared.teachback_example import (  # noqa: E402
    _DENY_TEMPLATES,
    _IMPERATIVE_FIRST_WORDS,
    format_deny_reason,
)


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

class TestDenyTemplatesRegistry:
    """Every expected reason_code has a registered template."""

    def test_all_expected_keys_present(self):
        expected = {
            "missing_submit",
            "missing_submit_simplified",
            "invalid_submit",
            "awaiting_approval",
            "unaddressed_items",
            "corrections_pending",
        }
        assert expected <= set(_DENY_TEMPLATES.keys())

    def test_all_templates_non_empty(self):
        for key, tmpl in _DENY_TEMPLATES.items():
            assert tmpl.strip(), f"template for {key!r} is empty/whitespace"


# ---------------------------------------------------------------------------
# Imperative-first framing (F11 honest-reframe gate)
# ---------------------------------------------------------------------------

class TestImperativeFirstFraming:
    """Every template starts with an imperative verb from the approved set."""

    def test_approved_first_word_set_has_expected_members(self):
        # Regression guard: if the approved set changes, update the drift
        # test carefully. The 6 verbs below cover missing/invalid/awaiting/
        # unaddressed/corrections paths.
        assert _IMPERATIVE_FIRST_WORDS == frozenset({
            "Send", "Fix", "Update", "Correct", "Address", "Resubmit",
        })

    @pytest.mark.parametrize("key", list(_DENY_TEMPLATES.keys()))
    def test_template_first_word_is_imperative(self, key):
        template = _DENY_TEMPLATES[key]
        first_word = template.split(maxsplit=1)[0]
        assert first_word in _IMPERATIVE_FIRST_WORDS, (
            f"template {key!r} starts with {first_word!r}; expected one of "
            f"{sorted(_IMPERATIVE_FIRST_WORDS)}"
        )


class TestBannedWordsAbsent:
    """Templates must not open with passive/advisory framing."""

    _BANNED_FIRST_WORDS = {
        "Reminder", "Note", "Advisory", "Tip", "Consider", "Optional",
        "You",  # "You may want to" leading
    }

    @pytest.mark.parametrize("key", list(_DENY_TEMPLATES.keys()))
    def test_template_does_not_open_with_banned_word(self, key):
        first_word = _DENY_TEMPLATES[key].split(maxsplit=1)[0]
        assert first_word not in self._BANNED_FIRST_WORDS, (
            f"template {key!r} opens with banned word {first_word!r}"
        )


# ---------------------------------------------------------------------------
# Phase 2 consequence mention
# ---------------------------------------------------------------------------

class TestPhase2ConsequenceMentioned:
    """Every template except awaiting_approval mentions Phase 2 or blocking."""

    _PHASE_2_REGEX = re.compile(r"phase\s*2\s*will\s*block", re.IGNORECASE)

    @pytest.mark.parametrize("key", [
        "missing_submit",
        "missing_submit_simplified",
        "invalid_submit",
        "unaddressed_items",
        "corrections_pending",
    ])
    def test_phase2_mentioned(self, key):
        assert self._PHASE_2_REGEX.search(_DENY_TEMPLATES[key]), (
            f"template {key!r} missing 'Phase 2 will block' consequence"
        )

    def test_awaiting_approval_omits_phase2(self):
        # awaiting_approval is post-submit; teammate is blocked by the lead
        # not by the gate. Phase warning doesn't apply.
        tmpl = _DENY_TEMPLATES["awaiting_approval"]
        assert not self._PHASE_2_REGEX.search(tmpl), (
            "awaiting_approval should not mention Phase 2 — teammate already "
            "submitted, gate is not the blocker here"
        )


# ---------------------------------------------------------------------------
# format_deny_reason happy paths
# ---------------------------------------------------------------------------

class TestFormatDenyReasonHappyPath:
    """Each reason_code formats cleanly with representative context."""

    def test_missing_submit_full(self):
        result = format_deny_reason(
            "missing_submit",
            context={
                "task_id": "17",
                "tool_name": "Edit",
                "variety_total": 11,
                "threshold": 7,
            },
            protocol_level="full",
        )
        assert 'TaskUpdate(taskId="17"' in result
        assert "Edit" in result
        assert "variety 11" in result
        assert "most_likely_wrong" in result  # full schema includes this field

    def test_missing_submit_simplified_switches_template(self):
        result = format_deny_reason(
            "missing_submit",
            context={
                "task_id": "17",
                "tool_name": "Write",
                "variety_total": 8,
                "threshold": 7,
            },
            protocol_level="simplified",
        )
        # Simplified MUST NOT include full-only fields
        assert "most_likely_wrong" not in result
        assert "least_confident_item" not in result
        # But MUST include simplified-required fields
        assert "understanding" in result
        assert "first_action" in result

    def test_invalid_submit_interpolates_field_error(self):
        result = format_deny_reason(
            "invalid_submit",
            context={
                "task_id": "42",
                "tool_name": "Edit",
                "fail_field": "understanding",
                "fail_error": "min 100 chars (got 42)",
                "actual_value": "too short",
            },
        )
        assert "understanding" in result
        assert "min 100 chars" in result
        assert "too short" in result

    def test_awaiting_approval(self):
        result = format_deny_reason(
            "awaiting_approval",
            context={"tool_name": "Edit"},
        )
        assert "teachback_approved" in result
        assert "teachback_corrections" in result

    def test_unaddressed_items_accepts_list(self):
        result = format_deny_reason(
            "unaddressed_items",
            context={
                "task_id": "7",
                "tool_name": "Write",
                "unaddressed": ["scope_a", "scope_b"],
            },
        )
        assert "scope_a, scope_b" in result

    def test_unaddressed_items_accepts_string(self):
        result = format_deny_reason(
            "unaddressed_items",
            context={
                "task_id": "7",
                "tool_name": "Write",
                "unaddressed": "scope_a, scope_b",
            },
        )
        assert "scope_a, scope_b" in result

    def test_corrections_pending_joins_lists(self):
        result = format_deny_reason(
            "corrections_pending",
            context={
                "task_id": "99",
                "tool_name": "Edit",
                "corrections_issues": [
                    "most_likely_wrong too generic",
                    "first_action missing citation",
                ],
                "corrections_targets": ["most_likely_wrong", "first_action"],
            },
        )
        assert "too generic" in result
        assert "missing citation" in result
        assert "most_likely_wrong, first_action" in result


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestFormatDenyReasonGraceful:
    def test_unknown_reason_code_returns_empty_string(self):
        assert format_deny_reason("no_such_reason", context={}) == ""

    def test_missing_placeholders_fall_back_to_defaults(self):
        # Minimal context — _DEFAULT_CONTEXT fills the rest
        result = format_deny_reason("missing_submit", context={})
        # Placeholders that were provided as defaults should still render.
        # task_id default is "" so the rendered TaskUpdate line shows empty.
        assert 'TaskUpdate(taskId=""' in result
        # variety_total default 0 + threshold default 7
        assert "variety 0" in result
        assert "threshold 7" in result

    def test_none_context_tolerated(self):
        # None context is equivalent to empty dict
        result = format_deny_reason("awaiting_approval", context=None)  # type: ignore[arg-type]
        assert "teachback_approved" in result

    def test_format_error_returns_minimal_fallback(self, monkeypatch):
        # Inject a template with an unknown placeholder to force KeyError
        from shared import teachback_example as te

        bad_template = "Send a teachback: {nonexistent_placeholder}"
        monkeypatch.setitem(te._DENY_TEMPLATES, "_test_broken", bad_template)

        result = format_deny_reason("_test_broken", context={"tool_name": "Edit"})
        assert "Send a teachback before Edit" in result
        assert "_test_broken" in result  # reason_code surfaced in fallback


# ---------------------------------------------------------------------------
# Template curly-brace escaping (JSON examples must survive format())
# ---------------------------------------------------------------------------

class TestBraceEscaping:
    """Literal JSON braces in templates must escape through format()."""

    def test_missing_submit_full_renders_literal_json(self):
        result = format_deny_reason(
            "missing_submit",
            context={"task_id": "5", "tool_name": "Edit", "variety_total": 10, "threshold": 7},
            protocol_level="full",
        )
        # Literal { and } from the TaskUpdate JSON example must survive
        assert '{"teachback_submit"' in result
        # And there must be no unescaped placeholder leftovers
        assert "{task_id}" not in result
        assert "{tool_name}" not in result


# ---------------------------------------------------------------------------
# Module importability smoke test
# ---------------------------------------------------------------------------

class TestModuleSurface:
    def test_format_deny_reason_is_public(self):
        assert callable(getattr(teachback_example, "format_deny_reason", None))

    def test_deny_templates_exposed_for_drift_tests(self):
        assert isinstance(teachback_example._DENY_TEMPLATES, dict)

    def test_imperative_words_exposed_for_drift_tests(self):
        assert isinstance(teachback_example._IMPERATIVE_FIRST_WORDS, frozenset)
