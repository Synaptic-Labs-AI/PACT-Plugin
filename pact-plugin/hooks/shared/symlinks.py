"""
Location: pact-plugin/hooks/shared/symlinks.py
Summary: Plugin symlink management for PACT environment setup.
Used by: session_init.py during SessionStart hook to create symlinks
         for @reference resolution in CLAUDE.md.

Creates two types of symlinks:
1. ~/.claude/protocols/pact-plugin/ -> plugin/protocols/
   (enables @~/.claude/protocols/pact-plugin/... references)
2. ~/.claude/agents/pact-*.md -> plugin/agents/pact-*.md
   (enables non-prefixed agent names like "pact-secretary")
"""

from __future__ import annotations

import os
from pathlib import Path

from .paths import get_claude_config_dir


def setup_plugin_symlinks() -> str | None:
    """
    Create symlinks for plugin resources to ~/.claude/.

    Creates:
    1. ~/.claude/protocols/pact-plugin/ -> plugin/protocols/
       (enables @~/.claude/protocols/pact-plugin/... references in CLAUDE.md)
    2. ~/.claude/agents/pact-*.md -> plugin/agents/pact-*.md
       (enables non-prefixed agent names like "pact-secretary")

    Returns:
        Status message or None if successful
    """
    plugin_root_str = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not plugin_root_str:
        return None

    plugin_root = Path(plugin_root_str)
    if not plugin_root.exists():
        return None

    home_claude = Path.home() / ".claude"
    config_root = get_claude_config_dir()
    messages = []

    # 1. Symlink protocols/ directory.
    # DUAL-LOCATION (#926): the protocols symlink is consumed by a CLAUDE.md
    # `@~/.claude/protocols/...` import. Whether Claude expands the literal `~`
    # to $HOME or rewrites it to $CLAUDE_CONFIG_DIR is not knowable here, so we
    # create the link in BOTH roots when they differ (answer-immune). With the
    # env unset the two roots are equal and we create exactly once.
    protocols_src = plugin_root / "protocols"
    if protocols_src.exists():
        protocols_roots = [home_claude]
        if config_root.resolve() != home_claude.resolve():
            protocols_roots.append(config_root)
        for root in protocols_roots:
            protocols_dst = root / "protocols" / "pact-plugin"
            try:
                # mkdir INSIDE the try (#926): a pathological config_root
                # (relative / unwritable / file-in-path) must fail-open per this
                # function's "returns None/status, never raises" contract, not
                # propagate to the caller. mode=0o700 applies to the leaf dir
                # only; parent dirs use umask.
                protocols_dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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

    # 2. Symlink individual agent files (enables non-prefixed agent names).
    # Agents FOLLOW the config dir — Claude discovers subagents from
    # $CLAUDE_CONFIG_DIR/agents/.
    agents_src = plugin_root / "agents"
    if agents_src.exists():
        agents_dst = config_root / "agents"
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
