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
    """Run the real _ensure_initialized against a fake StaticModel, return the call.

    THE SNAPSHOT RESOLUTION IS PINNED TO None, AND THAT IS WHAT KEEPS THESE
    ARMS CACHE-INDEPENDENT. `_ensure_initialized` resolves the revision the
    cache calls `main` and passes that DIRECTORY positionally when it can,
    falling back to the bare model id when it cannot. So the positional
    argument would otherwise depend on whether the machine running the suite
    happens to have a populated HuggingFace cache -- green on a cold checkout,
    red on a warm one, for a call that is correct in both. That is precisely
    the dependence this file's header rules out, so the ambient answer is
    replaced with a fixed one rather than the assertion below being loosened to
    admit both shapes.

    🔴 THE PATCH TARGETS THE FUNCTION'S OWN `__globals__`, NOT THE MODULE NAME,
    AND THE DIFFERENCE IS NOT STYLE. `tests/test_embedding_catchup.py` evicts
    `scripts.embeddings` from `sys.modules` and re-imports it, so from that
    point on TWO module objects for this file are live: the one this file
    imported `EmbeddingService` from, and the newer one occupying the name.
    `patch("scripts.embeddings._cache_snapshot_for")` writes into the NEWER
    one; the method body reads its own `__globals__`, which is still the older
    one. MEASURED: after the eviction, `__globals__ is sys.modules[...].__dict__`
    is False, and a patch applied by name is invisible to the call.

    That made this pin pass when the file ran alone and fail in a full suite,
    on the same machine with the same cache -- alphabetical order puts the
    catchup file first. A function's `__globals__` IS the namespace its body
    resolves through, by construction rather than by lookup, so it cannot
    drift from the code under test no matter what `sys.modules` is holding.
    """
    fake_cls = MagicMock()
    fake_module = MagicMock(StaticModel=fake_cls)
    service = EmbeddingService()
    with patch.dict("sys.modules", {"model2vec": fake_module}), patch.dict(
        EmbeddingService._ensure_initialized.__globals__,
        {"_cache_snapshot_for": lambda _name: None},
    ):
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


def _accepts_force_download(func) -> bool:
    """Whether `func` accepts a `force_download` KEYWORD argument.

    Split out from the arm below so its discrimination can be pinned by
    `test_the_detector_rejects_a_signature_without_the_parameter`. A detector
    nothing tests is the same instrument defect this file exists to prevent,
    one level up.
    """
    import inspect

    params = inspect.signature(func).parameters
    found = params.get("force_download")
    if found is not None:
        return found.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    # A `**kwargs` passthrough accepts the name at call time even though it
    # does not appear in the signature. Reporting False there would be a
    # false alarm, not a stricter check.
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def test_the_real_library_still_accepts_force_download():
    """THE ARGUMENT MUST BE ACCEPTED BY THE LIBRARY, not merely passed by us.

    Every other arm above inspects a MagicMock, which accepts ANY keyword. So
    they pin that our call site passes the argument and can say nothing about
    whether model2vec still takes it. If a future version drops or renames the
    parameter, the production call raises TypeError, `_ensure_initialized`
    catches it under `except Exception`, sets `_available = False`, and every
    save from then on silently stores no vector -- with the mock-based arms
    above still green and CI still passing.

    This is the same shape as the defect one file over: a harness that SUPPLIES
    what production must OBTAIN cannot measure whether production can obtain
    it. The fix is to ask the real library.

    Distinct from `test_the_upstream_default_is_still_true`, which asserts the
    DEFAULT VALUE. A parameter could be present with a changed default (that
    arm fires), or absent entirely (this one fires), or present but
    positional-only (only this one fires).
    """
    model2vec = pytest.importorskip("model2vec")
    assert _accepts_force_download(model2vec.StaticModel.from_pretrained), (
        "model2vec.StaticModel.from_pretrained no longer accepts a "
        "`force_download` keyword. The production call in embeddings.py will "
        "raise TypeError, be swallowed by its `except Exception`, and disable "
        "embeddings for the rest of every process -- silently."
    )


def test_the_detector_rejects_a_signature_without_the_parameter():
    """COUNTER-TEST for the arm above, and it is committed rather than run once.

    The library cannot be mutated, so the demonstration that the check
    discriminates has to be made against its INPUT instead. Pinning it here
    keeps that demonstration permanent: an edit that made
    `_accepts_force_download` return True unconditionally would pass the arm
    above forever, and fails here.
    """
    def without(name):
        return name

    def with_kwargs(name, **kwargs):
        return name

    def positional_only(name, force_download, /):
        return name

    assert _accepts_force_download(without) is False, "accepted a signature without it"
    assert _accepts_force_download(with_kwargs) is True, "**kwargs does accept it"
    assert _accepts_force_download(positional_only) is False, (
        "a positional-only parameter cannot be passed by keyword, which is how "
        "embeddings.py passes it"
    )


