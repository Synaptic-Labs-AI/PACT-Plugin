"""
Home for the harvest-side reconciliation primitives in
shared/agent_handoff_marker.py: the emit-side subject sentinel, the disk
identity, and the task-file-preferred HANDOFF union.

The rule is AUTHORED in skills/pact-handoff-harvest/SKILL.md Step 3; these
tests pin the executable copy so the two cannot drift. They also pin the
current union behaviour: the shape of the extraction is that it changes
nothing, so every row here is a regression net for a "cleanup" that would.

Not covered elsewhere: no existing test reaches the union — it lived only as
pseudocode before this module.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.agent_handoff_marker import (
    OLD_FORMAT_MIXED,
    OLD_FORMAT_OWNED,
    OLD_FORMAT_UNMATCHED,
    OldFormatOutcome,
    disk_identity,
    emit_side_subject,
    mixed_old_format_event,
    occupant_hash,
    old_format_destination,
    union_preferring_task_file,
)

SKILL_TEXT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "pact-handoff-harvest"
    / "SKILL.md"
).read_text(encoding="utf-8")


class TestEmitSideSubject:
    def test_missing_empty_and_whitespace_get_the_sentinel(self):
        for value in (None, "", "   ", " \t\n"):
            assert emit_side_subject(value) == "(no subject)"

    def test_present_subject_is_returned_unchanged(self):
        """The value is NOT str-coerced or trimmed — occupant_hash does the
        stringifying, exactly as it does at both emit paths."""
        assert emit_side_subject("design X") == "design X"
        assert emit_side_subject("  padded  ") == "  padded  "


class TestDiskIdentity:
    def test_equals_occupant_hash_with_the_substituted_subject(self):
        assert disk_identity("review-backend", None) == occupant_hash(
            "review-backend", "(no subject)"
        )
        assert disk_identity("review-backend", "   ") == occupant_hash(
            "review-backend", "(no subject)"
        )

    def test_present_subject_hashes_as_itself(self):
        assert disk_identity("coder-a", "land the arms") == occupant_hash(
            "coder-a", "land the arms"
        )


class TestUnionPreferringTaskFile:
    # ---- the extraction's stated arms --------------------------------
    def test_present_and_empty_keeps_the_journal_value(self):
        merged, reports = union_preferring_task_file(
            {"open_questions": ["resolved question"]},
            {"open_questions": []},
            "61",
        )
        assert merged == {"open_questions": ["resolved question"]}
        assert reports == []

    def test_present_and_non_empty_disk_value_wins(self):
        merged, _ = union_preferring_task_file(
            {"decisions": ["old"]}, {"decisions": ["new"]}, "61"
        )
        assert merged == {"decisions": ["new"]}

    def test_field_absent_from_the_task_file_survives(self):
        merged, _ = union_preferring_task_file(
            {"produced": "x", "uncertainty": ["u"]}, {"produced": "y"}, "61"
        )
        assert merged == {"produced": "y", "uncertainty": ["u"]}

    # ---- current-behaviour pins a rewrite could silently break --------
    @pytest.mark.parametrize("empty", [None, "", [], {}])
    def test_each_empty_spelling_keeps_journal_content(self, empty):
        merged, _ = union_preferring_task_file(
            {"decisions": ["journal"]}, {"decisions": empty}, "61"
        )
        assert merged == {"decisions": ["journal"]}

    def test_zero_is_content_and_wins(self):
        """DISCRIMINATING: emptiness is equality membership in
        (None, "", [], {}), so 0 is a VALUE. A truthiness test on the disk
        side (`if not disk_value and journal_value`) would keep the journal
        here instead."""
        merged, _ = union_preferring_task_file(
            {"decisions": ["journal"]}, {"decisions": 0}, "61"
        )
        assert merged == {"decisions": 0}

    def test_false_is_content_and_wins(self):
        """DISCRIMINATING for the same reason as 0: `False == 0` but neither
        is in _EMPTY_VALUES, so a truthiness test would keep the journal."""
        merged, _ = union_preferring_task_file(
            {"decisions": ["journal"]}, {"decisions": False}, "61"
        )
        assert merged == {"decisions": False}

    def test_empty_string_journal_value_does_not_trigger_the_exception(self):
        """DISCRIMINATING: the exception tests the JOURNAL value's TRUTHINESS,
        not membership. "" is a member of _EMPTY_VALUES, so a membership test
        on the journal side would trigger the exception and keep ""; the
        current rule lets the task file's [] win."""
        merged, _ = union_preferring_task_file(
            {"decisions": ""}, {"decisions": []}, "61"
        )
        assert merged == {"decisions": []}

    def test_zero_and_false_journal_values_do_not_trigger_the_exception(self):
        """DISCRIMINATING against a JOURNAL-SIDE MEMBERSHIP rewrite: 0 and
        False are falsy but NOT in _EMPTY_VALUES, so a rule testing
        `journal_value not in _EMPTY_VALUES` would call them content and keep
        them, while this rule tests truthiness and lets the task file's []
        win. The [] and {} rows are the non-discriminating tail of the falsy
        family — they ARE set members, so every variant agrees on them; they
        are kept for completeness, not as guards. The "" arm above covers the
        set-member case."""
        for falsy in (0, False, [], {}):
            merged, _ = union_preferring_task_file(
                {"decisions": falsy}, {"decisions": []}, "61"
            )
            assert merged == {"decisions": []}

    def test_keys_are_compared_raw_with_no_alias_normalization(self):
        """An alias spelling and its canonical name are two distinct keys in
        the current rule. Alias normalization is a behaviour change, so it
        must show up as a changed expectation rather than slip in."""
        merged, _ = union_preferring_task_file(
            {"key_decisions": ["journal"]}, {"decisions": ["disk"]}, "61"
        )
        assert merged == {"key_decisions": ["journal"], "decisions": ["disk"]}

    def test_inputs_are_not_mutated(self):
        journal = {"decisions": ["old"], "open_questions": ["q"]}
        disk = {"decisions": ["new"], "open_questions": []}
        union_preferring_task_file(journal, disk, "61")
        assert journal == {"decisions": ["old"], "open_questions": ["q"]}
        assert disk == {"decisions": ["new"], "open_questions": []}

    def test_task_id_is_accepted_and_empty_inputs_are_safe(self):
        assert union_preferring_task_file({}, {}, "61") == ({}, [])


