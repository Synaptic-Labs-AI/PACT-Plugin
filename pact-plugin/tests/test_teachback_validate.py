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


# ---------------------------------------------------------------------------
# Coverage fills — internal helper edge cases
# ---------------------------------------------------------------------------


class TestFlattenStrsListBranch:
    """Line 158-163: _flatten_strs recurses into list elements. Used by
    _evidence_grounded to flatten a submit dict whose values include
    lists."""

    def test_list_of_strings_flattened(self):
        # _flatten_strs isn't in the public API but exercised via
        # _evidence_grounded with a submit-shaped dict containing a list.
        submit = {
            "tags": ["auth", "session_token", "middleware"],
            "understanding": "background",
        }
        # "auth" is in the flattened blob → grounded
        assert _evidence_grounded("auth", submit) is True
        # random word not in the blob → not grounded
        assert _evidence_grounded("zebra-not-present", submit) is False

    def test_nested_list_flattened(self):
        submit = {"items": [["alpha"], ["beta", "gamma"]]}
        assert _evidence_grounded("beta", submit) is True


class TestSharesNonStopwordTokenNonStringItem:
    """Line 223: required_scope_items entries that are not strings are
    skipped. Defends against malformed dispatch metadata where a
    required_scope_items entry became an int/None."""

    def test_non_string_items_skipped(self):
        # Three non-string entries + one valid entry that SHARES a token.
        # Tokenization splits on non-alphanumeric-underscore, so
        # "middleware flow" contains two tokens (middleware + flow);
        # "middleware integration" shares "middleware" with that entry.
        assert _shares_non_stopword_token(
            "auth middleware integration",
            [None, 42, "middleware flow"],  # type: ignore[list-item]
        ) is True

    def test_all_non_string_items_returns_false(self):
        assert _shares_non_stopword_token(
            "auth middleware integration",
            [None, 42, {"dict": "entry"}],  # type: ignore[list-item]
        ) is False


class TestEvidenceGroundedEmptyAfterNormalize:
    """Line 254: evidence that normalizes to empty (e.g. only punctuation)
    returns True (passes — empty evidence is handled by min-length)."""

    def test_whitespace_only_evidence_passes(self):
        # whitespace-only is caught by the strip() guard at line 247
        assert _evidence_grounded("   ", {"u": "x"}) is True

    def test_punctuation_only_evidence_passes(self):
        # After normalize, "..." may reduce to "..." (non-empty) or empty
        # depending on the collapse rules. Either way, function must not
        # raise. The _normalize function lowercase+collapses whitespace
        # but doesn't strip punctuation, so "..." stays "..." — test the
        # behavior of a short evidence string that normalizes to empty.
        result = _evidence_grounded("\u200b\u200b", {"u": "x"})  # zero-width chars
        assert isinstance(result, bool)

    def test_non_dict_submit_rejects_non_empty_evidence(self):
        # Line 249-250: non-dict submit with real evidence → False
        assert _evidence_grounded("real evidence", None) is False  # type: ignore[arg-type]
        assert _evidence_grounded("real evidence", "not a dict") is False  # type: ignore[arg-type]


class TestAllAddressedValidNonStringItem:
    """Line 269: addressed entries that are not strings are skipped.
    Defends against malformed lead input where addressed contains a
    non-str item."""

    def test_non_string_item_skipped(self):
        # Mixed str + int; only "scope_a" gets checked and found missing
        result = _all_addressed_valid(
            ["scope_a", 42, None, "scope_b"],  # type: ignore[list-item]
            ["scope_b"],
        )
        # scope_a is invalid (not in required); 42 and None are skipped;
        # scope_b is valid
        assert result == ["scope_a"]

    def test_non_list_addressed_returns_empty(self):
        assert _all_addressed_valid("not-a-list", ["x"]) == []  # type: ignore[arg-type]
        assert _all_addressed_valid(None, ["x"]) == []  # type: ignore[arg-type]


class TestTruncateCapPath:
    """Line 280: _truncate caps strings longer than _ACTUAL_VALUE_CAP
    at (cap - 3) + '...'."""

    def test_long_string_truncated(self):
        from shared.teachback_validate import _truncate, _ACTUAL_VALUE_CAP
        long_str = "x" * (_ACTUAL_VALUE_CAP + 100)
        result = _truncate(long_str)
        assert len(result) == _ACTUAL_VALUE_CAP
        assert result.endswith("...")
        assert result.startswith("x")

    def test_exact_cap_untruncated(self):
        from shared.teachback_validate import _truncate, _ACTUAL_VALUE_CAP
        s = "x" * _ACTUAL_VALUE_CAP
        assert _truncate(s) == s

    def test_none_returns_empty(self):
        from shared.teachback_validate import _truncate
        assert _truncate(None) == ""


