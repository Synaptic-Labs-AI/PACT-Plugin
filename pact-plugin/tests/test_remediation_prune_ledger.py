"""The prune ledger's writer: a reaped session keeps its legacy evidence.

Location: pact-plugin/tests/test_remediation_prune_ledger.py.

A legacy memory bucket whose file rows are all relative is admitted only on a
recorded session from this repository, and the session reaper
(`hooks/session_end.py::_reap_slug_dir`) deletes session folders after 30 days
(180 if paused). Before it deletes one, it appends that session's evidence line
`{session_id, project_dir, started_at}` to the slug's prune ledger
(`shared.pact_context.PRUNE_LEDGER_NAME`), copied from the folder's own
`pact-session-context.json`, so the evidence outlives the folder. The memory
store's reader then requires the repository at `project_dir` to predate
`started_at` (its `description` file's ctime), on a live record and a ledger
line alike.

These arms pin the writer's guarantees: a line on reap and the reap itself, no
line when the record cannot be trusted, a symlinked or non-regular ledger
refused, and a failure that never keeps a folder alive. Every refusal arm also
reaps a trustworthy sibling and requires ITS line, so no arm can pass against a
writer that writes nothing. The end-to-end arm runs the real context writer and
the real reaper over the per-test sandbox config root, with nothing mocked.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import session_end
import shared.pact_context as pact_context
from shared.paths import get_claude_config_dir

SID = "aaaaaaaa-1111-2222-3333-444444444444"
OTHER = "bbbbbbbb-1111-2222-3333-444444444444"
GOOD = "cccccccc-1111-2222-3333-444444444444"
STARTED = "2026-09-06T10:00:00.000000+00:00"
LEDGER = pact_context.PRUNE_LEDGER_NAME


def _age(path: Path, days: float = 40) -> None:
    """Make `path` and every child look `days` old to the reaper."""
    old = time.time() - days * 86400
    for child in [path, *path.rglob("*")]:
        os.utime(child, (old, old), follow_symlinks=False)


def _write_context(entry: Path, record: dict) -> None:
    (entry / "pact-session-context.json").write_text(json.dumps(record), encoding="utf-8")
    _age(entry)


def _session(slug_dir: Path, project_dir: Path, session_id: str = SID, **context) -> Path:
    """An aged session folder whose context names `project_dir`."""
    entry = slug_dir / session_id
    entry.mkdir(parents=True)
    record = {
        "session_id": session_id,
        "project_dir": str(project_dir),
        "started_at": STARTED,
    }
    record.update(context)
    _write_context(entry, record)
    return entry


def _reap(slug_dir: Path) -> None:
    session_end._reap_slug_dir(slug_dir, OTHER, max_age_days=30, paused_max_age_days=180)


def _lines(slug_dir: Path) -> list:
    ledger = slug_dir / LEDGER
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]


def _ids(slug_dir: Path) -> list:
    return [line["session_id"] for line in _lines(slug_dir)]


def _with_control(slug_dir: Path, project: Path) -> list:
    """A trustworthy sibling session reaped in the same sweep; its id."""
    control_project = project.parent / "control"
    control_project.mkdir()
    _session(slug_dir, control_project, session_id=GOOD)
    return [GOOD]


@pytest.fixture
def world(tmp_path):
    project = tmp_path / "checkout" / "proj"
    project.mkdir(parents=True)
    slug_dir = tmp_path / "pact-sessions" / "proj"
    slug_dir.mkdir(parents=True)
    return project, slug_dir


class TestTheReaperRecordsBeforeItDeletes:
    def test_a_reaped_session_leaves_one_ledger_line_and_is_still_reaped(self, world):
        """Catches evidence lost with the folder, and a ledger write that keeps
        the folder alive. RED WHEN the reaper deletes without recording, or
        records without deleting."""
        project, slug_dir = world
        entry = _session(slug_dir, project)
        _reap(slug_dir)
        assert not entry.exists(), "the aged session folder was not reaped"
        assert _lines(slug_dir) == [{
            "session_id": SID,
            "project_dir": str(project),
            "started_at": STARTED,
        }]
        assert (slug_dir / LEDGER).stat().st_mode & 0o777 == 0o600

    def test_a_second_reap_appends_and_never_rewrites(self, world):
        """Catches a writer that truncates the ledger. RED WHEN the open drops
        O_APPEND or uses a rewrite."""
        project, slug_dir = world
        _session(slug_dir, project)
        _reap(slug_dir)
        _with_control(slug_dir, project)
        _reap(slug_dir)
        assert _ids(slug_dir) == [SID, GOOD]


class TestNoLineWithoutATrustworthyRecord:
    def test_a_gone_project_dir_writes_nothing(self, world):
        """Catches a line naming a checkout that no longer exists. RED WHEN the
        writer records without checking project_dir, or records nothing at all
        (the control's line is then missing)."""
        project, slug_dir = world
        expected = _with_control(slug_dir, project)
        entry = _session(slug_dir, project)
        project.rmdir()
        _reap(slug_dir)
        assert not entry.exists()
        assert _ids(slug_dir) == expected

    @pytest.mark.parametrize(
        "started_at",
        [None, "", "yesterday", "2026-09-06T10:00:00", 1757152800],
        ids=["absent", "empty", "unparseable", "naive", "not-a-string"],
    )
    def test_a_start_time_that_cannot_be_compared_writes_nothing(
        self, world, started_at
    ):
        """Catches a line the reader could only judge by guessing. A naive
        timestamp names no zone, so it cannot be ordered against a ctime. RED
        WHEN the writer copies started_at without parsing it zone-aware."""
        project, slug_dir = world
        expected = _with_control(slug_dir, project)
        entry = _session(slug_dir, project)
        record = json.loads((entry / "pact-session-context.json").read_text())
        if started_at is None:
            del record["started_at"]
        else:
            record["started_at"] = started_at
        _write_context(entry, record)
        _reap(slug_dir)
        assert not entry.exists()
        assert _ids(slug_dir) == expected

    @pytest.mark.parametrize("body", ["{not json", "[]", '"a string"'])
    def test_a_malformed_context_is_skipped_and_still_reaped(self, world, body):
        """Catches a malformed context that raises out of the reaper, stops the
        sweep, or writes a line. RED WHEN a parse error escapes or the
        well-formed sibling loses its line."""
        project, slug_dir = world
        expected = _with_control(slug_dir, project)
        entry = _session(slug_dir, project)
        (entry / "pact-session-context.json").write_text(body, encoding="utf-8")
        _age(entry)
        _reap(slug_dir)
        assert not entry.exists()
        assert _ids(slug_dir) == expected

    def test_a_session_id_that_is_not_the_folder_name_writes_nothing(self, world):
        """Catches a context copied into another session's folder. RED WHEN the
        writer skips the name check."""
        project, slug_dir = world
        expected = _with_control(slug_dir, project)
        entry = _session(slug_dir, project, session_id=SID)
        record = json.loads((entry / "pact-session-context.json").read_text())
        record["session_id"] = OTHER
        _write_context(entry, record)
        _reap(slug_dir)
        assert not entry.exists()
        assert _ids(slug_dir) == expected

    def test_a_symlinked_context_is_not_read(self, world, tmp_path):
        """Catches evidence read through a planted context link. RED WHEN the
        writer follows a symlinked pact-session-context.json."""
        project, slug_dir = world
        expected = _with_control(slug_dir, project)
        entry = _session(slug_dir, project)
        real = tmp_path / "planted-context.json"
        (entry / "pact-session-context.json").rename(real)
        (entry / "pact-session-context.json").symlink_to(real)
        _age(entry)
        _reap(slug_dir)
        assert _ids(slug_dir) == expected


class TestTheLedgerIsNeverWrittenThroughALink:
    def test_a_symlinked_ledger_is_refused_and_the_folder_still_reaped(self, world, tmp_path):
        """Catches a write through a planted link. RED WHEN the writer follows
        the symlink (the link's target gains a line), or records nothing even
        once the link is gone."""
        project, slug_dir = world
        target = tmp_path / "elsewhere.txt"
        target.write_text("", encoding="utf-8")
        (slug_dir / LEDGER).symlink_to(target)
        entry = _session(slug_dir, project)
        _reap(slug_dir)
        assert not entry.exists()
        assert target.read_text(encoding="utf-8") == ""
        (slug_dir / LEDGER).unlink()
        _with_control(slug_dir, project)
        _reap(slug_dir)
        assert _ids(slug_dir) == [GOOD]

    def test_a_fifo_ledger_never_hangs_the_reaper(self, world):
        """Catches a blocking open on a planted FIFO. RED WHEN the open lacks
        O_NONBLOCK and the regular-file check (the test then hangs), or the
        writer records nothing once the FIFO is gone."""
        project, slug_dir = world
        os.mkfifo(slug_dir / LEDGER)
        entry = _session(slug_dir, project)
        _reap(slug_dir)
        assert not entry.exists()
        (slug_dir / LEDGER).unlink()
        _with_control(slug_dir, project)
        _reap(slug_dir)
        assert _ids(slug_dir) == [GOOD]


class TestTheContextFileGainsNoField:
    def test_the_context_records_exactly_its_existing_fields(self, tmp_path):
        """Catches an inode capture creeping back into every session's context.
        The evidence rule needs only `started_at`, which the context already
        records. RED WHEN build_context_cache adds a field."""
        project = tmp_path / "proj"
        project.mkdir()
        _target, context = pact_context.build_context_cache("session-x", SID, str(project))
        assert set(context) == {
            "team_name", "session_id", "project_dir", "plugin_root",
            "started_at", "process_key", "harness", "lead_address",
        }


class TestOneLedgerName:
    def test_the_writer_and_the_reader_name_the_ledger_through_the_constant(self):
        """Catches two spellings of one file name. RED WHEN session_end or the
        memory store's reader spells the name as a literal."""
        hooks = Path(session_end.__file__).parent
        scripts = hooks.parent / "skills" / "pact-memory" / "scripts"
        literal = '"' + LEDGER + '"'
        for source in (hooks / "session_end.py", scripts / "memory_api.py"):
            text = source.read_text(encoding="utf-8")
            assert "PRUNE_LEDGER_NAME" in text, f"{source.name} does not use the constant"
            assert literal not in text, f"{source.name} spells the ledger name literally"


class TestEndToEndOverTheRealSeam:
    def test_session_init_context_then_reap_writes_its_start_time(self):
        """The writer and the reaper meet only through a file under the config
        root, so this runs both for real over the per-test sandbox root:
        build_context_cache + persist_context write the context, the real
        cleanup_old_sessions reaps it, and the ledger line carries the
        context's own started_at. RED WHEN the reaper stops recording or the
        line drops the start time."""
        root = get_claude_config_dir()
        project = root.parent / "work" / "proj"
        project.mkdir(parents=True)
        target, context = pact_context.build_context_cache("session-x", SID, str(project))
        target.parent.mkdir(parents=True, exist_ok=True)
        assert pact_context.persist_context(target, context)
        entry = target.parent
        _age(entry)
        session_end.cleanup_old_sessions(pact_context.project_slug(str(project)), OTHER)
        assert not entry.exists()
        assert _lines(entry.parent) == [{
            "session_id": SID,
            "project_dir": str(project),
            "started_at": context["started_at"],
        }]
