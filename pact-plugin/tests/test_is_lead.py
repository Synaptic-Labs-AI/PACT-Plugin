"""Tests for is_lead / classify_session_role — the lead/teammate role predicate.

The predicate reads the TOP-LEVEL ``agent_type`` field directly (NOT via
resolve_agent_name) and tests membership in LEAD_AGENT_TYPES. It is PURE
(agent_type only), TOTAL (never raises on a dict input), case-SENSITIVE, and
a COORDINATION control — not a security boundary.

Coverage:

is_lead() truth-table:
1.  Both lead spellings → True (qualified PACT:pact-orchestrator, unqualified)
2.  Teammate agent_type → False
3.  Plain frame (agent_type absent) → False
4.  Empty-string / None / non-string agent_type → False
5.  Mixed-case lead spelling → False (case-sensitive exact match)
6.  agent_id present but agent_type not a lead → False (#812 guard)

is_lead() purity:
7.  Does NOT read tool_input (a forged tool_input.agent_type cannot flip it)

classify_session_role() 3-way:
8.  lead / teammate / unknown across the same frame matrix

LEAD_AGENT_TYPES SSOT:
9.  Exactly the two sanctioned spellings; is_lead agrees with membership
"""

import pytest

from shared.pact_context import (
    LEAD_AGENT_TYPES,
    classify_session_role,
    is_lead,
)
from fixtures.role_frames import (
    lead_frame_qualified,
    lead_frame_unqualified,
    plain_frame,
    teammate_frame,
)


class TestIsLead:
    """is_lead() truth-table — both lead spellings True, everything else False."""

    def test_qualified_lead_spelling_is_lead(self):
        """`--agent PACT:pact-orchestrator` → True."""
        assert is_lead({"agent_type": "PACT:pact-orchestrator"}) is True

    def test_unqualified_lead_spelling_is_lead(self):
        """`--agent pact-orchestrator` → True."""
        assert is_lead({"agent_type": "pact-orchestrator"}) is True

    @pytest.mark.parametrize("agent_type", [
        "pact-backend-coder",
        "pact-test-engineer",
        "pact-secretary",
        "pact-orchestrator-helper",   # superstring of a lead spelling, not equal
        "orchestrator",               # the Step-4-stripped form is NOT a lead spelling
        "PACT:pact-backend-coder",
    ])
    def test_teammate_agent_types_are_not_lead(self, agent_type):
        """Any non-lead agent_type → False."""
        assert is_lead({"agent_type": agent_type}) is False

    def test_plain_frame_is_not_lead(self):
        """agent_type absent (no --agent / non-PACT primary) → False."""
        assert is_lead({}) is False
        assert is_lead({"session_id": "abc", "hook_event_name": "SessionStart"}) is False

    @pytest.mark.parametrize("agent_type", [
        "",        # empty string (falsy, not in set)
        None,      # explicit None
        123,       # non-string int
        ["pact-orchestrator"],  # unhashable list — isinstance(str) guard keeps it total
        {"x": 1},  # unhashable dict — isinstance(str) guard keeps it total
    ])
    def test_non_lead_agent_type_values_are_not_lead(self, agent_type):
        """Empty / None / non-string agent_type → False, never raises.

        The unhashable (list / dict) cases specifically pin TOTALITY: a bare
        ``x in frozenset`` would raise TypeError for them; the isinstance(str)
        guard short-circuits to False instead.
        """
        assert is_lead({"agent_type": agent_type}) is False

    @pytest.mark.parametrize("agent_type", [
        "PACT:PACT-ORCHESTRATOR",
        "Pact-Orchestrator",
        "PACT:Pact-Orchestrator",
        "pact-Orchestrator",
    ])
    def test_mixed_case_lead_spelling_is_not_lead(self, agent_type):
        """Case-SENSITIVE exact match — a mixed-case spelling is NOT a lead."""
        assert is_lead({"agent_type": agent_type}) is False

    def test_agent_id_present_but_not_orchestrator_is_not_lead(self):
        """#812 guard: an agent_id field never promotes a non-lead to lead.

        is_lead ignores agent_id entirely — only agent_type decides. A frame
        carrying an agent_id (e.g. a future CC build re-adds it) with a
        non-lead agent_type stays False.
        """
        frame = {"agent_type": "pact-backend-coder", "agent_id": "backend-coder@team"}
        assert is_lead(frame) is False

    def test_synthesized_role_frames(self):
        """The shared role-frame fixtures classify as expected."""
        assert is_lead(lead_frame_qualified()) is True
        assert is_lead(lead_frame_unqualified()) is True
        assert is_lead(teammate_frame()) is False
        assert is_lead(plain_frame()) is False


