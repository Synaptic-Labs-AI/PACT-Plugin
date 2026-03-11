"""
Location: pact-plugin/hooks/shared/merge_guard_common.py
Summary: Shared constants and utilities for the merge guard hook pair.
Used by: merge_guard_pre.py (PreToolUse) and merge_guard_post.py (PostToolUse)

Centralizes TOKEN_TTL, TOKEN_DIR, TOKEN_PREFIX, and consumed-token cleanup
so both hooks stay in sync without duplicating logic.
"""

import glob
import os
import time
from pathlib import Path

# Token TTL in seconds (5 minutes)
TOKEN_TTL = 300

# Directory for token files
TOKEN_DIR = Path.home() / ".claude"

# Token file prefix
TOKEN_PREFIX = "merge-authorized-"


def cleanup_consumed_tokens(token_dir: Path) -> None:
    """Remove stale .consumed token files older than TOKEN_TTL.

    Called from both hooks: during token scanning (pre-hook) and during
    token creation (post-hook) to prevent accumulation.

    Args:
        token_dir: Directory containing token files
    """
    consumed_pattern = str(token_dir / f"{TOKEN_PREFIX}*.consumed")
    now = time.time()
    for consumed_path in glob.glob(consumed_pattern):
        try:
            # Use file modification time as a proxy for consumption time
            mtime = os.path.getmtime(consumed_path)
            if now - mtime > TOKEN_TTL:
                try:
                    os.unlink(consumed_path)
                except OSError:
                    pass
        except OSError:
            # File may have been cleaned up concurrently — ignore
            pass
