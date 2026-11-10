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

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from fixtures.tracked_files import tracked_files as _tracked_files

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


def _tracked_markdown(dir_path: Path) -> list[Path]:
    relative = dir_path.relative_to(PLUGIN_ROOT.parent).as_posix()
    return sorted(path for path in _tracked_files(PLUGIN_ROOT.parent, relative)
                  if path.parent == dir_path and path.suffix == ".md")


def _on_disk(rel_dir: str, dir_path: Path) -> set[str]:
    return {f"./{rel_dir}/{p.name}" for p in _tracked_markdown(dir_path)}


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
    for md in _tracked_markdown(AGENTS_DIR):
        declared = _frontmatter_name(md)
        if declared != md.stem:
            mismatches.append(
                f"{md.name}: frontmatter name={declared!r}, filename stem={md.stem!r}"
            )
    assert not mismatches, (
        "Agent frontmatter `name:` diverges from filename stem:\n  "
        + "\n  ".join(mismatches)
    )


def _synthetic_plugin(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    for relative, text in files.items():
        path = plugin / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", "pact-plugin"],
        check=True, capture_output=True)
    return plugin


def test_tracked_manifest_population_controls_untracked_files(tmp_path, monkeypatch):
    plugin = _synthetic_plugin(tmp_path, {
        "commands/orchestrate.md": "",
        "agents/pact-orchestrator.md": "",
    })
    monkeypatch.setitem(globals(), "PLUGIN_ROOT", plugin)
    for rel_dir, known_name in (
        ("commands", "orchestrate.md"),
        ("agents", "pact-orchestrator.md"),
    ):
        dir_path = plugin / rel_dir
        before = _tracked_markdown(dir_path)
        assert dir_path / known_name in before
        stray = dir_path / "000_backend_manifest_issue1714_probe.md"
        stray.write_text("", encoding="utf-8")
        try:
            assert _tracked_markdown(dir_path) == before
        finally:
            stray.unlink()


def test_manifest_probe_coexists_with_raw_glob_teammate_consumer(tmp_path, monkeypatch):
    plugin = _synthetic_plugin(
        tmp_path, {"agents/pact-orchestrator.md": ""})
    monkeypatch.setitem(globals(), "PLUGIN_ROOT", plugin)
    agents = plugin / "agents"
    before = _tracked_markdown(agents)
    assert agents / "pact-orchestrator.md" in before
    stray = agents / "000_backend_manifest_issue1714_probe.md"
    shared_stray = AGENTS_DIR / stray.name
    assert not shared_stray.exists()
    stray.write_text("", encoding="utf-8")
    try:
        from test_lead_address_prose_pins import (
            test_every_teammate_agent_preloads_the_rule,
        )

        test_every_teammate_agent_preloads_the_rule()
        assert _tracked_markdown(agents) == before
        assert not shared_stray.exists()
    finally:
        stray.unlink()
    assert not shared_stray.exists()


def test_tracked_manifest_population_omits_a_deleted_member(monkeypatch):
    known = AGENTS_DIR / "pact-orchestrator.md"
    is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda path: False if path == known else is_file(path))
    assert known not in _tracked_markdown(AGENTS_DIR)
