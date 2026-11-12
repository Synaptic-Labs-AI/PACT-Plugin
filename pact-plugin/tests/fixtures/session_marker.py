"""Per-test session ids for tests whose memory saves reach the embedding marker.

memory_init.maybe_embed_pending() touches /tmp/pact_embedding_attempted_<sid>
once per session id and skips the catch-up whenever that file exists. A test
module that keeps one fixed session id therefore leaves a marker that makes
every later run, and every other worker, skip the sweep. fresh_session_id gives
each test its own id, refuses an id already handed out in this process, and
removes that id's marker afterwards.
"""

import contextlib
from pathlib import Path

# The directory memory_init composes the marker in. The ids built by callers use
# only [0-9a-z-], which its slug sanitiser keeps, and stay under its 64-char cap.
_MARKER_DIR = Path("/tmp")


def embedding_marker(session_id):
    """The marker path memory_init uses for a session id of safe characters."""
    return _MARKER_DIR / f"pact_embedding_attempted_{session_id}"


@contextlib.contextmanager
def fresh_session_id(monkeypatch, module, name, make_id, seen):
    """Bind ``module.<name>`` to ``make_id()`` for the block, then remove its marker.

    ``seen`` is a set owned by the calling module; an id already in it fails the
    test, so a fixed id cannot come back unnoticed.
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
        embedding_marker(session_id).unlink(missing_ok=True)
