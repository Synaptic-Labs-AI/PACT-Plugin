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
            self._model = StaticModel.from_pretrained(
                MODEL_NAME, force_download=False
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
