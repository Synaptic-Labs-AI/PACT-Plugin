"""`last_embedding_status` must not survive a call that did not reach it.

FOUND BY A SWEEP, NOT BY A REVIEWER. A review finding put the identical defect
on ``_last_project_scope``; censusing every ``_last_*`` field across
save/update/sync then showed this one has it too, in BOTH methods, and against
a wider window than the reported instance -- it is assigned only after the store
write, so every exit above that point could leave a previous call's reason code
readable as this one's.

WHY PARTIALITY DOES NOT EXCUSE IT, which is the step a reader is most likely to
get wrong. ``last_embedding_status`` is PARTIAL: absent means "nothing to
report", not "no call ran". That invites the conclusion that a gap is expected
here and staleness therefore tolerable. It is not. PARTIAL GOVERNS WHAT ABSENCE
MEANS, NOT WHETHER A STALE VALUE IS WRONG -- the property promises a code from
"the most recent save() or update()", and a value surviving from an earlier call
is not from the most recent one whatever absence would have signified.

BOTH DIRECTIONS, because one alone proves nothing: an arm that only shows the
field cleared cannot distinguish a working clear from a field that is never set
at all, so each method has a matched positive showing a real status still
arrives on the success path.

Isolation: a throwaway database per test, ``sync_to_claude=False``, and
``_ensure_ready`` patched out. No ambient store and no CLAUDE.md is reachable.
"""
from unittest.mock import patch

import pytest

from scripts import memory_api
from scripts.memory_api import PACTMemory

SENTINEL = "degraded:keyword"


@pytest.fixture
def mem(tmp_path):
    return PACTMemory(
        project_id="staleness-probe", session_id="s", db_path=tmp_path / "stale.db"
    )


def _save(mem, status, context="probe"):
    """A real save() with the embedding producer pinned to `status`."""
    with patch.object(memory_api, "_ensure_ready", lambda: None), \
         patch.object(PACTMemory, "_store_embedding", return_value=status):
        return mem.save({"context": context}, sync_to_claude=False)


def _refuse(call):
    """Drive `call` into the env/record refusal, which fires before any write."""
    with patch.object(
        memory_api, "env_record_project_dir_disagreement",
        return_value=("/env/elsewhere", "/record/here"),
    ):
        with pytest.raises(memory_api.ProjectScopeDisagreementError):
            call()


# --- save() -----------------------------------------------------------------

def test_save_publishes_a_real_status_on_the_success_path(mem):
    """POSITIVE control. Without it, the staleness arm below cannot tell a
    working clear from a field nothing ever sets."""
    _save(mem, SENTINEL)
    assert mem.last_embedding_status == SENTINEL


def test_a_refused_save_does_not_leave_the_previous_status_readable(mem):
    """The defect: a second save that refuses must not report the first's code."""
    _save(mem, SENTINEL)
    assert mem.last_embedding_status == SENTINEL, "setup failed: nothing to go stale"

    _refuse(lambda: mem.save({"context": "refused"}, sync_to_claude=False))

    assert mem.last_embedding_status is None, (
        f"the refused save left {mem.last_embedding_status!r} behind -- a "
        "previous call's reason code read as this one's"
    )


def test_a_save_raising_before_the_store_write_clears_the_status(mem):
    """A NON-refusal exit, proving the clear is not specific to one path."""
    _save(mem, SENTINEL)
    with patch.object(memory_api, "_ensure_ready", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            mem.save({"context": "never lands"}, sync_to_claude=False)
    assert mem.last_embedding_status is None


# --- update() ---------------------------------------------------------------

def test_a_refused_update_does_not_surface_the_last_saves_status(mem):
    """update() SHARES the field with save(), so a failed update could
    otherwise surface a code the last SAVE produced -- misattributing across
    two different methods, not merely two calls."""
    _save(mem, SENTINEL)
    assert mem.last_embedding_status == SENTINEL, "setup failed: nothing to go stale"

    _refuse(lambda: mem.update("deadbeef" * 4, {"goal": "x"}))

    assert mem.last_embedding_status is None, (
        f"the refused update left the last SAVE's {mem.last_embedding_status!r} "
        "readable"
    )


def test_update_still_reports_its_own_status_on_the_success_path(mem):
    """POSITIVE control for update(), matching the save() one.

    Pins that the clear did not simply break the channel for this method: a
    real update must still publish the code its own store write produced.
    """
    memory_id = _save(mem, None, context="row to update")
    assert mem.last_embedding_status is None

    with patch.object(memory_api, "_ensure_ready", lambda: None), \
         patch.object(PACTMemory, "_store_embedding", return_value=SENTINEL):
        mem.update(memory_id, {"goal": "updated"})

    assert mem.last_embedding_status == SENTINEL, (
        "update() stopped publishing its own reason code -- the clear removed "
        "the channel rather than the staleness"
    )
