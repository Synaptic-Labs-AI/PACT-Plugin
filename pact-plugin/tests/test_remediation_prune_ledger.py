"""The prune ledger's writer: a reaped session keeps its legacy evidence.

Location: pact-plugin/tests/test_remediation_prune_ledger.py.

A legacy memory bucket whose file rows are all relative is admitted only on a
recorded session from this repository, and the session reaper
(`hooks/session_end.py::_reap_slug_dir`) deletes session folders after 30 days
(180 if paused). Before it deletes one, it appends that session's evidence line
`{session_id, project_dir, started_at}` to the slug's prune ledger
(`shared.pact_context.PRUNE_LEDGER_NAME`), copied from the folder's own
`pact-session-context.json`, so the evidence outlives the folder. Its
`started_at` is the earliest start the session's journal confirms; with no
readable first journal event the line omits it. The memory
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
import threading
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
    """An aged session folder whose context names `project_dir`, with the
    journal whose first event confirms its start, as a real session has."""
    entry = slug_dir / session_id
    entry.mkdir(parents=True)
    (entry / "session-journal.jsonl").write_text(
        json.dumps({"v": 1, "type": "session_start", "ts": STARTED}) + "\n",
        encoding="utf-8",
    )
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

    def test_a_failed_delete_leaves_folder_and_line_and_counts_the_session_once(
        self, monkeypatch
    ):
        """The line is written before the delete, so a delete that fails leaves
        the folder AND its ledger line. The census reader still counts the
        session's rows once: a present folder is the record, and a session
        found in several records is counted at one place. RED WHEN the reader
        adds a session's rows once per record it finds."""
        import scripts.database as database
        from scripts.memory_api import _legacy_session_census

        root = get_claude_config_dir()
        project = root.parent / "work" / "proj"
        project.mkdir(parents=True)
        target, context = pact_context.build_context_cache("session-x", SID, str(project))
        target.parent.mkdir(parents=True, exist_ok=True)
        assert pact_context.persist_context(target, context)
        entry = target.parent
        _age(entry)
        monkeypatch.setattr(session_end.shutil, "rmtree", lambda *args, **kwargs: None)
        session_end.cleanup_old_sessions(pact_context.project_slug(str(project)), OTHER)
        assert entry.exists() and _ids(entry.parent) == [SID]

        monkeypatch.setattr(database, "bucket_session_counts", lambda conn, candidate: [(SID, 3)])
        counts = _legacy_session_census(None, "proj", [str(project)], None)
        assert sum(counts.values()) == 3 and counts["unattributed"] == 0, counts

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
        [None, "", "yesterday", "2026-09-06T10:00:00", 1757152800, STARTED],
        ids=["absent", "empty", "unparseable", "naive", "not-a-string", "valid"],
    )
    def test_a_start_no_journal_confirms_is_not_recorded(self, world, started_at):
        """Catches a line the reader could only judge by guessing. With no
        readable first journal event the start is not confirmed (a heal or
        resume records its own time), so the line keeps the folder and omits
        started_at, whatever the context holds. RED WHEN the writer copies an
        unconfirmed started_at, or drops the line (its folder still places
        the session)."""
        project, slug_dir = world
        _with_control(slug_dir, project)
        entry = _session(slug_dir, project)
        (entry / "session-journal.jsonl").unlink()
        record = json.loads((entry / "pact-session-context.json").read_text())
        if started_at is None:
            del record["started_at"]
        else:
            record["started_at"] = started_at
        _write_context(entry, record)
        _reap(slug_dir)
        assert not entry.exists()
        lines = {line["session_id"]: line for line in _lines(slug_dir)}
        assert lines[SID] == {"session_id": SID, "project_dir": str(project)}, lines
        assert lines[GOOD]["started_at"] == STARTED, lines

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
        assert not entry.exists()
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
        O_NONBLOCK and the regular-file check: the reap then blocks, and the
        arm fails by assertion after a bounded wait instead of hanging. Also
        RED WHEN the writer records nothing once the FIFO is gone."""
        project, slug_dir = world
        fifo = slug_dir / LEDGER
        os.mkfifo(fifo)
        entry = _session(slug_dir, project)
        errors = []

        def reap():
            try:
                _reap(slug_dir)
            except BaseException as exc:  # surfaced by the assertion below
                errors.append(exc)

        reaper = threading.Thread(target=reap, daemon=True)
        reaper.start()
        reaper.join(timeout=10)
        hung = reaper.is_alive()
        if hung:
            # Open the read end so the blocked writer's open returns and the
            # thread ends instead of outliving the test.
            release = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            try:
                reaper.join(timeout=10)
            finally:
                os.close(release)
        assert not hung, "the reaper blocked on a FIFO ledger for 10 s"
        assert errors == []
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
        for source in (hooks / "session_end.py", scripts / "memory_api.py"):
            text = source.read_text(encoding="utf-8")
            assert "PRUNE_LEDGER_NAME" in text, f"{source.name} does not use the constant"
            for quote in ('"', "'"):
                literal = quote + LEDGER + quote
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
        from datetime import datetime

        from shared.session_journal import append_event, make_event

        target, context = pact_context.build_context_cache("session-x", SID, str(project))
        target.parent.mkdir(parents=True, exist_ok=True)
        assert pact_context.persist_context(target, context)
        entry = target.parent
        assert append_event(
            make_event("session_start", session_id=SID, project_dir=str(project)),
            session_dir=str(entry),
        )
        first = json.loads((entry / "session-journal.jsonl").read_text().splitlines()[0])["ts"]
        _age(entry)
        session_end.cleanup_old_sessions(pact_context.project_slug(str(project)), OTHER)
        assert not entry.exists()
        earliest = min(
            (context["started_at"], first), key=lambda ts: datetime.fromisoformat(ts)
        )
        assert _lines(entry.parent) == [{
            "session_id": SID,
            "project_dir": str(project),
            "started_at": earliest,
        }]



class TestAHeldLedgerLockIsWaitedOnOnce:
    def test_a_held_lock_costs_one_wait_and_every_folder_is_still_reaped(
        self, tmp_path, monkeypatch
    ):
        """RED WHEN each reaped folder waits out the held lock again, so a
        contended ledger stalls SessionEnd about a second per folder. After the
        one wait, each later line gets one attempt without waiting."""
        import fcntl

        slug_dir = tmp_path / "slug"
        project = tmp_path / "proj"
        project.mkdir()
        entries = [
            _session(slug_dir, project, session_id=f"{i:08d}-1111-2222-3333-444444444444")
            for i in range(5)
        ]
        ledger = slug_dir / LEDGER
        ledger.write_bytes(b"")
        waits = []
        real_lock = session_end._lock_ledger_bounded

        def counted(fd, wait=True):
            waits.append(wait)
            return real_lock(fd, wait)

        monkeypatch.setattr(session_end, "_lock_ledger_bounded", counted)
        holder = os.open(ledger, os.O_RDWR)
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            started = time.monotonic()
            _reap(slug_dir)
            elapsed = time.monotonic() - started
        finally:
            os.close(holder)
        assert waits == [True, False, False, False, False], waits
        assert elapsed < 3, elapsed
        assert not any(entry.exists() for entry in entries)
        assert _lines(slug_dir) == []

    def test_control_a_free_lock_records_every_folder(self, tmp_path):
        slug_dir = tmp_path / "slug"
        project = tmp_path / "proj"
        project.mkdir()
        ids = [f"{i:08d}-1111-2222-3333-444444444444" for i in range(5)]
        for session_id in ids:
            _session(slug_dir, project, session_id=session_id)
        _reap(slug_dir)
        assert sorted(_ids(slug_dir)) == ids

    def test_a_lock_freed_mid_sweep_records_the_later_folders(
        self, tmp_path, monkeypatch
    ):
        """RED WHEN the sweep gives up on the ledger after its one wait, so a
        lock released mid-sweep loses every later line."""
        import fcntl

        slug_dir = tmp_path / "slug"
        project = tmp_path / "proj"
        project.mkdir()
        ids = [f"{i:08d}-1111-2222-3333-444444444444" for i in range(5)]
        for session_id in ids:
            _session(slug_dir, project, session_id=session_id)
        ledger = slug_dir / LEDGER
        ledger.write_bytes(b"")
        holder = [os.open(ledger, os.O_RDWR)]
        fcntl.flock(holder[0], fcntl.LOCK_EX)
        waits = []
        real_lock = session_end._lock_ledger_bounded

        def release_after_first_wait(fd, wait=True):
            waits.append(wait)
            taken = real_lock(fd, wait)
            if holder:
                os.close(holder.pop())
            return taken

        monkeypatch.setattr(session_end, "_lock_ledger_bounded", release_after_first_wait)
        try:
            started = time.monotonic()
            _reap(slug_dir)
            elapsed = time.monotonic() - started
        finally:
            for fd in holder:
                os.close(fd)
        assert waits == [True, False, False, False, False], waits
        assert elapsed < 3, elapsed
        assert len(_ids(slug_dir)) == 4 and set(_ids(slug_dir)) < set(ids), _ids(slug_dir)
