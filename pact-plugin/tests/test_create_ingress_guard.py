"""
Location: pact-plugin/tests/test_create_ingress_guard.py

Summary: Arms for the type guard at the create ingress of the memory store.
A list field that receives a bare string must RAISE. Before this guard, the
create ingress tested truthiness and passed the string into the merge, which
iterated it with `list()` and stored its characters.

READ THIS BEFORE YOU EDIT OR DELETE AN ARM BELOW. The three classes of arm
here are NOT interchangeable, and two of them cannot go red against the
change they accompany. Each one carries its own label. The labels are
load-bearing text.

  CLASS 1, THE REFUSAL ARMS. These assert `ValueError`. They go red against
  the unpatched create ingress AND against a mutant that turns the raise into
  a coercion. They are the witness for the guard.

  CLASS 2, THE UPDATE NON-REGRESSION ARM. It cannot go red against this
  change, because the update ingress carried the guard before this change was
  made. It pins that guard against removal. See its own docstring.

  CLASS 3, THE END-TO-END ARM. It asserts that the damage detector finds
  nothing after a save. It cannot go red against a coercion mutant, because a
  coercion produces no damage. See its own docstring.

WHY THE REFUSAL SPELLING AND NOT A DAMAGE SPELLING. An arm that asserts "the
stored field is not shredded" also goes red against the unpatched ingress, so
it looks adequate. It is green against a mutant that replaces the raise with
`return [value]`, because that mutant preserves the caller data whole. The
refusal spelling goes red against the unpatched ingress AND against that
mutant, at the same cost. Assert the REFUSAL, not the INTEGRITY OF THE DATA.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from helpers import create_test_schema

from scripts.database import sqlite3

from memory_repair import shred_detect  # noqa: E402
from scripts.database import (  # noqa: E402
    DICT_LIST_FIELDS,
    LIST_FIELDS,
    create_memory,
    get_memory,
    update_memory,
)


PROSE = "The merge accepted a bare string and iterated it with list."


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "ingress.db"


@pytest.fixture
def db_conn(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    create_test_schema(conn)
    with patch("scripts.database.ensure_initialized"):
        yield conn
    conn.close()


def healthy_value(field):
    """Return a well-formed value for one list field."""
    if field in DICT_LIST_FIELDS:
        key = {"active_tasks": "task", "decisions": "decision", "entities": "name"}[field]
        return [{key: "A well formed item of prose."}]
    return ["A well formed item of prose."]


# ---------------------------------------------------------------------------
# CLASS 1. The refusal arms. These are the witness for the guard.
# ---------------------------------------------------------------------------

class TestTheCreateIngressRefusesANonList:
    """RED against the unpatched create ingress, and RED against the coercion
    mutant that replaces the raise in `_normalize_list_field` with
    `return [value]`. One arm, two mutants."""

    @pytest.mark.parametrize("field", sorted(LIST_FIELDS))
    def test_a_bare_string_raises_at_save(self, db_conn, field):
        with pytest.raises(ValueError) as caught:
            create_memory(db_conn, {"context": "control", field: PROSE})
        assert field in str(caught.value)
        assert "must be a list" in str(caught.value)

    @pytest.mark.parametrize("field", sorted(LIST_FIELDS))
    def test_the_record_is_not_written_when_the_guard_raises(self, db_conn, field):
        # The guard must refuse BEFORE the write, so a refused save leaves no
        # row. A guard that raises after the INSERT reports an error and keeps
        # the damage.
        with pytest.raises(ValueError):
            create_memory(db_conn, {"context": "control", field: PROSE})
        count = db_conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert count == 0

    @pytest.mark.parametrize("value", ["", {}, 0, "x", 7])
    def test_a_falsy_non_list_raises_too(self, db_conn, value):
        # THE TRUTHINESS TEST WAS THE DEFECT, NOT ONLY THE MISSING TYPE TEST.
        # The unpatched ingress tested `if items:`, so an empty string, an
        # empty dict and 0 were skipped IN SILENCE. The update ingress applies
        # no such pre-test and raises on each of them today. This arm holds
        # the two ingresses to one contract.
        with pytest.raises(ValueError):
            create_memory(db_conn, {"context": "control", "reasoning_chains": value})


class TestBehaviourThatMustNotChange:
    """The guard must refuse a non-list and must change nothing else."""

    def test_an_absent_field_is_skipped(self, db_conn):
        mem_id = create_memory(db_conn, {"context": "no list fields here"})
        stored = get_memory(db_conn, mem_id)
        assert stored["context"] == "no list fields here"

    @pytest.mark.parametrize("field", sorted(LIST_FIELDS))
    def test_none_is_accepted_and_skipped(self, db_conn, field):
        mem_id = create_memory(db_conn, {"context": "control", field: None})
        assert get_memory(db_conn, mem_id) is not None

    @pytest.mark.parametrize("field", sorted(LIST_FIELDS))
    def test_an_empty_list_is_accepted(self, db_conn, field):
        mem_id = create_memory(db_conn, {"context": "control", field: []})
        assert get_memory(db_conn, mem_id) is not None

    @pytest.mark.parametrize("field", sorted(LIST_FIELDS))
    def test_a_valid_list_is_stored(self, db_conn, field):
        value = healthy_value(field)
        mem_id = create_memory(db_conn, {"context": "control", field: value})
        stored = get_memory(db_conn, mem_id)
        assert len(stored[field]) == 1

    def test_within_batch_dedup_survives(self, db_conn):
        # The merge call at the create ingress exists for within-batch dedup.
        # The guard must not remove that behaviour.
        mem_id = create_memory(db_conn, {
            "context": "control",
            "lessons_learned": ["one", "one", "two"],
        })
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["one", "two"]


# ---------------------------------------------------------------------------
# CLASS 2. The update non-regression arm.
# ---------------------------------------------------------------------------

class TestTheUpdateIngressContinuesToRefuse:

    @pytest.mark.parametrize("replace", [False, True])
    def test_a_bare_string_raises_at_update(self, db_conn, replace):
        """NON-REGRESSION ARM. THIS ARM CANNOT GO RED AGAINST THE CREATE-INGRESS
        CHANGE, AND THAT IS NOT A REASON TO DELETE IT.

        WHY IT CANNOT GO RED. `update_memory` routes each list field through
        `_normalize_list_field` at database.py:1094, which raises on a
        non-list, and it does that BEFORE the merge at :1102. So the update
        ingress carried this guard before the create-ingress change was made.
        The change edits the create ingress only. This arm gives the same
        result whether that change is applied or not.

        WHAT IT DOES PROVE. It pins the guard that protects the update
        ingress.

        THE MUTATIONS THAT DO MAKE IT RED, and they are the reason it ships:
          U1: delete the `_normalize_list_field` call at database.py:1094.
          U2: replace the raise at database.py:741-743 with `return [value]`.
        U2 is the dangerous one. It looks like a repair, because it preserves
        the caller data whole and produces no damage. It converts the contract
        from a REFUSAL into a SILENT ACCEPTANCE.

        IF YOU DELETE THIS ARM BECAUSE IT NEVER FAILS, the update guard loses
        its only witness and U1 or U2 lands later with nothing to stop it.
        """
        mem_id = create_memory(db_conn, {"context": "control"})
        with pytest.raises(ValueError):
            update_memory(
                db_conn, mem_id, {"reasoning_chains": PROSE}, replace=replace,
            )


# ---------------------------------------------------------------------------
# CLASS 3. The end-to-end arm.
# ---------------------------------------------------------------------------

class TestTheDetectorFindsNothingAfterAGuardedSave:

    def test_a_store_built_through_the_save_path_carries_no_damage(
        self, db_conn, db_path,
    ):
        """END-TO-END ARM. THIS ARM IS NOT THE WITNESS FOR THE GUARD, AND IT
        CANNOT GO RED AGAINST THE COERCION MUTANT U2.

        WHY NOT. The detector fires on 5 or more items whose text length is 0
        or 1. U2 replaces the raise with `return [value]`, so a bare string
        becomes a ONE-ITEM list holding the whole prose. That produces no
        damage at all, so the detector correctly finds nothing, and this arm
        is green while the refusal contract is gone.

        THE WITNESS FOR THE GUARD IS TestTheCreateIngressRefusesANonList,
        which asserts the REFUSAL rather than the absence of damage.

        WHAT THIS ARM DOES PROVE, and it is worth its cost: that a store built
        through the save path with well-formed input carries none of the
        damage the detector was written to find. It is the end-to-end
        statement, joining the guard to the instrument that measured the
        original damage.
        """
        for field in sorted(LIST_FIELDS):
            create_memory(db_conn, {"context": "control", field: healthy_value(field)})
        db_conn.commit()

        report = shred_detect.scan_database(str(db_path))

        # CONTROL: assert the input was non-empty before the negative result.
        assert report["summary"]["rows_scanned"] == len(LIST_FIELDS)
        assert report["summary"]["rows_annotated"] == 0

    def test_the_detector_does_find_damage_written_around_the_guard(
        self, db_conn, db_path,
    ):
        """POSITIVE CONTROL FOR THE ARM ABOVE. Without this, a detector that
        reports nothing for a broken reason gives that arm a green.

        This writes the damaged shape DIRECTLY with SQL, around the ingress,
        which is the only way to produce it once the guard is in place.
        """
        shredded = json.dumps(sorted({c.strip() for c in PROSE}))
        db_conn.execute(
            "INSERT INTO memories (id, context, reasoning_chains) VALUES (?, ?, ?)",
            ("damaged", "control", shredded),
        )
        db_conn.commit()

        report = shred_detect.scan_database(str(db_path))

        assert report["summary"]["rows_scanned"] == 1
        assert report["summary"]["rows_annotated"] == 1
