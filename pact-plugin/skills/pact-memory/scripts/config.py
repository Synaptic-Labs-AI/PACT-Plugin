"""
PACT Memory Configuration

Location: pact-plugin/skills/pact-memory/scripts/config.py

Centralized configuration for the PACT Memory skill.
All path constants and directory configurations are defined here
to ensure consistency across all modules.

Used by:
- database.py: Database path configuration (DB_PATH, PACT_MEMORY_DIR)
- setup_memory.py: Directory creation (PACT_MEMORY_DIR)
"""

from pathlib import Path

# Base directory for all PACT memory data
PACT_MEMORY_DIR = Path.home() / ".claude" / "pact-memory"

# Database configuration
DB_PATH = PACT_MEMORY_DIR / "memory.db"

# Session tracking directory
SESSION_TRACKING_DIR = PACT_MEMORY_DIR / "session-tracking"
