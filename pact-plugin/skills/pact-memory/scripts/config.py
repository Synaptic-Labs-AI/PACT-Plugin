"""
PACT Memory Configuration

Location: pact-plugin/skills/pact-memory/scripts/config.py

Centralized configuration for the PACT Memory skill.
All path constants and directory configurations are defined here
to ensure consistency across all modules.

Used by:
- database.py: Database path configuration
- embeddings.py: Model path configuration
- setup_memory.py: Directory creation and model download
"""

from pathlib import Path

# Base directory for all PACT memory data
PACT_MEMORY_DIR = Path.home() / ".claude" / "pact-memory"

# Database configuration
DB_PATH = PACT_MEMORY_DIR / "memory.db"

# Model configuration
MODELS_DIR = PACT_MEMORY_DIR / "models"
DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2-Q8_0.gguf"
DEFAULT_MODEL_PATH = MODELS_DIR / DEFAULT_MODEL_NAME

# Session tracking directory
SESSION_TRACKING_DIR = PACT_MEMORY_DIR / "session-tracking"

# Model download URL
MODEL_URL = (
    "https://huggingface.co/second-state/All-MiniLM-L6-v2-Embedding-GGUF/"
    "resolve/main/all-MiniLM-L6-v2-Q8_0.gguf"
)

# Embedding configuration
# Embedding dimension is defined in embeddings.py (EMBEDDING_DIM = 256 for model2vec)
MODEL_SIZE_MB = 24  # Approximate size for GGUF model (sqlite-lembed fallback)