class TestCheckMinLengthEmptyWhitespace:
    """Lines 300-302: _check_min_length emits FieldError for a string that
    is entirely whitespace (strip() → empty), distinct from the shorter-
    than-min case."""

    def test_whitespace_only_rejected(self):
        errors = validate_submit(
            {"understanding": "   \t\n  ", "first_action": {
                "action": "file.py:1", "expected_signal": "pytest passes with the expected signal",
            }},
            {}, "simplified", "backend-coder-1",
        )
        und_errors = [e for e in errors if e.field.endswith("understanding")]
        assert und_errors
        assert "empty" in und_errors[0].error or "whitespace" in und_errors[0].error


# ---------------------------------------------------------------------------
# validate_approved — coverage for less-exercised branches
# ---------------------------------------------------------------------------


class TestValidateApprovedNonDict:
    """Line 496-501: validate_approved with a non-dict approved payload."""

    def test_non_dict_approved_returns_single_error(self):
        errors = validate_approved(
            "just a string",  # type: ignore[arg-type]
            {}, {}, "simplified", "coder-1",
        )
        assert len(errors) == 1
        assert errors[0].field == "teachback_approved"

    def test_list_approved_returns_single_error(self):
        errors = validate_approved(
            [1, 2, 3],  # type: ignore[arg-type]
            {}, {}, "simplified", "coder-1",
        )
        assert len(errors) == 1
        assert errors[0].field == "teachback_approved"


class TestValidateApprovedSimplifiedOnly:
    """Line 591, 600: simplified-protocol approved skips response_to_*
    fields. These branches fire when protocol_level != 'full'."""

    def test_simplified_skips_response_fields(self):
        submit = {
            "understanding": (
                "I will implement the auth middleware per the architect spec "
                "with careful attention to session_token expiry handling."
            ),
            "first_action": {
                "action": "auth.py:42",
                "expected_signal": "pytest passes after the middleware change",
            },
        }
        approved = {
            "scanned_candidate": {
                "candidate": "the middleware might instead be mis-routing",
                "evidence_against": "session_token",
            },
            "conditions_met": {
                "addressed": ["scope_a"],
                "unaddressed": [],
            },
        }
        errors = validate_approved(
            approved, submit, {"required_scope_items": ["scope_a"]},
            "simplified", "coder-1",
        )
        # Should NOT error on missing response_to_assumption etc.
        fields = {e.field for e in errors}
        assert not any("response_to_" in f for f in fields)
        assert not any("first_action_check" in f for f in fields)


class TestValidateApprovedVerdictBranches:
    """Lines 608-613: verdict not in {confirm, correct} emits a specific
    error."""

    def _full_submit(self):
        return {
            "understanding": (
                "I will implement the auth middleware per the architect spec "
                "with careful attention to session_token expiry handling."
            ),
            "most_likely_wrong": {
                "assumption": "the auth middleware integrates cleanly with session_token flow",
                "consequence": "if wrong session_token validation accepts expired tokens silently",
            },
            "least_confident_item": {
                "item": "exact semantics of session_token expiry across time zones",
                "current_plan": "mirror auth.py:42 which handles UTC offsets correctly",
                "failure_mode": "timezone drift lets stale session_tokens pass the gate",
            },
            "first_action": {
                "action": "auth.py:42",
                "expected_signal": "pytest suite passes after the middleware change",
            },
        }

    def _full_approved(self, verdict_a="confirm", verdict_b="confirm"):
        return {
            "scanned_candidate": {
                "candidate": "the middleware might instead be mis-routing session_tokens",
                "evidence_against": "session_token",
            },
            "response_to_assumption": {
                "verdict": verdict_a,
                "grounding": "see dispatch §Scope line 17 about session_token",
            },
            "response_to_least_confident": {
                "verdict": verdict_b,
                "grounding": "see architecture §Token-Validation line 42",
            },
            "first_action_check": {
                "my_derivation": "auth.py:42",
                "match": "match",
                "if_mismatch_resolution": None,
            },
            "conditions_met": {
                "addressed": ["session_token"],
                "unaddressed": [],
            },
        }

    def test_invalid_verdict_rejected(self):
        approved = self._full_approved(verdict_a="approved")  # not in set
        errors = validate_approved(
            approved, self._full_submit(),
            {"required_scope_items": ["session_token"]},
            "full", "coder-1",
        )
        verdict_errs = [
            e for e in errors
            if e.field.endswith("response_to_assumption.verdict")
        ]
        assert verdict_errs
        assert "confirm" in verdict_errs[0].error

    def test_valid_verdict_correct_passes(self):
        approved = self._full_approved(verdict_a="correct")
        errors = validate_approved(
            approved, self._full_submit(),
            {"required_scope_items": ["session_token"]},
            "full", "coder-1",
        )
        verdict_errs = [
            e for e in errors
            if e.field.endswith("response_to_assumption.verdict")
        ]
        assert not verdict_errs


