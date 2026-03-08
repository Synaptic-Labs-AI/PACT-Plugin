"""
Location: pact-plugin/hooks/shared/claude_md_manager.py
Summary: CLAUDE.md file manipulation for PACT environment setup.
Used by: session_init.py during SessionStart hook to install/update
         the PACT Orchestrator prompt and ensure project memory sections.

Manages two CLAUDE.md files:
1. ~/.claude/CLAUDE.md -- global user config with PACT Orchestrator prompt
2. {project}/CLAUDE.md -- project-level file with memory sections
"""

import os
from pathlib import Path


def update_claude_md() -> str | None:
    """
    Update ~/.claude/CLAUDE.md with PACT content.

    Automatically merges or updates the PACT Orchestrator prompt in the user's
    CLAUDE.md file. Uses explicit markers to manage the PACT section without
    disturbing other user customizations.

    Strategy:
    1. If file missing -> create with PACT content in markers.
    2. If markers found -> replace content between markers.
    3. If no markers but "PACT Orchestrator" found -> assume manual install, warn.
    4. If no markers and no conflict -> append PACT content with markers.

    Returns:
        Status message or None if no change.
    """
    plugin_root_str = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not plugin_root_str:
        return None

    plugin_root = Path(plugin_root_str)
    if not plugin_root.exists():
        return None

    source_file = plugin_root / "CLAUDE.md"
    if not source_file.exists():
        return None

    target_file = Path.home() / ".claude" / "CLAUDE.md"

    START_MARKER = "<!-- PACT_START: Managed by pact-plugin - Do not edit this block -->"
    END_MARKER = "<!-- PACT_END -->"

    try:
        source_content = source_file.read_text(encoding="utf-8")
        wrapped_source = f"{START_MARKER}\n{source_content}\n{END_MARKER}"

        # Case 1: Target doesn't exist
        if not target_file.exists():
            target_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target_file.write_text(wrapped_source, encoding="utf-8")
            os.chmod(str(target_file), 0o600)
            return "Created CLAUDE.md with PACT Orchestrator"

        target_content = target_file.read_text(encoding="utf-8")

        # Case 2: Markers found - update if changed
        if START_MARKER in target_content and END_MARKER in target_content:
            parts = target_content.split(START_MARKER)
            pre = parts[0]
            # Handle case where multiple markers might exist (take first and last valid)
            # but usually just one block.
            rest = parts[1]
            if END_MARKER in rest:
                post = rest.split(END_MARKER, 1)[1]
                new_full_content = f"{pre}{wrapped_source}{post}"

                if new_full_content != target_content:
                    target_file.write_text(new_full_content, encoding="utf-8")
                    os.chmod(str(target_file), 0o600)
                    return "PACT Orchestrator updated"
                return None

        # Case 3: No markers but content similar to PACT found
        if "PACT Orchestrator" in target_content:
            # Check if it looks roughly like what we expect, or just leave it
            # Returning a message prompts the user to check it
            return "PACT present but unmanaged (add markers to auto-update)"

        # Case 4: No markers, no specific PACT content -> Append
        # Ensure we append on a new line
        if not target_content.endswith("\n"):
            target_content += "\n"

        new_content = f"{target_content}\n{wrapped_source}"
        target_file.write_text(new_content, encoding="utf-8")
        os.chmod(str(target_file), 0o600)
        return "PACT Orchestrator added to CLAUDE.md"

    except Exception as e:
        return f"PACT update failed: {str(e)[:30]}"


def ensure_project_memory_md() -> str | None:
    """
    Ensure project has a CLAUDE.md with memory sections.

    Creates a minimal project-level CLAUDE.md containing only the memory
    sections (Retrieved Context, Working Memory) if one doesn't exist.
    These sections are project-specific and managed by the pact-memory skill.

    If the project already has a CLAUDE.md, this function does nothing
    (preserves existing project configuration).

    Returns:
        Status message or None if no action taken.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file = Path(project_dir) / "CLAUDE.md"

    # Don't overwrite existing project CLAUDE.md
    if target_file.exists():
        return None

    # Create minimal CLAUDE.md with memory sections
    memory_template = """# Project Memory

This file contains project-specific memory managed by the PACT framework.
The global PACT Orchestrator is loaded from `~/.claude/CLAUDE.md`.

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
        target_file.write_text(memory_template, encoding="utf-8")
        os.chmod(str(target_file), 0o600)
        return "Created project CLAUDE.md with memory sections"
    except Exception as e:
        return f"Project CLAUDE.md failed: {str(e)[:30]}"
