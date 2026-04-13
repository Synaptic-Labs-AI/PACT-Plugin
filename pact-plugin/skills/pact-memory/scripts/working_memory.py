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

# Plugin-managed HTML comment boundary prefixes. Used by _parse_working_memory_section
# and _parse_retrieved_context_section to terminate scans on any PACT-managed
# boundary marker. Extracted (round 5, item 1) so the three-prefix union is
# defined once within this file.
#
# TWIN COPY: The canonical definition lives in
# pact-plugin/hooks/shared/claude_md_manager.py as PACT_BOUNDARY_PREFIXES.
# This module cannot cleanly import from hooks/shared/ (skills/pact-memory/scripts/
# is a separate package), so we duplicate the tuple here and rely on a
# drift-detection test (test_working_memory_parser.test_boundary_prefixes_in_sync)
# to assert the two tuples stay identical. This is the same pattern as the
# _estimate_tokens twin (see line 130).
_PACT_BOUNDARY_PREFIXES: tuple = (
    "PACT_MEMORY_",
    "PACT_MANAGED_",
    "PACT_ROUTING_",
)
# Regex alternation used at the three parser sites below.
_PACT_BOUNDARY_ALT = "|".join(_PACT_BOUNDARY_PREFIXES)


def _find_existing_claude_md(base: Path) -> Optional[Path]:
    """
    Return the first existing CLAUDE.md under `base`, checking both
    supported locations in priority order.

    Claude Code accepts project memory at either `.claude/CLAUDE.md` (new
    default) or `./CLAUDE.md` (legacy). This helper checks `.claude/CLAUDE.md`
    first, then falls back to `./CLAUDE.md`, returning the first match or
    None if neither exists.

    Args:
        base: Directory to probe for CLAUDE.md.

    Returns:
        Path to the existing CLAUDE.md, or None if neither location exists.
    """
    dot_claude = base / ".claude" / "CLAUDE.md"
    if dot_claude.exists():
        return dot_claude
    legacy = base / "CLAUDE.md"
    if legacy.exists():
        return legacy
    return None


