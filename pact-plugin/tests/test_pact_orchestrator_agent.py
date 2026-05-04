"""
Structural tests for agents/pact-orchestrator.md (v4.0.0 orchestrator agent file).

The orchestrator persona is delivered via `claude --agent PACT:pact-orchestrator`
under v4.0.0; the agent body carries orchestration content inlined from the
pre-v4.0.0 skills/orchestration/SKILL.md. Frontmatter is intentionally
minimal (name, description, memory:user, color) — model/permissionMode/tools
are inherited defaults, and `skills:` is omitted because the preload mechanism
does not apply to --agent main sessions.

Marker discipline (C2): tests whose passing depends on a NOT-YET-LANDED
production change carry `@pytest.mark.xfail(strict=True, reason="v4.0.0 — flips in C10")`.
Tests against production already on disk (C1: orchestrator file present)
are plain tests. C10 flips the remaining xfail markers as their dependent
commits land.
"""
from pathlib import Path

import pytest

from helpers import parse_frontmatter

ORCHESTRATOR_PATH = (
    Path(__file__).parent.parent / "agents" / "pact-orchestrator.md"
)

REQUIRED_FRONTMATTER_KEYS = {"name", "description", "memory", "color"}
EXPECTED_NAME = "pact-orchestrator"
EXPECTED_MEMORY = "user"
EXPECTED_COLOR = "#FFD700"


def test_pact_orchestrator_file_exists():
    assert ORCHESTRATOR_PATH.exists(), (
        f"pact-orchestrator.md missing at {ORCHESTRATOR_PATH}"
    )


def test_pact_orchestrator_frontmatter_has_required_keys():
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    assert fm is not None, "pact-orchestrator.md missing YAML frontmatter"
    missing = REQUIRED_FRONTMATTER_KEYS - set(fm.keys())
    assert not missing, f"missing required frontmatter keys: {missing}"


def test_pact_orchestrator_frontmatter_name_matches():
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    assert fm["name"] == EXPECTED_NAME, (
        f"frontmatter.name expected {EXPECTED_NAME!r}, got {fm['name']!r}"
    )


def test_pact_orchestrator_memory_is_user():
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    assert fm["memory"] == EXPECTED_MEMORY, (
        f"frontmatter.memory expected {EXPECTED_MEMORY!r}, got {fm['memory']!r}"
    )


def test_pact_orchestrator_color_is_gold():
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    actual = fm["color"].strip().strip('"').strip("'")
    assert actual == EXPECTED_COLOR, (
        f"frontmatter.color expected {EXPECTED_COLOR!r}, got {fm['color']!r}"
    )


def test_pact_orchestrator_omits_model_permissionmode_tools():
    """Frontmatter asymmetry: orchestrator inherits defaults; teammate files keep them."""
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    forbidden = {"model", "permissionMode", "tools"}
    present = forbidden & set(fm.keys())
    assert not present, (
        f"orchestrator frontmatter should inherit defaults; found explicit keys: {present}"
    )


def test_pact_orchestrator_omits_skills_frontmatter():
    """`skills:` preload does not apply to --agent main sessions; omitting is intentional."""
    text = ORCHESTRATOR_PATH.read_text()
    fm = parse_frontmatter(text)
    assert "skills" not in fm, (
        "orchestrator must omit `skills:` frontmatter — preload does not apply "
        "to --agent main sessions; declaring skills creates a misleading contract"
    )


def test_pact_orchestrator_body_has_pre_response_channel_check():
    """Body must hoist Pre-Response Channel Check to §1 per architect's reorganization."""
    text = ORCHESTRATOR_PATH.read_text()
    assert "Pre-Response Channel Check" in text, (
        "orchestrator body must contain Pre-Response Channel Check section"
    )


def test_pact_orchestrator_body_references_imperative_protocols():
    """6 imperative protocols must appear as cross-references in the body."""
    text = ORCHESTRATOR_PATH.read_text()
    expected_imperative = [
        "algedonic.md",
        "pact-communication-charter.md",
        "pact-s4-tension.md",
        "pact-s5-policy.md",
        "pact-state-recovery.md",
        "pact-completion-authority.md",
    ]
    missing = [p for p in expected_imperative if p not in text]
    assert not missing, (
        f"orchestrator body missing imperative-protocol cross-references: {missing}"
    )


def test_pact_orchestrator_body_references_soft_protocols():
    """3 soft (reference-only) protocols must appear as cross-references in the body."""
    text = ORCHESTRATOR_PATH.read_text()
    expected_soft = [
        "pact-variety.md",
        "pact-s4-checkpoints.md",
        "pact-workflows.md",
    ]
    missing = [p for p in expected_soft if p not in text]
    assert not missing, (
        f"orchestrator body missing soft-protocol cross-references: {missing}"
    )