class TestPresentAndEmptyControl:
    """P4 control: the field must be GENUINELY present-and-empty and the
    journal must hold content, or the arm goes green for the wrong reason."""

    def test_own_team_present_and_empty_uses_the_journal_content(self):
        journal = {"open_questions": ["was the question resolved?"], "produced": "x"}
        disk = {"open_questions": [], "produced": "x"}
        assert disk["open_questions"] == [] and "open_questions" in disk
        merged, _ = union_preferring_task_file(journal, disk, "61")
        assert merged["open_questions"] == ["was the question resolved?"]


# =============================================================================
# OLD-FORMAT cross-check (C3)
# =============================================================================
class TestMixedOldFormatEvent:
    EVENT_HANDOFF = {
        "produced": "x",
        "decisions": ["old decision"],
        "uncertainty": ["old uncertainty"],
        "integration": "i",
        "open_questions": [],
    }

    def _task(self, owner, subject, handoff=None, task_id="57"):
        return {
            "id": task_id,
            "owner": owner,
            "subject": subject,
            "metadata": {"handoff": handoff or dict(self.EVENT_HANDOFF)},
        }

    def _classify(self, agent, subject, candidates):
        return mixed_old_format_event(
            agent, subject, dict(self.EVENT_HANDOFF), candidates
        )

    def test_content_match_with_a_different_identity_is_mixed(self):
        """DISCRIMINATING: the event's content is another store's, and its
        identity is not that file's. This is the #57 shape."""
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [{"team": "session-old", "task": self._task("review-backend", "OLD subject")}],
        )
        assert outcome.state == OLD_FORMAT_MIXED
        assert outcome.team == "session-old"
        assert outcome.report == (
            "handoff identity mismatch for task 57: journal content equals "
            "team session-old task 57, whose identity it does not carry"
        )

    def test_content_and_identity_both_matching_is_owned_by_that_store(self):
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [{"team": "session-old", "task": self._task("review-backend", "NEW subject")}],
        )
        assert outcome.state == OLD_FORMAT_OWNED
        assert outcome.team == "session-old"
        assert outcome.report is None

    def test_content_matching_no_named_store_is_unmatched(self):
        other = {**self.EVENT_HANDOFF, "produced": "something else"}
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [{"team": "session-old", "task": self._task("someone", "OLD", handoff=other)}],
        )
        assert outcome.state == OLD_FORMAT_UNMATCHED
        assert outcome.team is None

    def test_an_identity_match_wins_over_a_later_content_match(self):
        """The two-pass scan: a coincidental content match in a second store
        must not override the store whose identity the event carries."""
        same = self._task("review-backend", "NEW subject")
        foreign = self._task("review-backend", "OLD subject")
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [
                {"team": "session-this", "task": same},
                {"team": "session-old", "task": foreign},
            ],
        )
        assert outcome.state == OLD_FORMAT_OWNED
        assert outcome.team == "session-this"

    def test_subject_sentinel_is_applied_to_the_file_identity(self):
        outcome = self._classify(
            "review-backend",
            "(no subject)",
            [{"team": "session-old", "task": self._task("review-backend", "")}],
        )
        assert outcome.state == OLD_FORMAT_OWNED
        assert outcome.team == "session-old"

    def test_non_dict_event_handoff_is_unmatched(self):
        outcome = mixed_old_format_event(
            "review-backend",
            "NEW subject",
            "not a mapping",
            [{"team": "session-old", "task": self._task("review-backend", "OLD")}],
        )
        assert outcome.state == OLD_FORMAT_UNMATCHED

    def test_no_candidates_is_unmatched(self):
        assert (
            self._classify("review-backend", "NEW subject", []).state
            == OLD_FORMAT_UNMATCHED
        )


