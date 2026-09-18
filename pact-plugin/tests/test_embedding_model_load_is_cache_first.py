"""The embedding model load must not revalidate a cached model over the network.

``model2vec.StaticModel.from_pretrained`` defaults ``force_download=True``. Left
un-overridden, every process that generates an embedding makes TEN METADATA
ROUND-TRIPS -- one per file -- to revalidate a copy already on disk.

MEASURED, so nobody has to re-derive it and nobody restates it wrongly: the
default does NOT re-transfer the model. With a warm cache not one blob changes
mtime or size, and the fetch reports ten files in under 0.01s. The cost is the
round-trips, roughly 1.4s against 0.3s. An earlier account of this defect called
it a 59MB re-download; that was inferred from the parameter's NAME and is false.

WHY IT IS A CORRECTNESS BUG AND NOT A PERFORMANCE ONE. Each round-trip is a
network call on the common save path, and that call can block in a raw SSL read.
``_ensure_initialized`` wraps the load in ``except Exception`` and degrades
gracefully -- warns, sets ``_available = False``, lets the save proceed without a
vector. But A HANG IS NOT AN EXCEPTION, so on a stalled connection that handler
never runs and the save waits without bound. The error handling is correct and
unreachable. Ten round-trips per process is ten chances to meet that; a cached
model needs none.

WHAT THIS PINS AND WHAT IT DOES NOT. It asserts the ARGUMENT, not the transfer:
the test must not depend on network state, a populated cache, or timing, or it
would be the flaky thing it exists to prevent. It therefore patches
``StaticModel`` and inspects the call. The both-directions pair is
``force_download`` being passed as ``False`` versus the upstream default being
allowed through -- a mutant that drops the argument fails the first arm.
"""
import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.embeddings import EmbeddingService


def _load_with_fake_model2vec():
    """Run the real _ensure_initialized against a fake StaticModel, return the call."""
    fake_cls = MagicMock()
    fake_module = MagicMock(StaticModel=fake_cls)
    service = EmbeddingService()
    with patch.dict("sys.modules", {"model2vec": fake_module}):
        assert service._ensure_initialized() is True, (
            "the load reported failure against a fake model -- the probe is "
            "measuring an error path, not the call"
        )
    assert fake_cls.from_pretrained.called, "from_pretrained was never called"
    return fake_cls.from_pretrained.call_args


def test_the_model_load_passes_force_download_false():
    """The positive arm: the override is present and is False.

    A mutant that removes the argument, restoring model2vec's default of True,
    fails here -- which is the regression this exists to catch.
    """
    _, kwargs = _load_with_fake_model2vec()
    assert "force_download" in kwargs, (
        "force_download was not passed, so model2vec's default of True applies "
        "and every embedding-generating process revalidates over the network"
    )
    assert kwargs["force_download"] is False, (
        f"force_download={kwargs['force_download']!r}; must be False so a cached "
        "model is used without network round-trips"
    )


def test_the_upstream_default_is_still_true():
    """The matched negative: this pin is only worth having while it differs.

    If model2vec ever changes its default to False, the override becomes
    redundant and this test says so loudly rather than leaving a line nobody
    can explain. Skips rather than fails when model2vec is absent, because the
    upstream default is not this repo's property to assert without the library.
    """
    model2vec = pytest.importorskip("model2vec")
    import inspect

    default = inspect.signature(
        model2vec.StaticModel.from_pretrained
    ).parameters["force_download"].default
    assert default is True, (
        f"model2vec's force_download default is now {default!r}. If it is "
        "False, the override in embeddings.py is redundant and the comment "
        "explaining it is stale -- remove both deliberately."
    )


def test_the_model_name_is_still_the_one_we_pin():
    """Non-vacuity guard for the call inspection above.

    Both arms read `call_args`. If the load ever stopped passing a model name
    positionally, `kwargs` could be inspected on a call that no longer resembles
    the one under test. Pinning the positional argument keeps the shape honest.
    """
    from scripts.embeddings import MODEL_NAME

    args, _ = _load_with_fake_model2vec()
    assert args and args[0] == MODEL_NAME, (
        f"expected the model name positionally, got {args!r}"
    )


# --- the invariant: no call site may be added without the override ----------

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PRODUCTION_CALL_SITE = "skills/pact-memory/scripts/embeddings.py"


def _real_call_sites():
    """Every place the repo actually CALLS `.from_pretrained(...)`.

    Matches `ast.Call` nodes whose callee attribute is `from_pretrained`, which
    is a PROPERTY OF THE CALL rather than a filename rule. That distinction
    matters: this file mentions `from_pretrained` several times -- on a
    MagicMock, and inside `inspect.signature(...)` -- and none of those are
    calls to it, so they are excluded because of what they ARE and not because
    of where they live. A filename exclusion would also have hidden a genuine
    call added to this file later.

    Returns a list of (relative_path, lineno, force_download_source_or_None).
    """
    found = []
    for path in sorted(_PLUGIN_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "from_pretrained"
            ):
                kwargs = {k.arg: ast.unparse(k.value) for k in node.keywords}
                found.append(
                    (
                        str(path.relative_to(_PLUGIN_ROOT)),
                        node.lineno,
                        kwargs.get("force_download"),
                    )
                )
    return found


def test_the_scan_reaches_real_code():
    """NON-VACUITY, and it is the guard the invariant below depends on.

    A repo scan that matches nothing passes every "all of them are correct"
    assertion trivially -- the failure mode is a test that cannot fail, and it
    is exactly the instrument defect that produced a dead probe elsewhere in
    this work. Pinning a KNOWN-PRESENT member proves the scanner reaches
    production code, so a zero result fails here rather than passing silently
    two tests down.
    """
    sites = _real_call_sites()
    assert sites, "the scan found NO call sites at all -- the instrument is broken"
    assert any(path == _PRODUCTION_CALL_SITE for path, _, _ in sites), (
        f"the scan did not reach {_PRODUCTION_CALL_SITE}, which is known to "
        f"contain a call. Found instead: {[p for p, _, _ in sites]}"
    )


def test_every_real_call_site_overrides_force_download():
    """THE CLASS, not the instance.

    A census fixes today's members and expires the moment someone adds a call
    site. This asserts the property of EVERY call site instead, so a third one
    cannot be introduced silently with model2vec's default of True still in
    force. Both known sites were found by hand -- the production loader, then a
    test fixture that hung an entire suite -- and the second was missed on the
    first pass precisely because a census was run once rather than encoded.
    """
    offenders = [
        (path, lineno, value)
        for path, lineno, value in _real_call_sites()
        if value != "False"
    ]
    assert not offenders, (
        "these call sites do not pass force_download=False, so they take "
        "model2vec's default of True and revalidate over the network on every "
        f"load: {offenders}"
    )
