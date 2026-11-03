"""Tests for shared/handoff_schema.py — canonical handoff schema constants,
the required/recommended split, the legacy alias map, and both validators.

Pins:
  - HANDOFF_CANONICAL_FIELDS: the 6 names in template order
  - HANDOFF_REQUIRED_FIELDS: FIVE, derived by carving the recommended set out
    of the canonical set — never six, and never hard-coded
  - HANDOFF_LEGACY_ALIASES: exactly the three spellings the repo's own docs
    once taught; a fourth would be inference, not evidence
  - validate_handoff_schema: fires on a missing required key (naming a legacy
    alias when one is present) and stays SILENT on the three shapes the real
    handoff population is made of
  - resolve_handoff_field: canonical first, legacy alias only as fallback,
    both selected by membership in (None, '', [], {})

THE SILENT ARMS ARE THE LOAD-BEARING ONES. A validator that fires on a
recommended-field absence, on an extra key, or on a legitimately empty value
would fire on most real handoffs, so each of those has an explicit arm here.
"""

from __future__ import annotations


from fixtures.emitter import VALID_HANDOFF  # noqa: E402

from shared.handoff_schema import (  # noqa: E402
    HANDOFF_CANONICAL_FIELDS,
    HANDOFF_LEGACY_ALIASES,
    HANDOFF_RECOMMENDED_FIELDS,
    HANDOFF_REQUIRED_FIELDS,
    HANDOFF_SCHEMA_ECHO,
    resolve_handoff_field,
    validate_handoff_schema,
)


class TestConstants:
    def test_canonical_is_the_six_in_template_order(self):
        assert HANDOFF_CANONICAL_FIELDS == (
            "produced",
            "decisions",
            "reasoning_chain",
            "uncertainty",
            "integration",
            "open_questions",
        )

    def test_required_is_five_and_excludes_reasoning_chain(self):
        """The 6->5 correction. A six-field requirement fires on handoffs the
        template surfaces themselves call correct."""
        assert len(HANDOFF_REQUIRED_FIELDS) == 5
        assert "reasoning_chain" not in HANDOFF_REQUIRED_FIELDS

    def test_required_agrees_with_the_canonical_minus_recommended_derivation(self):
        """REQUIRED equals canonical-minus-recommended AS EVALUATED NOW.

        NAMED FOR WHAT IT PINS, WHICH IS NARROWER THAN "is derived". Measured:
        replacing the derivation with a hard-coded tuple of the SAME five names
        leaves this arm green — a literal that happens to agree is
        indistinguishable from a derivation here. What it does catch is the
        DRIFT that duplication eventually produces: hard-code the tuple AND
        move a field across the split, and this reds.

        Detecting the duplication itself would mean reading this module's own
        source, which pins the spelling of an implementation rather than its
        behaviour. A correctly-named weaker arm is worth more than a
        strongly-named one that cannot support its name.
        """
        assert HANDOFF_REQUIRED_FIELDS == tuple(
            f for f in HANDOFF_CANONICAL_FIELDS
            if f not in HANDOFF_RECOMMENDED_FIELDS
        )

    def test_alias_map_is_exactly_the_three_taught_spellings(self):
        assert HANDOFF_LEGACY_ALIASES == {
            "key_decisions": "decisions",
            "areas_of_uncertainty": "uncertainty",
            "integration_points": "integration",
        }

    def test_every_alias_target_is_a_canonical_field(self):
        assert set(HANDOFF_LEGACY_ALIASES.values()) <= set(HANDOFF_CANONICAL_FIELDS)

    def test_schema_echo_names_every_field_and_states_the_counts(self):
        """The echo enumerates all six names and states the 5/1 split.

        NAMED FOR WHAT IT PINS, WHICH IS NARROWER THAN "derives". A substring
        check cannot separate a DERIVED echo from a HARD-CODED one carrying
        the same text, and the strengthening that would is not available here:
        building the expected counts from len() of the tuples is the CIRCULAR
        form a pin exists to avoid, and detecting the hard-coding itself means
        reading this module's own source, which pins an implementation's
        spelling rather than its behaviour.

        THE LITERAL IS DELIBERATE AND MUST STAY A LITERAL. It restates the
        split independently, so it reds when the echo and the tuples diverge
        in either direction -- which is the drift that matters and the only
        thing any form of this arm could catch.
        """
        for field in HANDOFF_CANONICAL_FIELDS:
            assert field in HANDOFF_SCHEMA_ECHO
        assert "5 required, 1 recommended" in HANDOFF_SCHEMA_ECHO