class TestOldFormatDestination:
    """The ROUTING arms. The previous routing sent every non-mixed event to
    this team, so `test_a_from_store_owned_event_routes_to_that_team` is the
    arm that fails under it: an old-format HANDOFF owned by a realigned FROM
    store must not be processed under this team."""

    def test_mixed_is_discarded(self):
        outcome = OldFormatOutcome(OLD_FORMAT_MIXED, "session-old", "report me")
        assert old_format_destination(outcome, "session-this") == "mixed"

    def test_owned_by_this_team_stays_here(self):
        outcome = OldFormatOutcome(OLD_FORMAT_OWNED, "session-this")
        assert old_format_destination(outcome, "session-this") == "this"

    def test_owned_by_a_realigned_from_team_routes_to_that_team(self):
        outcome = OldFormatOutcome(OLD_FORMAT_OWNED, "session-old")
        assert old_format_destination(outcome, "session-this") == "from"

    def test_unmatched_stays_here(self):
        outcome = OldFormatOutcome(OLD_FORMAT_UNMATCHED)
        assert old_format_destination(outcome, "session-this") == "this"

    def test_end_to_end_owned_by_from_store_routes_there(self):
        """The lead's arm: classify an event whose content AND identity both
        match a from store, then route it. It lands under that team's
        section, never in this team's merged output."""
        handoff = {"produced": "old team work", "decisions": ["d"]}
        task = {
            "id": "57",
            "owner": "coder-a",
            "subject": "old team subject",
            "metadata": {"handoff": dict(handoff)},
        }
        outcome = mixed_old_format_event(
            "coder-a", "old team subject", handoff,
            [{"team": "session-old", "task": task}],
        )
        assert outcome.state == OLD_FORMAT_OWNED
        assert old_format_destination(outcome, "session-this") == "from"
        assert outcome.team == "session-old"


