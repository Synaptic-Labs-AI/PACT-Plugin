"""
PACT Memory Embedding Service

Location: pact-plugin/skills/pact-memory/scripts/embeddings.py

Embedding generation for semantic search in the PACT Memory skill.
Uses Model2Vec for fast, stable, pure-Python embeddings.

Used by:
- search.py: Generates query embeddings for semantic search
- memory_api.py: Generates embeddings when saving memories
"""

import logging
import threading
from typing import Any, Dict, List, Optional

# Configure logging
logger = logging.getLogger(__name__)

# Model2Vec configuration
MODEL_NAME = "minishlab/potion-base-8M"
EMBEDDING_DIM = 256
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
            self._model = StaticModel.from_pretrained(MODEL_NAME)
            self._available = True
            logger.info(f"Loaded model2vec model: {MODEL_NAME}")
            return True
        except ImportError:
            logger.warning(
                "model2vec not installed. "
                "Install for semantic search: pip install model2vec"
            )
            self._available = False
            return False
        except Exception as e:
            logger.warning(f"Failed to load model2vec: {e}")
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
            embeddings = self._model.encode([text])
            return embeddings[0].tolist()
        except Exception as e:
            logger.warning(f"Embedding generation failed: {e}")
            return None

    def is_available(self) -> bool:
        """Check if model2vec is available."""
        if self._available is not None:
            return self._available

        try:
            from model2vec import StaticModel
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