def test_the_model_name_is_still_the_one_we_pin():
    """Non-vacuity guard for the call inspection above, ON THE FALLBACK BRANCH.

    Both arms read `call_args`. If the load ever stopped passing a model name
    positionally, `kwargs` could be inspected on a call that no longer resembles
    the one under test. Pinning the positional argument keeps the shape honest.

    WHAT THIS CHECKS IS NARROWER THAN IT ONCE WAS, and the sentence is corrected
    rather than the assertion widened. The load now passes
    `snapshot or MODEL_NAME` positionally, and the helper pins the snapshot
    resolution to None so these arms stay cache-independent -- so what this sees
    is the FALLBACK branch, never the resolved-directory one.

    IT STILL DOES ITS JOB. Both arms run under the same pin and inspect the same
    call, so a load that stopped passing a model name positionally still fails
    here before its sibling reads `kwargs` off a call it no longer recognises.
    That is the whole of what this guard was for, and it is unaffected.

    THE RESOLVED BRANCH IS PINNED ELSEWHERE, by arms written for it:
    `test_embedding_snapshot_is_the_cache_ref.py` asserts the resolved directory
    reaches the first load positionally, and that the retry is handed the bare
    id instead. This arm not reaching that branch is correct, not a gap.
    """
    from scripts.embeddings import MODEL_NAME

    args, _ = _load_with_fake_model2vec()
    assert args and args[0] == MODEL_NAME, (
        f"expected the model name positionally, got {args!r}"
    )


# --- the invariant: no call site may be added without the override ----------

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PRODUCTION_CALL_SITE = "skills/pact-memory/scripts/embeddings.py"
_FIXTURE_CALL_SITE = "tests/test_memory_layer_composition.py"

# BOTH known members, not one. A single pinned member cannot see a narrowing
# that drops the OTHER one -- see `test_the_scan_reaches_real_code`.
_KNOWN_CALL_SITES = (_PRODUCTION_CALL_SITE, _FIXTURE_CALL_SITE)


def _scan():
    """Walk the tree once; return (call_sites, unparseable_paths).

    Matches `ast.Call` nodes whose callee attribute is `from_pretrained`, which
    is a PROPERTY OF THE CALL rather than a filename rule. That distinction
    matters: this file mentions `from_pretrained` several times -- on a
    MagicMock, and inside `inspect.signature(...)` -- and none of those are
    calls to it, so they are excluded because of what they ARE and not because
    of where they live. A filename exclusion would also have hidden a genuine
    call added to this file later.

    THE SECOND RETURN VALUE IS NOT BOOKKEEPING. A file this walk cannot parse
    contributes NOTHING to the census, so a non-compliant call site inside one
    is invisible to the invariant below -- measured: a planted call site in an
    unparseable file survived, while the identical site in a parseable file was
    caught. Every skip is therefore surfaced rather than swallowed, and
    `test_no_file_is_silently_skipped` is what makes the swallow impossible.

    THE FOURTH TUPLE FIELD IS THE REPAIR-PATH DISCRIMINATOR. A call inside an
    `except` handler runs only AFTER a load has already failed, and there a
    re-fetch is what the caller wants -- so `force_download=True` is correct
    there and wrong everywhere else. Keyed on the ENCLOSING `except` block
    rather than on a path or line allowlist, which would rot the moment the
    repair moved.

    call_sites: list of (relative_path, lineno, force_download_source_or_None,
                         inside_except_handler).
    unparseable_paths: list of (relative_path, exception_class_name).
    """
    found = []
    skipped = []
    for path in sorted(_PLUGIN_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            skipped.append((str(path.relative_to(_PLUGIN_ROOT)), type(exc).__name__))
            continue
        handled = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                handled.update(id(sub) for sub in ast.walk(node))
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
                        id(node) in handled,
                    )
                )
    return found, skipped


def _real_call_sites():
    """Every place the repo actually CALLS `.from_pretrained(...)`."""
    return _scan()[0]


