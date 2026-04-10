"""
Location: pact-plugin/hooks/shared/claude_md_manager.py
Summary: CLAUDE.md file manipulation for PACT environment setup.
Used by: session_init.py during SessionStart hook to migrate any obsolete
         kernel block out of the home CLAUDE.md and to keep the project
         CLAUDE.md routing block canonical.

Manages two CLAUDE.md files:
1. ~/.claude/CLAUDE.md -- one-time migration: strip the obsolete
   PACT_START/PACT_END kernel block left over from prior plugin versions.
2. {project}/CLAUDE.md -- project-level file (at .claude/CLAUDE.md preferred,
   or legacy ./CLAUDE.md) with memory sections and the PACT_ROUTING block.

Project CLAUDE.md location resolution:
Claude Code supports two locations for project-level memory:
  - $CLAUDE_PROJECT_DIR/.claude/CLAUDE.md  (preferred / new default)
  - $CLAUDE_PROJECT_DIR/CLAUDE.md          (legacy)
The resolve_project_claude_md_path() helper picks whichever exists, with
.claude/CLAUDE.md taking priority. When neither exists, it returns the new
default path so creators land at the preferred location.
"""

import os
import re
from pathlib import Path

# Project-level CLAUDE.md is preferred at .claude/CLAUDE.md (the new default)
# but Claude Code also accepts ./CLAUDE.md for backwards compatibility.
_DOT_CLAUDE_RELATIVE = Path(".claude") / "CLAUDE.md"
_LEGACY_RELATIVE = Path("CLAUDE.md")

_ROUTING_START_MARKER = "<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->"
_ROUTING_END_MARKER = "<!-- PACT_ROUTING_END -->"

_PACT_ROUTING_BLOCK = """<!-- PACT_ROUTING_START: Managed by pact-plugin - do not edit this block -->
## PACT Routing

Before any other work, determine your PACT role and invoke the appropriate
bootstrap skill. Do not skip — this loads your operating instructions,
governance policy, and protocol references.

Check your context for a `PACT ROLE:` marker:
- `PACT ROLE: orchestrator` → invoke `Skill("PACT:bootstrap")` unless already loaded.
- `PACT ROLE: teammate (...)` → invoke `Skill("PACT:teammate-bootstrap")` unless already loaded.

No marker present? Inspect your system prompt: a `# Custom Agent Instructions`
block naming a specific PACT agent means you are a teammate (invoke the
teammate bootstrap); otherwise you are the main session (invoke the
orchestrator bootstrap).

Re-invoke after compaction if the bootstrap content is no longer present.
<!-- PACT_ROUTING_END -->"""


def resolve_project_claude_md_path(
    project_dir: str | os.PathLike[str],
) -> tuple[Path, str]:
    """
    Resolve the project-level CLAUDE.md path with dual-location support.

    Detection priority:
      1. $project_dir/.claude/CLAUDE.md   -> ("dot_claude", existing)
      2. $project_dir/CLAUDE.md           -> ("legacy", existing)
      3. Neither exists                    -> ("new_default", .claude/CLAUDE.md)

    Callers that only read use the returned Path directly. Callers that
    create the file use the source string to know whether they need to
    `mkdir` the `.claude/` parent directory first.

    Args:
        project_dir: The CLAUDE_PROJECT_DIR root.

    Returns:
        Tuple of (path, source) where source is one of:
          - "dot_claude": existing .claude/CLAUDE.md
          - "legacy": existing ./CLAUDE.md
          - "new_default": neither exists; path points to .claude/CLAUDE.md
            so a creator can write to the preferred location.
    """
    base = Path(project_dir)
    dot_claude = base / _DOT_CLAUDE_RELATIVE
    legacy = base / _LEGACY_RELATIVE

    if dot_claude.exists():
        return dot_claude, "dot_claude"
    if legacy.exists():
        return legacy, "legacy"
    return dot_claude, "new_default"


def ensure_dot_claude_parent(path: Path) -> None:
    """
    Ensure the parent directory of a `.claude/CLAUDE.md` path exists.

    No-op when the parent already exists. Creates the directory with mode
    0o700 to match the rest of the PACT plugin's secure-by-default file
    permissions. Safe to call for any CLAUDE.md path -- if the parent is
    not a `.claude` dir, this is just an existence check.

    Args:
        path: The target CLAUDE.md path (e.g. /proj/.claude/CLAUDE.md).
    """
    parent = path.parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def remove_stale_kernel_block() -> str | None:
    """
    One-time migration: remove the obsolete PACT_START/PACT_END block from
    ~/.claude/CLAUDE.md if present. Preserves all user content outside the
    markers.

    Handles the transition from the PR #390 kernel-in-home-dir architecture
    to the kernel-elimination architecture. Existing installations will have
    a PACT_START/PACT_END delimited block in ~/.claude/CLAUDE.md from previous
    plugin versions; this function strips that block cleanly.

    Called from session_init.py on every SessionStart. Idempotent no-op when
    the markers are absent (i.e., for fresh installs or after first cleanup).

    Returns:
        Status message on successful removal, None on no-op.
    """
    target_file = Path.home() / ".claude" / "CLAUDE.md"
    if not target_file.exists():
        return None

    try:
        content = target_file.read_text(encoding="utf-8")
    except OSError:
        return None

    START_MARKER = "<!-- PACT_START:"
    END_MARKER = "<!-- PACT_END -->"

    if START_MARKER not in content or END_MARKER not in content:
        return None

    pre_marker, rest = content.split(START_MARKER, 1)
    if END_MARKER not in rest:
        return None  # Malformed — leave alone to avoid data loss

    _, post_marker = rest.split(END_MARKER, 1)

    # Normalize whitespace around the removal point
    new_content = pre_marker.rstrip() + "\n" + post_marker.lstrip()

    try:
        target_file.write_text(new_content, encoding="utf-8")
        os.chmod(str(target_file), 0o600)
        return "Removed obsolete PACT kernel block from ~/.claude/CLAUDE.md"
    except OSError as e:
        return f"Failed to remove stale kernel block: {str(e)[:50]}"


