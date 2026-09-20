"""
PACT Memory Embedding Service

Location: pact-plugin/skills/pact-memory/scripts/embeddings.py

Embedding generation for semantic search in the PACT Memory skill.
Uses Model2Vec for fast, stable, pure-Python embeddings.

Used by:
- search.py: Generates query embeddings for semantic search
- memory_api.py: Generates embeddings when saving memories
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

# Configure logging
logger = logging.getLogger(__name__)

# Model2Vec configuration
MODEL_NAME = "minishlab/potion-base-8M"
EMBEDDING_DIM = 256

# The median record length in the store, in tokens, at the time of measurement.
#
# A NAMED CONSTANT RATHER THAN A COMMENT, so a test can compare the window
# against it. It records a population, not a target.
MEASURED_MEDIAN_TOKENS = 1992

# The token window passed to the encoder.
#
# THE DEFECT THIS ENDS. The call below passed no window, so the encoder applied
# its own default of 512 tokens. A median record therefore reached the semantic
# index at roughly its first quarter, and nothing reported the loss.
#
# THE BASIS. The window is a little above the measured median above. It is the
# SMALLEST DEFENSIBLE MEMBER OF A PLATEAU and NOT A PEAK: the author of the
# measurement flagged a marginal cell and declined to name one best value,
# because four metric-and-arm pairs gave four different maxima. Read it as the
# cheapest value that loses nothing separable.
#
# WHAT IT DOES NOT DO. It does not cover the store. The longest record runs to
# roughly 28,000 tokens and the encoder continues to cut it heavily. This
# window reduces the loss. It does not end it.
#
# TWO TRIGGERS TO RE-CHECK IT, and the second one matters more:
#   1. The record-length distribution moves, so the median above goes stale.
#   2. MODEL_NAME changes. The tokenizer changes with the model, so the token
#      count of one record changes. The truncation also has a CHARACTER
#      pre-slice at `max_length * model.median_token_length`, and that
#      multiplier is a MODEL PROPERTY rather than a library constant. The
#      measured density of the store sits below the multiplier for the model
#      above, so the token cut is the arm that binds and a token-level test
#      covers the behaviour. A different model can move the multiplier below
#      the density of the store, and the character pre-slice then binds
#      instead, so that argument must be re-made rather than assumed.
EMBEDDING_MAX_TOKENS = 2048
# Minimum free RAM (MB) required before running embedding catch-up.
# Model2Vec uses ~59MB; 75MB provides a safety margin.
MIN_CATCHUP_RAM_MB = 75.0


def _cache_snapshot_for(model_name: str) -> Optional[str]:
    """Return the snapshot directory the CACHE ITSELF calls `main`, or None.

    WHY THIS EXISTS. model2vec resolves a bare model id through its own cache
    probe, which picks a snapshot directory by `max(mtime)` and does not check
    that the directory is the revision anyone asked for. With several snapshots
    cached, the one that loads is whichever was touched last -- an incidental
    property of download order, not a statement about which revision this is.
    So the vectors that enter the index are chosen by a filesystem timestamp.

    WHAT REPLACES IT. `refs/main` is a pointer huggingface_hub maintains, so
    "which revision is main" is the CACHE'S answer rather than a policy we
    choose here. `try_to_load_from_cache` performs exactly that resolution and
    is documented never to raise, so this asks the library the question instead
    of reimplementing its cache layout.

    RETURNS None RATHER THAN RAISING, AND THAT IS THE WHOLE INTERFACE. Every
    way this can fail degrades to the caller's existing behaviour:

      * no repo directory, no `refs/` directory, or no `refs/main`   -> None
      * a ref naming a snapshot directory that is not present        -> None
      * a snapshot present but missing `model.safetensors`           -> None
      * the file recorded as known-to-not-exist (`_CACHED_NO_EXIST`) -> None
      * anything unexpected from the library at all                  -> None

    A ref with stray whitespace also lands in the first group, because
    huggingface_hub reads the ref file WITHOUT stripping and a padded value
    then matches no snapshot directory. That is a quiet fallback rather than an
    error, which is the correct direction here but does mean this is
    best-effort determinism: when it cannot answer, the previous arbitrary
    selection is what runs.
    """
    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache(model_name, "model.safetensors")
    except Exception:
        return None
    # `isinstance(str)` is the library's own documented test: it rejects both
    # None (not cached) and the `_CACHED_NO_EXIST` sentinel in one check.
    if not isinstance(cached, str):
        return None
    snapshot = os.path.dirname(cached)
    return snapshot if os.path.isdir(snapshot) else None


class EmbeddingService:
    """
    Embedding service using Model2Vec.

    Model2Vec provides:
    - Pure Python (no native code crashes)
    - Fast: 85K sentences/sec
    - Small: 59MB model, 256-dim embeddings
    - Auto-downloads from HuggingFace on first use
    """

    def __init__(self):
        """Initialize the embedding service."""
        self._model = None
        self._available: Optional[bool] = None

    def _ensure_initialized(self) -> bool:
        """Load the model if needed (lazy initialization)."""
        if self._model is not None:
            return True

        if self._available is False:
            return False

        try:
            from model2vec import StaticModel
            # force_download=False OVERRIDES model2vec's DEFAULT OF TRUE.
            #
            # WHAT THE DEFAULT COSTS, measured rather than assumed: it does NOT
            # re-transfer the model. With a warm cache, not one blob changes
            # mtime or size and the fetch reports ten files in under 0.01s. What
            # it does cost is TEN METADATA ROUND-TRIPS, one per file, to
            # revalidate a copy already on disk -- about 1.4s against 0.3s here.
            #
            # WHY THAT MATTERS MORE THAN THE TIME: each round-trip is a network
            # call on the common path, and the call below can block in a raw SSL
            # read. The handler around this load degrades gracefully when the
            # model cannot load -- but a HANG IS NOT AN EXCEPTION, so that
            # handler never runs and the save waits indefinitely. Ten
            # round-trips per embedding-generating process is ten chances to
            # meet that. A cached model needs none of them.
            #
            # A genuine first run still downloads: this suppresses
            # revalidation, not acquisition.
            # RESOLVE THE SNAPSHOT BEFORE LOADING, so the revision is the
            # one the cache calls `main` rather than whichever directory was
            # touched last. Falls back to the bare id -- and therefore to the
            # previous behaviour -- whenever the cache cannot answer.
            snapshot = _cache_snapshot_for(MODEL_NAME)
            try:
                self._model = StaticModel.from_pretrained(
                    snapshot or MODEL_NAME, force_download=False
                )
            except Exception as cached_copy_unusable:
                # REPAIR ONCE, BECAUSE A CACHE-FIRST LOAD CAN PICK A BROKEN
                # COPY AND THEN NEVER STOP PICKING IT.
                #
                # model2vec's cache probe selects a snapshot directory by
                # max(mtime) and does NOT check that the snapshot is complete.
                # An interrupted transfer therefore leaves a directory that
                # LOOKS newest and cannot load, and `force_download=False`
                # re-selects that same directory on every subsequent run -- so
                # without this the degradation is PERMANENT rather than
                # transient, and no later save repairs it.
                #
                # MEASURED, not inferred: a snapshot copied complete loads; the
                # same snapshot with `model.safetensors` removed returns
                # no-model. That is the end state of an interrupted fetch, and
                # it is the mechanism behind a 12-of-14-file cache that
                # reported no-model while the files sat on disk.
                #
                # `force_download=True` BYPASSES the cache probe, which is the
                # point -- it is the only way back past a poisoned selection.
                # It costs a re-fetch, and it runs ONLY when the cached copy
                # already failed, which is exactly when a re-fetch is what the
                # caller wants.
                #
                # THIS RETRY PASSES `MODEL_NAME`, NOT THE RESOLVED SNAPSHOT,
                # AND THAT IS LOAD-BEARING RATHER THAN AN OVERSIGHT. model2vec
                # resolves its argument with `_resolve_folder`, whose FIRST
                # action is `if folder_or_repo_path.exists(): return it` --
                # before `force_download` is read at all. Hand it an existing
                # directory and `force_download=True` cannot reach the network:
                # the retry would re-select the same unusable snapshot, fail
                # identically, and restore the permanent degradation this
                # handler exists to break. MEASURED, not inferred: an existing
                # path with `force_download=True` comes back unchanged, while a
                # non-existent one falls through. The tidy-up that hoists one
                # `target` variable for both calls is exactly what must not
                # happen, which is why there is a test pinning it.
                #
                # THIS DOES NOT WEAKEN THE OFFLINE GUARANTEE. Under
                # `HF_HUB_OFFLINE=1` the retry raises immediately instead of
                # reaching the network, so an offline caller still degrades
                # rather than hanging -- the outer handler below catches it.
                logger.debug(
                    "cached model2vec copy did not load (%s); re-fetching once",
                    cached_copy_unusable,
                )
                self._model = StaticModel.from_pretrained(
                    MODEL_NAME, force_download=True
                )
            self._available = True
            logger.info(f"Loaded model2vec model: {MODEL_NAME}")
            return True
        # DEBUG, NOT WARNING, ON BOTH HANDLERS, AND THE REASON IS THE CHANNEL
        # RATHER THAN THE SEVERITY. A failed model load is worth telling someone
        # about, so WARNING is the obvious level and it is the wrong one here:
        # `cli.py` configures no logging, so `logging.lastResort` emits WARNING
        # and above to STDERR -- and the CLI's stderr carries its structured
        # JSON error envelope, which callers parse. One free-text line corrupts
        # that parse. The outcome is NOT being swallowed: it reaches the caller
        # as `embedding_status: "degraded:<mode>"` on stdout, which is the
        # channel a caller can act on. `memory_api._store_embedding` made the
        # same trade at its own handler and its comment carries the same
        # reasoning; these are siblings and should not disagree.
        #
        # THIS STOPPED BEING THEORETICAL when the test child envs gained
        # `HF_HUB_OFFLINE=1`: an empty cache now RAISES here instead of hanging,
        # so the second handler fires on every cold-cache run rather than
        # almost never.
        except ImportError:
            logger.debug(
                "model2vec not installed. "
                "Install for semantic search: pip install model2vec"
            )
            self._available = False
            return False
        except Exception as e:
            logger.debug(f"Failed to load model2vec: {e}")
            self._available = False
            return False

    def generate(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding for text.

        Args:
            text: Input text to embed.

        Returns:
            List of floats representing the embedding, or None if unavailable.
        """
        if not text or not text.strip():
            return None

        if not self._ensure_initialized():
            return None

        try:
            # model2vec.encode returns numpy array of shape (n_texts, dim)
            #
            # PASS THE WINDOW EXPLICITLY. Omit the keyword and the encoder
            # applies its own default, which is smaller than a median record
            # in this store and truncates without a report.
            embeddings = self._model.encode([text], max_length=EMBEDDING_MAX_TOKENS)
            return embeddings[0].tolist()
        except Exception as e:
            # DEBUG for the reason given at `_ensure_initialized`'s handlers,
            # and it applies here for the same structural reason rather than by
            # analogy: this method returns `Optional[List[float]]` and hands the
            # caller `None` on failure, so the outcome already reaches a channel
            # the caller can act on and the level change loses no signal.
            #
            # LEAVING THIS ONE AS WARNING WOULD NOT HAVE BEEN NEUTRAL. With its
            # two neighbours converted, a reader would reasonably infer this one
            # was considered and deliberately kept — a false signal planted in
            # the code, which is worse than the stderr line itself.
            #
            # `logging.lastResort` is a stderr handler at WARNING, so `debug`
            # and `info` are both dropped by it; the `logger.info` on the
            # successful-load path is therefore already harmless and needs
            # nothing. With this line converted the module has no remaining
            # call at WARNING or above.
            logger.debug(f"Embedding generation failed: {e}")
            return None

    def is_available(self) -> bool:
        """Check if model2vec is available."""
        if self._available is not None:
            return self._available

        try:
            from model2vec import StaticModel  # noqa: F401  # availability probe: import success is the signal
            self._available = True
            return True
        except ImportError:
            self._available = False
            return False

    @property
    def backend_name(self) -> str:
        """Get the backend name."""
        return "model2vec"

    @property
    def embedding_dimension(self) -> int:
        """Get the embedding dimension (256 for model2vec)."""
        return EMBEDDING_DIM


