"""
Location: pact-plugin/tests/test_shadow_scan_nonregular.py
Summary: The shadowed-type scan in shared/actor_identity.py reads only
         REGULAR files (security-rev final confirm of 8cbc6c9, LOW 1). A FIFO
         named *.md in a scanned agents folder used to block the hook in
         open() until something wrote to it; it must now return at once and
         count as SHADOWED (its name cannot be read, and a FIFO can still feed
         Claude Code a definition). A FIFO manifest beside a plugin-cache
         symlink must not hang the provenance check either, and proves nothing.
Used by: the pact-plugin test suite.
"""

from __future__ import annotations

import os
import socket
import threading

import pytest

from shared import actor_identity

pytestmark = pytest.mark.skipif(
    not hasattr(os, "mkfifo"), reason="needs os.mkfifo (Unix)"
)

_OTHER = "---\nname: other\n---\nbody\n"


def _agents(tmp_path):
    agents = tmp_path / "proj" / ".claude" / "agents"
    agents.mkdir(parents=True)
    return agents


def _finishes(fn, timeout=5.0):
    """Run fn in a thread; return (finished, result). A hang reads as
    finished=False instead of stalling the suite."""
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("r", fn()), daemon=True)
    thread.start()
    thread.join(timeout)
    return not thread.is_alive(), box.get("r")


def test_fifo_definition_does_not_block_and_counts_as_shadowed(tmp_path):
    agents = _agents(tmp_path)
    os.mkfifo(agents / "pact-secretary.md")
    finished, result = _finishes(
        lambda: actor_identity._scan_for_definition(agents, "pact-secretary")
    )
    assert finished, "the scan blocked on a FIFO"
    assert result is True


def test_fifo_under_any_name_counts_as_shadowed(tmp_path):
    """Its frontmatter name is unknowable, so the stem does not matter."""
    agents = _agents(tmp_path)
    os.mkfifo(agents / "innocent.md")
    finished, result = _finishes(
        lambda: actor_identity.typed_without_frame(
            "pact-secretary", str(tmp_path / "proj")
        )
    )
    assert finished
    assert result is None  # shadowed: the bare type is not trusted


def test_socket_definition_counts_as_shadowed(tmp_path):
    agents = _agents(tmp_path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        path = agents / "s.md"
        try:
            sock.bind(str(path))
        except OSError as error:  # AF_UNIX path length limits on some hosts
            pytest.skip(f"cannot bind a unix socket here: {error}")
        assert actor_identity._scan_for_definition(agents, "pact-secretary") is True
    finally:
        sock.close()


def test_regular_definitions_are_unchanged(tmp_path):
    """Controls: a regular non-matching file is clean, a matching one is a
    shadow, and a symlink to a regular file is still read."""
    agents = _agents(tmp_path)
    (agents / "other.md").write_text(_OTHER)
    assert actor_identity._scan_for_definition(agents, "pact-secretary") is False
    target = tmp_path / "elsewhere.md"
    target.write_text("---\nname: pact-secretary\n---\n")
    os.symlink(target, agents / "linked.md")
    assert actor_identity._scan_for_definition(agents, "pact-secretary") is True


def test_definition_name_raises_only_for_non_regular(tmp_path):
    regular = tmp_path / "r.md"
    regular.write_text(_OTHER)
    assert actor_identity._definition_name(regular) == "other"
    fifo = tmp_path / "f.md"
    os.mkfifo(fifo)
    with pytest.raises(actor_identity._NotRegularFile):
        actor_identity._definition_name(fifo)
    assert actor_identity._definition_name(tmp_path / "missing.md") is None


def test_fifo_manifest_does_not_block_the_provenance_check(tmp_path, pact_context):
    """A symlink into an `agents/` folder under <config>/plugins whose
    plugin.json is a FIFO: the check returns at once and the definition is
    not plugin-owned."""
    pact_context(team_name="t", session_id="s")
    from shared.paths import get_claude_config_dir

    plugin = get_claude_config_dir() / "plugins" / "cache" / "x" / "PACT" / "1"
    (plugin / "agents").mkdir(parents=True)
    (plugin / ".claude-plugin").mkdir()
    os.mkfifo(plugin / ".claude-plugin" / "plugin.json")
    definition = plugin / "agents" / "pact-secretary.md"
    definition.write_text("---\nname: pact-secretary\n---\n")
    link = tmp_path / "pact-secretary.md"
    os.symlink(definition, link)
    finished, result = _finishes(lambda: actor_identity._is_plugin_definition(link))
    assert finished, "the provenance check blocked on a FIFO manifest"
    assert result is False
