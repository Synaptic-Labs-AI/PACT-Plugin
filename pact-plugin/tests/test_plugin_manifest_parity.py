"""
Manifest-vs-filesystem parity invariants for pact-plugin/.claude-plugin/plugin.json.

Pins set-membership symmetry between the `commands` and `agents` arrays in
plugin.json and the `*.md` files on disk under commands/ and agents/.
A command or agent file that ships without a manifest entry is non-discoverable
and non-invokable; a manifest entry without a backing file is a stale reference.
"""

import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
COMMANDS_DIR = PLUGIN_ROOT / "commands"
AGENTS_DIR = PLUGIN_ROOT / "agents"


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def _on_disk(rel_dir: str, dir_path: Path) -> set[str]:
    return {f"./{rel_dir}/{p.name}" for p in dir_path.glob("*.md")}


def test_every_command_md_file_is_registered():
    manifest = _load_manifest()
    registered = set(manifest["commands"])
    on_disk = _on_disk("commands", COMMANDS_DIR)
    missing = on_disk - registered
    assert not missing, (
        f"Commands present on disk but missing from plugin.json: {sorted(missing)}"
    )


def test_no_stale_command_entries():
    manifest = _load_manifest()
    registered = set(manifest["commands"])
    on_disk = _on_disk("commands", COMMANDS_DIR)
    stale = registered - on_disk
    assert not stale, (
        f"Commands registered in plugin.json without a backing file: {sorted(stale)}"
    )


def test_every_agent_md_file_is_registered():
    manifest = _load_manifest()
    registered = set(manifest["agents"])
    on_disk = _on_disk("agents", AGENTS_DIR)
    missing = on_disk - registered
    assert not missing, (
        f"Agents present on disk but missing from plugin.json: {sorted(missing)}"
    )


def test_no_stale_agent_entries():
    manifest = _load_manifest()
    registered = set(manifest["agents"])
    on_disk = _on_disk("agents", AGENTS_DIR)
    stale = registered - on_disk
    assert not stale, (
        f"Agents registered in plugin.json without a backing file: {sorted(stale)}"
    )
