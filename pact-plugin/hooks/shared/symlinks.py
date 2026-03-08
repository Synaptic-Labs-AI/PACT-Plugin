"""
Location: pact-plugin/hooks/shared/symlinks.py
Summary: Plugin symlink management for PACT environment setup.
Used by: session_init.py during SessionStart hook to create symlinks
         for @reference resolution in CLAUDE.md.

Creates two types of symlinks:
1. ~/.claude/protocols/pact-plugin/ -> plugin/protocols/
   (enables @~/.claude/protocols/pact-plugin/... references)
2. ~/.claude/agents/pact-*.md -> plugin/agents/pact-*.md
   (enables non-prefixed agent names like "pact-memory-agent")
"""

import os
from pathlib import Path


def setup_plugin_symlinks() -> str | None:
    """
    Create symlinks for plugin resources to ~/.claude/.

    Creates:
    1. ~/.claude/protocols/pact-plugin/ -> plugin/protocols/
       (enables @~/.claude/protocols/pact-plugin/... references in CLAUDE.md)
    2. ~/.claude/agents/pact-*.md -> plugin/agents/pact-*.md
       (enables non-prefixed agent names like "pact-memory-agent")

    Returns:
        Status message or None if successful
    """
    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", ""))
    if not plugin_root.exists():
        return None

    claude_dir = Path.home() / ".claude"
    messages = []

    # 1. Symlink protocols/ directory
    protocols_src = plugin_root / "protocols"
    if protocols_src.exists():
        protocols_dst = claude_dir / "protocols" / "pact-plugin"
        # mode=0o700 applies to the leaf directory only; parent dirs use umask
        protocols_dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        try:
            if protocols_dst.is_symlink():
                if protocols_dst.resolve() != protocols_src.resolve():
                    protocols_dst.unlink()
                    protocols_dst.symlink_to(protocols_src)
                    messages.append("protocols updated")
            elif not protocols_dst.exists():
                protocols_dst.symlink_to(protocols_src)
                messages.append("protocols linked")
        except OSError as e:
            messages.append(f"protocols failed: {str(e)[:20]}")

    # 2. Symlink individual agent files (enables non-prefixed agent names)
    agents_src = plugin_root / "agents"
    if agents_src.exists():
        agents_dst = claude_dir / "agents"
        # mode=0o700 applies to the leaf directory only; parent dirs use umask
        agents_dst.mkdir(parents=True, exist_ok=True, mode=0o700)

        agents_updated = 0
        agents_created = 0
        for agent_file in agents_src.glob("pact-*.md"):
            dst_file = agents_dst / agent_file.name
            try:
                if dst_file.is_symlink():
                    if dst_file.resolve() != agent_file.resolve():
                        dst_file.unlink()
                        dst_file.symlink_to(agent_file)
                        agents_updated += 1
                elif not dst_file.exists():
                    dst_file.symlink_to(agent_file)
                    agents_created += 1
                # Skip if real file exists (user override)
            except OSError:
                continue

        if agents_created:
            messages.append(f"{agents_created} agents linked")
        if agents_updated:
            messages.append(f"{agents_updated} agents updated")

    if not messages:
        return "PACT symlinks verified"
    return "PACT: " + ", ".join(messages)
