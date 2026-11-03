"""
Home for the harvest-side reconciliation primitives in
shared/handoff_merge.py (the disk identity and the task-file-preferred
HANDOFF union), plus the emit-side subject sentinel they depend on, which
stays in shared/agent_handoff_marker.py and is re-exported.

The rule is AUTHORED in skills/pact-handoff-harvest/SKILL.md Step 3; these
tests pin the executable copy so the two cannot drift. The union arms are the
REQUIRED-key-set gate: fixtures A-F from the preparation census, the three
journal-only arms, and the alias-ordering case. The earlier extraction arms
that pinned the superseded rule are deliberately replaced here — commit 4's
whole point is that their expectations changed.

Not covered elsewhere: no existing test reaches the union — it lived only as
pseudocode before this module.
"""
import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.handoff_merge import (
    OLD_FORMAT_MIXED,
    OLD_FORMAT_OWNED,
    OLD_FORMAT_UNMATCHED,
    OldFormatOutcome,
    disk_identity,
    emit_side_subject,
    mixed_old_format_event,
    occupant_hash,
    old_format_destination,
    report_safe,
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


class TestReportSafe:
    """The sanitiser itself: every line boundary a consumer may split on,
    not only the ASCII ones. `str.splitlines()` — Python's own, and many log
    consumers — recognises all of these."""

    @pytest.mark.parametrize(
        "sep", ["\n", "\r", "\r\n", "\x85", "\u2028", "\u2029"]
    )
    def test_every_line_boundary_is_stripped(self, sep):
        assert report_safe("before" + sep + "after") == "beforeafter"

    def test_non_line_controls_are_still_stripped(self):
        assert report_safe("a\x00b\x07c\x7fd") == "abcd"

    def test_c1_controls_are_stripped_even_when_not_line_boundaries(self):
        """\x9b is C1 but not a line boundary. An enumerated class widened
        only to NEL would leave it; the category filter does not."""
        assert report_safe("before\x9bafter") == "beforeafter"

    def test_visible_text_survives_and_the_value_is_capped(self):
        assert report_safe("x" * 500, limit=10) == "xxxxxxxxxx"


class TestUnionPreferringTaskFile:
    # ---- fixtures A-F: the required-key-set gate ----------------------
    # Canonical required set: produced, decisions, uncertainty, integration,
    # open_questions. reasoning_chain is recommended, and a legal revision may
    # omit it — that is why the comparison is scoped to the required set.
    JOURNAL = {
        "produced": "p",
        "decisions": ["d"],
        "reasoning_chain": ["r"],
        "uncertainty": ["u"],
        "integration": "i",
        "open_questions": ["q"],
    }

    def test_fixture_a_accidental_partial_restores_the_cleared_field(self):
        """D = {produced, open_questions}, a proper subset of J's five -> the
        present-and-empty field is restored from the journal, and reported."""
        disk = {"produced": "p2", "open_questions": []}
        merged, reports = union_preferring_task_file(
            dict(self.JOURNAL), disk, "61"
        )
        assert merged["open_questions"] == ["q"]
        assert merged["produced"] == "p2"
        assert any(
            "restored from the journal" in line and "open_questions" in line
            for line in reports
        )

    def test_fixture_b_complete_revision_keeps_the_cleared_field(self):
        disk = {
            "produced": "p2",
            "decisions": ["d2"],
            "reasoning_chain": ["r2"],
            "uncertainty": ["u2"],
            "integration": "i2",
            "open_questions": [],
        }
        merged, reports = union_preferring_task_file(
            dict(self.JOURNAL), disk, "61"
        )
        assert merged["open_questions"] == []
        assert any("kept empty" in line and "covers" in line for line in reports)

    def test_fixture_c_journal_extras_do_not_make_a_complete_revision_partial(self):
        """DISCRIMINATING against a FULL-KEY comparison: the journal's extra
        key would make a full-key task file look like a proper subset."""
        journal = {**self.JOURNAL, "notes": "an extra"}
        disk = {
            "produced": "p2",
            "decisions": ["d2"],
            "uncertainty": ["u2"],
            "integration": "i2",
            "open_questions": [],
        }
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["open_questions"] == []
        assert any("kept empty" in line for line in reports)
        assert merged["notes"] == "an extra"
        assert any(
            "not a required field" in line and "notes" in line
            for line in reports
        )

    def test_fixture_d_revision_omitting_reasoning_chain_keeps_the_field(self):
        """DISCRIMINATING against a FULL-KEY comparison: a legal revision may
        omit the recommended field."""
        journal = dict(self.JOURNAL)
        disk = {
            "produced": "p2",
            "decisions": ["d2"],
            "uncertainty": ["u2"],
            "integration": "i2",
            "open_questions": [],
        }
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["open_questions"] == []
        assert any("kept empty" in line for line in reports)
        assert merged["reasoning_chain"] == ["r"]
        assert any(
            "not a required field" in line and "reasoning_chain" in line
            for line in reports
        )

    def test_fixture_e_partial_plus_unknown_key_restores(self):
        """DISCRIMINATING against a FULL-KEY comparison: the unknown key makes
        the sets incomparable there, so the restore never fires."""
        disk = {"produced": "p2", "open_questions": [], "weird_key": 1}
        merged, reports = union_preferring_task_file(
            dict(self.JOURNAL), disk, "61"
        )
        assert merged["open_questions"] == ["q"]
        assert merged["weird_key"] == 1
        assert any("restored from the journal" in line for line in reports)

    def test_fixture_f_rename_and_add_keeps_the_field_after_normalization(self):
        """DISCRIMINATING FOR THE ORDERING. Normalization runs BEFORE the set
        math. Reversed, `key_decisions` is not a required key, so D's required
        set is 4 against J's 5 -> proper subset -> restore, and this arm
        fails with open_questions == ["q"]."""
        disk = {
            "produced": "p2",
            "key_decisions": ["d2"],
            "uncertainty": ["u2"],
            "integration": "i2",
            "open_questions": [],
            "notes": "n",
        }
        merged, _ = union_preferring_task_file(dict(self.JOURNAL), disk, "61")
        assert merged["open_questions"] == []
        assert merged["key_decisions"] == ["d2"]
        assert "decisions" not in merged

    # ---- P10: journal-only fields, three arms --------------------------
    def test_p10_required_journal_only_under_a_proper_subset_is_kept(self):
        """THE #61 RESIDUAL'S ARM. This is the same proper-subset branch the
        residual describes: a DELIBERATE complete removal of a required key
        and an accidental partial rewrite both leave D a proper subset, so the
        branch restores the journal value and reports that it did. Key sets
        cannot separate the two, and the rule text says so."""
        journal = {"produced": "p", "decisions": ["d"], "uncertainty": ["u"]}
        disk = {"produced": "p2", "decisions": ["d2"]}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["uncertainty"] == ["u"]
        assert any(
            "restored from the journal" in line and "uncertainty" in line
            for line in reports
        )

    def test_p10_required_journal_only_under_incomparable_sets_is_dropped(self):
        journal = {"produced": "p", "decisions": ["d"]}
        disk = {"produced": "p2", "uncertainty": ["u2"]}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert "decisions" not in merged
        assert any(
            "undecidable" in line and "decisions" in line for line in reports
        )

    def test_p10_non_required_journal_only_alongside_an_equal_required_set_is_kept(self):
        """The keep-positive is written as a journal-only OPTIONAL field with
        EQUAL required sets. 'A covering set keeps a journal-only field' is
        vacuous: a required journal-only key cannot exist under covering."""
        journal = {"produced": "p", "decisions": ["d"], "reasoning_chain": ["r"]}
        disk = {"produced": "p2", "decisions": ["d2"]}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["reasoning_chain"] == ["r"]
        assert any(
            "not a required field" in line and "reasoning_chain" in line
            for line in reports
        )

    def test_p10b_legacy_spelling_is_not_swallowed_by_the_incomparable_branch(self):
        """`key_decisions` normalizes to `decisions`, so the sets are EQUAL,
        not incomparable, and nothing is dropped."""
        journal = {"produced": "p", "key_decisions": ["d-journal"]}
        disk = {"produced": "p2", "decisions": ["d-disk"]}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged == {"produced": "p2", "decisions": ["d-disk"]}
        assert reports == []

    # ---- retained behaviour pins --------------------------------------
    def test_present_and_non_empty_disk_value_wins(self):
        merged, _ = union_preferring_task_file(
            {"decisions": ["old"]}, {"decisions": ["new"]}, "61"
        )
        assert merged == {"decisions": ["new"]}

    @pytest.mark.parametrize("empty", [None, "", [], {}])
    def test_each_empty_spelling_yields_to_a_covering_task_file(self, empty):
        """Emptiness is EQUALITY MEMBERSHIP. Under a covering (here equal)
        required set the empty value stands."""
        merged, _ = union_preferring_task_file(
            {"decisions": ["journal"]}, {"decisions": empty}, "61"
        )
        assert merged == {"decisions": empty}

    # THE FOUR FALSY ARMS BELOW ARE RETAINED-BEHAVIOUR PINS, NOT GUARDS. TEST
    # measured all four green under BOTH auditor mutants: at EQUAL required key
    # sets the membership and truthiness readings route identically. They are
    # kept because the 0/False-as-content property is what this branch depends
    # on; the two PROPER-SUBSET probes after them are the only regime that
    # separates the readings and are the arms that fail under the mutants.
    def test_zero_is_content_and_wins(self):
        """RETAINED-BEHAVIOUR PIN, NOT A DISCRIMINATOR (both mutants green):
        documents that 0 is content and wins the disk side. The discriminating
        proper-subset probe is `test_p10_proper_subset_disk_zero_is_content`."""
        merged, _ = union_preferring_task_file(
            {"decisions": ["journal"]}, {"decisions": 0}, "61"
        )
        assert merged == {"decisions": 0}

    def test_false_is_content_and_wins(self):
        """RETAINED-BEHAVIOUR PIN, NOT A DISCRIMINATOR (both mutants green):
        documents that False is content, like 0."""
        merged, _ = union_preferring_task_file(
            {"decisions": ["journal"]}, {"decisions": False}, "61"
        )
        assert merged == {"decisions": False}

    def test_empty_string_journal_value_does_not_trigger_the_rule(self):
        """RETAINED-BEHAVIOUR PIN, NOT A DISCRIMINATOR (both mutants green):
        documents the journal-truthiness reading; "" is both falsy and a set
        member, so every variant agrees at equal key sets."""
        merged, _ = union_preferring_task_file(
            {"decisions": ""}, {"decisions": []}, "61"
        )
        assert merged == {"decisions": []}

    def test_falsy_journal_values_let_the_task_file_win(self):
        """RETAINED-BEHAVIOUR PIN, NOT A DISCRIMINATOR (both mutants green):
        documents that a falsy journal value does not itself trigger the
        present-and-empty state. The proper-subset probes below are the
        guards."""
        for falsy in (0, False, [], {}):
            merged, _ = union_preferring_task_file(
                {"decisions": falsy}, {"decisions": []}, "61"
            )
            assert merged == {"decisions": []}

    def test_none_is_empty_under_a_proper_subset(self):
        """KEEP-None RATIONALE, pinned. The empty-value arm above uses EQUAL
        key sets, where removing None from _EMPTY_VALUES changes nothing.
        Here the task file is a PROPER SUBSET, so dropping None makes the
        disk's null win — the erasure the narrowing exists to prevent."""
        journal = {"decisions": ["j"], "uncertainty": ["u"]}
        disk = {"decisions": None}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["decisions"] == ["j"]
        assert any(
            "restored from the journal" in line and "decisions" in line
            for line in reports
        )

    def test_p10_proper_subset_disk_zero_is_content(self):
        """DISCRIMINATING (proper subset): D = {decisions} is a proper subset
        of J = {decisions, uncertainty}, so 0 is content on the disk side and
        wins. Mutant `not disk_value` restores ["j"] and reddens this arm."""
        journal = {"decisions": ["j"], "uncertainty": ["u"]}
        disk = {"decisions": 0}
        merged, _ = union_preferring_task_file(journal, disk, "61")
        assert merged["decisions"] == 0

    def test_p10_proper_subset_restores_journal_zero(self):
        """DISCRIMINATING (proper subset): the journal's 0 is NOT a member of
        (None, '', [], {}), so it is content, and a proper-subset task file
        restores it. Mutant `journal_value` (truthiness) keeps the task
        file's [] and reddens this arm."""
        journal = {"decisions": 0, "uncertainty": ["u"]}
        disk = {"decisions": []}
        merged, _ = union_preferring_task_file(journal, disk, "61")
        assert merged["decisions"] == 0

    def test_present_and_empty_with_incomparable_sets_keeps_the_empty_value(self):
        """THE INCOMPARABLE / PRESENT-AND-EMPTY BRANCH, which had no arm at
        all. J = {produced, decisions, open_questions}, D = {produced,
        uncertainty, open_questions}: neither covers the other, so the empty
        value stands and the state is reported as undecidable. The old rule
        (always keep the journal on present-and-empty) returns ["q"] here."""
        journal = {
            "produced": "p",
            "decisions": ["d"],
            "open_questions": ["q"],
        }
        disk = {
            "produced": "p2",
            "uncertainty": ["u"],
            "open_questions": [],
        }
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["open_questions"] == []
        assert any(
            "undecidable" in line
            and "open_questions" in line
            and "incomparable" in line
            for line in reports
        )

    def test_inputs_are_not_mutated(self):
        journal = {"decisions": ["old"], "open_questions": ["q"]}
        disk = {"decisions": ["new"], "open_questions": []}
        union_preferring_task_file(journal, disk, "61")
        assert journal == {"decisions": ["old"], "open_questions": ["q"]}
        assert disk == {"decisions": ["new"], "open_questions": []}

    def test_task_id_is_accepted_and_empty_inputs_are_safe(self):
        assert union_preferring_task_file({}, {}, "61") == ({}, [])

    # ---- security: report injection (CWE-117) and duplicate spellings --
    def test_report_task_id_strips_control_characters(self):
        """A newline in the task id must not forge a second report line; the
        reports are read by an agent that may act on them."""
        _, reports = union_preferring_task_file(
            {"decisions": ["d"]}, {"decisions": []}, "61\nFORGED: approved"
        )
        assert reports
        assert all("\n" not in line and "\r" not in line for line in reports)
        assert "61FORGED: approved" in reports[0]

    def test_report_field_name_strips_control_characters(self):
        """Field names come from the task file and the journal, so a key can
        carry a newline too."""
        _, reports = union_preferring_task_file(
            {"produced": "p", "weird\nkey": "x"}, {"produced": "p2"}, "61"
        )
        assert any("weirdkey" in line for line in reports)
        assert all("\n" not in line for line in reports)

    @pytest.mark.parametrize("sep", ["\x85", "\u2028"])
    def test_report_field_name_strips_unicode_line_separators(self, sep):
        """DISCRIMINATING for the ASCII-only class: NEL and LINE SEPARATOR are
        not C0, so the old sanitiser left them and a `splitlines()` renderer
        would see a forged line."""
        _, reports = union_preferring_task_file(
            {"produced": "p", ("weird" + sep + "key"): "x"},
            {"produced": "p2"},
            "61",
        )
        assert any("weirdkey" in line for line in reports)
        assert sep not in "\n".join(reports)

    def test_report_field_name_is_length_capped(self):
        long_key = "k" * 500
        _, reports = union_preferring_task_file(
            {"produced": "p", long_key: "x"}, {"produced": "p2"}, "61"
        )
        assert reports
        assert "k" * 129 not in reports[0]
        assert len(reports[0]) < 400

    def test_duplicate_spellings_leave_no_stale_journal_copy(self):
        """DISCRIMINATING: `merged` starts from the RAW journal mapping while
        `_field_map` collapses both spellings, so dropping only the selected
        spelling would leave the alias in the output."""
        journal = {"decisions": ["old"], "key_decisions": ["old alias"]}
        disk = {"decisions": ["new"]}
        merged, _ = union_preferring_task_file(journal, disk, "61")
        assert merged == {"decisions": ["new"]}

    def test_dual_spelling_disk_copy_keeps_the_alias_value(self):
        """DISCRIMINATING (alignment with resolve_handoff_field): a FALSY
        canonical spelling on the DISK side must fall through to a truthy
        alias. Before the alignment the map selected the empty canonical, so
        the alias value was ignored AND the journal content was dropped."""
        journal = {"produced": "p", "decisions": ["journal"]}
        disk = {
            "produced": "p2",
            "decisions": "",
            "key_decisions": ["disk alias"],
        }
        merged, _ = union_preferring_task_file(journal, disk, "61")
        assert merged["key_decisions"] == ["disk alias"]
        assert "decisions" not in merged

    # ---- input totality: a malformed copy is ABSENT, not a crash ----------
    def test_non_dict_disk_copy_is_treated_as_absent(self):
        """B-F4, RED before the coercion: a `metadata.handoff` holding a
        string reaches the union through the harvest's read loop. Raising
        here makes the whole loop hostage to one bad field; the module's
        convention is that a malformed copy is absent."""
        merged, reports = union_preferring_task_file(
            {"key_decisions": ["x"]}, "not-a-dict", "61"
        )
        assert isinstance(merged, dict)
        assert isinstance(reports, list)
        assert merged["key_decisions"] == ["x"]

    def test_non_dict_journal_copy_is_treated_as_absent(self):
        merged, reports = union_preferring_task_file(
            [], {"produced": "p"}, "61"
        )
        assert isinstance(merged, dict)
        assert isinstance(reports, list)
        assert merged["produced"] == "p"

    def test_malformed_copy_is_reported(self):
        """R2: the coercion is NOT silent. `{}` journal + a truthy non-dict
        disk returned ({}, []) with zero reports; the only copy that could
        have carried content was the malformed one, and the harvest would
        process the task and record it as done."""
        merged, reports = union_preferring_task_file({}, "oops", "7")
        assert merged == {}
        assert len(reports) == 1
        assert "task-file copy was not a mapping" in reports[0]
        # Mirror: the journal side, with a real disk copy still merged.
        merged, reports = union_preferring_task_file(
            [], {"produced": "p"}, "7"
        )
        assert merged["produced"] == "p"
        assert len(reports) == 1
        assert "journal copy was not a mapping" in reports[0]

    def test_field_map_agrees_with_resolve_handoff_field_on_all_orders(self):
        """FIX 1b: a BEHAVIOURAL arm, not a sentence. `_field_map` claims to
        mirror the shipped reader; a falsy alias overwriting a falsy
        canonical made it disagree for canonical-first copies. Each case runs
        both orderings, because insertion order was the defect."""
        from shared.handoff_merge import _field_map
        from shared.handoff_schema import resolve_handoff_field

        cases = [
            # all-falsy pair: the canonical spelling must win, both orders
            ([("decisions", ""), ("key_decisions", [])], "decisions", ""),
            ([("key_decisions", []), ("decisions", "")], "decisions", ""),
            ([("decisions", 0), ("key_decisions", False)], "decisions", 0),
            ([("key_decisions", False), ("decisions", 0)], "decisions", 0),
            # truthy alias control
            ([("decisions", ""), ("key_decisions", ["x"])],
             "key_decisions", ["x"]),
            ([("key_decisions", ["x"]), ("decisions", "")],
             "key_decisions", ["x"]),
            # canonical-truthy control
            ([("decisions", ["d"]), ("key_decisions", ["k"])],
             "decisions", ["d"]),
            ([("key_decisions", ["k"]), ("decisions", ["d"])],
             "decisions", ["d"]),
        ]
        for pairs, expected_key, expected_value in cases:
            handoff = dict(pairs)
            key, value = _field_map(handoff)["decisions"]
            assert value == resolve_handoff_field(handoff, "decisions"), (
                f"map and reader disagree for {pairs!r}: {value!r}"
            )
            assert value == expected_value
            assert key == expected_key, (
                f"wrong raw key for {pairs!r}: {key!r}"
            )

    def test_drop_removes_every_spelling_of_the_dropped_identity(self):
        journal = {"produced": "p", "decisions": ["d"], "key_decisions": ["d2"]}
        disk = {"produced": "p2", "uncertainty": ["u"]}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert "decisions" not in merged
        assert "key_decisions" not in merged
        assert any(
            "undecidable" in line and "decisions" in line for line in reports
        )


class TestPresentAndEmptyControl:
    """P4 control: the field must be GENUINELY present-and-empty and the
    journal must hold content. Under the required-key-set gate the restore
    branch serves the ACCIDENTAL PARTIAL rewrite (the task file is a proper
    subset), so the control is built in that shape — not in the covering
    shape, which correctly keeps the empty value."""

    def test_present_and_empty_with_a_partial_task_file_restores(self):
        journal = {
            "open_questions": ["was the question resolved?"],
            "produced": "x",
            "decisions": ["d"],
        }
        disk = {"open_questions": [], "produced": "x"}
        assert disk["open_questions"] == [] and "open_questions" in disk
        assert "decisions" not in disk  # proper subset: the partial-rewrite shape
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["open_questions"] == ["was the question resolved?"]
        assert any("restored from the journal" in line for line in reports)


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
            "57", agent, subject, dict(self.EVENT_HANDOFF), candidates
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

    def test_content_matching_no_named_store_is_unmatched_and_reports(self):
        """The UNMATCHED state is the COMMON old-format path, and it carries
        the unavailable-check line ON THE OUTCOME so the caller cannot choose
        silently. An outcome without a report here would be the silent-branch
        defect the arc exists to fix."""
        other = {**self.EVENT_HANDOFF, "produced": "something else"}
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [{"team": "session-old", "task": self._task("someone", "OLD", handoff=other)}],
        )
        assert outcome.state == OLD_FORMAT_UNMATCHED
        assert outcome.team is None
        assert outcome.report == (
            "handoff cross-team check unavailable for task 57: no other "
            "named store carries the identity"
        )

    def test_ownerless_content_match_reports_its_own_state(self):
        """R3a: a candidate that CARRIES the content but has no owner, so its
        identity cannot be compared, must be distinguishable from no
        candidate carrying the content — the two states call for different
        actions."""
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [{"team": "session-old", "task": self._task(None, "OLD subject")}],
        )
        assert outcome.state == OLD_FORMAT_UNMATCHED
        assert outcome.report == (
            "handoff cross-team check unavailable for task 57: a candidate "
            "carries the content but its record has no owner; its identity "
            "could not be compared"
        )
        assert "no other named store carries the identity" not in outcome.report

    def test_no_candidate_still_reports_the_generic_line(self):
        """CONTROL for R3a: with no candidate at all the generic bound line
        is unchanged."""
        outcome = self._classify("review-backend", "NEW subject", [])
        assert outcome.state == OLD_FORMAT_UNMATCHED
        assert "no other named store carries the identity" in outcome.report
        assert "its record has no owner" not in outcome.report

    def test_ownerless_skip_does_not_settle_an_event_another_candidate_owns(self):
        """R3a rationale: the skip removes one candidate; a later candidate
        that owns the event still decides it."""
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [
                {"team": "session-old", "task": self._task(None, "OLD subject")},
                {
                    "team": "session-this",
                    "task": self._task("review-backend", "NEW subject"),
                },
            ],
        )
        assert outcome.state == OLD_FORMAT_OWNED
        assert outcome.team == "session-this"

    def test_an_identity_match_wins_over_an_earlier_content_match(self):
        """The two-pass scan: the CONTENT match comes FIRST, so a classifier
        that short-circuits at the first content match returns MIXED here.
        The correct classifier checks every candidate and finds the identity
        match in the second one. (The order was the reverse before: with the
        identity match first, the short-circuit mutant passed every arm.)"""
        foreign = self._task("review-backend", "OLD subject")
        same = self._task("review-backend", "NEW subject")
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [
                {"team": "session-old", "task": foreign},
                {"team": "session-this", "task": same},
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

    @pytest.mark.parametrize("owner", [None, "   "])
    def test_empty_owner_candidate_is_not_mixed_and_is_kept(self, owner):
        """DISCRIMINATING (F2): the emit substitutes the platform teammate
        name for an empty owner, and the record cannot reproduce it. A content
        match against such a file must NOT be read as a foreign identity —
        before the fix this returned MIXED and the event was discarded."""
        task = {
            "id": "57",
            "subject": "NEW subject",
            "metadata": {"handoff": dict(self.EVENT_HANDOFF)},
        }
        if owner is not None:
            task["owner"] = owner
        outcome = self._classify(
            "review-backend", "NEW subject",
            [{"team": "session-old", "task": task}],
        )
        assert outcome.state == OLD_FORMAT_UNMATCHED
        assert old_format_destination(outcome, "session-this") == "this"

    def test_mixed_report_is_sanitised(self):
        """SITE 2 of report_safe: the classifier's own report interpolates the
        task id and the team. Without the sanitiser a line boundary in either
        forges a report line. This is the report site the one-sided count pin
        did not reach."""
        outcome = mixed_old_format_event(
            "57\nFORGED",
            "review-backend",
            "NEW subject",
            dict(self.EVENT_HANDOFF),
            [
                {
                    "team": "session-old\nFORGED",
                    "task": self._task("review-backend", "OLD subject"),
                }
            ],
        )
        assert outcome.state == OLD_FORMAT_MIXED
        assert "\n" not in outcome.report
        assert "57FORGED" in outcome.report
        assert "session-oldFORGED" in outcome.report

    def test_non_dict_event_handoff_is_unmatched(self):
        outcome = mixed_old_format_event(
            "57",
            "review-backend",
            "NEW subject",
            "not a mapping",
            [{"team": "session-old", "task": self._task("review-backend", "OLD")}],
        )
        assert outcome.state == OLD_FORMAT_UNMATCHED
        assert "cross-team check unavailable" in outcome.report

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
            "57", "coder-a", "old team subject", handoff,
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
    """PROSE PINS. These protect the INSTRUCTION SURFACE, not behaviour: a
    code mutant cannot redden them (test-only changes), and a text deletion
    reddens them on their own assertion.

    A REWORD MEANS UPDATING THE PIN, NEVER DELETING IT. A pin that is deleted
    because it reddened on a harmless reword leaves the rule unpinned, which
    is the state these pins exist to prevent. Tighten or loosen the anchor and
    keep the assertion.
    """

    @staticmethod
    def _from_section():
        """The Step 3 block's from-team section."""
        return SKILL_TEXT.split("    # FROM SECTIONS", 1)[1].split(
            "    # THIS SECTION.", 1
        )[0]

    @staticmethod
    def _this_section():
        """The Step 3 block's this-team section."""
        return SKILL_TEXT.split("    # THIS SECTION.", 1)[1].split("\n```", 1)[0]

    def _from_disk_only_branch(self):
        """The from section's branch for a store with no journal group."""
        return self._from_section().split("        if not groups:", 1)[1].split(
            "\n        elif not from_task:", 1
        )[0]

    def test_partition_clause_present(self):
        assert (
            "THIS IS A PARTITION: A PARTITION IS NEVER DISCARDED FOR NAMING "
            "ANOTHER TEAM" in SKILL_TEXT
        )
        # M-2: the FOREIGN clause. Removing "do not union it" flips the
        # instruction to allow merging a foreign event.
        assert "do not process it, do not union it" in SKILL_TEXT

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

    def test_group_key_carries_the_arc(self):
        """The group key is the occupant identity PLUS the arc, so two
        instances of one task id that share agent and subject are two groups
        when the journal records their creations. Without a creation
        boundary they collapse, and the skill says so and reports it."""
        assert "Group the match with the `group_events` route" in SKILL_TEXT
        assert "plus the event's ARC" in SKILL_TEXT
        assert (
            "they form one group and only the latest is processed" in SKILL_TEXT
        )
        assert (
            "distinct contents with no creation boundary; the latest was "
            "processed; the earlier contents remain in the journal" in SKILL_TEXT
        )
        assert "**DO NOT FOLD THE TEAM INTO `occupant_hash`.**" in SKILL_TEXT
        # The unearned claim is gone from the instruction surface.
        assert "carries one identity for each arc" not in SKILL_TEXT

    def test_renderer_source_does_not_restate_the_arc_claim(self):
        """The renderer is the executable twin of the harvest. A copy of the
        claim there would be a second, unverified statement of the assertion
        the skill now states with its limit."""
        renderer = (
            Path(__file__).resolve().parents[1]
            / "hooks"
            / "shared"
            / "session_resume.py"
        ).read_text(encoding="utf-8")
        assert "carries one identity for each arc" not in renderer

    def test_from_team_route_present(self):
        """P6: a filter-shaped fix fails here — the realigned earlier team is
        routed to its own section, not dropped."""
        assert (
            "Each partition naming a team the journal realigned FROM"
            in SKILL_TEXT
        )
        assert "synthesize it under that team's `team={from}` label" in SKILL_TEXT
        # M-2: the P6 CORE guarantee. Flipping "never union it" to "union it"
        # would merge a realigned team's copy into this team's task file.
        assert "never union it with this team's task file" in SKILL_TEXT
        assert "UNION IT WITH THAT TEAM'S TASK FILE" in SKILL_TEXT

    def test_from_team_partition_unions_with_its_own_store(self):
        """The from-team loop reads THAT team's store and unions that copy.
        The journal alone drops task-file-only content, and reading THIS
        team's store produces a false 'task file absent' report for a task
        that lives in the from store."""
        block = self._from_section()
        assert "from_task = read_task_json(task_id, team)" in block
        assert "union_preferring_task_file(handoff, disk, task_id)" in block
        assert "handle(team, task_id, merged, meta, handoff)" in block
        # The main section still reads THIS team's store, and the false gap
        # for an all-from-team task is guarded.
        assert "read_task_json(task_id, this_team)" in SKILL_TEXT
        assert "elif not from_handled_any:" in SKILL_TEXT

    def test_old_format_cross_check_present(self):
        assert "AN OLD-FORMAT EVENT CARRIES NO `team` FIELD" in SKILL_TEXT
        assert "handoff cross-team check unavailable for task N" in SKILL_TEXT
        assert "DO NOT ENUMERATE THE TASKS ROOT" in SKILL_TEXT
        # M-2: flipping "discard" to "keep" would process a mixed event.
        assert "discard a mixed event" in SKILL_TEXT

    def test_partition_route_is_called_not_inlined(self):
        """A-F6: the NEW-FORMAT partition lives in `partition_destination`
        beside `old_format_destination`, and the skill branches on the
        exported labels. The inline comparison was the unexecutable half that
        produced a prose/pseudocode disagreement."""
        assert (
            "DECIDE EACH ROUTE WITH THE SHARED `partition_destination`"
            in SKILL_TEXT
        )
        assert "partition_destination(" in SKILL_TEXT
        for label in (
            "PARTITION_OLD_FORMAT",
            "PARTITION_THIS",
            "PARTITION_FROM",
            "PARTITION_FOREIGN",
        ):
            assert label in SKILL_TEXT
        # The inline comparison must not come back. (`for team in from_teams`
        # in the membership loop is a different construct and stays.)
        assert "elif team == this_team:" not in SKILL_TEXT
        assert "elif team in from_teams:" not in SKILL_TEXT

    def test_old_format_routing_uses_the_structured_outcome(self):
        """The pseudocode must ROUTE on the outcome, not collapse every
        non-mixed classification into this team. A from-store-owned event is
        appended to that team's partition, never to this team's."""
        assert "old_format_destination(outcome, this_team)" in SKILL_TEXT
        assert "from_events.setdefault(outcome.team, []).append(e)" in SKILL_TEXT
        assert 'if destination == OLD_FORMAT_DEST_MIXED:' in SKILL_TEXT
        assert 'elif destination == OLD_FORMAT_DEST_FROM:' in SKILL_TEXT

    def test_old_format_section_reports_branch_locally(self):
        """BRANCH-LOCAL: one report in the MIXED block, ZERO in the FROM
        block, one in the ELSE (UNMATCHED) block. A whole-file count of two
        would pass a version that reported twice in MIXED and never in the
        UNMATCHED branch, which is the silent-choice defect on the common
        old-format path."""
        after_from_label = SKILL_TEXT.split('elif destination == OLD_FORMAT_DEST_FROM:', 1)[1]
        mixed_block = SKILL_TEXT.split('if destination == OLD_FORMAT_DEST_MIXED:', 1)[1].split(
            'elif destination == OLD_FORMAT_DEST_FROM:', 1
        )[0]
        from_block = after_from_label.split("\n            else:", 1)[0]
        else_block = after_from_label.split("\n            else:", 1)[1].split(
            "    # STEP 2", 1
        )[0]
        assert mixed_block.count("report(outcome.report)") == 1
        assert from_block.count("report(outcome.report)") == 0
        assert else_block.count("report(outcome.report)") == 1
        assert "task_id, e.get(\"agent\", \"\")" in SKILL_TEXT

    def test_skill_calls_the_shared_identity_helpers(self):
        """The skill must NOT carry a local `def emit_side_subject` and must
        CALL `disk_identity` (directly, and through `group_events`), so the
        identity rule has one executable home."""
        assert "def emit_side_subject" not in SKILL_TEXT
        assert "disk_identity(task.get(" in SKILL_TEXT
        # `disk_identity` is the ONE identity derivation: the block never
        # composes `occupant_hash` itself.
        fence = SKILL_TEXT.split("```python\n", 1)[1].split("\n```", 1)[0]
        assert "occupant_hash(" not in fence
        assert "group_events(" in fence

    def test_divergence_report_is_decided_after_the_group_loop(self):
        """The divergence reports compare ONE task file with the WHOLE group
        set, so both sections decide them AFTER their loop and emit each
        ONCE. Inside the loop they would fire once per work unit, and for a
        multi-arc task_id 'no event group carries its identity' is false
        whenever another group carries it. A DRAINED from store reports in
        the from section too, so the two sections speak in the same two
        states and are quiet in the same one."""
        assert "is not the task file's group" not in SKILL_TEXT
        assert "processed from the journal copy alone" not in SKILL_TEXT
        for section in (self._from_section(), self._this_section()):
            loop = section.split("for key, group in groups.items():", 1)[1]
            body, after = loop.split("        if not groups:", 1) if (
                "        if not groups:" in loop
            ) else loop.split("    if not task:", 1)
            assert 'carries its identity")' not in body
            assert 'task file absent")' not in body
            assert after.count('f"task file present, no event group carries its identity")') == 1
            assert "owner is None:" in after
        assert self._from_section().count(': task file absent")') == 2
        assert self._this_section().count(': task file absent")') == 1

    def test_divergence_report_rule_prose_states_the_once_rule(self):
        """The rule text must carry the once-post-loop instruction and the
        from-team parity, so a reader cannot re-derive a per-group report or
        a total-mismatch message for a group that lost to another match."""
        assert (
            "decide its report AFTER the group loop and emit it ONCE per task"
            in SKILL_TEXT
        )
        assert "A from-team section applies the SAME rule" in SKILL_TEXT
        assert "both the absent-file and the no-matching-group report" in SKILL_TEXT
        assert "with the SAME wording" in SKILL_TEXT
        assert "report NOTHING" in SKILL_TEXT

    def test_merge_report_lines_land_in_the_memory_entry(self):
        """F4, architect option (c): a merge report records a DECISION, and
        the journal preserves only the raw content, so the entry's `context`
        is the decision's only durable home. Dropping a report line in the
        synthesis is a DEFECT — it reproduces the gap one layer up — not a
        discretionary omission of colour. The rule must name BOTH report
        families, or a reader can exempt the union lines or the partitions.

        THE ANCHORS ARE VOCABULARY-LEVEL ON PURPOSE. Each survives a
        grammatical reword (EVERY ONE->ALL, SYNTHESIS->SUMMARY, a dropped
        "both" or "Step 3"), so a legitimate reword does not tempt the next
        editor to delete the pin. A swap of the rule's OWN TERMS
        ("reports"->"outputs", "lands"->"is written") is SUPPOSED to red:
        that changes what the rule says and buys a pin update. Do not loosen
        these to absorb it."""
        assert "MEMORY ENTRY'S `context` MUST CARRY" in SKILL_TEXT
        assert "DROPS A REPORT LINE IS A DEFECT" in SKILL_TEXT
        assert "partitions' reports and the union's per-field lines" in SKILL_TEXT
        assert "report line for that unit lands in `context`" in SKILL_TEXT

    def test_report_lines_use_the_shared_sanitiser(self):
        """F1: the skill's hand-written report sites must interpolate through
        `report_safe`. The helper-produced strings are already sanitised; the
        asymmetry this pin closes is the four sites the skill composes
        itself."""
        assert "report_safe" in SKILL_TEXT
        assert SKILL_TEXT.count("report_safe(") >= 4

    def test_empty_membership_rule_is_stated(self):
        """The rule text must define EMPTY as membership, not falsiness: 0 and
        False being content is the property the whole branch depends on, and
        prose is its only carrier."""
        assert "`EMPTY` MEANS MEMBERSHIP IN" in SKILL_TEXT

    def test_output_spelling_rule_is_stated(self):
        """The rule text must say that no journal spelling of a task-file-won
        field survives; the code drops every alias spelling and the arms
        assert it, but without this sentence a reader cannot know."""
        assert "NO JOURNAL SPELLING" in SKILL_TEXT

    def test_old_format_candidate_ordering_is_stated(self):
        """The rule text must say an identity match in ANY candidate beats an
        earlier content-only match; otherwise a reader can short-circuit at
        the first mixed result."""
        assert "CHECK EVERY NAMED CANDIDATE" in SKILL_TEXT

    def test_old_format_candidates_are_read_only_when_needed(self):
        """Architecture performance note: new-format events take no extra
        read. An unguarded candidate build reads the stores for every task,
        then reads this team's file AGAIN for the union."""
        assert "if old_format:" in SKILL_TEXT

    def test_unreadable_task_list_branch_is_implemented(self):
        """The prose and the guard name the SAME state — an absent
        `Task list:` or an underivable last path component (empty this_team).
        That makes EVERY event OLD-FORMAT and fires the underivable report;
        without the guard, team-carrying events go to the FOREIGN report
        instead, the opposite instruction."""
        route = SKILL_TEXT.split("def route(e):", 1)[1].split("def handle(", 1)[0]
        assert "if not this_team:\n        return PARTITION_OLD_FORMAT" in route
        assert "if matches and not this_team:" in SKILL_TEXT
        assert "task_list_dir else" in SKILL_TEXT

    def test_snapshot_section_join_present(self):
        assert "handoff snapshot identity mismatch for task N" in SKILL_TEXT
        # M-2: the DROP is the rule; "is reported" without "DROPPED" would
        # let a foreign snapshot's metadata be used.
        assert "names another team is DROPPED and reported" in SKILL_TEXT

    def test_occupant_hash_is_not_folded_with_team(self):
        assert "DO NOT FOLD THE TEAM INTO `occupant_hash`" in SKILL_TEXT

    def test_skill_does_not_describe_the_renderer(self):
        """The resume renderer applies its own rule and states it in its own
        code; a description of it in the selection step is a second copy of
        a rule this file does not own."""
        step3 = SKILL_TEXT.split("### Step 3: Read All HANDOFFs", 1)[1].split(
            "### Step 3.1", 1
        )[0]
        assert "session_resume" not in step3

    def test_skill_carries_no_measurement_narrative(self):
        """The skill is instruction: measurements and review history belong
        in the commit record, not in text every harvest loads."""
        for phrase in ("MEASURED", "this branch", " of 1421", "Three rounds of review"):
            assert phrase not in SKILL_TEXT, phrase

    def test_data_boundary_sentence_present(self):
        assert "EVERY VALUE CROSSES AS DATA, NEVER AS COMMAND TEXT" in SKILL_TEXT

    # ---- PROSE-REVIEW FIXES. One pin per finding: a prose fix with no pin
    # is how the earlier divergences survived a green suite. ----

    def test_b1_missing_required_field_is_not_a_proper_subset(self):
        """B1: 'the task file lacks a required field' does NOT imply D ⊂ J.
        The old sentence routed that state to the restore branch and told the
        reader to revive the cleared field — the defect this branch exists to
        prevent. The measured counterexample must stay in the text."""
        assert "A MISSING REQUIRED FIELD DOES NOT IMPLY A PROPER SUBSET" in SKILL_TEXT
        assert (
            "against disk `{produced, uncertainty, open_questions}`"
            in SKILL_TEXT
        )
        assert (
            "If the task file lacks any REQUIRED field the journal copy "
            "carries, `D` is a proper subset of `J`"
            not in SKILL_TEXT
        )

    def test_journal_side_is_membership(self):
        """The code gates State A on MEMBERSHIP on both sides; the text must
        say so and must not describe a truthiness test on the journal side."""
        assert (
            "**`EMPTY` MEANS MEMBERSHIP IN `(None, '', [], {})`, NOT "
            "FALSINESS, ON BOTH SIDES.**" in SKILL_TEXT
        )
        assert (
            "a journal `0` or `False` beside an empty task-file field is "
            "content and the State A rules decide it" in SKILL_TEXT
        )
        assert "DECIDED BY TRUTHINESS" not in SKILL_TEXT

    def test_b3_alias_map_is_the_normaliser_not_resolve_handoff_field(self):
        """B3: `resolve_handoff_field` returns None for absent AND for
        present-None, so using it to normalise keys collapses the very
        distinction State A/B rests on. The text must name the alias map."""
        assert "`resolve_handoff_field` IS NOT A KEY NORMALISER" in SKILL_TEXT
        assert "HANDOFF_LEGACY_ALIASES` map" in SKILL_TEXT
        assert (
            "to their canonical names with the shared `resolve_handoff_field`"
            not in SKILL_TEXT
        )

    def test_b4_ownerless_candidate_is_skipped_not_mixed(self):
        """B4: a content-matching candidate whose record has no owner is
        SKIPPED, leaving the event UNMATCHED. Read literally as an identity
        mismatch, the code would discard the realigned team's own HANDOFF."""
        assert (
            "A CONTENT-MATCHING CANDIDATE WHOSE OWN RECORD CARRIES NO `owner` "
            "IS SKIPPED" in SKILL_TEXT
        )

    def test_sixth_no_stale_per_group_report_pointer(self):
        """The orphan: the selection rule still pointed at a per-group report
        message that the once-post-loop rule deleted. It must now say the
        other group gets NO report."""
        assert (
            "report each other group through the report-message rule below"
            not in SKILL_TEXT
        )
        assert "gets NO per-group report" in SKILL_TEXT

    def test_minor_from_team_units_are_recorded_under_their_store(self):
        """A from-team work unit is PROCESSED, SYNTHESIZED and RECORDED in
        this team's section under a `{from}/` token, so a later pass skips it
        and the token never matches this team's task of the same number.
        Writing a `## team={from}` section would modify another team's
        ledger."""
        assert (
            "name team `{from}` as the source in the entry's context"
            in SKILL_TEXT
        )
        assert (
            "Record its unit token, `{from}/{id}#{key}`, in this team's "
            "section at Step 8" in SKILL_TEXT
        )
        assert "DO NOT RUN STEP 2 OR STEP 8 FOR THEM" not in SKILL_TEXT
        assert (
            "run Step 2 and Step 8 for them against a `## team={from}` section"
            not in SKILL_TEXT
        )

    def test_minor_process_is_defined(self):
        """The pseudocode calls `process`; its header says what it does, so
        a reader executing the block does not invent the ingestion step."""
        assert "process(handoff, metadata=None, section=None) runs" in SKILL_TEXT
        assert "Steps 4-7 for one work unit" in SKILL_TEXT

    def test_minor_task_file_only_branch_has_a_prose_rule(self):
        """A task with no journal event and a present task file is a normal
        population, not a gap; the pseudocode had the branch, the prose did
        not."""
        assert (
            "When the task carries NO `agent_handoff` event in a SECTION and "
            "that SECTION's task file EXISTS" in SKILL_TEXT
        )

    def test_from_team_disk_only_task_is_processed(self):
        """The from-team half has rule 5's disk-only path: the file is
        processed under `## team={from}`, and the store produces NEITHER the
        absent-file NOR the no-match report."""
        assert "THIS IS ONE RULE WITH TWO APPLICATIONS, NOT TWO RULES" in SKILL_TEXT
        assert (
            "it produces NEITHER the absent-file report NOR the no-match report"
            in SKILL_TEXT
        )
        # The store set is built from BOTH shapes, not from journal events
        # alone.
        assert (
            "for team in sorted(set(from_events) | {t for t in from_teams "
            "if (t, task_id) in candidates}):" in SKILL_TEXT
        )
        disk_only = self._from_disk_only_branch()
        assert "read_task_json" not in disk_only
        assert "handle(team, task_id, disk, meta, disk or meta)" in disk_only
        block = self._from_section()
        assert block.index("handle(team, task_id, disk, meta, disk or meta)") < block.index(
            "handoff divergence check unavailable for task"
        )

    def test_high_from_team_membership_is_census_provenance(self):
        """The disk shape is admitted by PROVENANCE, not by a content test:
        the from census admits a task by ANY agent-authored key, so a
        sibling-key-only file must enter and Step 3.1 resolve it. An id that
        is ONLY this team's must never admit a same-numbered stranger's file,
        and admission is per store."""
        assert (
            "for team in from_teams:\n"
            "    for task_id in census.get(team, set()):\n"
            "        candidates.add((team, task_id))" in SKILL_TEXT
        )
        disk_only = self._from_disk_only_branch()
        assert "if from_task and (team, task_id) in candidates:" in disk_only
        assert "candidate_handoff" not in SKILL_TEXT
        assert 'get("handoff")' not in disk_only

    def test_high_disk_only_from_branch_never_sets_handled_any(self):
        """`from_handled_any`'s only consumer is THIS team's report-gap line.
        A from-store file is not evidence about this team's task, so the
        disk-only branch must not set the flag — otherwise a drained
        this-team discovery vanishes with no gap report."""
        disk_only = self._from_disk_only_branch()
        assert "handle(team, task_id, disk, meta, disk or meta)" in disk_only
        assert "from_handled_any = True" not in disk_only
        # The gap line the flag would suppress is still reachable.
        assert 'report_gap(task_id, "unknown",' in SKILL_TEXT

    def test_from_team_journal_partition_still_sets_handled_any(self):
        """A journal event's `team` field is an explicit store claim, so the
        journal-group branch keeps setting the flag; the all-from-team carve
        out depends on it."""
        loop_body = self._from_section().split(
            "for key, group in groups.items():", 1
        )[1].split("        if not groups:", 1)[0]
        assert "from_handled_any = True" in loop_body
        assert "elif not from_handled_any:" in SKILL_TEXT

    def test_minor_from_handled_any_carve_out_is_prose(self):
        """The all-from-team task has no file in THIS store and must not
        emit the report-gap line; this was carried only by a pseudocode
        comment."""
        assert (
            "HAS NO TASK FILE IN THIS SECTION'S STORE, AND THE ABSENCE IS NOT "
            "A GAP" in SKILL_TEXT
        )

    def test_minor_report_gap_records_the_agent_when_known(self):
        """The rule says when the agent is knowable and what to record
        otherwise, and the call carries the field."""
        assert (
            "record the agent as `unknown` rather than omitting the field"
            in SKILL_TEXT
        )
        assert 'report_gap(task_id, "unknown",' in SKILL_TEXT

    def test_minor_dual_spelling_value_selection_is_stated(self):
        """The disk value of a dual-spelled field is chosen by the same rule
        as `resolve_handoff_field`: a non-empty canonical, else the first
        non-empty alias, where empty is membership in (None, '', [], {}). Without it a task file carrying `decisions: ''` beside
        `key_decisions: [...]` reads as empty."""
        assert (
            "VALUE SELECTION FOR A DUAL-SPELLED COPY IS A SEPARATE STEP"
            in SKILL_TEXT
        )
        assert "an EMPTY canonical falls through to the FIRST alias spelling whose value is not EMPTY" in SKILL_TEXT
        assert "TRUTHY" not in SKILL_TEXT

    def test_prose_contradictions_from_the_human_read_stay_fixed(self):
        """Each phrase contradicted the code it describes: a truthiness value
        rule (the code selects by membership), an arc called a creation stamp
        (it is the store epoch plus a journal position), a direct store read
        (stores are read through the route), a claim that every journal event
        predates the team field, an artifact mark the token ledger does not
        have, and a NEWEST `Last processed` that no step compares."""
        for phrase in (
            "FALSY canonical", "FIRST TRUTHY", "creation stamp",
            "`{that store}/{task_id}.json` directly",
            "every event already in a journal predates the `team` field",
            "extends to mark a `(workflow, feature)` artifact as read",
            "the NEWEST `Last processed`", "no processed IDs",
            "apply for you,.",
        ):
            assert phrase not in SKILL_TEXT, phrase
        assert "the store epoch and the journal POSITION of the `task_created` event" in SKILL_TEXT
        assert "Read that store's task file with the `read_task_json` route" in SKILL_TEXT
        assert "WITH THE `disk_identity` ROUTE, NEVER BY HAND" in SKILL_TEXT

    def test_orphan_recovery_sets_the_block_inputs_for_the_prior_session(self):
        """Orphan Recovery runs the Step 3 block with the prior session as
        THIS section, reads that session's whole journal, and prefixes the
        unprefixed tokens it records with the prior team."""
        orphan = SKILL_TEXT.split("## Orphaned Handoff Recovery", 1)[1]
        assert "RUN THE STEP 3 BLOCK WITH THE PRIOR SESSION AS THIS SECTION" in orphan
        assert "`task_list_dir` is `{config_dir}/tasks/{prior team_id}`" in orphan
        assert "`read_events_from(session_dir)` with no type" in orphan
        assert "Prefix each recorded token that carries no `/` with `{prior team}/`" in orphan
        assert "read_events_from(session_dir, 'agent_handoff')" not in SKILL_TEXT
        assert "re-derives its processed set" not in SKILL_TEXT

    def test_inventory_routes_every_listed_helper(self):
        """Every helper in this test's LITERAL list has a runnable import
        route in the inventory. The list is a literal, not derived from the
        skill text: a new helper used by the step must be added BOTH to the
        inventory and to this list, or this pin cannot see it.

        This parses the inventory's REAL import lines — a pin asserting a
        sentence about coverage passes while the sentence is wrong."""
        section = SKILL_TEXT.split(
            "**HOW TO CALL THE NAMES THIS STEP USES.**", 1
        )[1].split("🔴 **THE SUBSTITUTION IS CALLED", 1)[0]
        import_lines = [
            line for line in section.splitlines() if " import " in line
        ]
        assert import_lines, "the inventory has no import lines"
        for name in (
            "report_safe",
            "disk_identity",
            "handoff_content_key",
            "union_preferring_task_file",
            "latest_by_ts_then_journal_order",
            "group_events",
            "current_arc",
            "arc_of",
            "select_snapshot",
            "owning_group",
            "undivided_contents",
            "partition_destination",
            "old_format_destination",
            "mixed_old_format_event",
            "safe_from_teams",
            "unit_token",
            "parse_ledger_tokens",
            "read_task_json",
            "HANDOFF_LEGACY_ALIASES",
        ):
            routed = [line for line in import_lines if name in line]
            assert routed, f"{name} has no import line in the inventory"
            assert any("python3 -c" in line for line in routed), (
                f"{name} has no RUNNABLE route"
            )
        # The tie-break is shipped code; a local definition must not return.
        assert "def latest_by_ts_then_journal_order" not in SKILL_TEXT
        # The two label sets the routers return are documented with them.
        for label in (
            "PARTITION_THIS",
            "PARTITION_FROM",
            "PARTITION_FOREIGN",
            "PARTITION_OLD_FORMAT",
            "OLD_FORMAT_DEST_MIXED",
            "OLD_FORMAT_DEST_FROM",
            "OLD_FORMAT_DEST_THIS",
        ):
            assert label in section

    def test_discovery_routes_every_event_before_any_ledger_check(self):
        """The from-team population must not pass through an id-level ledger
        filter. Discovery routes each event to its section first, collects
        (section, id) pairs, and Step 3 checks each selected unit's token, so
        a from-store task whose NUMBER this team recorded is still reached."""
        assert (
            "**Route each remaining `agent_handoff` event at discovery with "
            "`partition_destination`, and collect `(section, task_id)` pairs, "
            "never bare ids.**" in SKILL_TEXT
        )
        assert "**Do not subtract anything from the discovered pairs here.**" in SKILL_TEXT
        assert 'candidates.add((e.get("team"), e.get("task_id")))' in SKILL_TEXT
        assert "token in processed_tokens" in SKILL_TEXT
        # The id-level population and its filter must not come back.
        assert "from_unprocessed" not in SKILL_TEXT
        assert "task_id not in unprocessed" not in SKILL_TEXT
        assert "THE FILTER APPLIES TO THIS TEAM'S IDS ONLY" not in SKILL_TEXT

    def test_ledger_records_unit_tokens(self):
        """The ledger records WORK-UNIT TOKENS, never bare task numbers: a
        number names different work in two stores and in a recreated store.
        Step 8 writes both sections' tokens on this team's line, and every
        writer inherits that."""
        assert (
            "**Write the unit tokens Step 3 recorded, from both sections, on "
            "this team's line.**" in SKILL_TEXT
        )
        assert "{comma-separated unit tokens}" in SKILL_TEXT
        assert "A from-team token carries its `{from}/` prefix" in SKILL_TEXT
        assert (
            "Write each `Processed task IDs` line at column 0, exactly as the "
            "template shows; never place tokens inside a note." in SKILL_TEXT
        )
        assert "recorded_tokens.append(token)" in SKILL_TEXT
        assert "**append** the recorded tokens" in SKILL_TEXT
        assert "Prefix each recorded token that carries no `/` with `{prior team}/`" in SKILL_TEXT
        assert "WRITE THIS TEAM'S IDS ONLY" not in SKILL_TEXT
        assert "{comma-separated IDs}" not in SKILL_TEXT

    # ---- second prose pass: blocking items, each RED on 86f8dd86 ----
    def test_blocking_b1_how_to_call_routes_the_three_helpers(self):
        """B1: a mandated call with no route. The step and pseudocode name
        `partition_destination`, `old_format_destination`,
        `mixed_old_format_event` and `HANDOFF_LEGACY_ALIASES`; every one must
        have a runnable or precisely-described entry in HOW TO CALL."""
        section = SKILL_TEXT.split(
            "**HOW TO CALL THE NAMES THIS STEP USES.**", 1
        )[1].split("🔴 **THE SUBSTITUTION IS CALLED", 1)[0]
        assert "partition_destination(event_team, this_team, from_teams)" in section
        assert (
            "from shared.handoff_merge import partition_destination"
            in section
        )
        assert "PARTITION_THIS" in section and "PARTITION_OLD_FORMAT" in section
        assert "old_format_destination(outcome, this_team)" in section
        assert "from shared.handoff_merge import OldFormatOutcome" in section
        assert "OLD_FORMAT_DEST_MIXED" in section
        assert (
            "mixed_old_format_event(task_id, event_agent, event_subject, "
            "event_handoff, candidate_tasks)" in section
        )
        assert (
            "from shared.handoff_merge import mixed_old_format_event"
            in section
        )
        assert "HANDOFF_LEGACY_ALIASES" in section
        assert "It is a DICT, not a call" in section

    def test_blocking_b2_union_identity_names_disk_identity(self):
        """The union identity sentence names `disk_identity`, which applies
        the `(no subject)` substitution; `occupant_hash` would skip it, so a
        subject-less task's identity would never match a group."""
        sentence = SKILL_TEXT.split(
            "4. The task file belongs to at most one group", 1
        )[1].split("**A group that does not carry", 1)[0]
        assert "disk_identity(owner, subject)" in sentence
        assert "occupant_hash(owner, subject)" not in sentence
        assert "never a recomposed `occupant_hash`" in sentence
        assert "SUBSTITUTED" in sentence
        assert "`owning_group` route" in sentence

    def test_blocking_b3_from_team_units_are_saved(self):
        """B3: "Steps 4-6" excludes Step 7 (Save to pact-memory), so every
        from-team work unit is extracted, deduped and thrown away."""
        assert "Steps 4-6" not in SKILL_TEXT
        assert "Synthesize each of their work units under Steps 4-7" in SKILL_TEXT
        assert "Steps 4-7" in SKILL_TEXT

    def test_blocking_b4_report_gap_is_defined_and_stamped(self):
        """`report_gap` is defined in the block and its call carries the
        timestamp the prose requires."""
        assert "def report_gap(task_id, agent, ts):" in SKILL_TEXT
        assert "report_safe(agent)" in SKILL_TEXT
        assert "report_safe(ts)" in SKILL_TEXT
        assert 'datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")' in SKILL_TEXT
        assert 'report_gap(task_id, "unknown",' in SKILL_TEXT
        assert SKILL_TEXT.index("def report_gap(") < SKILL_TEXT.index(
            'report_gap(task_id, "unknown",'
        )

    def test_minor_rule5_carries_its_own_provenance_gate(self):
        """Rule 5 states that a from-team application admits only census
        pairs, PER STORE, so a reader neither adds a per-file content test nor
        reads another store's same-numbered file."""
        assert "**ADMISSION IS PER STORE:**" in SKILL_TEXT
        assert "a `({from}, id)` pair that store's census produced" in SKILL_TEXT
        assert "ADMISSION IS AGGREGATE BY ID" not in SKILL_TEXT

    def test_minor_dual_spelling_sentence_is_qualified(self):
        """Minor 6: 'the selected raw key is the one the output carries' is
        false when the journal copy wins a dual-spelled field."""
        assert (
            "The selected raw key is the one the output carries WHEN THE TASK "
            "FILE WINS" in SKILL_TEXT
        )
        assert "BOTH spellings survive" in SKILL_TEXT

    def test_minor_three_report_conditions_are_exclusive(self):
        """Minor 7: absent-file, no-match and gap must be exclusive in one
        place, not fallbacks for one another."""
        assert "THE THREE CONDITIONS ARE EXCLUSIVE" in SKILL_TEXT
        assert "The GAP line requires NO source at all" in SKILL_TEXT

    def test_minor_disk_only_paths_pass_metadata_to_process(self):
        """Rule 5 promises Step 3.1 resolves sibling keys, so the disk-only
        branches pass the section's task metadata, not only its handoff."""
        assert "handle(team, task_id, disk, meta, disk or meta)" in SKILL_TEXT
        assert "handle(None, task_id, disk, meta, disk or meta)" in SKILL_TEXT
        assert "meta = task_metadata(from_task)" in SKILL_TEXT
        assert "meta = task_metadata(task)" in SKILL_TEXT

    def test_journal_paths_pass_their_own_store_metadata_to_process(self):
        """Every journal-group `process` hands Step 3.1 the metadata of the
        store it READ, and ONLY the group that owns that store's task file
        gets it; every other group gets None, so a same-numbered file of a
        different occupant never feeds another unit's siblings."""
        for section, label in ((self._from_section(), "team"), (self._this_section(), "None")):
            assert f"handle({label}, task_id, merged, meta, handoff)" in section
            assert f"handle({label}, task_id, handoff, None, handoff)" in section
            owner_branch = section.split("if key == owner:", 1)[1].split("else:", 1)[0]
            assert "merged, meta, handoff" in owner_branch

    # ---- amendment items ----
    def test_minor_partition_chain_fails_closed_on_an_unknown_label(self):
        """The partition chain's final `else` routes an unknown label to
        FOREIGN — reported, never processed. The OLD-FORMAT chain defaults the
        other way on purpose (its UNMATCHED case is this team's by identity),
        so the two default postures must be stated."""
        assert (
            "        elif destination == PARTITION_FROM:\n"
            '            from_events.setdefault(e.get("team"), []).append(e)\n'
            "        else:\n"
            "            # An UNKNOWN label fails CLOSED"
            in SKILL_TEXT
        )
        # The sentence wraps across comment lines, so the fragments are
        # asserted separately rather than as one contiguous phrase.
        assert "The OLD-FORMAT chain defaults" in SKILL_TEXT
        assert "the other way on purpose" in SKILL_TEXT
        assert "for e in foreign:" in SKILL_TEXT

    def test_m1_union_annotates_its_inputs_as_object(self):
        """M1: the annotations said `dict` while the function accepts — and
        the docstring now promises — non-dict input. The annotation is the
        contract a reader sees."""
        params = inspect.signature(union_preferring_task_file).parameters
        assert params["journal_handoff"].annotation == "object"
        assert params["disk_handoff"].annotation == "object"

    def test_m2_module_header_names_both_routers(self):
        """M2: the module summary described one router while the module now
        carries two."""
        header = (
            Path(__file__).resolve().parents[1]
            / "hooks"
            / "shared"
            / "handoff_merge.py"
        ).read_text(encoding="utf-8").split('"""', 2)[1]
        assert "partition_destination" in header
        assert "old_format_destination" in header

    def test_m3_the_router_function_is_authoritative_over_the_prose(self):
        """M3: the mapping is stated in both the prose and the function; the
        text must say which one a change belongs in."""
        assert (
            "THE FUNCTION IS AUTHORITATIVE; THIS PROSE NAMES THE OUTCOMES "
            "FOR THE READER" in SKILL_TEXT
        )

    def test_m6_underivable_report_needs_a_partition(self):
        """The underivable report fires only for an id with a journal match:
        a population id with no match has no partition to be underivable
        about."""
        assert (
            "    if matches and not this_team:\n"
            '        report(f"handoff team identity underivable for task "'
            in SKILL_TEXT
        )

    # ---- final text-accuracy pass: items 1-6 ----
    def test_item1_underivable_report_needs_a_journal_match(self):
        """Item 1: the prose said "every partition" unconditionally while
        the pseudocode gates the report on `matches`."""
        assert (
            "THE REPORT FIRES ONCE PER TASK AND ONLY WHEN A JOURNAL MATCH "
            "EXISTS" in SKILL_TEXT
        )
        assert "a census-only id has no partition to be underivable about" in SKILL_TEXT

    def test_item2_gap_counts_only_from_team_journal_partitions(self):
        """Item 2: a reader could count the disk-only from-team application
        as a source and wrongly silence this team's gap."""
        assert "no from-team JOURNAL PARTITION processed" in SKILL_TEXT
        assert (
            "The disk-only from-team application does NOT count as a source "
            "for this line" in SKILL_TEXT
        )

    def test_item3_admission_is_per_store(self):
        """An id admitted in one from store never admits another store's file
        of the same number."""
        assert (
            "an id admitted in one from store never admits another store's "
            "file of the same number" in SKILL_TEXT
        )

    def test_item4_gap_agent_is_unknown_when_unrecoverable(self):
        """Item 4: the agent is `unknown` when no source STILL IN SCOPE
        carries one; a file drained after discovery had an owner that is no
        longer recoverable."""
        assert "WHEN A SOURCE STILL IN SCOPE CARRIES ONE" in SKILL_TEXT
        assert (
            "a task file drained between discovery and Step 3 had an owner "
            "that is no longer recoverable" in SKILL_TEXT
        )

    def test_item6_disk_identity_applies_the_substitution(self):
        """The step calls only `disk_identity`, which applies the emit paths'
        substitution; no separate substitution route exists to call."""
        assert (
            "with the emit paths' `(no subject)` substitution applied"
            in SKILL_TEXT
        )
        assert "the step names it but calls only" not in SKILL_TEXT

    def test_classifier_docstring_states_the_ownerless_skip(self):
        """FIX 2, QUALIFIED: the skip removes ONE candidate; another named
        candidate can still decide the event. The earlier wording claimed the
        skip left the event UNMATCHED, which is false beside a second
        candidate that returns OWNED_BY or MIXED."""
        doc = " ".join((mixed_old_format_event.__doc__ or "").split())
        assert "no non-empty `owner` is SKIPPED" in doc
        assert "would manufacture a MISMATCH" in doc
        assert (
            "THE SKIP REMOVES THIS CANDIDATE FROM CONSIDERATION AND DOES NOT "
            "SETTLE THE EVENT" in doc
        )
        assert "is UNMATCHED only when no other candidate decides it" in doc
        # The unqualified claim must not come back, on either surface.
        assert "Skipping leaves the event UNMATCHED" not in doc
        assert (
            "SKIPPING REMOVES THIS CANDIDATE FROM CONSIDERATION; IT DOES NOT "
            "SETTLE THE EVENT" in SKILL_TEXT
        )
        assert "Skipping leaves the event UNMATCHED" not in SKILL_TEXT

    def test_s3_realign_team_names_go_through_the_shared_filter(self):
        """`from_teams` is built from journal-supplied `team_realigned` `from`
        fields, so it goes through `safe_from_teams`: a non-str, an unsafe
        path component and this team's own name never enter the list, and
        each rejection is reported."""
        assert (
            "from_teams, from_reports = safe_from_teams(realign_events, this_team)"
            in SKILL_TEXT
        )
        assert "for line in from_reports:" in SKILL_TEXT
        assert "Never join a `from` that route rejected into a path" in SKILL_TEXT

    def test_step31_item1_uses_only_the_metadata_step3_passed(self):
        """Step 3.1 reads task-file siblings ONLY through the metadata Step 3
        passed, which only the owning group receives. An identity-only read
        would hand an older instance of the same occupant the current
        instance's siblings."""
        assert "use ONLY the `metadata` Step 3 passed for this unit" in SKILL_TEXT
        assert (
            "When Step 3 passed none, skip this item and use the snapshot "
            "fallback; do not read the task file." in SKILL_TEXT
        )
        assert "Only the group that owns the task file ever reads its siblings" in SKILL_TEXT
        assert "equals the unit's group identity" not in SKILL_TEXT

    def test_step31_snapshots_are_scoped_to_the_units_arc(self):
        """The snapshot fallback is the other site where one instance could
        take another instance's siblings. Every snapshot's arc must equal the
        unit's group arc: a team-less snapshot (null arc) reaches only a
        team-less unit, and a dated unit takes only its own team and arc."""
        assert "**THE SNAPSHOT'S ARC MUST EQUAL THE UNIT'S GROUP ARC.**" in SKILL_TEXT
        assert "with the `arc_of` route" in SKILL_TEXT
        assert (
            "**Select with the `select_snapshot` route in Step 3, under HOW TO "
            "CALL THE NAMES THIS STEP USES, and never by hand:**" in SKILL_TEXT
        )
        assert (
            "a DATED unit, one whose group arc is not null, accepts only a "
            "snapshot that carries its team and its arc" in SKILL_TEXT
        )
        # The pass-through for a team-less snapshot must not come back.
        assert "A snapshot with NO `team` is OLD-FORMAT and passes" not in SKILL_TEXT

    def test_resweep_claims_are_stated_in_units_and_tokens(self):
        """The re-sweep paragraph speaks in units and tokens like Step 3 and
        Incremental: the safety net skips recorded tokens, and a revision
        that re-emits is collected because its token is new; only a write to
        the task file alone escapes every trigger."""
        assert (
            "the Consolidation Harvest Workflow safety net processes only the "
            "units whose tokens it lacks" in SKILL_TEXT
        )
        assert "reads only tasks that are not in it" not in SKILL_TEXT
        assert "its unit has a NEW token and the next pass collects it" in SKILL_TEXT
        assert (
            "A write to the task file alone — a sibling key, or a handoff edit "
            "that fires no new emit — leaves the recorded token unchanged"
            in SKILL_TEXT
        )
        assert (
            "An agent writes content to a task after the pass that recorded "
            "that task as processed" not in SKILL_TEXT
        )

    def test_population_is_never_framed_as_an_id_delta(self):
        """The population is the units whose tokens the ledger lacks; no
        sentence frames it as a delta subtracted from the ledger."""
        assert "delta against" not in SKILL_TEXT
        assert "delta-only pass" not in SKILL_TEXT
        assert "processes only the delta" not in SKILL_TEXT
        assert "the units whose tokens this ledger lacks are the population" in SKILL_TEXT

    def test_discovery_skips_a_non_string_task_id(self):
        """Journal task ids are sorted, so a non-str id is skipped and
        reported at discovery, in the prose and in the block."""
        assert (
            "Skip an `agent_handoff` event whose `task_id` is not a non-empty "
            "string" in SKILL_TEXT
        )
        assert 'if not isinstance(e.get("task_id"), str) or not e.get("task_id"):' in SKILL_TEXT

    def test_foreign_non_str_team_reports_a_schema_line(self):
        """A non-str team is a SCHEMA violation wearing a routing label: the
        event is not a real team name to route away from. The two states must
        not share the identity-mismatch line. Pseudocode-level pin (the
        harvest is prose-executed)."""
        block = SKILL_TEXT.split("for e in foreign:", 1)[1].split(
            "# OLD-FORMAT events carry", 1
        )[0]
        assert "isinstance(team_value, str)" in block
        assert "handoff team value invalid for task" in block
        # The sentence wraps across two adjacent f-string literals; assert
        # the fragments rather than a phrase no line contains.
        schema_branch = block.split(
            "if not isinstance(team_value, str):", 1
        )[1].split("else:", 1)[0]
        assert "is not a valid team name" in schema_branch
        assert "event not processed" in schema_branch
        # The genuine foreign-STRING branch keeps the identity line.
        assert "journal event names team" in block
        assert "identity mismatch" not in schema_branch
