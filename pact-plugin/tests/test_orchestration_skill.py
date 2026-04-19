"""
Tests for #452 — dual-purpose orchestration skill invariants.

skills/orchestration/SKILL.md is a dual-purpose file:
  - Readable as markdown via the Read tool (bootstrap.md Reads it as
    its first Read target; Tier-2 Read-tracker durability).
  - Invokable as a skill via Skill("PACT:orchestration") (Tier-1
    Skills-restored durability, backup only).

Both delivery paths must resolve to the same file contents, and the
skill must be auto-discoverable via plugin.json's directory-reference
skill schema.

Test coverage (plan doc row IDs):
  T6  bootstrap.md first Read target path equals skills/orchestration/SKILL.md
  T7  Skill body byte-identical to what bootstrap.md Reads (SSOT byte-diff)
  T8  PACT:orchestration resolvable via plugin.json skill auto-discovery
  T9  End-to-end Skill("PACT:orchestration") returns skill body
      (harness-limited: SKIP-marked)
  T15 Frontmatter description contains role-scoping trigger vocabulary
"""
import json
import re
from pathlib import Path

import pytest

from helpers import parse_frontmatter

PLUGIN_ROOT = Path(__file__).parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"
ORCHESTRATION_SKILL_PATH = SKILLS_DIR / "orchestration" / "SKILL.md"
BOOTSTRAP_MD = PLUGIN_ROOT / "commands" / "bootstrap.md"
PLUGIN_JSON_PATH = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"


class TestDualPurposeInvariants:
    """T6 + T7: Bootstrap.md's first Read target must point to the same
    file that Skill("PACT:orchestration") loads. The two delivery paths
    have zero content divergence tolerance — any drift is a dead-on-arrival
    contract violation."""

    def test_bootstrap_first_read_target_path(self):
        """Bootstrap.md's first numbered Read target must be the
        orchestration skill file (by path)."""
        text = BOOTSTRAP_MD.read_text(encoding="utf-8")
        # Find the numbered list: item 1 should reference the skill file.
        pattern = re.compile(
            r"1\.\s+`\{plugin_root\}/((?:protocols|skills)/[\w\-/]+\.md)`"
        )
        match = pattern.search(text)
        assert match is not None, (
            "bootstrap.md has no numbered Read instruction #1"
        )
        assert match.group(1) == "skills/orchestration/SKILL.md", (
            f"bootstrap.md first Read target is '{match.group(1)}', "
            f"expected 'skills/orchestration/SKILL.md'. The dual-purpose "
            f"contract requires the first Read target to point at the "
            f"skill body file; diverging paths break the Tier-2 durability "
            f"path for orchestration content."
        )

    def test_skill_file_readable_and_substantial(self):
        """T7 (byte-diff form): the skill body file must exist and contain
        substantial content. Because bootstrap.md's first Read target and
        the Skill tool both resolve to the SAME on-disk path
        (skills/orchestration/SKILL.md), a single-source-of-truth byte
        diff against itself is trivially satisfied. The architectural
        invariant this test guards is the one-file rule: if someone
        forked the content into two files (e.g. a stub at the old
        protocols/ path plus a copy at skills/), this test would stay
        green but T13 (dead-reference guard in test_cross_references.py)
        would catch the fork via the on-disk path grep."""
        assert ORCHESTRATION_SKILL_PATH.is_file(), (
            f"Skill body missing on disk: {ORCHESTRATION_SKILL_PATH}. "
            f"bootstrap.md's first Read target cannot resolve and "
            f"Skill('PACT:orchestration') cannot load."
        )
        body = ORCHESTRATION_SKILL_PATH.read_text(encoding="utf-8")
        assert len(body) > 2000, (
            f"Skill body is only {len(body)} chars — expected ~15000 "
            f"(524 lines × ~30 chars/line). Content loss during migration?"
        )


