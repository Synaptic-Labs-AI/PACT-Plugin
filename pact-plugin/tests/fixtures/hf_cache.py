"""HuggingFace cache resolution for BUILT child-process test environments.

Consumed by the tests whose child environment is CONSTRUCTED rather than
inherited and whose HOME is redirected below tmp_path:
tests/test_working_memory_redirected_store_refusal.py and
tests/test_working_memory_projection.py.

WHY THIS EXISTS. Redirecting a child's HOME relocates the HuggingFace cache
with it, because huggingface_hub derives its cache root from HOME at IMPORT
time in the child. A child with a redirected HOME therefore sees an EMPTY
cache, model2vec's cached-model early return cannot fire, and every save
performs a GENUINE FIRST-RUN DOWNLOAD — measured at ~59M across 14 files on a
machine with no cache, which is every CI run.

THE HOME REDIRECT ITSELF MUST STAY. It is what keeps a pathless child's default
STORE below tmp_path instead of on the operator's live one, so the fix is to
pin the cache explicitly, never to stop redirecting HOME.

Per the thin-conftest rule this is a direct-import helper, not a pytest
fixture; nothing here is injected.
"""

import os


def hf_cache_env() -> dict:
    """The two variables a built child env needs so it neither re-downloads the
    embedding model nor reaches the network at all.

    ``HF_HOME`` — THIS EXPRESSION IS A MIRROR of huggingface_hub.constants' own
    resolution, which is
    ``getenv("HF_HOME", join(getenv("XDG_CACHE_HOME", expanduser("~") + "/.cache"), "huggingface"))``.
    It is MIRRORED RATHER THAN IMPORTED for two reasons, in this order.
    PRIMARY, DETECTABILITY: importing another library's internals SILENTLY
    SUCCEEDS against changed internals, whereas a mirrored expression that
    drifts is at least readable. SECONDARY, PACKAGING: a hard import makes this
    module un-collectable wherever huggingface_hub is absent. A reader who
    knows only the packaging half reaches for the import the first time
    huggingface_hub is guaranteed present, which is the failure this note
    exists to prevent.

    🔴 DO NOT SUBSTITUTE ``Path.home()``, AND THAT IS NOT A STYLE PREFERENCE.
    The autouse ``_isolate_config_root_to_tmp`` fixture in tests/conftest.py
    monkeypatches it to ``tmp_path`` for EVERY test, so ``Path.home()`` here
    resolves to the per-test temp directory and this helper would hand the
    child the very empty cache it exists to avoid. That is the defect this
    helper replaces, not a hypothetical. ``os.environ`` and
    ``os.path.expanduser`` read the real variable, which that fixture
    deliberately leaves alone.

    OMITTING ``XDG_CACHE_HOME`` IS THE SAME CLASS OF BUG one term along: it
    resolves correctly only on machines that do not set it, and diverges
    silently everywhere else.

    ``HF_HUB_OFFLINE`` — DEFENCE IN DEPTH, and it guards a different failure
    than HF_HOME does. HF_HOME corrects WHICH cache the child is handed;
    offline mode bounds what happens when that cache is nonetheless EMPTY, by
    turning an unbounded network read into an immediate exception that the
    embedding service already degrades on. Measured: a cold cache with this set
    completes in 0.31s reporting ``degraded:keyword``, and a WARM cache with it
    set still embeds normally, so it costs the healthy path nothing.
    """
    return {
        "HF_HOME": os.environ.get("HF_HOME") or os.path.join(
            os.environ.get(
                "XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")
            ),
            "huggingface",
        ),
        "HF_HUB_OFFLINE": "1",
    }