def test_the_scan_reaches_real_code():
    """NON-VACUITY, and it is the guard the invariant below depends on.

    A repo scan that matches nothing passes every "all of them are correct"
    assertion trivially -- the failure mode is a test that cannot fail, and it
    is exactly the instrument defect that produced a dead probe elsewhere in
    this work.

    EVERY known member is pinned, not one, and the difference is measurable
    rather than stylistic. Pinning one member catches a change to the scan
    ROOT -- the pinned paths are relative to it, so moving the root moves the
    path and breaks the match. It does NOT catch a FILTER: adding
    `if "tests" in path.parts: continue` leaves the production path spelled
    identically, so a single-member guard stays green while HALF the population
    silently leaves the census, and a real regression at the dropped site then
    goes undetected. One stamp behind a two-member population cannot see a
    one-of-N failure.

    This is a REACH check, not a completeness claim: it proves the scanner
    still arrives at the sites we know about. Removing a call site legitimately
    means editing `_KNOWN_CALL_SITES` deliberately, which is the point -- the
    edit is visible in review rather than absorbed by a threshold.
    """
    sites = _real_call_sites()
    assert sites, "the scan found NO call sites at all -- the instrument is broken"
    reached = {path for path, _, _, _ in sites}
    missing = [known for known in _KNOWN_CALL_SITES if known not in reached]
    assert not missing, (
        f"the scan did not reach {missing}, known to contain calls. The census "
        f"has been narrowed -- by a changed root, or by a filter that drops "
        f"them while leaving the others spelled the same. Found: {sorted(reached)}"
    )


def test_no_file_is_silently_skipped():
    """The parse-failure branch must never hide a member of the population.

    `_scan` skips any file it cannot parse. A skip is indistinguishable from
    "this file contains no call site", so a non-compliant call in an
    unparseable file is invisible to the invariant below -- measured, it
    survived, while the identical call in a parseable file was caught.

    Asserting ZERO skips rather than logging them is deliberate: a count that
    is merely reported is a count nobody reads, and the whole defect class here
    is a signal that exists and is never looked at.
    """
    _, skipped = _scan()
    assert not skipped, (
        f"files were skipped by the parser and are therefore absent from the "
        f"call-site census: {skipped}. Any `.from_pretrained(` call inside one "
        "is invisible to test_every_real_call_site_overrides_force_download."
    )


def test_every_real_call_site_overrides_force_download():
    """THE CLASS, not the instance -- and the property is EXPLICITNESS.

    A census fixes today's members and expires the moment someone adds a call
    site. This asserts the property of EVERY call site instead, so a new one
    cannot be introduced silently with model2vec's default of True still in
    force. Both original sites were found by hand -- the production loader,
    then a test fixture that hung an entire suite -- and the second was missed
    on the first pass precisely because a census was run once rather than
    encoded.

    TWO RULES, AND THE FIRST IS THE ONE THAT MATTERS:

      1. EVERY site must pass `force_download` EXPLICITLY. Taking model2vec's
         default is the defect, wherever the call sits.
      2. Outside an `except` handler it must be `False`.

    WHY `except` IS THE DISCRIMINATOR AND NOT AN ALLOWLIST. A repair retry runs
    only AFTER a cache-first load has already failed, and there a re-fetch is
    exactly what the caller wants -- the original rationale for this pin, that
    a True site "revalidates over the network on every load", does not reach a
    site that runs on no ordinary load at all. An allowlist by path or line
    would rot the moment that code moved; the enclosing-handler rule travels
    with it.

    Rule 1 is what stops the exception widening into a hole: a repair site may
    override in the OTHER direction, deliberately and visibly, but it may not
    omit the argument and inherit the default.
    """
    sites = _real_call_sites()

    silent = [(p, n) for p, n, value, _ in sites if value is None]
    assert not silent, (
        "these call sites pass NO `force_download`, so they take model2vec's "
        f"default of True: {silent}. An `except` handler does not excuse this "
        "-- a repair path must override deliberately, not inherit."
    )

    wrong_direction = [
        (p, n, value) for p, n, value, in_except in sites
        if not in_except and value != "False"
    ]
    assert not wrong_direction, (
        "these call sites are NOT inside an `except` handler and do not pass "
        "force_download=False, so they revalidate over the network on every "
        f"ordinary load: {wrong_direction}"
    )


def test_the_except_discriminator_tells_the_two_positions_apart():
    """COUNTER-TEST for the rule above, committed rather than run once.

    The exemption is only as good as the discriminator, and a discriminator
    that answered True everywhere would make rule 2 vacuous while every arm
    above still passed. Pins it against synthetic source holding both
    positions, so the scanner's answer is measured rather than assumed.
    """
    source = (
        "def outside():\n"
        "    M.from_pretrained(N, force_download=True)\n"
        "def repair():\n"
        "    try:\n"
        "        M.from_pretrained(N, force_download=False)\n"
        "    except Exception:\n"
        "        M.from_pretrained(N, force_download=True)\n"
    )
    tree = ast.parse(source)
    handled = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            handled.update(id(sub) for sub in ast.walk(node))
    verdicts = [
        (node.lineno, id(node) in handled)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "from_pretrained"
    ]
    assert sorted(verdicts) == [(2, False), (5, False), (7, True)], (
        f"the enclosing-handler discriminator mis-classified: {sorted(verdicts)}. "
        "Line 2 is bare, line 5 is inside the `try` (NOT the handler), and only "
        "line 7 is inside the `except`."
    )