def update_pact_routing() -> str | None:
    """
    Ensure the project CLAUDE.md contains the canonical PACT_ROUTING block.

    Idempotent: on every SessionStart, find the PACT_ROUTING_START/PACT_ROUTING_END
    markers in the project CLAUDE.md and replace content between them with the
    canonical routing block. If markers are absent, insert the block near the
    top of the file below the title. If the file doesn't exist, defer to
    ensure_project_memory_md() which creates it with the block in its template.

    Preserves all user content outside the markers.

    Returns:
        Status message on change, None when no write was needed.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file, source = resolve_project_claude_md_path(project_dir)

    if source == "new_default":
        # File doesn't exist yet; ensure_project_memory_md() will create it
        # with the routing block in its template.
        return None

    try:
        content = target_file.read_text(encoding="utf-8")
    except OSError:
        return None

    # Case 1: markers present — replace content between them
    if _ROUTING_START_MARKER in content and _ROUTING_END_MARKER in content:
        pattern = re.compile(
            re.escape(_ROUTING_START_MARKER) + r".*?" + re.escape(_ROUTING_END_MARKER),
            re.DOTALL,
        )
        new_content = pattern.sub(_PACT_ROUTING_BLOCK, content)

        if new_content == content:
            return None  # Already canonical

        try:
            target_file.write_text(new_content, encoding="utf-8")
            os.chmod(str(target_file), 0o600)
            return "PACT routing block updated in project CLAUDE.md"
        except OSError as e:
            return f"Failed to update PACT routing: {str(e)[:50]}"

    # Case 2: markers absent — insert near the top of the file below the title
    lines = content.splitlines(keepends=True)
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            insert_idx = i + 1
            # Skip any blank lines or short description lines immediately
            # after the title before inserting.
            for j in range(i + 1, min(i + 6, len(lines))):
                if lines[j].startswith("##") or lines[j].startswith("<!--"):
                    insert_idx = j
                    break
                insert_idx = j + 1
            break

    new_lines = (
        lines[:insert_idx]
        + ["\n", _PACT_ROUTING_BLOCK + "\n", "\n"]
        + lines[insert_idx:]
    )
    new_content = "".join(new_lines)

    try:
        target_file.write_text(new_content, encoding="utf-8")
        os.chmod(str(target_file), 0o600)
        return "PACT routing block inserted into project CLAUDE.md"
    except OSError as e:
        return f"Failed to insert PACT routing: {str(e)[:50]}"


def ensure_project_memory_md() -> str | None:
    """
    Ensure project has a CLAUDE.md with memory sections.

    Creates a minimal project-level CLAUDE.md containing only the memory
    sections (Retrieved Context, Working Memory) if one doesn't exist.
    These sections are project-specific and managed by the pact-memory skill.

    Honors both supported project CLAUDE.md locations:
      - $CLAUDE_PROJECT_DIR/.claude/CLAUDE.md  (preferred / new default)
      - $CLAUDE_PROJECT_DIR/CLAUDE.md          (legacy)
    If either exists, no action is taken (preserves existing project
    configuration). When neither exists, creates the file at the preferred
    `.claude/CLAUDE.md` location, creating the `.claude/` directory if needed.

    Returns:
        Status message or None if no action taken.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file, source = resolve_project_claude_md_path(project_dir)

    # Don't overwrite existing project CLAUDE.md (either location)
    if source != "new_default":
        return None

    # Create minimal CLAUDE.md with memory sections at the new default location
    memory_template = f"""# Project Memory

This file contains project-specific memory managed by the PACT framework.

{_PACT_ROUTING_BLOCK}

<!-- SESSION_START -->
## Current Session
<!-- Auto-managed by session_init hook. Overwritten each session. -->
<!-- SESSION_END -->

## Retrieved Context
<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->

## Working Memory
<!-- Auto-managed by pact-memory skill. Last 3 memories shown. Full history searchable via pact-memory skill. -->
"""

    try:
        ensure_dot_claude_parent(target_file)
        target_file.write_text(memory_template, encoding="utf-8")
        os.chmod(str(target_file), 0o600)
        return "Created project CLAUDE.md with memory sections"
    except Exception as e:
        return f"Project CLAUDE.md failed: {str(e)[:30]}"