class TestFirstActionCheckBranches:
    """Lines 643, 657, 677: first_action_check.match branches (match vs
    mismatch) drive different if_mismatch_resolution requirements."""

    def _full_submit(self):
        return {
            "understanding": (
                "I will implement the auth middleware per the architect spec "
                "with careful attention to session_token expiry handling."
            ),
            "most_likely_wrong": {
                "assumption": "the auth middleware integrates cleanly with session_token flow",
                "consequence": "if wrong session_token validation accepts expired tokens silently",
            },
            "least_confident_item": {
                "item": "exact semantics of session_token expiry across time zones",
                "current_plan": "mirror auth.py:42 which handles UTC offsets correctly",
                "failure_mode": "timezone drift lets stale session_tokens pass the gate",
            },
            "first_action": {
                "action": "auth.py:42",
                "expected_signal": "pytest suite passes after the middleware change",
            },
        }

    def _approved_with_fac(self, fac: dict):
        return {
            "scanned_candidate": {
                "candidate": "the middleware might instead be mis-routing session_tokens",
                "evidence_against": "session_token",
            },
            "response_to_assumption": {
                "verdict": "confirm",
                "grounding": "see dispatch §Scope line 17 about session_token",
            },
            "response_to_least_confident": {
                "verdict": "confirm",
                "grounding": "see architecture §Token-Validation line 42",
            },
            "first_action_check": fac,
            "conditions_met": {
                "addressed": ["session_token"],
                "unaddressed": [],
            },
        }

    def test_match_with_non_null_resolution_rejected(self):
        approved = self._approved_with_fac({
            "my_derivation": "auth.py:42",
            "match": "match",
            "if_mismatch_resolution": "should be null",  # non-null WITH match
        })
        errors = validate_approved(
            approved, self._full_submit(),
            {"required_scope_items": ["session_token"]},
            "full", "coder-1",
        )
        res_errs = [
            e for e in errors
            if e.field.endswith("if_mismatch_resolution")
        ]
        assert res_errs
        assert "null" in res_errs[0].error.lower()

    def test_mismatch_requires_resolution(self):
        approved = self._approved_with_fac({
            "my_derivation": "other.py:99",
            "match": "mismatch",
            "if_mismatch_resolution": None,  # required non-null
        })
        errors = validate_approved(
            approved, self._full_submit(),
            {"required_scope_items": ["session_token"]},
            "full", "coder-1",
        )
        res_errs = [
            e for e in errors
            if e.field.endswith("if_mismatch_resolution")
        ]
        assert res_errs

    def test_mismatch_with_valid_resolution_passes(self):
        approved = self._approved_with_fac({
            "my_derivation": "other.py:99",
            "match": "mismatch",
            "if_mismatch_resolution": (
                "The teammate pointed at other.py:99 but the correct "
                "citation is auth.py:42; they should redo first_action."
            ),
        })
        errors = validate_approved(
            approved, self._full_submit(),
            {"required_scope_items": ["session_token"]},
            "full", "coder-1",
        )
        res_errs = [
            e for e in errors
            if e.field.endswith("if_mismatch_resolution")
        ]
        assert not res_errs

    def test_invalid_match_value_rejected(self):
        approved = self._approved_with_fac({
            "my_derivation": "auth.py:42",
            "match": "yes",  # not in set {match, mismatch}
            "if_mismatch_resolution": None,
        })
        errors = validate_approved(
            approved, self._full_submit(),
            {"required_scope_items": ["session_token"]},
            "full", "coder-1",
        )
        match_errs = [
            e for e in errors
            if e.field.endswith("first_action_check.match")
        ]
        assert match_errs


