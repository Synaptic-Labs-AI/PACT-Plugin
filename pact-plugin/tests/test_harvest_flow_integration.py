"""End-to-end harvest flow over a REAL on-disk journal and REAL task stores.

The journal is a file of `make_event` lines read back through the skill's own
command, `session_journal.py read --session-dir` with no type filter, run as
a child process. The task stores are directories under the test's config
root, read through the shipped `read_task_json(..., base_fallback=False)`.
The Step 3 block from `SKILL.md` runs with the shipped `handoff_merge`
helpers; only `process` and `report` are collectors.

The journal holds every population the remediation separates at once: a
realigned team's journal unit with a richer task file, a team-less legacy
event that owns this store's file, `task_created` events, a store-epoch
boundary that splits two instances of one number with no creation event on
the second, and a journal-only unit whose file the recreated store no longer
has. A second pass over the recorded tokens, parsed back from a written
ledger section, processes nothing.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shared import handoff_merge
from shared.handoff_merge import disk_identity, handoff_content_key
from shared.session_journal import make_event
from shared.task_utils import read_task_json, task_dir_for

_PLUGIN = Path(__file__).resolve().parents[1]

SKILL_TEXT = (
    _PLUGIN / "skills" / "pact-handoff-harvest" / "SKILL.md"
).read_text(encoding="utf-8")
CODE = compile(
    SKILL_TEXT.split("Pseudocode for the read.", 1)[1]
    .split("```python\n", 1)[1].split("\n```", 1)[0],
    "SKILL.md#step3", "exec",
)
HELPERS = (
    "PARTITION_THIS", "PARTITION_FROM", "PARTITION_FOREIGN",
    "PARTITION_OLD_FORMAT", "OLD_FORMAT_DEST_MIXED", "OLD_FORMAT_DEST_FROM",
    "OLD_FORMAT_DEST_THIS", "partition_destination", "old_format_destination",
    "mixed_old_format_event", "union_preferring_task_file", "disk_identity",
    "report_safe", "latest_by_ts_then_journal_order", "positioned",
    "store_epochs", "group_events", "current_arc", "owning_group",
    "undivided_contents", "task_metadata", "disk_handoff", "safe_from_teams",
    "unit_token",
)

THIS, FROM = "session-flow-new", "session-flow-old"


def _h(produced, **extra):
    h = {"produced": produced, "decisions": "d", "uncertainty": "u",
         "integration": "i", "open_questions": "q"}
    h.update(extra)
    return h


FROM_2 = _h("from-team work")
LEGACY_4 = _h("legacy work")
OLD_3, NEW_3 = _h("old store's 3"), _h("new store's 3")
OLD_7 = _h("old store's 7")


def _ts(second):
    return f"2026-11-01T00:00:{second:02d}Z"


def _handoff_event(task_id, handoff, second, team=None, agent="coder", subject=None):
    fields = {"agent": agent, "task_id": task_id,
              "task_subject": subject or f"subject {task_id}",
              "handoff": handoff, "ts": _ts(second)}
    if team is not None:
        fields["team"] = team
    return make_event("agent_handoff", **fields)


def _created(task_id, team, second):
    return make_event("task_created", task_id=task_id, team=team, ts=_ts(second))


JOURNAL = [
    make_event("team_realigned", **{"from": FROM, "to": THIS, "ts": _ts(0)}),
    _created("2", FROM, 1),
    _handoff_event("2", FROM_2, 2, team=FROM),
    _handoff_event("4", LEGACY_4, 3),                       # pre-upgrade, no team
    _created("7", THIS, 4),
    _handoff_event("3", OLD_3, 5, team=THIS),               # old store's 3, no creation
    _handoff_event("7", OLD_7, 6, team=THIS),
    _created("1", THIS, 7),                                 # 1 <= 7: the store was recreated
    _handoff_event("3", NEW_3, 8, team=THIS),               # tmux-created 3: no creation event
]


def _task(task_id, handoff, **siblings):
    meta = {"handoff": handoff}
    meta.update(siblings)
    return {"id": task_id, "owner": "coder", "subject": f"subject {task_id}",
            "status": "completed", "metadata": meta}


@pytest.fixture
def world(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    stores = {
        FROM: {"2": _task("2", dict(FROM_2, reasoning_chain="from disk only"), sib="from")},
        THIS: {"4": _task("4", dict(LEGACY_4, reasoning_chain="this disk only"), sib="this-4"),
               "3": _task("3", NEW_3, sib="this-3")},
    }
    for team, tasks in stores.items():
        store = task_dir_for(team)
        store.mkdir(parents=True)
        for task_id, task in tasks.items():
            (store / f"{task_id}.json").write_text(json.dumps(task), encoding="utf-8")
    # A same-numbered file at the bare tasks directory: never a named store's.
    (config / "tasks" / "7.json").write_text(json.dumps(_task("7", _h("bare"))))
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "session-journal.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in JOURNAL), encoding="utf-8")
    return config, session_dir


def _read_journal(session_dir):
    """Step 1 source 1, run as the skill writes it: one read, no type filter."""
    proc = subprocess.run(
        [sys.executable, str(_PLUGIN / "hooks" / "shared" / "session_journal.py"),
         "read", "--session-dir", str(session_dir)],
        capture_output=True, text=True, timeout=60,
        env={"HOME": str(session_dir.parent), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _harvest(session_dir, processed=frozenset()):
    journal = _read_journal(session_dir)
    processed_units, reports, reads = [], [], []

    def read(task_id, team):
        reads.append((team, task_id))
        return read_task_json(task_id, team, base_fallback=False)

    ns = {name: getattr(handoff_merge, name) for name in HELPERS}
    ns.update(
        Path=Path, datetime=datetime, timezone=timezone,
        task_list_dir=str(task_dir_for(THIS)),
        realign_events=[e for e in journal if e.get("type") == "team_realigned"],
        journal=journal,
        tasklist_ids=[],
        census={THIS: {"3", "4"}, FROM: {"2"}},
        processed_tokens=set(processed),
        forced_ids=set(),
        report=reports.append,
        read_task_json=read,
        process=lambda handoff, metadata=None, section=None:
            processed_units.append((section, handoff, metadata)),
    )
    exec(CODE, ns)
    return processed_units, reports, ns["recorded_tokens"], journal


def _by_produced(units):
    return {handoff["produced"]: (section, handoff, metadata)
            for section, handoff, metadata in units}


def test_the_journal_read_keeps_every_event_in_order(world):
    _, session_dir = world
    journal = _read_journal(session_dir)
    assert [e["type"] for e in journal] == [e["type"] for e in JOURNAL]


def test_every_population_becomes_exactly_its_own_unit(world):
    _, session_dir = world
    units, reports, recorded, _ = _harvest(session_dir)
    by = _by_produced(units)
    assert sorted(by) == sorted(
        ["from-team work", "legacy work", "old store's 3", "new store's 3",
         "old store's 7"])
    assert len(units) == 5


def test_the_from_unit_is_unioned_with_its_own_store(world):
    _, session_dir = world
    by = _by_produced(_harvest(session_dir)[0])
    section, handoff, metadata = by["from-team work"]
    assert section == f"team={FROM}"
    assert handoff["reasoning_chain"] == "from disk only"
    assert metadata["sib"] == "from"


def test_the_legacy_teamless_event_owns_this_stores_file(world):
    _, session_dir = world
    units, reports, _, _ = _harvest(session_dir)
    section, handoff, metadata = _by_produced(units)["legacy work"]
    assert section is None
    assert handoff["reasoning_chain"] == "this disk only"
    assert metadata["sib"] == "this-4"
    assert not any("identity mismatch for task 4" in r for r in reports)


def test_the_epoch_boundary_splits_two_instances_of_one_number(world):
    _, session_dir = world
    by = _by_produced(_harvest(session_dir)[0])
    # The recreated store's file belongs to the instance after the boundary.
    assert by["new store's 3"][2]["sib"] == "this-3"
    assert by["old store's 3"][2] is None
    assert by["old store's 3"][0] is None and by["new store's 3"][0] is None


def test_a_journal_only_unit_is_processed_and_its_absent_file_reported(world):
    _, session_dir = world
    units, reports, _, _ = _harvest(session_dir)
    section, handoff, metadata = _by_produced(units)["old store's 7"]
    assert (section, metadata) == (None, None)
    assert handoff == OLD_7
    assert sum("divergence check unavailable for task 7" in r for r in reports) == 1


def test_named_stores_are_read_and_the_bare_base_never_is(world):
    _, session_dir = world
    units, _, _, _ = _harvest(session_dir)
    assert all(h.get("produced") != "bare" for _, h, _ in units)


def test_recorded_tokens_name_each_unit_and_its_store(world):
    _, session_dir = world
    _, _, recorded, _ = _harvest(session_dir)
    expected = {
        f"{FROM}/2#{handoff_content_key(FROM_2)}",
        f"4#{handoff_content_key(LEGACY_4)}",
        f"3#{handoff_content_key(OLD_3)}",
        f"3#{handoff_content_key(NEW_3)}",
        f"7#{handoff_content_key(OLD_7)}",
    }
    assert set(recorded) == expected and len(recorded) == 5


def test_a_second_pass_over_the_written_ledger_processes_nothing(world):
    _, session_dir = world
    _, _, recorded, _ = _harvest(session_dir)
    section_text = (
        f"## team={THIS} (proj, session x)\n\n"
        f"Processed task IDs (this team): {', '.join(recorded)}\n"
        f"Last processed (this team): 2026-11-01T00:01:00Z\n"
    )
    units, _, again, _ = _harvest(
        session_dir, processed=handoff_merge.parse_ledger_tokens(section_text))
    assert units == []
    assert again == []


def test_the_group_identity_is_the_record_identity(world):
    """The identity the block joins on is the one a reader computes from the
    task record; a snapshot occupant uses the same function."""
    _, session_dir = world
    journal = _read_journal(session_dir)
    event = next(e for e in journal if e.get("task_id") == "4"
                 and e["type"] == "agent_handoff")
    record = read_task_json("4", THIS, base_fallback=False)
    assert disk_identity(event["agent"], event["task_subject"]) == disk_identity(
        record["owner"], record["subject"])