class TestIsLeadPurity:
    """is_lead must read ONLY agent_type — never tool_input, agent_id, env, config."""

    def test_does_not_read_tool_input(self):
        """A forged tool_input.agent_type cannot flip the verdict.

        agent_type at top level is harness-set; tool_input is request-shaped
        content. If is_lead ever consulted tool_input, an untrusted tool
        argument could forge lead status. This pins it shut.
        """
        forged = {
            "agent_type": "pact-backend-coder",  # genuine: a teammate
            "tool_input": {"agent_type": "PACT:pact-orchestrator"},  # forged
        }
        assert is_lead(forged) is False

    def test_no_agent_type_with_forged_tool_input_still_not_lead(self):
        """Plain frame stays False even if tool_input claims lead."""
        forged = {"tool_input": {"agent_type": "pact-orchestrator"}}
        assert is_lead(forged) is False


class TestClassifySessionRole:
    """classify_session_role() — the 3-way lead/teammate/unknown classifier."""

    @pytest.mark.parametrize("frame, expected", [
        ({"agent_type": "PACT:pact-orchestrator"}, "lead"),
        ({"agent_type": "pact-orchestrator"}, "lead"),
        ({"agent_type": "pact-backend-coder"}, "teammate"),
        ({"agent_type": "orchestrator"}, "teammate"),   # stripped form is a teammate, NOT lead
        ({"agent_type": "some-non-pact-type"}, "teammate"),
        ({}, "unknown"),                                 # absent
        ({"agent_type": ""}, "unknown"),                 # empty == falsy == unknown
        ({"agent_type": None}, "unknown"),               # explicit None
    ])
    def test_classify_matrix(self, frame, expected):
        assert classify_session_role(frame) == expected

    def test_classify_agrees_with_is_lead(self):
        """classify==lead iff is_lead is True, across the role-frame fixtures."""
        for builder in (lead_frame_qualified, lead_frame_unqualified):
            frame = builder()
            assert (classify_session_role(frame) == "lead") is is_lead(frame)
        for frame in (teammate_frame(), plain_frame()):
            assert classify_session_role(frame) != "lead"
            assert is_lead(frame) is False


class TestLeadAgentTypesSSOT:
    """LEAD_AGENT_TYPES is the single source of truth for both helpers."""

    def test_exactly_two_sanctioned_spellings(self):
        assert LEAD_AGENT_TYPES == frozenset({"PACT:pact-orchestrator", "pact-orchestrator"})

    def test_is_lead_agrees_with_membership(self):
        """For every member, is_lead is True; for a clear non-member, False."""
        for spelling in LEAD_AGENT_TYPES:
            assert is_lead({"agent_type": spelling}) is True
        assert is_lead({"agent_type": "pact-backend-coder"}) is False

    def test_frozenset_is_immutable(self):
        """A frozenset cannot be mutated in place — guards the SSOT."""
        assert isinstance(LEAD_AGENT_TYPES, frozenset)
        with pytest.raises(AttributeError):
            LEAD_AGENT_TYPES.add("pact-rogue")  # type: ignore[attr-defined]