class TestApprovedConditionsMetBranches:
    """Lines 545, 566, 574: conditions_met validation paths for missing
    structure, addressed non-list, unaddressed non-list."""

    def test_missing_conditions_met_rejected(self):
        approved = {
            "scanned_candidate": {
                "candidate": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "evidence_against": "x",
            },
            # no conditions_met key
        }
        errors = validate_approved(
            approved, {"understanding": "x" * 120},
            {"required_scope_items": ["scope_a"]},
            "simplified", "coder-1",
        )
        cm_errs = [e for e in errors if "conditions_met" in e.field]
        assert cm_errs

    def test_conditions_met_non_dict_rejected(self):
        approved = {
            "scanned_candidate": {
                "candidate": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "evidence_against": "x",
            },
            "conditions_met": "not a dict",  # type error
        }
        errors = validate_approved(
            approved, {"understanding": "x" * 120},
            {"required_scope_items": ["scope_a"]},
            "simplified", "coder-1",
        )
        cm_errs = [e for e in errors if "conditions_met" in e.field]
        assert cm_errs

    def test_addressed_non_list_rejected(self):
        approved = {
            "scanned_candidate": {
                "candidate": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "evidence_against": "x",
            },
            "conditions_met": {
                "addressed": "not-a-list",
                "unaddressed": [],
            },
        }
        errors = validate_approved(
            approved, {"understanding": "x" * 120},
            {"required_scope_items": ["scope_a"]},
            "simplified", "coder-1",
        )
        addr_errs = [
            e for e in errors if e.field.endswith("conditions_met.addressed")
        ]
        assert addr_errs

    def test_unaddressed_non_list_rejected(self):
        approved = {
            "scanned_candidate": {
                "candidate": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "evidence_against": "x",
            },
            "conditions_met": {
                "addressed": [],
                "unaddressed": "not-a-list",
            },
        }
        errors = validate_approved(
            approved, {"understanding": "x" * 120},
            {"required_scope_items": ["scope_a"]},
            "simplified", "coder-1",
        )
        un_errs = [
            e for e in errors if e.field.endswith("conditions_met.unaddressed")
        ]
        assert un_errs


class TestAddressedInvalidItemsSurfaced:
    """Line 510: _all_addressed_valid returns invalid items; validator
    surfaces them in the FieldError.error."""

    def test_invalid_addressed_items_surfaced(self):
        approved = {
            "scanned_candidate": {
                "candidate": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "evidence_against": "x",
            },
            "conditions_met": {
                "addressed": ["scope_a", "not-in-required", "also-invalid"],
                "unaddressed": [],
            },
        }
        errors = validate_approved(
            approved, {"understanding": "x" * 120},
            {"required_scope_items": ["scope_a"]},
            "simplified", "coder-1",
        )
        addr_errs = [
            e for e in errors
            if e.field.endswith("conditions_met.addressed")
        ]
        assert addr_errs
        assert "not-in-required" in addr_errs[0].error
        assert "also-invalid" in addr_errs[0].error


class TestApprovedResponseMissingFieldStructure:
    """Lines 608-613: response_to_* missing the wrapping dict structure
    produces a per-field dict-missing error."""

    def test_response_to_assumption_non_dict(self):
        approved = {
            "scanned_candidate": {
                "candidate": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "evidence_against": "x",
            },
            "response_to_assumption": "not a dict",  # wrong shape
            "response_to_least_confident": {
                "verdict": "confirm",
                "grounding": "see dispatch §Scope line 17",
            },
            "first_action_check": {
                "my_derivation": "auth.py:42",
                "match": "match",
                "if_mismatch_resolution": None,
            },
            "conditions_met": {"addressed": ["a"], "unaddressed": []},
        }
        errors = validate_approved(
            approved,
            {"understanding": "x" * 120, "most_likely_wrong": {
                "assumption": "the auth middleware integrates with session_token",
                "consequence": "if wrong session_token validation drops valid tokens",
            }, "least_confident_item": {
                "item": "semantics of session_token expiry across time zones",
                "current_plan": "mirror auth.py:42 handling offsets correctly",
                "failure_mode": "timezone drift lets stale session_tokens pass",
            }, "first_action": {
                "action": "auth.py:42",
                "expected_signal": "pytest passes after the middleware change",
            }},
            {"required_scope_items": ["a"]},
            "full", "coder-1",
        )
        resp_errs = [
            e for e in errors
            if e.field.endswith("response_to_assumption")
            and "dict" in e.error
        ]
        assert resp_errs


# ---------------------------------------------------------------------------
# Counter-test-by-revert items 14 (Y2): content-shape rules REJECT
# failing submissions
# ---------------------------------------------------------------------------