# =============================================================================
# session_resume renderer — the coupling (P12)
# =============================================================================
class TestResumeGroupingKey:
    def _event(self, task_id, team, decision, ts):
        return {
            "type": "agent_handoff",
            "task_id": task_id,
            "team": team,
            "agent": "coder",
            "task_subject": "same subject",
            "ts": ts,
            "handoff": {"decisions": [decision]},
        }

    def _render(self, events):
        with patch(
            "shared.session_resume.read_events_from", return_value=events
        ):
            from shared.session_resume import _build_journal_resume_inner

            return _build_journal_resume_inner("/tmp/not-a-real-session")

    def test_two_teams_same_numbered_task_render_as_two_entries(self):
        """DISCRIMINATING: with the old (agent, subject) key the later team
        overwrites the earlier and only one entry renders."""
        rendered = self._render(
            [
                self._event("1", "session-a", "team a work", "2026-01-01T00:00:01Z"),
                self._event("1", "session-b", "team b work", "2026-01-01T00:00:02Z"),
            ]
        )
        assert "team a work" in rendered
        assert "team b work" in rendered
        assert rendered.count("- coder: same subject") == 2

    def test_two_task_ids_sharing_an_occupant_render_as_two_entries(self):
        rendered = self._render(
            [
                self._event("1", "session-a", "task one", "2026-01-01T00:00:01Z"),
                self._event("2", "session-a", "task two", "2026-01-01T00:00:02Z"),
            ]
        )
        assert "task one" in rendered and "task two" in rendered
        assert rendered.count("- coder: same subject") == 2


# =============================================================================
# Prose routing pins (P1-P6, P6-absence side)
#
# The partition/old-format routing needs the journal read, the context team
# name and multi-store file reads, so it is not a pure predicate (architecture
# §5 observability). These bounded-section presence pins are its regression
# protection: a filter-shaped rewrite deletes the clauses they assert.
# =============================================================================
class TestHarvestRuleProse:
    def test_partition_clause_present(self):
        assert (
            "THIS IS A PARTITION: A PARTITION IS NEVER DISCARDED FOR NAMING "
            "ANOTHER TEAM" in SKILL_TEXT
        )

    def test_step3_selection_text_carries_no_filter_wording(self):
        """A negated rule ('not a filter') is re-derived into the positive one
        by the next reader. The rule text states the partition positively and
        the word does not appear in either selection block at all."""
        selection = SKILL_TEXT.split("**SELECTION, because")[1].split(
            "### Step 3.1"
        )[0]
        snapshot_step = SKILL_TEXT.split("### Step 3.1:")[1].split(
            "### Step 3.5"
        )[0]
        assert "filter" not in selection.lower()
        assert "filter" not in snapshot_step.lower()

    def test_from_team_route_present(self):
        """P6: a filter-shaped fix fails here — the realigned earlier team is
        routed to its own section, not dropped."""
        assert (
            "Each partition naming a team the journal realigned FROM"
            in SKILL_TEXT
        )
        assert "run Step 2 and Step 8 for it against its own `## team={from}` section" in SKILL_TEXT

    def test_old_format_cross_check_present(self):
        assert "AN OLD-FORMAT EVENT CARRIES NO `team` FIELD" in SKILL_TEXT
        assert "handoff cross-team check unavailable for task N" in SKILL_TEXT
        assert "DO NOT ENUMERATE THE TASKS ROOT" in SKILL_TEXT

    def test_old_format_routing_uses_the_structured_outcome(self):
        """The pseudocode must ROUTE on the outcome, not collapse every
        non-mixed classification into this team. A from-store-owned event is
        appended to that team's partition, never to this team's."""
        assert "old_format_destination(outcome, this_team)" in SKILL_TEXT
        assert "from_events.setdefault(outcome.team, []).append(e)" in SKILL_TEXT
        assert 'if destination == "mixed":' in SKILL_TEXT
        assert 'elif destination == "from":' in SKILL_TEXT

    def test_snapshot_section_join_present(self):
        assert "handoff snapshot identity mismatch for task N" in SKILL_TEXT

    def test_occupant_hash_is_not_folded_with_team(self):
        assert "DO NOT FOLD THE TEAM INTO `occupant_hash`" in SKILL_TEXT

    def test_resume_coupling_sentence_present(self):
        assert "(task_id, team, agent, task_subject)" in SKILL_TEXT

    def test_data_boundary_sentence_present(self):
        assert "THE AGENT AND THE SUBJECT MUST CROSS AS DATA" in SKILL_TEXT
