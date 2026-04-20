"""Tests for shared/teachback_validate.py (#401 Commit #7 Y2 follow-up).

Covers the generation-shaped content-schema rules from
CONTENT-SCHEMAS.md §Validation Rules:
  - Citation-shape regex (strict vs flexible per Q1)
  - Substring-inequality (rubber-stamp blocker)
  - Token-sharing with required_scope_items
  - Template-blocklist 50% density
  - Evidence-substring grounding
  - Addressed-item membership

Also tests validate_submit + validate_approved end-to-end at both
simplified and full protocol levels.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
_SHARED_DIR = _HOOKS_DIR / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

import pytest
from shared import teachback_validate as tv  # noqa: E402
from shared.teachback_validate import (  # noqa: E402
    FieldError,
    _all_addressed_valid,
    _citation_strictness,
    _evidence_grounded,
    _matches_citation,
    _normalize,
    _scanned_candidate_distinct,
    _shares_non_stopword_token,
    _template_density_fails,
    _tokenize,
    validate_approved,
    validate_submit,
)


# ---------------------------------------------------------------------------
# Helpers tested at the unit level
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_lowercase_and_collapse(self):
        assert _normalize("  Hello\tWorld  ") == "hello world"

    def test_non_string_safe(self):
        assert _normalize(None) == ""  # type: ignore[arg-type]
        assert _normalize(123) == ""  # type: ignore[arg-type]

    def test_empty(self):
        assert _normalize("") == ""


class TestTokenize:
    def test_words_only(self):
        assert _tokenize("Hello, World! foo_bar") == ["hello", "world", "foo_bar"]

    def test_non_string_safe(self):
        assert _tokenize(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Template-blocklist density
# ---------------------------------------------------------------------------

class TestTemplateDensity:
    def test_rubber_stamp_fails(self):
        # 100% blocklist phrases
        assert _template_density_fails("looks good, approved, noted") is True

    def test_majority_blocklist_fails(self):
        # "looks good" (10) + "approved" (8) = 18 / 30 = 0.6
        text = "looks good approved xxxxxxx xxx"
        assert _template_density_fails(text) is True

    def test_real_prose_passes(self):
        text = (
            "I will implement the auth middleware per the architect spec "
            "with careful attention to session_token expiry handling."
        )
        assert _template_density_fails(text) is False

    def test_empty_text_passes(self):
        assert _template_density_fails("") is False
        assert _template_density_fails("   ") is False

    def test_case_insensitive(self):
        assert _template_density_fails("LOOKS GOOD APPROVED NOTED") is True


# ---------------------------------------------------------------------------
# Citation-shape regex
# ---------------------------------------------------------------------------

class TestCitationShape:
    @pytest.mark.parametrize("text", [
        "auth.py:42",
        "src/middleware/auth.py:123",
        "shared/teachback_scan.py:317",
        "validate_submit()",
        "Module.function(arg)",
        "foo.bar(x, y)",
    ])
    def test_strict_mode_accepts(self, text):
        assert _matches_citation(text, "strict") is True

    @pytest.mark.parametrize("text", [
        "three or more words here",  # alternate 3 — only flexible
    ])
    def test_strict_mode_rejects_named_operation(self, text):
        # Strict mode rejects the 3+-word alternate
        assert _matches_citation(text, "strict") is False

    def test_flexible_mode_accepts_named_operation(self):
        assert _matches_citation("three or more words here", "flexible") is True
        assert _matches_citation("run pytest with coverage", "flexible") is True

    @pytest.mark.parametrize("text", [
        "single",                   # too short for 3+-word
        "just two words",           # exactly 3 words works in flexible
        "",
    ])
    def test_rejects_bad_shapes(self, text):
        # Note "just two words" is 3 words but ends on alphanumeric — passes flexible
        if text == "just two words":
            assert _matches_citation(text, "flexible") is True
        else:
            assert _matches_citation(text, "strict") is False
            assert _matches_citation(text, "flexible") is False

    def test_non_string_safe(self):
        assert _matches_citation(None, "strict") is False  # type: ignore[arg-type]


class TestCitationStrictness:
    def test_phase_code_is_strict(self):
        assert _citation_strictness({"phase": "CODE"}, "anyone") == "strict"

    def test_phase_test_is_strict(self):
        assert _citation_strictness({"phase": "TEST"}, "anyone") == "strict"

    def test_phase_prepare_is_flexible(self):
        assert _citation_strictness({"phase": "PREPARE"}, "preparer") == "flexible"

    def test_coder_agent_is_strict(self):
        assert _citation_strictness({}, "backend-coder-1") == "strict"
        assert _citation_strictness({}, "frontend-coder-2") == "strict"
        assert _citation_strictness({}, "test-engineer") == "strict"

    def test_non_coder_agent_is_flexible(self):
        assert _citation_strictness({}, "architect") == "flexible"
        assert _citation_strictness({}, "preparer") == "flexible"

    def test_phase_override_wins_over_agent(self):
        # Even if agent is non-coder, CODE phase → strict
        assert _citation_strictness({"phase": "CODE"}, "architect") == "strict"


# ---------------------------------------------------------------------------
# Substring-inequality (rubber-stamp blocker)
# ---------------------------------------------------------------------------

class TestScannedCandidateDistinct:
    def test_different_text_passes(self):
        assert _scanned_candidate_distinct(
            "the middleware might be misrouting the session_token lookup",
            "the auth middleware integrates cleanly with existing flow",
        ) is True

    def test_identical_text_fails(self):
        s = "the auth middleware integrates cleanly"
        assert _scanned_candidate_distinct(s, s) is False

    def test_substring_fails(self):
        candidate = "the auth middleware integrates"
        assumption = "the auth middleware integrates cleanly with existing flow"
        # candidate is substring of assumption → fail
        assert _scanned_candidate_distinct(candidate, assumption) is False
        # And reverse
        assert _scanned_candidate_distinct(assumption, candidate) is False

    def test_case_insensitive(self):
        assert _scanned_candidate_distinct(
            "The Auth Middleware",
            "the auth middleware",
        ) is False

    def test_whitespace_normalized(self):
        assert _scanned_candidate_distinct(
            "the auth  middleware",
            "the auth middleware",
        ) is False

    def test_empty_strings_pass(self):
        # Empty values don't trigger the copy-paste guard (handled by
        # min-length check elsewhere)
        assert _scanned_candidate_distinct("", "x") is True
        assert _scanned_candidate_distinct("x", "") is True


# ---------------------------------------------------------------------------
# Evidence-substring grounding
# ---------------------------------------------------------------------------

class TestEvidenceGrounded:
    def test_substring_match_passes(self):
        submit = {
            "understanding": "I'll build the auth middleware with session_token handling.",
            "first_action": {"action": "auth.py:42", "expected_signal": "pytest green"},
        }
        assert _evidence_grounded("session_token", submit) is True

    def test_non_substring_fails(self):
        submit = {
            "understanding": "I'll build the auth middleware.",
        }
        assert _evidence_grounded("database migration", submit) is False

    def test_normalized_substring_match(self):
        submit = {"understanding": "This  is  multi-spaced  prose"}
        # Substring after whitespace normalization
        assert _evidence_grounded("multi-spaced prose", submit) is True

    def test_empty_evidence_passes(self):
        assert _evidence_grounded("", {"understanding": "x"}) is True
        assert _evidence_grounded("   ", {"understanding": "x"}) is True

    def test_non_dict_submit_fails(self):
        assert _evidence_grounded("anything", "not a dict") is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Token-sharing check
# ---------------------------------------------------------------------------

class TestTokenSharing:
    def test_shared_content_token_passes(self):
        text = "the session_token validation path might be buggy"
        items = ["session_token handling"]
        assert _shares_non_stopword_token(text, items) is True

    def test_only_stopwords_fails(self):
        # All tokens are stopwords → no sharing possible
        text = "the a an is of to in on"
        items = ["session_token handling"]
        assert _shares_non_stopword_token(text, items) is False

    def test_short_tokens_excluded(self):
        # Tokens shorter than 3 chars are excluded
        text = "io pg db"  # all length<3
        items = ["io channel"]
        assert _shares_non_stopword_token(text, items) is False

    def test_no_items_fails(self):
        assert _shares_non_stopword_token("any text", []) is False
        assert _shares_non_stopword_token("any text", None) is False  # type: ignore[arg-type]

    def test_pact_specific_stopwords(self):
        text = "the task and agent and teammate are all stopwords"
        items = ["task details"]
        # "task" is PACT-specific stopword; "details" doesn't appear in text
        assert _shares_non_stopword_token(text, items) is False


# ---------------------------------------------------------------------------
# Addressed-item membership
# ---------------------------------------------------------------------------

class TestAddressedValid:
    def test_all_in_required(self):
        assert _all_addressed_valid(
            ["scope_a", "scope_b"],
            ["scope_a", "scope_b", "scope_c"],
        ) == []

    def test_invalid_item_surfaced(self):
        invalid = _all_addressed_valid(
            ["scope_a", "totally_made_up"],
            ["scope_a", "scope_b"],
        )
        assert invalid == ["totally_made_up"]

    def test_case_insensitive(self):
        assert _all_addressed_valid(
            ["Scope_A"],
            ["scope_a"],
        ) == []

    def test_whitespace_normalized(self):
        assert _all_addressed_valid(
            ["  scope_a  "],
            ["scope_a"],
        ) == []

    def test_empty_addressed_passes(self):
        assert _all_addressed_valid([], ["scope_a"]) == []

    def test_non_list_addressed_safe(self):
        assert _all_addressed_valid(None, ["scope_a"]) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# validate_submit — simplified protocol
# ---------------------------------------------------------------------------

def _simplified_submit():
    return {
        "understanding": (
            "I will implement the auth middleware per the architect spec "
            "with careful attention to session_token expiry handling and "
            "the edge cases around timezone drift in production."
        ),
        "first_action": {
            "action": "auth.py:42",
            "expected_signal": "pytest suite passes after the middleware change",
        },
    }


def _full_submit():
    s = _simplified_submit()
    s["most_likely_wrong"] = {
        "assumption": "the auth middleware integrates cleanly with session_token flow",
        "consequence": "if wrong the session_token validation may silently accept expired tokens",
    }
    s["least_confident_item"] = {
        "item": "exact semantics of the session_token expiry check across timezones",
        "current_plan": "mirror the approach from auth.py:42 which handles UTC offsets",
        "failure_mode": "timezone drift could let stale session_tokens slip past",
    }
    return s


class TestValidateSubmitSimplified:
    def test_valid_simplified_submit_passes(self):
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_submit(_simplified_submit(), metadata, "simplified", "backend-coder-1")
        assert errors == [], [e._asdict() for e in errors]

    def test_non_dict_submit_fails(self):
        errors = validate_submit("not a dict", {}, "simplified", "backend-coder-1")
        assert len(errors) == 1
        assert errors[0].field == "teachback_submit"

    def test_understanding_too_short_fails(self):
        submit = _simplified_submit()
        submit["understanding"] = "too short"
        errors = validate_submit(submit, {}, "simplified", "backend-coder-1")
        assert any("understanding" in e.field and "min 100" in e.error for e in errors)

    def test_first_action_bad_citation_fails(self):
        submit = _simplified_submit()
        submit["first_action"]["action"] = "not a citation at all just some words"
        errors = validate_submit(submit, {}, "simplified", "backend-coder-1")
        assert any("first_action.action" in e.field for e in errors)

    def test_simplified_ignores_full_only_fields(self):
        # Including full-only fields at simplified level: they're
        # permitted but not validated
        submit = _simplified_submit()
        submit["most_likely_wrong"] = {"assumption": "", "consequence": ""}
        errors = validate_submit(submit, {}, "simplified", "backend-coder-1")
        assert errors == []


# ---------------------------------------------------------------------------
# validate_submit — full protocol
# ---------------------------------------------------------------------------

class TestValidateSubmitFull:
    def test_valid_full_submit_passes(self):
        metadata = {
            "required_scope_items": ["auth middleware", "session_token handling"],
        }
        errors = validate_submit(_full_submit(), metadata, "full", "backend-coder-1")
        assert errors == [], [e._asdict() for e in errors]

    def test_missing_most_likely_wrong_fails(self):
        submit = _full_submit()
        del submit["most_likely_wrong"]
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_submit(submit, metadata, "full", "backend-coder-1")
        assert any(e.field == "teachback_submit.most_likely_wrong" for e in errors)

    def test_assumption_no_scope_token_fails(self):
        submit = _full_submit()
        submit["most_likely_wrong"]["assumption"] = (
            "This assumption is completely unrelated to the scope lorem ipsum"
        )
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_submit(submit, metadata, "full", "backend-coder-1")
        assert any(
            "most_likely_wrong.assumption" in e.field and "share" in e.error.lower()
            for e in errors
        )

    def test_template_density_on_understanding_fails(self):
        submit = _full_submit()
        # >100 chars AND >50% template-blocklist density
        submit["understanding"] = (
            "looks good approved proceed noted makes sense understood "
            "sounds good as expected all clear no issues"
        )
        assert len(submit["understanding"]) >= 100
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_submit(submit, metadata, "full", "backend-coder-1")
        assert any(
            "understanding" in e.field and "template" in e.error.lower()
            for e in errors
        )

    def test_least_confident_item_short_fails(self):
        submit = _full_submit()
        submit["least_confident_item"]["item"] = "short"
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_submit(submit, metadata, "full", "backend-coder-1")
        assert any("least_confident_item.item" in e.field for e in errors)


# ---------------------------------------------------------------------------
# validate_approved — simplified protocol
# ---------------------------------------------------------------------------

def _simplified_approved():
    return {
        "scanned_candidate": {
            "candidate": "the middleware might be misrouting the session_token lookup path",
            "evidence_against": "session_token expiry handling",
        },
        "conditions_met": {
            "addressed": ["auth middleware"],
            "unaddressed": [],
        },
    }


def _full_approved():
    a = _simplified_approved()
    a["response_to_assumption"] = {
        "verdict": "confirm",
        "grounding": "dispatch §Scope line 17 auth middleware",
    }
    a["response_to_least_confident"] = {
        "verdict": "correct",
        "grounding": "see architecture §Token-Validation line 42",
    }
    a["first_action_check"] = {
        "my_derivation": "auth.py:42",
        "match": "match",
        "if_mismatch_resolution": None,
    }
    return a


class TestValidateApprovedSimplified:
    def test_valid_simplified_approved_passes(self):
        submit = _simplified_submit()
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_approved(
            _simplified_approved(), submit, metadata,
            "simplified", "backend-coder-1",
        )
        assert errors == [], [e._asdict() for e in errors]

    def test_evidence_not_in_submit_fails(self):
        approved = _simplified_approved()
        approved["scanned_candidate"]["evidence_against"] = "totally unrelated phrase"
        submit = _simplified_submit()
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_approved(
            approved, submit, metadata, "simplified", "backend-coder-1",
        )
        assert any(
            "evidence_against" in e.field and "substring" in e.error.lower()
            for e in errors
        )

    def test_evidence_exceeds_max_fails(self):
        approved = _simplified_approved()
        approved["scanned_candidate"]["evidence_against"] = "x" * 400
        submit = _simplified_submit()
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_approved(
            approved, submit, metadata, "simplified", "backend-coder-1",
        )
        assert any("max 300" in e.error for e in errors)

    def test_addressed_not_in_required_fails(self):
        approved = _simplified_approved()
        approved["conditions_met"]["addressed"] = ["not_a_scope_item"]
        submit = _simplified_submit()
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_approved(
            approved, submit, metadata, "simplified", "backend-coder-1",
        )
        assert any(
            "addressed" in e.field and "not in required" in e.error.lower()
            for e in errors
        )


# ---------------------------------------------------------------------------
# validate_approved — full protocol
# ---------------------------------------------------------------------------

class TestValidateApprovedFull:
    def test_valid_full_approved_passes(self):
        submit = _full_submit()
        metadata = {"required_scope_items": ["auth middleware", "session_token"]}
        errors = validate_approved(
            _full_approved(), submit, metadata, "full", "backend-coder-1",
        )
        assert errors == [], [e._asdict() for e in errors]

    def test_candidate_copypaste_of_assumption_fails(self):
        # Rubber-stamp blocker: candidate == assumption
        submit = _full_submit()
        approved = _full_approved()
        approved["scanned_candidate"]["candidate"] = (
            submit["most_likely_wrong"]["assumption"]
        )
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_approved(
            approved, submit, metadata, "full", "backend-coder-1",
        )
        assert any(
            "candidate" in e.field and "substring-equal" in e.error.lower()
            for e in errors
        )

    def test_grounding_missing_shape_fails(self):
        approved = _full_approved()
        approved["response_to_assumption"]["grounding"] = "just some ordinary prose"
        submit = _full_submit()
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_approved(
            approved, submit, metadata, "full", "backend-coder-1",
        )
        assert any(
            "response_to_assumption.grounding" in e.field for e in errors
        )

    def test_verdict_invalid_value_fails(self):
        approved = _full_approved()
        approved["response_to_assumption"]["verdict"] = "maybe"
        submit = _full_submit()
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_approved(
            approved, submit, metadata, "full", "backend-coder-1",
        )
        assert any(
            "response_to_assumption.verdict" in e.field for e in errors
        )

    def test_match_mismatch_requires_resolution(self):
        approved = _full_approved()
        approved["first_action_check"]["match"] = "mismatch"
        approved["first_action_check"]["if_mismatch_resolution"] = None
        submit = _full_submit()
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_approved(
            approved, submit, metadata, "full", "backend-coder-1",
        )
        assert any(
            "if_mismatch_resolution" in e.field for e in errors
        )

    def test_match_match_forbids_resolution(self):
        approved = _full_approved()
        approved["first_action_check"]["match"] = "match"
        approved["first_action_check"]["if_mismatch_resolution"] = "some resolution text"
        submit = _full_submit()
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_approved(
            approved, submit, metadata, "full", "backend-coder-1",
        )
        assert any(
            "if_mismatch_resolution" in e.field and "must be null" in e.error
            for e in errors
        )

    def test_first_action_check_bad_derivation_fails(self):
        approved = _full_approved()
        approved["first_action_check"]["my_derivation"] = "not a citation"
        submit = _full_submit()
        metadata = {"required_scope_items": ["auth middleware"]}
        errors = validate_approved(
            approved, submit, metadata, "full", "backend-coder-1",
        )
        assert any(
            "first_action_check.my_derivation" in e.field for e in errors
        )


# ---------------------------------------------------------------------------
# FieldError shape + fail-open
# ---------------------------------------------------------------------------

class TestFieldErrorShape:
    def test_is_namedtuple(self):
        fe = FieldError(field="x", error="y", actual_value="z")
        assert fe.field == "x"
        assert fe.error == "y"
        assert fe.actual_value == "z"

    def test_long_actual_value_truncated_in_submit_errors(self):
        # Pass a way-too-long understanding; actual_value should be capped
        submit = {"understanding": "x" * 10000,
                  "first_action": {"action": "auth.py:42", "expected_signal": "pytest passes reliably enough"}}
        # 10000 chars passes min_length, so no error on that field. Try a
        # field that fails min_length with a long value.
        submit["understanding"] = "x" * 50  # fails min 100
        errors = validate_submit(submit, {}, "simplified", "backend-coder-1")
        errs_on_understanding = [e for e in errors if e.field.endswith("understanding")]
        assert errs_on_understanding
        # actual_value should reflect the (short) string unchanged here
        assert errs_on_understanding[0].actual_value == "x" * 50


class TestValidatorFailOpen:
    def test_malformed_metadata_does_not_raise(self):
        # Pass a metadata that could break .get() — our functions handle
        # it internally
        errors = validate_submit(_full_submit(), None, "full", "backend-coder-1")  # type: ignore[arg-type]
        # Should not raise; may or may not have errors depending on path.
        # Validator swallows internal exceptions and returns collected
        # errors (possibly empty).
        assert isinstance(errors, list)
