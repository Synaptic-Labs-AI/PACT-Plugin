"""
Manifest-vs-filesystem parity invariants for pact-plugin/.claude-plugin/plugin.json.

Pins set-membership symmetry between the `commands` and `agents` arrays in
plugin.json and the `*.md` files on disk under commands/ and agents/.
A command or agent file that ships without a manifest entry is non-discoverable
and non-invokable; a manifest entry without a backing file is a stale reference.

Also pins:
- every `skills/<subdir>/` ships a `SKILL.md` (skills register via directory
  pointer in plugin.json, so a missing SKILL.md is silent non-discoverability)
- every `agents/*.md` frontmatter `name:` equals the filename stem (Claude Code
  resolves agents by frontmatter name, not filename)
"""

import json
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
COMMANDS_DIR = PLUGIN_ROOT / "commands"
AGENTS_DIR = PLUGIN_ROOT / "agents"
SKILLS_DIR = PLUGIN_ROOT / "skills"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_NAME_LINE_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)


def _frontmatter_name(md_path: Path) -> str | None:
    """Extract the `name:` field from a markdown file's YAML frontmatter.

    Returns None if no frontmatter block or no name field is found.
    """
    text = md_path.read_text(encoding="utf-8")
    fm = _FRONTMATTER_RE.match(text)
    if not fm:
        return None
    name_match = _NAME_LINE_RE.search(fm.group(1))
    if not name_match:
        return None
    return name_match.group(1).strip().strip('"').strip("'")


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


def test_every_skill_subdir_has_skill_md():
    """Every `skills/<subdir>/` must contain a `SKILL.md` file.

    Skills register via directory pointer (`"skills": "./skills/"` in
    plugin.json), so a subdirectory without a `SKILL.md` is silently
    non-discoverable rather than caught by manifest-array parity.
    """
    missing = sorted(
        f"./skills/{d.name}/"
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and not (d / "SKILL.md").is_file()
    )
    assert not missing, (
        f"Skill subdirectories without a SKILL.md: {missing}"
    )


def test_agent_frontmatter_name_matches_filename():
    """Every `agents/*.md` frontmatter `name:` field must equal the filename stem.

    Claude Code resolves agents by frontmatter `name:`, not filename. A divergence
    means dispatching the agent by its filename-derived id silently routes to a
    different identifier than the file's declared name.
    """
    mismatches = []
    for md in sorted(AGENTS_DIR.glob("*.md")):
        declared = _frontmatter_name(md)
        if declared != md.stem:
            mismatches.append(
                f"{md.name}: frontmatter name={declared!r}, filename stem={md.stem!r}"
            )
    assert not mismatches, (
        "Agent frontmatter `name:` diverges from filename stem:\n  "
        + "\n  ".join(mismatches)
    )
