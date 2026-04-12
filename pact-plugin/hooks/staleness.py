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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple


# Staleness detection constants

# Number of days after which a pinned entry referencing a merged PR is
# considered stale and gets an HTML comment marker.
PINNED_STALENESS_DAYS = 30

# Approximate token budget for the entire Pinned Context section. When
# exceeded, a warning comment is added (no auto-deletion).
# This is the sole definition of this constant; session_init.py imports it.
PINNED_CONTEXT_TOKEN_BUDGET = 1200


def _find_existing_claude_md(base: Path) -> Optional[Path]:
    """
    Look for an existing project CLAUDE.md under `base`, honoring both
    supported locations: `.claude/CLAUDE.md` (preferred) then `CLAUDE.md`
    (legacy). Returns the first match or None.
    """
    dot_claude = base / ".claude" / "CLAUDE.md"
    if dot_claude.exists():
        return dot_claude
    legacy = base / "CLAUDE.md"
    if legacy.exists():
        return legacy
    return None


def get_project_claude_md_path() -> Optional[Path]:
    """
    Get the path to the project-level CLAUDE.md.

    Honors both supported locations:
      - $base/.claude/CLAUDE.md  (preferred / new default)
      - $base/CLAUDE.md          (legacy)

    Resolution order for $base:
      1. CLAUDE_PROJECT_DIR env var
      2. Git common-dir parent (worktree-safe; --show-toplevel would return
         the worktree path, which often does not contain CLAUDE.md)
      3. Current working directory

    Returns:
        Path to an existing project CLAUDE.md if found, None otherwise.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        found = _find_existing_claude_md(Path(project_dir))
        if found is not None:
            return found

    # Fallback: detect git root (worktree-safe)
    # Uses --git-common-dir instead of --show-toplevel because the latter
    # returns the worktree path when run inside a worktree, which may not
    # contain CLAUDE.md. --git-common-dir always points to the shared .git
    # directory; its parent is the main repo root where CLAUDE.md lives.
    # NOTE: Twin pattern in skills/pact-memory/scripts/memory_api.py
    #       (_detect_project_id) and working_memory.py (_get_claude_md_path)
    #       -- keep in sync.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            git_common_dir = result.stdout.strip()
            repo_root = Path(git_common_dir).resolve().parent
            found = _find_existing_claude_md(repo_root)
            if found is not None:
                return found
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Last resort: current working directory
    return _find_existing_claude_md(Path.cwd())


# Backward-compatible alias (tests and session_init patch the underscore name)
_get_project_claude_md_path = get_project_claude_md_path


def estimate_tokens(text: str) -> int:
    """
    Estimate token count using word count * 1.3 approximation.

    NOTE: Twin copy exists in working_memory.py (_estimate_tokens) -- keep in sync.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    return int(len(text.split()) * 1.3)


# Backward-compatible alias (tests and session_init import the underscore name)
_estimate_tokens = estimate_tokens


def _parse_pinned_section(content: str) -> Optional[Tuple[int, int, str]]:
    """
    Extract the Pinned Context section from CLAUDE.md content.

    Args:
        content: Full CLAUDE.md file content.

    Returns:
        Tuple of (pinned_start, pinned_end, pinned_content) or None if
        no Pinned Context section exists or it is empty.
    """
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

    return pinned_start, pinned_end, pinned_content


def detect_stale_entries(
    pinned_content: str,
) -> List[Tuple[int, str, str]]:
    """
    Detect stale pinned context entries without modifying them.

    A pinned entry is stale if it contains a date (in a merged-PR reference
    or as a standalone YYYY-MM-DD) older than PINNED_STALENESS_DAYS, and
    has not already been marked with a STALE comment.

    Args:
        pinned_content: The text of the Pinned Context section (after the
            ## heading).

    Returns:
        List of (entry_index, date_string, entry_heading) tuples for each
        stale entry found. entry_index is the position within entry_starts.
    """
    entry_pattern = re.compile(r'^### ', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(pinned_content)]

    if not entry_starts:
        return []

    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(days=PINNED_STALENESS_DAYS)

    # Pattern to match "PR #NNN, merged YYYY-MM-DD" in entry text
    pr_merged_pattern = re.compile(
        r'PR\s*#\d+,?\s*merged\s+(\d{4}-\d{2}-\d{2})'
    )
    # Fallback: any standalone YYYY-MM-DD date in the entry header line
    standalone_date_pattern = re.compile(r'(\d{4}-\d{2}-\d{2})')
    # Pattern to detect existing staleness marker
    stale_marker_pattern = re.compile(r'<!-- STALE: Last relevant \d{4}-\d{2}-\d{2} -->')

    stale_entries: List[Tuple[int, str, str]] = []

    for i, start in enumerate(entry_starts):
        if i + 1 < len(entry_starts):
            end = entry_starts[i + 1]
        else:
            end = len(pinned_content)

        entry_text = pinned_content[start:end]

        # Skip entries already marked stale
        if stale_marker_pattern.search(entry_text):
            continue

        # Extract the heading line for context
        nl_pos = entry_text.find("\n")
        heading = entry_text[:nl_pos] if nl_pos != -1 else entry_text

        # Look for PR merged date first (most specific)
        date_str = None
        pr_match = pr_merged_pattern.search(entry_text)
        if pr_match:
            date_str = pr_match.group(1)
        else:
            # Fallback: find any YYYY-MM-DD date in the heading line
            date_match = standalone_date_pattern.search(heading)
            if date_match:
                date_str = date_match.group(1)

        if not date_str:
            continue

        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if entry_date < stale_threshold:
            stale_entries.append((i, date_str, heading))

    return stale_entries


