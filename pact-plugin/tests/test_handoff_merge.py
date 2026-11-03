"""
Location: pact-plugin/tests/test_handoff_merge.py
Summary: Contract tests for the reader-side helpers in
         hooks/shared/handoff_merge.py that the pact-handoff-harvest skill
         calls by route. Each arm is paired with the one-line mutation it
         exists to kill, named in its docstring.
Used by: pytest. The older union, identity and old-format arms live in
         test_handoff_merge_identity.py.
"""
from shared.handoff_merge import union_preferring_task_file

_REQUIRED_ALL = {
    "produced": "p",
    "decisions": ["d"],
    "reasoning_chain": "r",
    "uncertainty": ["u"],
    "integration": "i",
    "open_questions": ["o"],
}


class TestUnionJournalSideMembership:
    """State A decides the JOURNAL side by membership in
    (None, '', [], {}), the same test as the task-file side.
    Kills: `journal_value not in _EMPTY_VALUES` -> `journal_value`."""

    def test_journal_zero_beside_empty_disk_field_is_decided_and_reported(self):
        journal = dict(_REQUIRED_ALL, open_questions=0)
        disk = dict(_REQUIRED_ALL, open_questions=[])
        merged, reports = union_preferring_task_file(journal, disk, "7")
        # covering key sets -> the empty task-file value is kept, AND reported
        assert merged["open_questions"] == []
        assert reports == [
            "handoff field kept empty for task 7: field open_questions "
            "present and empty; the task-file required key set covers the "
            "journal copy"
        ]

    def test_journal_false_is_restored_when_disk_is_a_proper_subset(self):
        journal = dict(_REQUIRED_ALL, open_questions=False)
        disk = {k: v for k, v in _REQUIRED_ALL.items() if k != "produced"}
        disk["open_questions"] = ""
        merged, reports = union_preferring_task_file(journal, disk, "7")
        assert merged["open_questions"] is False
        assert any(
            "restored from the journal" in r and "open_questions" in r
            for r in reports
        )

    def test_journal_empty_values_still_do_not_trigger_state_a(self):
        for empty in (None, "", [], {}):
            journal = dict(_REQUIRED_ALL, open_questions=empty)
            disk = dict(_REQUIRED_ALL, open_questions=[])
            merged, reports = union_preferring_task_file(journal, disk, "7")
            assert merged["open_questions"] == []
            assert reports == []