class TestSkillAutoDiscovery:
    """T8: The orchestration skill must be auto-discoverable through the
    plugin.json directory-reference skill schema (`"skills": "./skills/"`)."""

    def test_plugin_json_uses_directory_schema(self):
        """plugin.json's skills field must be the directory-reference form
        (a string ending in 'skills/' or 'skills'), not a per-skill list.
        This is what allows adding skills/orchestration/SKILL.md to take
        effect without a plugin.json edit."""
        data = json.loads(PLUGIN_JSON_PATH.read_text(encoding="utf-8"))
        skills_field = data.get("skills")
        assert isinstance(skills_field, str), (
            f"plugin.json 'skills' field is {type(skills_field).__name__}, "
            f"expected str. The directory-reference schema is required "
            f"for auto-discovery; a list form would require an explicit "
            f"entry for the orchestration skill and break the 'no plugin.json "
            f"edit' property of the #452 migration."
        )
        assert skills_field.rstrip("/").endswith("skills"), (
            f"plugin.json 'skills' field is '{skills_field}', expected "
            f"a path ending in 'skills' (e.g. './skills/' or './skills'). "
            f"Auto-discovery scans this directory for SKILL.md files."
        )

    def test_orchestration_skill_under_skills_dir(self):
        """The orchestration SKILL.md must live under the directory
        referenced by plugin.json's 'skills' field, so auto-discovery
        finds it."""
        data = json.loads(PLUGIN_JSON_PATH.read_text(encoding="utf-8"))
        skills_field = data["skills"].lstrip("./").rstrip("/")
        expected_skills_dir = PLUGIN_ROOT / skills_field
        assert expected_skills_dir.resolve() == SKILLS_DIR.resolve(), (
            f"plugin.json 'skills' field resolves to "
            f"{expected_skills_dir.resolve()}, but SKILLS_DIR is "
            f"{SKILLS_DIR.resolve()}. Fixture drift."
        )
        assert ORCHESTRATION_SKILL_PATH.is_file(), (
            f"skills/orchestration/SKILL.md missing — auto-discovery "
            f"will not register PACT:orchestration."
        )
        # The skill directory's name (relative to SKILLS_DIR) is what
        # Claude Code uses as the skill's namespace-suffixed name.
        skill_dir_name = ORCHESTRATION_SKILL_PATH.parent.name
        assert skill_dir_name == "orchestration", (
            f"Skill directory name is '{skill_dir_name}', expected "
            f"'orchestration' (invoked as Skill('PACT:orchestration'))."
        )

    def test_frontmatter_name_matches_directory(self):
        """Per SKILL.md frontmatter convention (see other skills), the
        frontmatter 'name' field must match the directory name so the
        plugin's skill catalog registers the skill under the expected
        invocation token."""
        text = ORCHESTRATION_SKILL_PATH.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        assert fm is not None, (
            "skills/orchestration/SKILL.md has no YAML frontmatter — "
            "auto-discovery cannot register a skill without frontmatter."
        )
        assert fm.get("name") == "orchestration", (
            f"Frontmatter 'name' is {fm.get('name')!r}, expected "
            f"'orchestration'. Skill auto-discovery uses this token as "
            f"the invocation name (Skill('PACT:orchestration'))."
        )


@pytest.mark.skip(
    reason=(
        "T9 integration test: actual Skill('PACT:orchestration') invocation "
        "requires the Claude Code runtime, not available in pytest. "
        "Covered by the manual runbook (plan doc §T14)."
    )
)
class TestSkillEndToEnd:
    """T9: End-to-end Skill invocation returns the skill body.
    Harness-limited — pytest cannot dispatch the Skill tool. Kept here
    as a documented placeholder so the test suite records the coverage
    gap the manual runbook fills."""

    def test_skill_invocation_returns_body(self):
        pass  # pragma: no cover


class TestFrontmatterRoleScoping:
    """T15: The skill's frontmatter description must contain role-scoping
    vocabulary so Claude Code's skill-surfacing heuristic is
    biased away from surfacing PACT:orchestration to teammate specialists
    (who use their dedicated agent body + pact-agent-teams skill instead).

    This is a drift-shape pin on the description's role-filtering intent.
    If a future edit strips 'orchestrator' or 'Agent Team lead' from the
    description, surfacing behavior may silently change."""

    REQUIRED_SUBSTRINGS = (
        "orchestrator",
        "Agent Team lead",
    )

    def test_frontmatter_has_description(self):
        text = ORCHESTRATION_SKILL_PATH.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        assert fm is not None, (
            "skills/orchestration/SKILL.md missing frontmatter"
        )
        assert "description" in fm, (
            "skills/orchestration/SKILL.md frontmatter missing 'description' field"
        )
        assert fm["description"].strip(), (
            "skills/orchestration/SKILL.md frontmatter 'description' is empty"
        )

    @pytest.mark.parametrize("required", REQUIRED_SUBSTRINGS)
    def test_description_contains_role_scoping_vocabulary(self, required):
        """Drift-shape pin: description must contain role-scoping
        vocabulary that discourages surfacing to teammates."""
        text = ORCHESTRATION_SKILL_PATH.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        assert required in fm["description"], (
            f"skills/orchestration/SKILL.md description missing required "
            f"role-scoping substring {required!r}. The skill description "
            f"shapes Claude Code's skill-surfacing heuristic; stripping "
            f"role-scoping vocabulary risks surfacing the skill to "
            f"teammate specialists that should use pact-agent-teams instead."
        )