def apply_staleness_markings(
    content: str,
    pinned_start: int,
    pinned_end: int,
    pinned_content: str,
) -> Tuple[str, int, bool, str]:
    """
    Apply stale markers and budget warnings to pinned content.

    Detects stale entries, inserts STALE markers, and adds a budget
    warning comment if the content exceeds the token budget. Returns the
    modified full file content.

    Args:
        content: Full CLAUDE.md file content.
        pinned_start: Start offset of pinned section body in content.
        pinned_end: End offset of pinned section body in content.
        pinned_content: The pinned section body text.

    Returns:
        Tuple of (new_full_content, stale_count, was_modified, budget_warning_str).
    """
    entry_pattern = re.compile(r'^### ', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(pinned_content)]
    stale_marker_pattern = re.compile(r'<!-- STALE: Last relevant \d{4}-\d{2}-\d{2} -->')

    # Count already-marked entries
    already_stale = 0
    for i, start in enumerate(entry_starts):
        end = entry_starts[i + 1] if i + 1 < len(entry_starts) else len(pinned_content)
        entry_text = pinned_content[start:end]
        if stale_marker_pattern.search(entry_text):
            already_stale += 1

    # Detect new stale entries
    stale_entries = detect_stale_entries(pinned_content)
    modified = False

    # Apply stale markers in reverse order so string offsets remain valid
    for idx, date_str, _heading in reversed(stale_entries):
        start = entry_starts[idx]
        end = entry_starts[idx + 1] if idx + 1 < len(entry_starts) else len(pinned_content)
        entry_text = pinned_content[start:end]

        stale_marker = f"<!-- STALE: Last relevant {date_str} -->\n"
        nl_pos = entry_text.find("\n")
        if nl_pos == -1:
            # Entry is a single line with no newline; skip it
            continue
        heading_end = nl_pos + 1
        new_entry = entry_text[:heading_end] + stale_marker + entry_text[heading_end:]
        pinned_content = pinned_content[:start] + new_entry + pinned_content[end:]
        modified = True

    total_stale = already_stale + len(stale_entries)

    # Check token budget BEFORE inserting the warning (so warning text
    # does not inflate its own count)
    pinned_tokens = estimate_tokens(pinned_content)
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

    new_content = content[:pinned_start] + pinned_content + content[pinned_end:]
    return new_content, total_stale, modified, budget_warning


def check_pinned_staleness(claude_md_path: Optional[Path] = None) -> Optional[str]:
    """
    Detect stale pinned context entries in the project CLAUDE.md.

    A pinned entry is considered stale if it contains a date older than
    PINNED_STALENESS_DAYS. Dates are detected in PR merge references
    (e.g. "PR #123, merged 2026-01-15") and as standalone YYYY-MM-DD
    patterns in entry headings.

    Stale entries get a <!-- STALE: Last relevant YYYY-MM-DD --> comment
    inserted after their heading (if not already marked).

    Also checks if the total pinned content exceeds the token budget and
    adds a warning comment if so (does NOT auto-delete pins).

    This function orchestrates detection (detect_stale_entries) and
    mutation (apply_staleness_markings) as separate steps for testability.

    Args:
        claude_md_path: Explicit path to CLAUDE.md. If None, resolved via
            get_project_claude_md_path(). Callers (e.g. session_init.py)
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

    parsed = _parse_pinned_section(content)
    if parsed is None:
        return None

    pinned_start, pinned_end, pinned_content = parsed

    entry_pattern = re.compile(r'^### ', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(pinned_content)]
    if not entry_starts:
        return None

    new_content, stale_count, modified, budget_warning = apply_staleness_markings(
        content, pinned_start, pinned_end, pinned_content
    )

    # Write back if modified — under file_lock with TOCTOU symlink guard.
    # staleness.py is the 6th writer to project CLAUDE.md and must use the
    # same hardening as the other 5 (claude_md_manager + session_resume).
    # See `fcntl_sidecar_lock_pattern` for the canonical pattern.
    if modified:
        try:
            from shared.claude_md_manager import file_lock
            with file_lock(claude_md_path):
                # Symlink guard INSIDE the lock (TOCTOU defense). is_symlink
                # uses lstat so it does not follow the link. Status string is
                # deliberately opaque — see remove_stale_kernel_block.
                if claude_md_path.is_symlink():
                    return "Pinned staleness skipped: path precondition not met."
                # Re-read inside the lock — a concurrent update_pact_routing
                # or update_session_info may have landed between our outer
                # read at L348 and the lock acquisition. If content changed,
                # skip this pass: the staleness markers are idempotent and
                # the next session will re-detect any stale entries. This
                # avoids clobbering a concurrent writer's SESSION_START block.
                current = claude_md_path.read_text(encoding="utf-8")
                if current != content:
                    return None
                claude_md_path.write_text(new_content, encoding="utf-8")
        except TimeoutError:
            return "Pinned staleness update skipped: lock contention."
        except (IOError, OSError) as e:
            logger_msg = f"Failed to update pinned staleness: {e}"
            return logger_msg

    if stale_count > 0:
        return f"Pinned context: {stale_count} stale pin(s) detected{budget_warning}"
    if budget_warning:
        return f"Pinned context{budget_warning}"

    return None