# Module-level singleton for convenience
_lock = threading.Lock()
_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """
    Get the embedding service singleton.

    Returns:
        EmbeddingService instance.
    """
    global _service
    with _lock:
        if _service is None:
            _service = EmbeddingService()
    return _service


def reset_embedding_service() -> None:
    """Reset the singleton instance. Useful for testing."""
    global _service
    with _lock:
        _service = None


def generate_embedding(text: str) -> Optional[List[float]]:
    """
    Generate embedding for text using the default service.

    Convenience function for simple use cases.

    Args:
        text: Input text to embed.

    Returns:
        List of floats representing the embedding, or None if unavailable.
    """
    return get_embedding_service().generate(text)


def generate_embedding_text(memory: Dict[str, Any]) -> str:
    """
    Generate combined text from memory fields for embedding.

    Combines context, goal, lessons, and decisions into a single
    text block optimized for semantic similarity search.

    Uses MemoryObject.get_searchable_text() as the single source of truth.

    Args:
        memory: Memory dictionary with context, goal, lessons_learned, etc.

    Returns:
        Combined text suitable for embedding generation.
    """
    from .models import MemoryObject
    memory_obj = MemoryObject.from_dict(memory)
    return memory_obj.get_searchable_text()


def check_embedding_availability() -> Dict[str, Any]:
    """
    Check the status of embedding service.

    Returns:
        Dictionary with availability info.
    """
    service = get_embedding_service()

    return {
        "available": service.is_available(),
        "backend": "model2vec",
        "model": MODEL_NAME,
        "embedding_dimension": EMBEDDING_DIM,
    }
