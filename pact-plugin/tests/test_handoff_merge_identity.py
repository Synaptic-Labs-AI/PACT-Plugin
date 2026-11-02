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
import pytest

from shared.agent_handoff_marker import (
    disk_identity,
    emit_side_subject,
    occupant_hash,
    union_preferring_task_file,
)


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
        """Documentary: these are falsy and NOT in _EMPTY_VALUES, so a
        membership rewrite agrees here. The "" row above is the discriminator;
        this row keeps the falsy family visible."""
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
