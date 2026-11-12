"""
Location: pact-plugin/tests/test_shred_detect.py

Summary: Arms for the shredded-list-field detector. Each arm below names
the implementation it goes RED against, because a green arm with no named
mutant retires a question instead of answering it.

THE THREE ARMS THAT CARRY THE MOST WEIGHT:

1. `TestBlindImplementations` holds the two probes a reasonable author
   would write, and proves each one MISSES damage that the tool finds. The
   shape probe over string lists misses all three dict fields. The
   longest-leaf probe misses `active_tasks`, because that dataclass always
   emits `status="pending"`, whose text length is 7. Delete the per-field
   extractor and these arms go red.

2. `TestTheTwoReadingsOfTheSignature` holds a record whose qualifying items
   are INTERLEAVED with healthy prose, so the TOTAL count fires and the
   LONGEST CONSECUTIVE run does not. Change the tool to fire on the
   consecutive run and this arm goes red. The live store cannot supply this
   record, so the fixture is the only place the ruling is checkable.

3. `TestReadOnlyByMechanism` proves the refusal, not the intent. The tool
   stops when a `-wal` or `-shm` sidecar is present, and the store bytes
   are identical after a scan.

CONTROLS. Every negative assertion below runs after a positive control on
the same fixture, so an empty answer cannot come from an empty input.

WHY NO ARM PINS THE TEN KNOWN DAMAGED RECORD IDS. Those ids are a check on
a LIVE run, not a specification. An arm that pins them reports an
environment change as a code defect the first time a record is repaired or
an eleventh record is damaged. The live comparison belongs in the run
record, not in this file.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_glob as _shared_tracked_glob
from memory_repair import shred_detect  # noqa: E402


MODULE_PATH = Path(shred_detect.__file__)
PACKAGE_DIR = MODULE_PATH.parent
PLUGIN_ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    context TEXT,
    goal TEXT,
    active_tasks TEXT,
    lessons_learned TEXT,
    decisions TEXT,
    entities TEXT,
    reasoning_chains TEXT,
    agreements_reached TEXT,
    disagreements_resolved TEXT,
    project_id TEXT,
    session_id TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

PROSE = (
    "The merge accepted a bare string and iterated it with list, which "
    "yielded the characters of that string."
)


# ---------------------------------------------------------------------------
# Fixture builders. These reproduce what the merge STORES, from a reading of
# the merge code. They do not import the merge, and they do not copy rows
# out of the live store.
# ---------------------------------------------------------------------------

def shredded_string_items(prose):
    """
    Reproduce the stored form of a shredded STRING list field.

    The merge strips each character and drops a repeat of the stripped
    value, so a space becomes the empty string and only the first space
    survives.
    """
    out = []
    seen = set()
    for char in prose:
        text = char.strip()
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def shredded_dict_items(field, prose):
    """
    Reproduce the stored form of a shredded DICT list field.

    The merge sends each character through the dataclass of the field, and
    a bare string becomes the PRIMARY attribute. The dict path applies NO
    strip, so a space stays a space here, while the string path turns it
    into the empty string. Text length 1 and text length 0 both qualify, so
    the detector is not sensitive to that difference.
    """
    key = shred_detect.DICT_LIST_FIELD_KEYS[field]
    out = []
    seen = set()
    for char in prose:
        if char in seen:
            continue
        seen.add(char)
        item = {key: char}
        if field == "active_tasks":
            # The task dataclass always emits this default.
            item["status"] = "pending"
        out.append(item)
    return out


def healthy_items(field, count=6):
    """Return well-formed items for one field."""
    texts = [
        "The resolver composes the store path one time.",
        "A guard at the create ingress refuses a bare string.",
        "The window constant reaches the encoder as max_length.",
        "The drop and the insert share one transaction.",
        "A rowcount of zero means the partition moved.",
        "The census and the two subsets must sum.",
    ][:count]
    if field in shred_detect.DICT_LIST_FIELD_KEYS:
        key = shred_detect.DICT_LIST_FIELD_KEYS[field]
        out = []
        for text in texts:
            item = {key: text}
            if field == "active_tasks":
                item["status"] = "pending"
            out.append(item)
        return out
    return texts


def make_store(tmp_path, rows, name="fixture.db"):
    """Write a store file that carries the `memories` schema and `rows`."""
    path = tmp_path / name
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(SCHEMA)
        for index, row in enumerate(rows):
            payload = {
                "id": row.get("id", "row-{0}".format(index)),
                "project_id": row.get("project_id", "fixture-project"),
                "created_at": row.get("created_at", "2026-01-01 00:00:00"),
                "updated_at": row.get("updated_at", "2026-01-02 00:00:00"),
            }
            for name_ in shred_detect.LIST_FIELDS:
                value = row.get(name_)
                payload[name_] = None if value is None else json.dumps(value)
            columns = ", ".join(payload)
            marks = ", ".join(["?"] * len(payload))
            conn.execute(
                "INSERT INTO memories ({0}) VALUES ({1})".format(columns, marks),
                tuple(payload.values()),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def sha256_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fields_of(report, memory_id):
    for finding in report["findings"]:
        if finding["memory_id"] == memory_id:
            return finding["fields"]
    return {}


# ---------------------------------------------------------------------------
# The predicate reaches all seven fields.
# ---------------------------------------------------------------------------

class TestThePredicateReachesAllSevenFields:

    def test_the_field_map_agrees_with_the_store(self):
        """THE DETECTOR MUST READ THE FIELDS THE STORE WRITES.

        THIS ARM REPLACES A COUNT THAT FACED THE WRONG WAY. The line here used
        to read `len(shred_detect.LIST_FIELDS) == 7`. Add an eighth field to
        `database.LIST_FIELDS` and the detector omits that column from its
        SELECT, so the damage in it goes unseen. The count then BLOCKED the
        repair rather than DEMANDED it, and a pin that obstructs its own fix
        is worse than no pin.

        THE DUPLICATION IS CORRECT AND STAYS. `shred_detect` imports no part
        of `database` on purpose, so the repair tool rests on neither the CLI
        nor the memory API. A TEST MAY IMPORT THE TWO, which is the reason the
        agreement belongs here rather than in the package.

        COMPARED AS SETS, IN THE TWO DIRECTIONS. The shapes differ: this side
        is a sorted tuple and the other is a union of two frozensets. A length
        check passes on two sets of seven that disagree on a name, and a
        one-directional check passes when one side gains a field.

        MUTANT, EITHER DIRECTION: add a name to one side, or remove one. This
        arm reddens for a missing field and for an extra one.
        """
        from scripts.database import LIST_FIELDS as STORE_LIST_FIELDS

        detector = set(shred_detect.LIST_FIELDS)
        store = set(STORE_LIST_FIELDS)
        # NON-VACUITY GATE. Two empty sets agree, and an import that resolved
        # a renamed or emptied constant would pass the comparison below while
        # measuring nothing.
        assert detector, "the detector field set is empty"
        assert store, "the store field set is empty"
        assert detector == store, (
            "the detector and the store disagree on the list fields. "
            "MISSING FROM THE DETECTOR: {0}. EXTRA IN THE DETECTOR: {1}. A "
            "field the store writes and the detector omits carries damage "
            "that no pass reports".format(
                sorted(store - detector), sorted(detector - store))
        )

    def test_the_field_map_is_internally_consistent(self):
        """A DIFFERENT PROPERTY FROM THE AGREEMENT ABOVE, and it stays.

        This one guards the construction INSIDE `shred_detect`: the flat tuple
        must equal the union of the string fields and the dict-field keys. The
        arm above guards the boundary against `database`. Neither covers the
        other: a name added to the two sides of the boundary in step keeps the
        agreement arm green while it can break this construction.
        """
        assert set(shred_detect.LIST_FIELDS) == (
            set(shred_detect.STRING_LIST_FIELDS)
            | set(shred_detect.DICT_LIST_FIELD_KEYS)
        )

    @pytest.mark.parametrize("field", shred_detect.STRING_LIST_FIELDS)
    def test_a_shredded_string_field_fires(self, tmp_path, field):
        path = make_store(tmp_path, [
            {"id": "damaged", field: shredded_string_items(PROSE)},
        ])
        report = shred_detect.scan_database(str(path))
        assert report["summary"]["rows_scanned"] == 1, "control: the input was empty"
        assert field in fields_of(report, "damaged")

    @pytest.mark.parametrize("field", sorted(shred_detect.DICT_LIST_FIELD_KEYS))
    def test_a_shredded_dict_field_fires(self, tmp_path, field):
        path = make_store(tmp_path, [
            {"id": "damaged", field: shredded_dict_items(field, PROSE)},
        ])
        report = shred_detect.scan_database(str(path))
        assert report["summary"]["rows_scanned"] == 1, "control: the input was empty"
        assert field in fields_of(report, "damaged")

    @pytest.mark.parametrize("field", sorted(shred_detect.LIST_FIELDS))
    def test_a_healthy_field_does_not_fire(self, tmp_path, field):
        items = healthy_items(field)
        path = make_store(tmp_path, [{"id": "healthy", field: items}])
        report = shred_detect.scan_database(str(path))
        # CONTROL: prove the field carried items before the negative claim.
        assert len(items) >= shred_detect.MIN_SHRED_ITEMS
        assert report["summary"]["rows_scanned"] == 1
        assert report["summary"]["rows_annotated"] == 0


# ---------------------------------------------------------------------------
# The arms that go RED against a blind implementation.
# ---------------------------------------------------------------------------

def blind_shape_probe(items):
    """
    The first implementation a reasonable author writes: a shape test over
    string lists. It answers "is this item a string of text length 1".
    """
    hits = [i for i in items if isinstance(i, str) and len(i) <= 1]
    return len(hits) >= shred_detect.MIN_SHRED_ITEMS


def blind_longest_leaf_probe(items):
    """
    The second implementation, which repairs the dict blindness the obvious
    way and is still incorrect: it measures the LONGEST string leaf of the
    item.
    """
    def longest(item):
        if isinstance(item, dict):
            lengths = [len(v) for v in item.values() if isinstance(v, str)]
            return max(lengths) if lengths else 0
        return len(item) if isinstance(item, str) else 0

    hits = [i for i in items if longest(i) <= 1]
    return len(hits) >= shred_detect.MIN_SHRED_ITEMS


class TestBlindImplementations:

    @pytest.mark.parametrize("field", sorted(shred_detect.DICT_LIST_FIELD_KEYS))
    def test_the_shape_probe_misses_every_dict_field(self, tmp_path, field):
        items = shredded_dict_items(field, PROSE)
        # CONTROL: the fixture really is damaged.
        assert len(items) >= shred_detect.MIN_SHRED_ITEMS
        # The blind probe reports healthy.
        assert blind_shape_probe(items) is False
        # The tool reports damage. This is the whole arm.
        path = make_store(tmp_path, [{"id": "damaged", field: items}])
        report = shred_detect.scan_database(str(path))
        assert field in fields_of(report, "damaged")

    def test_the_longest_leaf_probe_misses_active_tasks(self, tmp_path):
        items = shredded_dict_items("active_tasks", PROSE)
        assert len(items) >= shred_detect.MIN_SHRED_ITEMS
        # `status` defaults to "pending", so the longest leaf is 7 characters
        # long and the probe calls each damaged item healthy.
        assert blind_longest_leaf_probe(items) is False
        path = make_store(tmp_path, [{"id": "damaged", "active_tasks": items}])
        report = shred_detect.scan_database(str(path))
        assert "active_tasks" in fields_of(report, "damaged")

    def test_the_extractor_returns_the_task_value_and_not_the_status(self):
        item = {"task": "T", "status": "pending"}
        assert shred_detect.item_text("active_tasks", item) == "T"

    @pytest.mark.parametrize("field,key", sorted(
        shred_detect.DICT_LIST_FIELD_KEYS.items()
    ))
    def test_the_extractor_reads_the_primary_key_of_each_dict_field(self, field, key):
        assert shred_detect.item_text(field, {key: "X"}) == "X"
        assert shred_detect.item_text(field, {"unrelated": "X"}) is None


# ---------------------------------------------------------------------------
# The ruling on the signature: fire on the total, report both readings.
# ---------------------------------------------------------------------------

class TestTheTwoReadingsOfTheSignature:

    def interleaved(self):
        """
        Return items whose qualifying total is 5 and whose longest
        consecutive run is 1. The live store cannot supply this shape, so
        this fixture is the only witness for the ruling.
        """
        return [
            "a", "The resolver composes the store path one time.",
            "b", "A guard at the create ingress refuses a bare string.",
            "c", "The drop and the insert share one transaction.",
            "d", "A rowcount of zero means the partition moved.",
            "e", "The census and the two subsets must sum.",
        ]

    def test_the_interleaved_record_fires_on_the_total_reading(self, tmp_path):
        items = self.interleaved()
        path = make_store(tmp_path, [{"id": "interleaved", "reasoning_chains": items}])
        report = shred_detect.scan_database(str(path))
        measured = fields_of(report, "interleaved")["reasoning_chains"]
        assert measured["qualifying_total"] == 5
        assert measured["qualifying_longest_run"] == 1
        assert measured["fires_on_total"] is True
        # THIS LINE IS THE ARM. A tool that fires on the consecutive run
        # returns no finding for this record at all, and the lookup above
        # raises a KeyError.
        assert measured["fires_on_longest_run"] is False

    def test_the_disagreement_is_counted_and_the_id_is_named(self, tmp_path):
        path = make_store(tmp_path, [
            {"id": "interleaved", "reasoning_chains": self.interleaved()},
        ])
        report = shred_detect.scan_database(str(path))
        assert report["summary"]["reading_disagreement_rows"] == 1
        assert report["summary"]["reading_disagreement_ids"] == ["interleaved"]

    def test_a_contiguous_record_produces_no_disagreement(self, tmp_path):
        path = make_store(tmp_path, [
            {"id": "damaged", "reasoning_chains": shredded_string_items(PROSE)},
        ])
        report = shred_detect.scan_database(str(path))
        # CONTROL: the row really did fire, so the zero below is a measured
        # agreement and not an empty scan.
        assert report["summary"]["rows_annotated"] == 1
        assert report["summary"]["reading_disagreement_rows"] == 0

    def test_the_disagreement_line_is_printed_even_when_it_is_zero(self, tmp_path):
        path = make_store(tmp_path, [
            {"id": "damaged", "reasoning_chains": shredded_string_items(PROSE)},
        ])
        report = shred_detect.scan_database(str(path))
        rendered = shred_detect.render_text(report)
        assert "disagree: 0" in rendered

    def test_the_threshold_is_not_off_by_one(self, tmp_path):
        below = ["a", "b", "c", "d"]
        at = ["a", "b", "c", "d", "e"]
        path = make_store(tmp_path, [
            {"id": "below", "lessons_learned": below},
            {"id": "at", "lessons_learned": at},
        ])
        report = shred_detect.scan_database(str(path))
        assert len(below) == shred_detect.MIN_SHRED_ITEMS - 1
        assert fields_of(report, "below") == {}
        assert "lessons_learned" in fields_of(report, "at")


# ---------------------------------------------------------------------------
# The empty member, and the healthy record that carries one stray item.
# ---------------------------------------------------------------------------

class TestTheEmptyMemberAndTheNegativeControl:

    def test_the_empty_member_counts_and_is_reported(self, tmp_path):
        items = shredded_string_items(PROSE)
        assert "" in items, "control: the prose held no space"
        path = make_store(tmp_path, [{"id": "damaged", "reasoning_chains": items}])
        report = shred_detect.scan_database(str(path))
        measured = fields_of(report, "damaged")["reasoning_chains"]
        assert measured["qualifying_empty"] == 1
        assert measured["qualifying_total"] == len(items)

    def test_one_stray_single_character_item_does_not_fire(self, tmp_path):
        items = healthy_items("lessons_learned") + ["x"]
        path = make_store(tmp_path, [{"id": "healthy", "lessons_learned": items}])
        report = shred_detect.scan_database(str(path))
        assert report["summary"]["rows_scanned"] == 1
        assert report["summary"]["rows_annotated"] == 0

    def test_a_value_that_cannot_become_a_list_is_counted_and_not_dropped(
        self, tmp_path
    ):
        path = tmp_path / "broken.db"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(SCHEMA)
            conn.execute(
                "INSERT INTO memories (id, reasoning_chains) VALUES (?, ?)",
                ("broken", "{not json"),
            )
            conn.commit()
        finally:
            conn.close()
        report = shred_detect.scan_database(str(path))
        unreadable = report["summary"]["unreadable_field_values"]
        assert unreadable.get("reasoning_chains:invalid_json") == 1


# ---------------------------------------------------------------------------
# The report contract that a later pass depends on.
# ---------------------------------------------------------------------------

class TestTheReportContract:

    def test_the_fingerprint_carries_the_three_values_a_later_pass_re_reads(
        self, tmp_path
    ):
        path = make_store(tmp_path, [
            {"id": "a", "updated_at": "2026-02-01 00:00:00"},
            {"id": "b", "updated_at": "2026-03-01 00:00:00"},
        ])
        report = shred_detect.scan_database(str(path))
        fingerprint = report["fingerprint"]
        assert fingerprint["db_path"] == str(path.resolve())
        assert fingerprint["row_count"] == 2
        assert fingerprint["max_updated_at"] == "2026-03-01 00:00:00"

    def test_the_report_dates_itself(self, tmp_path):
        path = make_store(tmp_path, [{"id": "a"}])
        report = shred_detect.scan_database(str(path))
        # A count with no date becomes a constant in the reader's mind.
        assert report["scanned_at"].endswith("+00:00")

    def test_the_bound_travels_with_the_count(self, tmp_path):
        path = make_store(tmp_path, [
            {"id": "damaged", "reasoning_chains": shredded_string_items(PROSE)},
        ])
        report = shred_detect.scan_database(str(path))
        bound = report["signature"]["false_negative_bound"]
        assert str(shred_detect.MIN_SHRED_ITEMS) in bound
        assert "not a census" in bound
        assert bound in shred_detect.render_text(report)

    def test_the_report_says_which_rows_were_untouched_since_creation(
        self, tmp_path
    ):
        # THE FLAG IS ONE-DIRECTIONAL AND ITS NAME MUST SAY SO. True proves
        # no update touched the row, so the damage dates from the create
        # step. False proves only that SOME update touched the row, because
        # an update to any field stamps `updated_at`. False does NOT name
        # the step that caused the damage, and a name like
        # "shredded_at_save" would assert the second reading.
        path = make_store(tmp_path, [
            {
                "id": "untouched",
                "created_at": "2026-01-01 00:00:00",
                "updated_at": "2026-01-01 00:00:00",
                "reasoning_chains": shredded_string_items(PROSE),
            },
            {
                "id": "touched-later",
                "created_at": "2026-01-01 00:00:00",
                "updated_at": "2026-05-05 00:00:00",
                "reasoning_chains": shredded_string_items(PROSE),
            },
        ])
        report = shred_detect.scan_database(str(path))
        flags = {
            finding["memory_id"]: finding["never_updated_since_create"]
            for finding in report["findings"]
        }
        assert flags == {"untouched": True, "touched-later": False}

    def test_the_tool_annotates_and_does_not_filter(self, tmp_path):
        path = make_store(tmp_path, [
            {"id": "damaged", "reasoning_chains": shredded_string_items(PROSE)},
            {"id": "healthy", "lessons_learned": healthy_items("lessons_learned")},
        ])
        report = shred_detect.scan_database(str(path))
        # The scan covers the whole table. The findings are an annotation on
        # top of it, and the row count is untouched by them.
        assert report["summary"]["rows_scanned"] == 2
        assert report["fingerprint"]["row_count"] == 2
        assert report["summary"]["rows_annotated"] == 1


# ---------------------------------------------------------------------------
# Read-only by mechanism.
# ---------------------------------------------------------------------------

class TestReadOnlyByMechanism:

    @pytest.mark.parametrize("suffix", ["-wal", "-shm"])
    def test_a_sidecar_stops_the_run(self, tmp_path, suffix):
        path = make_store(tmp_path, [{"id": "a"}])
        # CONTROL: the scan succeeds before the sidecar is present.
        assert shred_detect.scan_database(str(path))["summary"]["rows_scanned"] == 1
        sidecar = path.parent / (path.name + suffix)
        sidecar.write_bytes(b"")
        with pytest.raises(shred_detect.PreconditionError) as caught:
            shred_detect.scan_database(str(path))
        assert "no-writer precondition FAILED" in str(caught.value)

    def test_the_sidecar_check_uses_the_resolved_path(self, tmp_path):
        path = make_store(tmp_path, [{"id": "a"}])
        link = tmp_path / "alias.db"
        link.symlink_to(path)
        (path.parent / (path.name + "-wal")).write_bytes(b"")
        # The check must follow the link to the real name. A check against
        # the spelling "alias.db-wal" finds nothing and reads as safe.
        with pytest.raises(shred_detect.PreconditionError):
            shred_detect.scan_database(str(link))

    def test_the_open_passes_mode_ro_and_immutable(self, tmp_path, monkeypatch):
        # THIS ARM WATCHES THE PRODUCTION CALL, NOT THE SOURCE TEXT. An
        # assertion on the module text passes while the caller ignores the
        # helper. The spy below records what `sqlite3.connect` actually
        # received during a real scan.
        #
        # WITHOUT THIS ARM THE READ-ONLY CLAIM IS UNPINNED. A scan that
        # opens the file WRITABLE changes no bytes, because the scan issues
        # only SELECT statements. So the byte-comparison arm below stays
        # green against a writable open. It measures the absence of a write
        # this tool does not attempt, and not the mechanism that forbids
        # one.
        path = make_store(tmp_path, [{"id": "a"}])
        seen = []
        real_connect = sqlite3.connect

        def spy(target, *args, **kwargs):
            seen.append(target)
            return real_connect(target, *args, **kwargs)

        monkeypatch.setattr(shred_detect.sqlite3, "connect", spy)
        shred_detect.scan_database(str(path))
        assert len(seen) == 1, "control: the scan opened no connection"
        assert "mode=ro" in seen[0]
        assert "immutable=1" in seen[0]

    def test_the_connection_refuses_a_write(self, tmp_path):
        path = make_store(tmp_path, [{"id": "a"}])
        conn, _resolved = shred_detect.open_readonly(str(path))
        try:
            # CONTROL: the connection can read, so a refusal below is about
            # the write and not about a dead handle.
            assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError) as caught:
                conn.execute("UPDATE memories SET project_id = 'moved'")
            assert "readonly" in str(caught.value).lower()
        finally:
            conn.close()

    def test_the_scan_does_not_change_the_store_bytes(self, tmp_path):
        path = make_store(tmp_path, [
            {"id": "damaged", "reasoning_chains": shredded_string_items(PROSE)},
        ])
        before = sha256_of(path)
        report = shred_detect.scan_database(str(path))
        after = sha256_of(path)
        # CONTROL: the scan really read the file.
        assert report["summary"]["rows_annotated"] == 1
        assert before == after
        # No sidecar was created either, which a write would leave behind.
        assert not (path.parent / (path.name + "-wal")).exists()

    def test_an_absent_file_stops_the_run(self, tmp_path):
        with pytest.raises(shred_detect.PreconditionError):
            shred_detect.scan_database(str(tmp_path / "absent.db"))


# ---------------------------------------------------------------------------
# The command line.
# ---------------------------------------------------------------------------

class TestTheCommandLine:

    def test_a_finished_scan_exits_zero_even_with_findings(self, tmp_path, capsys):
        path = make_store(tmp_path, [
            {"id": "damaged", "reasoning_chains": shredded_string_items(PROSE)},
        ])
        code = shred_detect.main([str(path)])
        captured = capsys.readouterr()
        assert code == 0
        assert "damaged" in captured.out

    def test_a_failed_precondition_exits_two(self, tmp_path, capsys):
        path = make_store(tmp_path, [{"id": "a"}])
        (path.parent / (path.name + "-wal")).write_bytes(b"")
        code = shred_detect.main([str(path)])
        assert code == 2
        assert "REFUSED" in capsys.readouterr().err

    def test_the_report_is_written_as_json(self, tmp_path):
        path = make_store(tmp_path, [
            {"id": "damaged", "reasoning_chains": shredded_string_items(PROSE)},
        ])
        out = tmp_path / "reports" / "shred.json"
        out.parent.mkdir()
        code = shred_detect.main([str(path), "--report", str(out), "--quiet"])
        assert code == 0
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["summary"]["rows_annotated"] == 1
        assert written["fingerprint"]["db_path"] == str(path.resolve())

    def test_the_tool_refuses_to_write_its_report_beside_the_store(self, tmp_path):
        path = make_store(tmp_path, [{"id": "a"}])
        code = shred_detect.main(
            [str(path), "--report", str(tmp_path / "shred.json"), "--quiet"]
        )
        assert code == 2
        assert not (tmp_path / "shred.json").exists()


# ---------------------------------------------------------------------------
# Static discipline on the module itself.
# ---------------------------------------------------------------------------

def _package_sources():
    """The package's own sources, from the git index when it ships with us.

    An untracked scratch module dropped into the package must not be judged by
    the discipline arms; ``config.py`` is the known member that keeps a
    misnamed prefix from passing on an empty scan.
    """
    try:
        relative = PACKAGE_DIR.relative_to(PLUGIN_ROOT).as_posix()
    except ValueError:
        paths = sorted(PACKAGE_DIR.rglob("*.py"))
    else:
        prefix = f"{PLUGIN_ROOT.name}/{relative}"
        paths = _shared_tracked_glob(
            PLUGIN_ROOT.parent, f"{prefix}/**/*.py", f"{prefix}/shred_detect.py")
    assert paths, "control: the package scan found no source files"
    return paths


def _executable_strings(source_text):
    """
    Yield (lineno, value) for every string constant that is NOT a docstring.

    A plain substring search over the file text cannot serve here. The
    module docstring NAMES the vector table to explain that no query uses
    it, so the search would measure its own explanation. Comments never
    reach the tree, so docstrings are the only prose to remove.
    """
    tree = ast.parse(source_text)
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    prose = set()
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            prose.add(id(first.value))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if isinstance(node.value, str) and id(node) not in prose:
            yield node.lineno, node.value


class TestStaticDiscipline:

    def test_the_package_imports_no_pact_memory_module(self):
        forbidden = {"cli", "memory_api", "database", "models", "config",
                     "memory_init", "embeddings", "search", "graph"}
        for source in _package_sources():
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        assert root not in forbidden, (
                            "{0} imports {1}".format(source.name, alias.name)
                        )
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    assert root not in forbidden, (
                        "{0} imports from {1}".format(source.name, node.module)
                    )

    def test_untracked_module_cannot_join_the_package_scan(
            self, tmp_path, monkeypatch):
        """The discipline scan reads the index; an untracked module is invisible.

        Reverting ``_package_sources`` to ``PACKAGE_DIR.rglob("*.py")`` keeps
        the shipped package green; this arm drives the import arm and reddens
        naming the untracked module. ``config.py`` is the positive control.
        """
        repo = tmp_path / "repo"
        plugin = repo / "pact-plugin"
        package = plugin / "scripts" / "memory_repair"
        package.mkdir(parents=True)
        known = package / "shred_detect.py"
        known.write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        monkeypatch.setitem(globals(), "PLUGIN_ROOT", plugin)
        monkeypatch.setitem(globals(), "PACKAGE_DIR", package)

        assert known in _package_sources(), (
            "positive control: the tracked package source is missing from the scan")
        (package / "zzprobe.py").write_text("import config\n", encoding="utf-8")

        self.test_the_package_imports_no_pact_memory_module()

    def test_the_package_hard_codes_no_job_size(self):
        # A population count belongs to a COUNT at run time. Several
        # whole-store counts have gone stale in this store already, so a
        # large integer literal here is the shape of that defect.
        for source in _package_sources():
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, int):
                    if isinstance(node.value, bool):
                        continue
                    assert node.value < 1000, (
                        "{0}:{1} holds the integer literal {2}. Take a count "
                        "at run time.".format(source.name, node.lineno, node.value)
                    )

    def test_the_module_states_the_bound_in_its_own_text(self):
        assert "not a census" in shred_detect.FALSE_NEGATIVE_BOUND

    def test_the_vector_table_discriminator_fires_on_a_planted_query(self):
        # NEGATIVE CONTROL FOR THE ARM BELOW. That arm skips docstrings, so
        # it is looser than a substring search over the file. Prove here
        # that the loosening did not disarm it: a query in an executable
        # string IS found, and the same text inside a docstring is NOT.
        planted = (
            '"""A docstring that names vec_memories as prose."""\n'
            'QUERY = "SELECT memory_id FROM vec_memories"\n'
        )
        hits = [
            line for line, value in _executable_strings(planted)
            if "vec_memories" in value
        ]
        assert hits == [2]

    def test_no_query_names_the_vector_table(self):
        for source in _package_sources():
            for lineno, value in _executable_strings(
                source.read_text(encoding="utf-8")
            ):
                assert "vec_memories" not in value, (
                    "{0}:{1} names the vector table in an executable "
                    "string.".format(source.name, lineno)
                )


def test_a_usage_error_and_a_refusal_exit_DIFFERENTLY(tmp_path):
    """ONE RUN, VARIED INPUTS, BOTH CODES — and that is the whole design.

    argparse exits 2 by default and this script returns 2 for REFUSED, so a
    mistyped invocation was indistinguishable from a declined one. Two separate
    arms — one asserting usage returns 64, one asserting a refusal returns 2 —
    would BOTH pass against a collapsed axis, because each finds an input
    satisfying it in isolation. So the table is asserted whole.

    RED WHEN usage and refusal share a code again, in either direction.
    """
    def code(argv):
        try:
            return shred_detect.main(argv)
        except SystemExit as exc:
            return exc.code

    observed = {
        "malformed flag":  code(["--nope", str(tmp_path / "s.db")]),
        "missing operand": code([]),
        "refusal: absent": code([str(tmp_path / "not_here.db")]),
    }
    assert observed == {
        "malformed flag":  shred_detect._EXIT_USAGE,
        "missing operand": shred_detect._EXIT_USAGE,
        "refusal: absent": 2,
    }, "exit codes moved: {0}".format(observed)

    # The separation itself, stated rather than implied: routing everything
    # through one code would still satisfy a row-by-row reading.
    assert observed["malformed flag"] != observed["refusal: absent"], (
        "a mistyped command and a declined one are indistinguishable to a caller"
    )
