"""
Working Memory Sync Module

Location: pact-plugin/skills/pact-memory/scripts/working_memory.py

Summary: Handles synchronization of memories to the Working Memory section
in CLAUDE.md. Maintains a rolling window of the most recent memories for
quick reference during Claude sessions. Applies token budgets to prevent
unbounded growth of memory sections.

Used by:
- memory_api.py: Calls sync_to_claude_md() after saving memories
- Test files: test_working_memory.py tests all functions in this module
"""

import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Configure logging
logger = logging.getLogger(__name__)

# Constants for working memory section (saved memories).
# Working Memory provides structured, PACT-specific context (goals, decisions,
# lessons) synced from the SQLite database. It coexists with the platform's
# auto-memory (MEMORY.md), which captures free-form session learnings. Reduced
# from 5 to 3 entries to limit token overlap between the two systems while
# retaining the structured format that auto-memory does not provide.
WORKING_MEMORY_HEADER = "## Working Memory"
WORKING_MEMORY_COMMENT = "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. Full history searchable via pact-memory skill. -->"
MAX_WORKING_MEMORIES = 3

# Constants for retrieved context section (searched/retrieved memories)
RETRIEVED_CONTEXT_HEADER = "## Retrieved Context"
RETRIEVED_CONTEXT_COMMENT = "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->"
MAX_RETRIEVED_MEMORIES = 3

# Token budget constants.
# Approximation: 1 token ~ 0.75 words, so word_count * 1.3 ~ token count.
WORKING_MEMORY_TOKEN_BUDGET = 800
RETRIEVED_CONTEXT_TOKEN_BUDGET = 500
# Note: PINNED_CONTEXT_TOKEN_BUDGET is defined solely in hooks/staleness.py


