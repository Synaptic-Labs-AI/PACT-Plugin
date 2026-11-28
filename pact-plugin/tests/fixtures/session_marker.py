"""Per-test session ids for tests whose memory saves reach the embedding marker.

memory_init.maybe_embed_pending() touches pact_embedding_attempted_<sid> in its
marker directory (a per-test directory under the suite's conftest)
once per session id and skips the catch-up whenever that file exists. A test
module that keeps one fixed session id therefore leaves a marker that makes
every later run, and every other worker, skip the sweep. fresh_session_id gives
each test its own id, refuses an id already handed out in this process, and
removes that id's marker afterwards.

importer_session_id gives a module-level id that other modules import and bind
once. No save may reach the marker under such an id; the suite's conftest
fails any test after which one of these markers exists.
"""

import contextlib
import uuid

# Every id importer_session_id has handed out in this process.
_IMPORTER_SESSION_IDS: set[str] = set()


def embedding_marker(session_id):
    """The marker path memory_init uses for a session id of safe characters,
    in its current marker directory; tests/test_session_marker.py pins that it
    equals memory_init's path. The ids built by callers use only [0-9a-z-],
    which its slug sanitiser keeps, and stay under its 64-char cap."""
    from scripts import memory_init

    return memory_init._MARKER_DIR / f"pact_embedding_attempted_{session_id}"


def importer_session_id():
    """A session id for a module constant that importers bind at import.

    It is unique to this process, so a marker it names was made by this
    process: no other run or session can create or remove it.
    """
    session_id = str(uuid.uuid4())
    _IMPORTER_SESSION_IDS.add(session_id)
    return session_id


def remove_importer_markers():
    """Remove each marker named by an importer id, and return the removed paths."""
    removed = []
    for session_id in _IMPORTER_SESSION_IDS:
        marker = embedding_marker(session_id)
        if marker.exists():
            marker.unlink(missing_ok=True)
            removed.append(marker)
    return removed


@contextlib.contextmanager
def fresh_session_id(monkeypatch, module, name, make_id, seen):
    """Bind ``module.<name>`` to ``make_id()`` for the block, then remove its marker.

    ``seen`` is a set owned by the calling module; an id already in it fails the
    test, so a fixed id cannot come back unnoticed. Today's callers draw a uuid4
    each time, so the check does not fire for them. It fires on the second test
    of any caller whose ``make_id`` returns the same id again, which is the shared
    marker this helper exists to prevent; tests/test_run_condition_isolation.py
    makes it fire with a fixed ``make_id``.
    """
    session_id = make_id()
    assert session_id not in seen, (
        f"session id {session_id!r} was already used by an earlier test in this "
        f"process: a fixed id shares one embedding marker across tests and runs"
    )
    seen.add(session_id)
    monkeypatch.setattr(module, name, session_id)
    try:
        yield session_id
    finally:
        from scripts import memory_init

        # The read-attempt markers beside it too (`memory_init._unlink_marker`).
        memory_init._unlink_marker(embedding_marker(session_id))