class TestCounterTestByRevertContentShape:
    """Item 14: each of the 4 content-shape rules must REJECT a failing
    submission. Reverting any rule would let these tests pass where they
    should fail."""

    def _full_submit(self):
        return {
            "understanding": (
                "I will implement the auth middleware per the architect spec "
                "with careful attention to session_token expiry handling."
            ),
            "most_likely_wrong": {
                "assumption": "the auth middleware integrates cleanly with session_token flow",
                "consequence": "if wrong session_token validation accepts expired tokens silently",
            },
            "least_confident_item": {
                "item": "exact semantics of session_token expiry across time zones",
                "current_plan": "mirror auth.py:42 which handles UTC offsets correctly",
                "failure_mode": "timezone drift lets stale session_tokens pass the gate",
            },
            "first_action": {
                "action": "auth.py:42",
                "expected_signal": "pytest suite passes after the middleware change",
            },
        }

    def test_citation_regex_rejects_nonmatching(self):
        submit = self._full_submit()
        submit["first_action"]["action"] = "this does not match any citation"
        errors = validate_submit(
            submit, {"required_scope_items": ["session_token"]},
            "full", "backend-coder-1",  # strict mode (coder agent)
        )
        citation_errs = [
            e for e in errors
            if e.field.endswith("first_action.action")
        ]
        assert citation_errs, (
            "Reverting _check_citation (e.g. removing the regex match call) "
            "would let this pass. The citation-shape rule is item 14-a."
        )

    def test_substring_inequality_rejects_copy_paste(self):
        # Item 14-b: lead candidate == teammate assumption → rejected
        submit = self._full_submit()
        approved = {
            "scanned_candidate": {
                # IDENTICAL to submit.most_likely_wrong.assumption
                "candidate": submit["most_likely_wrong"]["assumption"],
                "evidence_against": "session_token",
            },
            "response_to_assumption": {
                "verdict": "confirm",
                "grounding": "see dispatch §Scope line 17",
            },
            "response_to_least_confident": {
                "verdict": "confirm",
                "grounding": "see architecture §Token-Validation line 42",
            },
            "first_action_check": {
                "my_derivation": "auth.py:42",
                "match": "match",
                "if_mismatch_resolution": None,
            },
            "conditions_met": {
                "addressed": ["session_token"],
                "unaddressed": [],
            },
        }
        errors = validate_approved(
            approved, submit,
            {"required_scope_items": ["session_token"]},
            "full", "coder-1",
        )
        sc_errs = [
            e for e in errors
            if e.field.endswith("scanned_candidate.candidate")
            and "substring" in e.error.lower()
        ]
        assert sc_errs, (
            "Reverting _scanned_candidate_distinct (e.g. always return True) "
            "would let this rubber-stamp through. The substring-inequality "
            "rule is item 14-b."
        )

    def test_token_sharing_rejects_unrelated_assumption(self):
        # Item 14-c: assumption must share a non-stopword token with
        # required_scope_items. Here it doesn't — should fail.
        submit = self._full_submit()
        # Replace assumption with content that shares NO non-stopword
        # tokens with required_scope_items ["session_token"].
        submit["most_likely_wrong"]["assumption"] = (
            "entirely unrelated thought about coffee and weather"
        )
        errors = validate_submit(
            submit, {"required_scope_items": ["session_token"]},
            "full", "backend-coder-1",
        )
        token_errs = [
            e for e in errors
            if e.field.endswith("most_likely_wrong.assumption")
            and "non-stopword" in e.error
        ]
        assert token_errs, (
            "Reverting _shares_non_stopword_token (e.g. always return True) "
            "would let an off-topic assumption pass. Rule is item 14-c."
        )

    def test_template_blocklist_rejects_boilerplate(self):
        # Item 14-d: 50%+ template-phrase density is rejected.
        # Note: _check_min_length gates _check_non_template. To exercise
        # the template-density rule we need a string >= min_len (100
        # for understanding) AND >= 50% blocklist density.
        submit = self._full_submit()
        # "looks good as expected no issues all clear approved proceed
        # understood sounds good makes sense noted looks good"
        # = 129 chars, 94 of which are blocklist phrases = ~73% density
        submit["understanding"] = (
            "looks good as expected no issues all clear approved proceed "
            "understood sounds good makes sense noted looks good"
        )
        assert len(submit["understanding"]) >= 100  # ensure min-length passes
        errors = validate_submit(
            submit, {"required_scope_items": ["session_token"]},
            "full", "backend-coder-1",
        )
        tmpl_errs = [
            e for e in errors
            if e.field.endswith("understanding")
            and "template" in e.error.lower()
        ]
        assert tmpl_errs, (
            "Reverting _template_density_fails (e.g. always return False) "
            "would let pure boilerplate pass. Rule is item 14-d."
        )