def _get_claude_md_path() -> Optional[Path]:
    """
    Get the path to CLAUDE.md in the project root.

    Uses CLAUDE_PROJECT_DIR environment variable if set, then falls back
    to git worktree/repo root detection, then to current working directory.

    Note: This mirrors the resolution strategy in hooks/staleness.py
    (_get_project_claude_md_path). Kept as a local copy because this
    module lives in skills/ and cannot import from hooks/.

    Returns:
        Path to CLAUDE.md if it exists, None otherwise.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        claude_md = Path(project_dir) / "CLAUDE.md"
        if claude_md.exists():
            return claude_md

    # Fallback: detect git root (worktree-safe)
    # Uses --git-common-dir instead of --show-toplevel because the latter
    # returns the worktree path when run inside a worktree, which may not
    # contain CLAUDE.md. --git-common-dir always points to the shared .git
    # directory; its parent is the main repo root where CLAUDE.md lives.
    # NOTE: Twin pattern in memory_api.py (_detect_project_id) and
    #       hooks/staleness.py (get_project_claude_md_path) -- keep in sync.
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
            claude_md = repo_root / "CLAUDE.md"
            if claude_md.exists():
                return claude_md
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Last resort: current working directory
    claude_md = Path.cwd() / "CLAUDE.md"
    if claude_md.exists():
        return claude_md

    return None


def _estimate_tokens(text: str) -> int:
    """
    Estimate token count for a text string.

    Uses word count multiplied by 1.3 as a simple approximation for
    English text. No external tokenizer dependency required.

    NOTE: Twin copy exists in hooks/staleness.py (estimate_tokens) -- keep in sync.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count (integer).
    """
    if not text:
        return 0
    return int(len(text.split()) * 1.3)


def _compress_memory_entry(entry: str) -> str:
    """
    Compress a full memory entry to a single-line summary.

    Preserves the date header and extracts the first sentence from the
    Context field. All other fields (Goal, Decisions, Lessons, Files,
    Memory ID) are dropped.

    Args:
        entry: Full markdown memory entry string starting with ### YYYY-MM-DD.

    Returns:
        Compressed entry with date header and one-line summary.
    """
    lines = entry.strip().split("\n")
    if not lines:
        return entry

    # Preserve the date header line (### YYYY-MM-DD HH:MM)
    date_line = lines[0]

    # Find the Context field and extract its first sentence
    summary_text = ""
    for line in lines[1:]:
        if line.startswith("**Context**:"):
            context_value = line.split("**Context**:", 1)[1].strip()
            # Take first sentence (up to first ". " boundary, or first 120 chars).
            # Uses ". " instead of "." to avoid truncating at version numbers
            # like v2.3.1 or decimal values.
            period_idx = context_value.find(". ")
            if period_idx > 0 and period_idx < 120:
                summary_text = context_value[:period_idx + 1]
            else:
                summary_text = context_value[:120]
                if len(context_value) > 120:
                    summary_text += "..."
            break

    if not summary_text:
        # Fallback: use first non-header line content
        for line in lines[1:]:
            stripped = line.strip()
            if stripped and stripped.startswith("**") and "**:" in stripped:
                # Extract value from any bold field
                summary_text = stripped.split("**:", 1)[1].strip()[:120]
                if len(stripped.split("**:", 1)[1].strip()) > 120:
                    summary_text += "..."
                break

    if summary_text:
        return f"{date_line}\n**Summary**: {summary_text}"
    return date_line


def _apply_token_budget(
    entries: List[str],
    token_budget: int
) -> List[str]:
    """
    Apply a token budget to a list of memory entries.

    Strategy: Keep the newest entry in full. Compress older entries to
    single-line summaries. If still over budget, reduce the number of
    entries shown.

    Args:
        entries: List of memory entry strings (newest first).
        token_budget: Maximum estimated tokens for all entries combined.

    Returns:
        List of entries (some possibly compressed) fitting within budget.
    """
    if not entries:
        return entries

    # Check if already within budget
    total_tokens = sum(_estimate_tokens(e) for e in entries)
    if total_tokens <= token_budget:
        return entries

    # Strategy: keep newest entry full, compress the rest
    result = [entries[0]]
    for entry in entries[1:]:
        compressed = _compress_memory_entry(entry)
        result.append(compressed)

    # Check if compressed version fits
    total_tokens = sum(_estimate_tokens(e) for e in result)
    if total_tokens <= token_budget:
        return result

    # Still over budget: drop entries from the end until we fit.
    # Subtract the popped entry's tokens instead of recalculating the full sum.
    while len(result) > 1 and total_tokens > token_budget:
        removed = result.pop()
        total_tokens -= _estimate_tokens(removed)

    return result


def _format_memory_entry(
    memory: Dict[str, Any],
    files: Optional[List[str]] = None,
    memory_id: Optional[str] = None
) -> str:
    """
    Format a memory as a markdown entry for CLAUDE.md.

    Args:
        memory: Memory dictionary with context, goal, decisions, etc.
        files: Optional list of file paths associated with this memory.
        memory_id: Optional memory ID to include for database reference.

    Returns:
        Formatted markdown string for the memory entry.
    """
    # Get date and time for header
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    lines = [f"### {date_str}"]

    # Add context if present
    if memory.get("context"):
        lines.append(f"**Context**: {memory['context']}")

    # Add goal if present
    if memory.get("goal"):
        lines.append(f"**Goal**: {memory['goal']}")

    # Add decisions if present
    decisions = memory.get("decisions")
    if decisions:
        if isinstance(decisions, list):
            # Extract decision text from list of dicts or strings
            decision_texts = []
            for d in decisions:
                if isinstance(d, dict):
                    decision_texts.append(d.get("decision", str(d)))
                else:
                    decision_texts.append(str(d))
            if decision_texts:
                lines.append(f"**Decisions**: {', '.join(decision_texts)}")
        elif isinstance(decisions, str):
            lines.append(f"**Decisions**: {decisions}")

    # Add lessons if present
    lessons = memory.get("lessons_learned")
    if lessons:
        if isinstance(lessons, list) and lessons:
            lines.append(f"**Lessons**: {', '.join(str(l) for l in lessons)}")
        elif isinstance(lessons, str):
            lines.append(f"**Lessons**: {lessons}")

    # Add files if present
    if files:
        lines.append(f"**Files**: {', '.join(files)}")

    # Add memory ID if provided
    if memory_id:
        lines.append(f"**Memory ID**: {memory_id}")

    return "\n".join(lines)


def _parse_working_memory_section(
    content: str
) -> Tuple[str, str, str, List[str]]:
    """
    Parse CLAUDE.md content to extract working memory section.

    Args:
        content: Full CLAUDE.md file content.

    Returns:
        Tuple of (before_section, section_header_with_comment, after_section, existing_entries)
        where existing_entries is a list of individual memory entry strings.
    """
    # Pattern to find the Working Memory section
    # Match ## Working Memory followed by optional comment and entries
    section_pattern = re.compile(
        r'^(## Working Memory)\s*\n'
        r'(<!-- [^>]*-->)?\s*\n?',
        re.MULTILINE
    )

    match = section_pattern.search(content)

    if not match:
        # Section doesn't exist
        return content, "", "", []

    section_start = match.start()
    section_header_end = match.end()

    # Find where the next ## section starts (end of working memory section)
    # Also stop at H1 (#), other H2 (##), or horizontal rules (---) to protect footers
    next_section_pattern = re.compile(r'^(#\s|##\s(?!Working Memory)|---)', re.MULTILINE)
    next_match = next_section_pattern.search(content, section_header_end)

    if next_match:
        section_end = next_match.start()
    else:
        section_end = len(content)

    before_section = content[:section_start]
    section_content = content[section_header_end:section_end].strip()
    after_section = content[section_end:]

    # Parse existing entries (each starts with ### YYYY-MM-DD)
    entry_pattern = re.compile(r'^### \d{4}-\d{2}-\d{2}', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(section_content)]

    existing_entries = []
    for i, start in enumerate(entry_starts):
        if i + 1 < len(entry_starts):
            entry = section_content[start:entry_starts[i + 1]].strip()
        else:
            entry = section_content[start:].strip()
        existing_entries.append(entry)

    return before_section, WORKING_MEMORY_HEADER, after_section, existing_entries


def sync_to_claude_md(
    memory: Dict[str, Any],
    files: Optional[List[str]] = None,
    memory_id: Optional[str] = None
) -> bool:
    """
    Sync a memory entry to the Working Memory section of CLAUDE.md.

    Maintains a rolling window of the last 3 memories. New entries are added
    at the top of the section, and entries beyond MAX_WORKING_MEMORIES are removed.

    This function is designed for graceful degradation - if CLAUDE.md doesn't
    exist or the sync fails for any reason, it logs a warning but doesn't
    raise an exception.

    Args:
        memory: Memory dictionary with context, goal, decisions, lessons_learned, etc.
        files: Optional list of file paths associated with this memory.
        memory_id: Optional memory ID to include for database reference.

    Returns:
        True if sync succeeded, False otherwise.
    """
    claude_md_path = _get_claude_md_path()

    if claude_md_path is None:
        logger.debug("CLAUDE.md not found, skipping working memory sync")
        return False

    try:
        # Read current content
        content = claude_md_path.read_text(encoding="utf-8")

        # Parse existing working memory section
        before_section, section_header, after_section, existing_entries = \
            _parse_working_memory_section(content)

        # Format new memory entry
        new_entry = _format_memory_entry(memory, files, memory_id)

        # Build new entries list: new entry first, then existing (up to max - 1)
        all_entries = [new_entry] + existing_entries
        trimmed_entries = all_entries[:MAX_WORKING_MEMORIES]

        # Apply token budget: compress older entries if over budget
        trimmed_entries = _apply_token_budget(
            trimmed_entries, WORKING_MEMORY_TOKEN_BUDGET
        )

        # Build new section content
        section_lines = [
            WORKING_MEMORY_HEADER,
            WORKING_MEMORY_COMMENT,
            ""  # Blank line after comment
        ]
        for entry in trimmed_entries:
            section_lines.append(entry)
            section_lines.append("")  # Blank line between entries

        section_text = "\n".join(section_lines)

        # Reconstruct file content
        if section_header:
            # Section existed, replace it
            new_content = before_section + section_text + after_section
        else:
            # Section didn't exist, append at end
            if not content.endswith("\n"):
                content += "\n"
            new_content = content + "\n" + section_text

        # Write back to file
        claude_md_path.write_text(new_content, encoding="utf-8")

        logger.info("Synced memory to CLAUDE.md Working Memory section")
        return True

    except Exception as e:
        logger.warning(f"Failed to sync memory to CLAUDE.md: {e}")
        return False


def _parse_retrieved_context_section(
    content: str
) -> Tuple[str, str, str, List[str]]:
    """
    Parse CLAUDE.md content to extract retrieved context section.

    Args:
        content: Full CLAUDE.md file content.

    Returns:
        Tuple of (before_section, section_header, after_section, existing_entries)
        where existing_entries is a list of individual memory entry strings.
    """
    # Pattern to find the Retrieved Context section
    section_pattern = re.compile(
        r'^(## Retrieved Context)\s*\n'
        r'(<!-- [^>]*-->)?\s*\n?',
        re.MULTILINE
    )

    match = section_pattern.search(content)

    if not match:
        # Section doesn't exist
        return content, "", "", []

    section_start = match.start()
    section_header_end = match.end()

    # Find where the next ## section starts (end of retrieved context section)
    # Also stop at H1 (#), other H2 (##), or horizontal rules (---) to protect footers
    next_section_pattern = re.compile(r'^(#\s|##\s(?!Retrieved Context)|---)', re.MULTILINE)
    next_match = next_section_pattern.search(content, section_header_end)

    if next_match:
        section_end = next_match.start()
    else:
        section_end = len(content)

    before_section = content[:section_start]
    section_content = content[section_header_end:section_end].strip()
    after_section = content[section_end:]

    # Parse existing entries (each starts with ### YYYY-MM-DD)
    entry_pattern = re.compile(r'^### \d{4}-\d{2}-\d{2}', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(section_content)]

    existing_entries = []
    for i, start in enumerate(entry_starts):
        if i + 1 < len(entry_starts):
            entry = section_content[start:entry_starts[i + 1]].strip()
        else:
            entry = section_content[start:].strip()
        existing_entries.append(entry)

    return before_section, RETRIEVED_CONTEXT_HEADER, after_section, existing_entries


def _format_retrieved_entry(
    memory: Dict[str, Any],
    query: str,
    score: Optional[float] = None,
    memory_id: Optional[str] = None
) -> str:
    """
    Format a retrieved memory as a markdown entry for CLAUDE.md.

    Args:
        memory: Memory dictionary with context, goal, decisions, etc.
        query: The search query that retrieved this memory.
        score: Optional similarity score.
        memory_id: Optional memory ID for reference.

    Returns:
        Formatted markdown string for the retrieved entry.
    """
    # Get date and time for header
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    lines = [f"### {date_str}"]
    lines.append(f"**Query**: \"{query}\"")

    if score is not None:
        lines.append(f"**Relevance**: {score:.2f}")

    # Add context if present
    if memory.get("context"):
        # Truncate long context for display
        context = memory['context']
        if len(context) > 200:
            context = context[:197] + "..."
        lines.append(f"**Context**: {context}")

    # Add goal if present
    if memory.get("goal"):
        lines.append(f"**Goal**: {memory['goal']}")

    # Add memory ID if provided
    if memory_id:
        lines.append(f"**Memory ID**: {memory_id}")

    return "\n".join(lines)


def sync_retrieved_to_claude_md(
    memories: List[Dict[str, Any]],
    query: str,
    scores: Optional[List[float]] = None,
    memory_ids: Optional[List[str]] = None
) -> bool:
    """
    Sync retrieved memories to the Retrieved Context section of CLAUDE.md.

    Maintains a rolling window of the last 3 retrieved memories. New entries
    are added at the top of the section, and entries beyond MAX_RETRIEVED_MEMORIES
    are removed.

    Args:
        memories: List of memory dictionaries that were retrieved.
        query: The search query used.
        scores: Optional list of similarity scores (same order as memories).
        memory_ids: Optional list of memory IDs (same order as memories).

    Returns:
        True if sync succeeded, False otherwise.
    """
    if not memories:
        return False

    claude_md_path = _get_claude_md_path()

    if claude_md_path is None:
        logger.debug("CLAUDE.md not found, skipping retrieved context sync")
        return False

    try:
        # Read current content
        content = claude_md_path.read_text(encoding="utf-8")

        # Parse existing retrieved context section
        before_section, section_header, after_section, existing_entries = \
            _parse_retrieved_context_section(content)

        # Format new entries (only the top result to avoid clutter)
        new_entries = []
        top_memory = memories[0]
        score = scores[0] if scores else None
        memory_id = memory_ids[0] if memory_ids else None
        new_entry = _format_retrieved_entry(top_memory, query, score, memory_id)
        new_entries.append(new_entry)

        # Build new entries list: new entry first, then existing (up to max - 1)
        all_entries = new_entries + existing_entries
        trimmed_entries = all_entries[:MAX_RETRIEVED_MEMORIES]

        # Apply token budget: reduce entry count if over budget.
        # Retrieved entries are already compact (~200 chars each), drop oldest rather than compress.
        # Subtract the popped entry's tokens instead of recalculating the full sum.
        total_tokens = sum(_estimate_tokens(e) for e in trimmed_entries)
        while len(trimmed_entries) > 1 and total_tokens > RETRIEVED_CONTEXT_TOKEN_BUDGET:
            removed = trimmed_entries.pop()
            total_tokens -= _estimate_tokens(removed)

        # Build new section content
        section_lines = [
            RETRIEVED_CONTEXT_HEADER,
            RETRIEVED_CONTEXT_COMMENT,
            ""  # Blank line after comment
        ]
        for entry in trimmed_entries:
            section_lines.append(entry)
            section_lines.append("")  # Blank line between entries

        section_text = "\n".join(section_lines)

        # Reconstruct file content
        if section_header:
            # Section existed, replace it
            # Ensure blank line before next section
            if after_section and not after_section.startswith("\n"):
                new_content = before_section + section_text + "\n" + after_section
            else:
                new_content = before_section + section_text + after_section
        else:
            # Section didn't exist, insert before Working Memory if it exists
            working_memory_match = re.search(
                r'^## Working Memory',
                content,
                re.MULTILINE
            )
            if working_memory_match:
                # Insert before Working Memory with blank line
                insert_pos = working_memory_match.start()
                new_content = content[:insert_pos] + section_text + "\n" + content[insert_pos:]
            else:
                # Append at end
                if not content.endswith("\n"):
                    content += "\n"
                new_content = content + "\n" + section_text

        # Write back to file
        claude_md_path.write_text(new_content, encoding="utf-8")

        logger.info("Synced retrieved memories to CLAUDE.md Retrieved Context section")
        return True

    except Exception as e:
        logger.warning(f"Failed to sync retrieved memories to CLAUDE.md: {e}")
        return False