def _get_claude_md_path() -> Optional[Path]:
    """
    Get the path to CLAUDE.md in the project root.

    Uses CLAUDE_PROJECT_DIR environment variable if set, then falls back
    to git worktree/repo root detection, then to current working directory.
    At each level, checks both `.claude/CLAUDE.md` (new default) and
    `./CLAUDE.md` (legacy) in priority order.

    Note: This mirrors the resolution strategy in hooks/staleness.py
    (get_project_claude_md_path). Kept as a local copy because this
    module lives in skills/ and cannot import from hooks/.

    Returns:
        Path to CLAUDE.md if it exists, None otherwise.
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
            found = _find_existing_claude_md(repo_root)
            if found is not None:
                return found
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Last resort: current working directory
    return _find_existing_claude_md(Path.cwd())


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

    # Add reasoning chains if present
    reasoning = memory.get("reasoning_chains")
    if reasoning:
        if isinstance(reasoning, list) and reasoning:
            lines.append(f"**Reasoning chains**: {', '.join(str(r) for r in reasoning)}")
        elif isinstance(reasoning, str):
            lines.append(f"**Reasoning chains**: {reasoning}")

    # Add agreements if present
    agreements = memory.get("agreements_reached")
    if agreements:
        if isinstance(agreements, list) and agreements:
            lines.append(f"**Agreements**: {', '.join(str(a) for a in agreements)}")
        elif isinstance(agreements, str):
            lines.append(f"**Agreements**: {agreements}")

    # Add disagreements resolved if present
    disagreements = memory.get("disagreements_resolved")
    if disagreements:
        if isinstance(disagreements, list) and disagreements:
            lines.append(f"**Disagreements resolved**: {', '.join(str(d) for d in disagreements)}")
        elif isinstance(disagreements, str):
            lines.append(f"**Disagreements resolved**: {disagreements}")

    # Add files if present
    if files:
        lines.append(f"**Files**: {', '.join(files)}")

    # Add memory ID if provided
    if memory_id:
        lines.append(f"**Memory ID**: {memory_id}")

    return "\n".join(lines)


def _find_terminator_offset(
    content: str,
    start: int,
    terminator_pattern: "re.Pattern[str]",
    in_code_fence: bool = False,
) -> int:
    """
    Fence-aware line walker that finds the absolute offset of the first line
    in `content[start:]` that matches `terminator_pattern`, skipping lines
    inside fenced code blocks (backtick or tilde, per CommonMark §4.5).

    Markdown fenced code blocks open and close with a line whose stripped
    form starts with ``` or ~~~ (optionally followed by a language tag).
    We track the two fence types as INDEPENDENT states and suppress
    terminator matches while inside EITHER. This prevents a user-authored
    fenced block from prematurely terminating the section — a concern for
    Working Memory and Retrieved Context, which may contain example
    markdown with boundary-marker-like HTML comments.

    Round 8 item 1: tilde-fence support. Pre-round-8 the walker recognized
    only backtick fences (```). A ~~~-wrapped example containing a PACT
    boundary marker would terminate the section inside the fence, eating
    the user's example. Tracking tilde fences as a second independent
    state fixes this: a ``` line inside a ~~~ fence is fence body (not a
    toggle), and vice versa.

    Args:
        content: Full CLAUDE.md file content.
        start: Absolute offset in `content` where scanning begins.
        terminator_pattern: Compiled regex (without MULTILINE — we match
            against individual stripped lines using `.match`). The caller
            provides a pattern that would match on its own line.
        in_code_fence: Initial fence state. Callers who start scanning
            mid-document after another parser has already consumed up to
            `start` should pass the fence state at that boundary; callers
            who start at a safe point (after a ## heading + optional
            comment) can pass False — section headers cannot appear inside
            a fence anyway. When True, the caller is assumed to be inside
            a backtick fence (the pre-round-8 default); tilde-specific
            seeding can be added if a future caller needs it.

    Returns:
        Absolute offset in `content` of the first line in `content[start:]`
        that (a) is not inside a code fence and (b) matches
        `terminator_pattern`. Returns `len(content)` if no terminator is
        found — the caller should treat that as "scan to EOF".
    """
    pos = start
    in_backtick_fence = in_code_fence
    in_tilde_fence = False
    while pos < len(content):
        nl = content.find("\n", pos)
        if nl == -1:
            line = content[pos:]
            line_end = len(content)
        else:
            line = content[pos:nl]
            line_end = nl + 1

        stripped = line.lstrip()
        is_backtick_fence_line = stripped.startswith("```")
        is_tilde_fence_line = stripped.startswith("~~~")

        if is_backtick_fence_line and not in_tilde_fence:
            in_backtick_fence = not in_backtick_fence
        elif is_tilde_fence_line and not in_backtick_fence:
            in_tilde_fence = not in_tilde_fence
        elif not (in_backtick_fence or in_tilde_fence) and terminator_pattern.match(line):
            return pos

        pos = line_end

    return len(content)


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
    # Match ## Working Memory followed by optional comment and entries.
    # Negative lookahead excludes the three plugin-managed boundary prefixes
    # (_PACT_BOUNDARY_PREFIXES) from being consumed as the auto-managed
    # comment — otherwise an empty Working Memory section followed
    # immediately by <!-- PACT_MEMORY_END --> would greedily swallow the
    # marker into section_header_end, and the marker would be lost on
    # write-back (#404). Using the specific prefixes (not a broad ^PACT_)
    # keeps the guard load-bearing to the markers we actually emit, so a
    # hypothetical user comment like <!-- PACT_note: user-owned --> still
    # parses as an auto-managed comment without breaking.
    section_pattern = re.compile(
        r'^(## Working Memory)\s*\n'
        rf'(<!-- (?!(?:{_PACT_BOUNDARY_ALT}))[^>]*-->)?\s*\n?',
        re.MULTILINE
    )

    match = section_pattern.search(content)

    if not match:
        # Section doesn't exist
        return content, "", "", []

    section_start = match.start()
    section_header_end = match.end()

    # Find where the next ## section starts (end of working memory section)
    # Also stop at H1 (#), other H2 (##), horizontal rules (---), or any
    # plugin-managed boundary marker (_PACT_BOUNDARY_PREFIXES) to prevent
    # silent marker erosion (#404).
    #
    # Fence-aware line walker (round 5, item 4): scan line-by-line so that a
    # fenced block containing a terminator-like line (e.g. an example H2 or
    # a quoted boundary marker) does NOT prematurely terminate the section.
    # The terminator regex is the same alternation as before but without the
    # `^` anchor — we match against individual lines via `.match`.
    next_section_pattern = re.compile(
        rf'(#\s|##\s(?!Working Memory)|---|<!-- (?:{_PACT_BOUNDARY_ALT}))',
    )
    section_end = _find_terminator_offset(
        content, section_header_end, next_section_pattern
    )

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
        os.chmod(str(claude_md_path), 0o600)

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
    # Pattern to find the Retrieved Context section.
    # Negative lookahead narrows to the plugin-managed boundary prefixes
    # (_PACT_BOUNDARY_PREFIXES) — see _parse_working_memory_section
    # for the full rationale (#404).
    section_pattern = re.compile(
        r'^(## Retrieved Context)\s*\n'
        rf'(<!-- (?!(?:{_PACT_BOUNDARY_ALT}))[^>]*-->)?\s*\n?',
        re.MULTILINE
    )

    match = section_pattern.search(content)

    if not match:
        # Section doesn't exist
        return content, "", "", []

    section_start = match.start()
    section_header_end = match.end()

    # Find where the next ## section starts (end of retrieved context section)
    # Also stop at H1 (#), other H2 (##), horizontal rules (---), or any
    # plugin-managed boundary marker (_PACT_BOUNDARY_PREFIXES) to prevent
    # silent marker erosion (#404).
    #
    # Fence-aware line walker (round 5, item 4): see _parse_working_memory_section
    # for rationale. Terminator regex uses no `^` anchor; matched against
    # individual lines via `_find_terminator_offset`.
    next_section_pattern = re.compile(
        rf'(#\s|##\s(?!Retrieved Context)|---|<!-- (?:{_PACT_BOUNDARY_ALT}))',
    )
    section_end = _find_terminator_offset(
        content, section_header_end, next_section_pattern
    )

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
        os.chmod(str(claude_md_path), 0o600)

        logger.info("Synced retrieved memories to CLAUDE.md Retrieved Context section")
        return True

    except Exception as e:
        logger.warning(f"Failed to sync retrieved memories to CLAUDE.md: {e}")
        return False
