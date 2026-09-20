"""The graceful-degradation contract: no model, but saves land and search works.

WHY THIS FILE EXISTS. ``EmbeddingService._ensure_initialized`` catches
``ImportError`` and ``Exception``, sets ``_available = False`` and lets the
caller proceed without a vector. That handler is the whole reason a machine
without model2vec can still use this subsystem -- and nothing asserted the
behaviour it provides. MEASURED against the full suite with the model made
unreachable: 2 failed and 2 errors out of ~16,900. Of those four, the two
errors were a fixture that could not build and the two failures were incidental
traversals; none of them asserts that a SAVE SUCCEEDS or that SEARCH STILL
RETURNS ROWS. So the degradation path could have started raising instead of
degrading and the suite would have reported the same four.

That the count was four rather than three also matters for how this is read:
two of the four were ERRORS, and a summary that drops the errors count -- which
the one in common use here does -- shows half of them.

WHAT THIS PINS. The user-facing contract, not the internals: with no model, a
save returns an id, the row is retrievable, and a keyword query finds it. The
reason code is asserted too, because ``degraded:<mode>`` is the documented
channel by which a caller learns search is running without vectors.

HOW THE MODEL IS MADE UNAVAILABLE, and why not by patching the service. Binding
``model2vec`` to ``None`` in ``sys.modules`` makes ``from model2vec import
StaticModel`` raise ``ImportError``, which drives the REAL ``_ensure_initialized``
through its REAL handler. Patching ``_ensure_initialized`` or ``is_available``
would stub the seam whose correct behaviour IS the thing under test, and would
pass with that handler deleted.
"""
import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from scripts.embeddings import get_embedding_service, reset_embedding_service
from scripts.memory_api import PACTMemory

_TOKEN = "zarquon"  # a term no other fixture in this suite writes
_CONTEXT = f"the {_TOKEN} deployment rollback procedure"


def _cause(status):
    """The cause segment of `degraded:<mode>:<cause>`, or the whole string.

    These arms pin the CAUSE only. The mode segment is already pinned in
    test_embedding_status_contract.py, and asserting it here would also make
    each arm depend on WHEN capabilities are sampled -- measured: read outside
    the model-unavailable context it reports `semantic`, inside it `keyword`,
    so a whole-string assertion fails for a reason that is not the subject.
    """
    parts = (status or "").split(":")
    return parts[2] if len(parts) > 2 else status


@contextmanager
def _model_unavailable():
    """Drive the real ImportError handler, and leave the singleton clean."""
    reset_embedding_service()
    try:
        with patch.dict(sys.modules, {"model2vec": None}):
            yield
    finally:
        # The service caches `_available = False`; a later test in this process
        # would inherit the degraded verdict without this.
        reset_embedding_service()


@pytest.fixture
def mem(tmp_path):
    return PACTMemory(
        project_id="degradation-probe",
        session_id="s",
        db_path=tmp_path / "degraded.db",
    )


def test_the_arm_actually_reaches_the_unavailable_state():
    """NON-VACUITY, and every assertion in this file rests on it.

    If the ``sys.modules`` bind stopped producing an ImportError -- a different
    import spelling, a vendored module, an eager import elsewhere -- the arms
    below would run with the model PRESENT and pass while proving nothing about
    degradation. This fails first instead.
    """
    with _model_unavailable():
        assert get_embedding_service().is_available() is False, (
            "the service reports AVAILABLE inside the unavailable context -- "
            "the ImportError bind is not reaching the real load, so every "
            "other arm in this file is measuring the ordinary path"
        )


def test_the_matched_opposite_is_available_when_the_model_is_present():
    """Without the matched positive, the guard above cannot be told apart from
    a service that reports unavailable unconditionally."""
    pytest.importorskip("model2vec")
    reset_embedding_service()
    try:
        assert get_embedding_service().is_available() is True, (
            "the service reports unavailable with model2vec installed, so the "
            "guard above would hold for a reason that is not the bind"
        )
    finally:
        reset_embedding_service()