class TestValidatorFires:
    def test_missing_required_key_is_named(self):
        handoff = {f: ["x"] for f in HANDOFF_REQUIRED_FIELDS if f != "decisions"}
        problem = validate_handoff_schema(handoff)
        assert problem is not None
        assert "decisions" in problem

    def test_legacy_spelling_is_named_as_the_alias_found(self):
        """The case the issue reports: legacy-spelled and otherwise perfect.
        One advisory that names both the missing key and the alias found."""
        handoff = {f: ["x"] for f in HANDOFF_REQUIRED_FIELDS if f != "decisions"}
        handoff["key_decisions"] = ["Used JWT"]
        problem = validate_handoff_schema(handoff)
        assert problem is not None
        assert "key_decisions" in problem and "decisions" in problem

    def test_non_dict_is_named_with_its_type(self):
        assert "str" in (validate_handoff_schema("not a handoff") or "")
        assert "NoneType" in (validate_handoff_schema(None) or "")


class TestValidatorStaysSilent:
    def test_silent_on_the_repos_own_valid_fixture(self):
        """VALID_HANDOFF carries the five and NOT reasoning_chain. If this ever
        fires, the required set has drifted back to six."""
        assert validate_handoff_schema(VALID_HANDOFF) is None

    def test_silent_when_reasoning_chain_absent(self):
        """CONSTRUCTS the absence, rather than naming a fixture that already
        has it. Start from the full canonical set — pinned silent by
        test_silent_on_a_fully_canonical_handoff below — and remove exactly
        reasoning_chain, so this arm reds only for a required set that has
        grown back to six.

        The earlier form passed dict(VALID_HANDOFF), a plain copy of a fixture
        already missing reasoning_chain, which made it an exact duplicate of
        test_silent_on_the_repos_own_valid_fixture: measured across 29
        mutations, no mutant separated the two.
        """
        handoff = {f: ["x"] for f in HANDOFF_CANONICAL_FIELDS}
        del handoff["reasoning_chain"]
        assert validate_handoff_schema(handoff) is None

    def test_silent_on_extra_keys(self):
        handoff = dict(VALID_HANDOFF, memory_saved=["m1"], findings=["f1"])
        assert validate_handoff_schema(handoff) is None

    def test_silent_on_present_but_empty_values(self):
        """Emptiness is the norm, not a defect — the templates sanction
        'No areas of uncertainty flagged' explicitly."""
        handoff = {f: [] for f in HANDOFF_REQUIRED_FIELDS}
        assert validate_handoff_schema(handoff) is None

    def test_silent_on_a_fully_canonical_handoff(self):
        handoff = {f: ["x"] for f in HANDOFF_CANONICAL_FIELDS}
        assert validate_handoff_schema(handoff) is None


class TestResolveHandoffField:
    def test_canonical_wins_over_alias(self):
        handoff = {"decisions": ["canonical"], "key_decisions": ["legacy"]}
        assert resolve_handoff_field(handoff, "decisions") == ["canonical"]

    def test_alias_read_when_canonical_absent(self):
        assert resolve_handoff_field({"key_decisions": ["legacy"]}, "decisions") == [
            "legacy"
        ]

    def test_all_three_aliases_resolve(self):
        for alias, canonical in HANDOFF_LEGACY_ALIASES.items():
            assert resolve_handoff_field({alias: ["v"]}, canonical) == ["v"]

    def test_fallback_keys_on_emptiness_not_on_renderability(self):
        """The shadowing edge, pinned in BOTH directions.

        The fallback fires "when the canonical key is absent or empty (None,
        '', [], {})", so MEMBERSHIP is the key -- not whether the value
        renders as anything. The second row is the one no other arm reaches:
        test_canonical_wins_over_alias uses a renderable canonical, so it
        cannot show which property the fallback actually tests.
        """
        # Empty canonical -> falls through to the legacy spelling.
        assert resolve_handoff_field(
            {"decisions": [], "key_decisions": ["legacy"]}, "decisions"
        ) == ["legacy"]
        # A non-empty canonical wins even though it renders as nothing,
        # shadowing a legacy value that would have rendered. Documented, not a
        # defect.
        assert resolve_handoff_field(
            {"decisions": [""], "key_decisions": ["legacy"]}, "decisions"
        ) == [""]

    def test_zero_and_false_are_content_on_both_spellings(self):
        """Membership, not truthiness: a canonical 0 or False wins over an
        alias, and a canonical None yields an alias 0. Kills a truthiness test
        on either the canonical or the alias side."""
        assert resolve_handoff_field(
            {"decisions": None, "key_decisions": 0}, "decisions"
        ) == 0
        assert resolve_handoff_field(
            {"decisions": 0, "key_decisions": "x"}, "decisions"
        ) == 0
        assert resolve_handoff_field(
            {"decisions": False, "key_decisions": "x"}, "decisions"
        ) is False
        assert resolve_handoff_field(
            {"decisions": "", "key_decisions": False}, "decisions"
        ) is False

    def test_empty_canonical_still_reads_empty_without_an_alias(self):
        """The empty fallback must not turn a legitimately empty field into a
        missing one — the resume brief's else-branch depends on this."""
        assert resolve_handoff_field({"decisions": []}, "decisions") == []

    def test_non_dict_and_unknown_field_return_none(self):
        assert resolve_handoff_field("not a handoff", "decisions") is None
        assert resolve_handoff_field({}, "produced") is None
