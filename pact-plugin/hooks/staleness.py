"""
Staleness Detection Module

Location: pact-plugin/hooks/staleness.py

Summary: Detects stale pinned context entries in the project CLAUDE.md and
checks whether pinned content exceeds its token budget. Stale entries are
marked with HTML comments so they can be identified for cleanup.

Used by:
- session_init.py: Calls check_pinned_staleness() during SessionStart hook
- Test files: test_staleness.py tests all functions in this module

Extracted from session_init.py to keep that file focused on hook orchestration
and under the 500-line maintainability limit.
"""

import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# Staleness detection constants

# Number of days after which a pinned entry referencing a merged PR is
# considered stale and gets an HTML comment marker.
PINNED_STALENESS_DAYS = 30

# Approximate token budget for the entire Pinned Context section. When
# exceeded, a warning comment is added (no auto-deletion).
# This is the sole definition of this constant; session_init.py imports it.
PINNED_CONTEXT_TOKEN_BUDGET = 1200


def _get_project_claude_md_path() -> Optional[Path]:
    """
    Get the path to the project-level CLAUDE.md.

    Checks CLAUDE_PROJECT_DIR env var first, then falls back to git
    worktree/repo root detection via `git rev-parse --show-toplevel`.

    Returns:
        Path to project CLAUDE.md if found, None otherwise.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        path = Path(project_dir) / "CLAUDE.md"
        if path.exists():
            return path

    # Fallback: detect git root
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            git_root = result.stdout.strip()
            path = Path(git_root) / "CLAUDE.md"
            if path.exists():
                return path
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None


def _estimate_tokens(text: str) -> int:
    """
    Estimate token count using word count * 1.3 approximation.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    return int(len(text.split()) * 1.3)


def check_pinned_staleness(claude_md_path: Optional[Path] = None) -> Optional[str]:
    """
    Detect stale pinned context entries in the project CLAUDE.md.

    A pinned entry is considered stale if it references a merged PR with a
    date older than PINNED_STALENESS_DAYS. Stale entries get a
    <!-- STALE: Last relevant YYYY-MM-DD --> comment prepended before their
    heading (if not already marked).

    Also checks if the total pinned content exceeds the token budget and
    adds a warning comment if so (does NOT auto-delete pins).

    Args:
        claude_md_path: Explicit path to CLAUDE.md. If None, resolved via
            _get_project_claude_md_path(). Callers (e.g. session_init.py)
            may pass the path explicitly so their own resolution can be
            patched independently in tests.

    Returns:
        Informational message about stale pins found, or None.
    """
    if claude_md_path is None:
        claude_md_path = _get_project_claude_md_path()
    if claude_md_path is None:
        return None

    try:
        content = claude_md_path.read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        return None

    # Find the Pinned Context section
    pinned_match = re.search(r'^## Pinned Context\s*\n', content, re.MULTILINE)
    if not pinned_match:
        return None

    pinned_start = pinned_match.end()

    # Find the end of pinned section (next H1 or H2 heading, or EOF)
    next_section = re.search(r'^#{1,2}\s', content[pinned_start:], re.MULTILINE)
    if next_section:
        pinned_end = pinned_start + next_section.start()
    else:
        pinned_end = len(content)

    pinned_content = content[pinned_start:pinned_end]
    if not pinned_content.strip():
        return None

    # Parse individual pinned entries (each starts with ###)
    entry_pattern = re.compile(r'^### ', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(pinned_content)]

    if not entry_starts:
        return None

    now = datetime.now()
    stale_threshold = now - timedelta(days=PINNED_STALENESS_DAYS)
    stale_count = 0
    modified = False

    # Pattern to match "PR #NNN, merged YYYY-MM-DD" in entry text
    pr_merged_pattern = re.compile(
        r'PR\s*#\d+,?\s*merged\s+(\d{4}-\d{2}-\d{2})'
    )
    # Pattern to detect existing staleness marker
    stale_marker_pattern = re.compile(r'<!-- STALE: Last relevant \d{4}-\d{2}-\d{2} -->')

    # Process entries in reverse order so string offsets remain valid
    for i in range(len(entry_starts) - 1, -1, -1):
        start = entry_starts[i]
        if i + 1 < len(entry_starts):
            end = entry_starts[i + 1]
        else:
            end = len(pinned_content)

        entry_text = pinned_content[start:end]

        # Skip entries already marked stale
        if stale_marker_pattern.search(entry_text):
            stale_count += 1
            continue

        # Look for PR merged date
        pr_match = pr_merged_pattern.search(entry_text)
        if not pr_match:
            continue

        try:
            merged_date = datetime.strptime(pr_match.group(1), "%Y-%m-%d")
        except ValueError:
            continue

        if merged_date < stale_threshold:
            # Mark as stale by inserting comment after the ### heading line.
            # The marker must be inside the entry_text (which starts at ###)
            # so that subsequent runs find it and skip re-marking.
            stale_marker = f"<!-- STALE: Last relevant {pr_match.group(1)} -->\n"
            nl_pos = entry_text.find("\n")
            if nl_pos == -1:
                # Entry is a single line with no newline; skip it
                continue
            heading_end = nl_pos + 1
            new_entry = entry_text[:heading_end] + stale_marker + entry_text[heading_end:]
            pinned_content = (
                pinned_content[:start] + new_entry + pinned_content[end:]
            )
            stale_count += 1
            modified = True

    # Check token budget for pinned content
    pinned_tokens = _estimate_tokens(pinned_content)
    budget_warning = ""
    if pinned_tokens > PINNED_CONTEXT_TOKEN_BUDGET:
        budget_warning_comment = (
            f"<!-- WARNING: Pinned context ~{pinned_tokens} tokens "
            f"(budget: {PINNED_CONTEXT_TOKEN_BUDGET}). "
            f"Consider archiving stale pins. -->\n"
        )
        # Add budget warning at the top of pinned section if not present
        if "<!-- WARNING: Pinned context" not in pinned_content:
            pinned_content = budget_warning_comment + pinned_content
            modified = True
        budget_warning = f", ~{pinned_tokens} tokens (budget: {PINNED_CONTEXT_TOKEN_BUDGET})"

    # Write back if modified
    if modified:
        new_content = content[:pinned_start] + pinned_content + content[pinned_end:]
        try:
            claude_md_path.write_text(new_content, encoding="utf-8")
        except (IOError, OSError) as e:
            logger_msg = f"Failed to update pinned staleness: {e}"
            return logger_msg

    if stale_count > 0:
        return f"Pinned context: {stale_count} stale pin(s) detected{budget_warning}"
    if budget_warning:
        return f"Pinned context{budget_warning}"

    return None
