"""
Location: pact-plugin/hooks/refresh/constants.py
Summary: Configuration constants for the workflow refresh system.
Used by: All refresh modules for consistent threshold and limit values.

This module centralizes configuration constants that may need tuning,
keeping regex patterns and workflow definitions in patterns.py while
extracting tunable numeric values here for maintainability.

STEP_DESCRIPTIONS and PROSE_CONTEXT_TEMPLATES are imported from
shared_constants.py to centralize constants shared by refresh consumers.
"""

# Import shared constants for re-export
from .shared_constants import STEP_DESCRIPTIONS, PROSE_CONTEXT_TEMPLATES

# === CONFIDENCE THRESHOLDS (Item 3) ===

# Minimum confidence score for checkpoint to be considered valid
CONFIDENCE_THRESHOLD = 0.3

# Threshold above which the system can auto-proceed without user confirmation.
# Below this threshold, refresh messages include "Get user approval before acting."
CONFIDENCE_AUTO_PROCEED_THRESHOLD = 0.8

# Medium confidence label for informational purposes
CONFIDENCE_LABEL_MEDIUM = 0.5

# === LENGTH LIMITS ===

# Maximum length for extracted text to prevent excessive data
PENDING_ACTION_INSTRUCTION_MAX_LENGTH = 200
REVIEW_PROMPT_INSTRUCTION_MAX_LENGTH = 150
TASK_SUMMARY_MAX_LENGTH = 200

# === PROCESSING LIMITS ===

# Termination detection window: number of turns after trigger to check
TERMINATION_WINDOW_TURNS = 10

# Size threshold for switching to efficient tail-reading (10 MB)
LARGE_FILE_THRESHOLD_BYTES = 10 * 1024 * 1024

# Maximum transcript lines to read for workflow detection
MAX_TRANSCRIPT_LINES = 500

# === CHECKPOINT CONFIGURATION ===

# Current checkpoint schema version
CHECKPOINT_VERSION = "1.0"

# Checkpoint file expiration in days (Item 11)
CHECKPOINT_MAX_AGE_DAYS = 7

# Note: STEP_DESCRIPTIONS and PROSE_CONTEXT_TEMPLATES are imported
# from shared_constants.py at the top of this file.