def test_a_save_still_succeeds_without_the_model(mem):
    """THE CONTRACT. A missing model must cost the vector, never the record."""
    with _model_unavailable():
        memory_id = mem.save({"context": _CONTEXT}, sync_to_claude=False)

    assert memory_id, f"save returned {memory_id!r} with no model available"
    stored = mem.get(memory_id)
    assert stored is not None, (
        f"save reported id {memory_id} but the row is not retrievable -- the "
        "record was lost, which is the failure this handler exists to prevent"
    )


def test_keyword_search_still_finds_the_row_without_the_model(mem):
    """Search must degrade to keyword mode, not return nothing.

    A save that lands but cannot be found again is indistinguishable from a
    save that was dropped, for every caller that only ever searches.
    """
    with _model_unavailable():
        memory_id = mem.save({"context": _CONTEXT}, sync_to_claude=False)
        results = mem.search(_TOKEN, sync_to_claude=False)

    assert [r for r in results if r.id == memory_id], (
        f"searching {_TOKEN!r} with no model returned "
        f"{[r.id for r in results]}; the row saved moments earlier is not "
        "among them, so the subsystem is write-only when the model is gone"
    )


def test_that_search_discriminates_rather_than_returning_everything(mem):
    """Control for the arm above: a hit must mean a MATCH.

    Without this, a search that returned every row in the store would satisfy
    the previous assertion while proving nothing about keyword matching.
    """
    with _model_unavailable():
        mem.save({"context": _CONTEXT}, sync_to_claude=False)
        absent = mem.search("chromatography", sync_to_claude=False)

    assert not [r for r in absent if _TOKEN in (r.context or "")], (
        "a query sharing no term with the record still returned it, so the "
        "previous arm's hit does not demonstrate keyword matching"
    )


def test_the_no_model_cause_is_named_on_its_own(mem):
    """The caller must be TOLD search is running without vectors, AND WHY.

    UNCONDITIONAL NOW, AND THE SKIP IT REPLACES IS THE POINT. This arm used to
    be `skipif`-guarded on ``SQLITE_EXTENSIONS_ENABLED``, because
    ``_store_embedding`` returned ONE code for BOTH the no-extension exit and
    the no-model exit -- so on a machine without pysqlite3 the arm went green
    for a cause that has nothing to do with the model. A wrong-reason pass.

    The codes now carry their cause, so the assertion below can name which
    mechanism fired instead of accepting either. That is the whole value of
    the change: one symbol for two faults meant the test had to be skipped
    rather than sharpened.

    ``SQLITE_EXTENSIONS_ENABLED`` IS PATCHED TRUE RATHER THAN ASSUMED. The
    no-extension exit sits ABOVE the model exit, so on a machine without
    extensions it fires first and this arm would measure the other mechanism.
    Forcing it closed is what makes the arm environment-independent rather
    than merely environment-tolerant -- the distinction the skip was papering
    over.
    """
    with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", True):
        with _model_unavailable():
            mem.save({"context": _CONTEXT}, sync_to_claude=False)
            status = mem.last_embedding_status

    assert _cause(status) == "no-model", (
        f"last_embedding_status was {status!r}. With the model unreachable and "
        "the vector store available, the save must name `no-model` -- naming "
        "`no-vector-store` here would send a reader to install a library that "
        "is already present."
    )


def test_the_no_vector_store_cause_is_named_on_its_own(mem):
    """THE MATCHED OPPOSITE, and it is what makes the arm above mean something.

    Without it, `no-model` could be a constant: a code that named `no-model`
    on every degraded exit would satisfy the arm above forever. This drives
    the OTHER mechanism and requires the other name.
    """
    with patch("scripts.memory_api.SQLITE_EXTENSIONS_ENABLED", False):
        mem.save({"context": _CONTEXT}, sync_to_claude=False)
        status = mem.last_embedding_status

    assert _cause(status) == "no-vector-store", (
        f"last_embedding_status was {status!r}. With no extension support no "
        "vector table is reachable at all, which is a different fault from a "
        "model that would not load and wants a different response."
    )
