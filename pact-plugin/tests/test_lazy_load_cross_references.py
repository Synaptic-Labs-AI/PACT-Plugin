"""
Cross-reference link validity across all 13 agent bodies (12 teammates + orchestrator).

Under v4.0.0 lazy-load via markdown cross-references (Option F), every
plugin-relative link to ../protocols/*.md or ../skills/*/SKILL.md in any
agent body MUST resolve to a real file. A dangling cross-reference would
fail the lazy-load contract: the agent reads the link, the file is absent,
and the directive collapses silently.

Two phrasing conventions exist (architect Q3):
  - IMPERATIVE: `Read [name](../protocols/name.md) immediately on detecting <trigger>.`
  - SOFT:      `For full detail, see [name](../protocols/name.md).`

These tests check link RESOLUTION; phrasing-convention enforcement is a
separate concern (and a separate test).

Marker discipline: tests whose passing depends on C1-C9 production landing
(orchestrator agent file present, 13-entry plugin.json) are xfail-strict
and flip in C10. Tests that enforce invariants holding pre- AND post-v4.0.0
(no dangling links, no @-refs) are NOT xfail-strict — they are normal CI
guards from day one.
"""
import re
from pathlib import Path

import pytest


AGENTS_DIR = Path(__file__).parent.parent / "agents"
PLUGIN_ROOT = Path(__file__).parent.parent

EXPECTED_AGENT_FILES = {
    "pact-architect.md",
    "pact-auditor.md",
    "pact-backend-coder.md",
    "pact-database-engineer.md",
    "pact-devops-engineer.md",
    "pact-frontend-coder.md",
    "pact-n8n.md",
    "pact-orchestrator.md",
    "pact-preparer.md",
    "pact-qa-engineer.md",
    "pact-secretary.md",
    "pact-security-engineer.md",
    "pact-test-engineer.md",
}

# Match plugin-relative markdown links to ../protocols/*.md or ../skills/*/SKILL.md
# Pattern: [text](../path/to/file.md)
LINK_PATTERN = re.compile(
    r"\[(?P<text>[^\]]+)\]\((?P<href>\.\.\/(?:protocols|skills)\/[^)]+\.md)\)"
)


def _resolve(agent_path: Path, href: str) -> Path:
    """Resolve a relative href against the agent file's parent dir."""
    return (agent_path.parent / href).resolve()


def test_all_13_agent_files_present():
    """All 13 agent files (12 teammates + orchestrator) must exist."""
    actual = {p.name for p in AGENTS_DIR.glob("*.md")}
    missing = EXPECTED_AGENT_FILES - actual
    extra = actual - EXPECTED_AGENT_FILES
    assert not missing, f"missing agent files: {missing}"
    assert not extra, f"unexpected agent files: {extra}"


def test_protocol_cross_references_resolve():
    """Every ../protocols/*.md link in every agent body must resolve.

    Invariant guard — must hold at every commit, pre- and post-v4.0.0.
    """
    failures = []
    for agent_path in sorted(AGENTS_DIR.glob("*.md")):
        text = agent_path.read_text()
        for m in LINK_PATTERN.finditer(text):
            href = m.group("href")
            if "/protocols/" not in href:
                continue
            target = _resolve(agent_path, href)
            if not target.exists():
                failures.append(f"{agent_path.name}: dangling {href} → {target}")
    assert not failures, "dangling protocol cross-references:\n" + "\n".join(failures)


def test_skill_cross_references_resolve():
    """Every ../skills/*/SKILL.md link in every agent body must resolve.

    Invariant guard — must hold at every commit, pre- and post-v4.0.0.
    """
    failures = []
    for agent_path in sorted(AGENTS_DIR.glob("*.md")):
        text = agent_path.read_text()
        for m in LINK_PATTERN.finditer(text):
            href = m.group("href")
            if "/skills/" not in href:
                continue
            target = _resolve(agent_path, href)
            if not target.exists():
                failures.append(f"{agent_path.name}: dangling {href} → {target}")
    assert not failures, "dangling skill cross-references:\n" + "\n".join(failures)


def test_imperative_protocols_referenced_by_at_least_one_agent():
    """Each of the 6 imperative protocols must appear in at least one agent body."""
    imperative = [
        "algedonic.md",
        "pact-communication-charter.md",
        "pact-s4-tension.md",
        "pact-s5-policy.md",
        "pact-state-recovery.md",
        "pact-completion-authority.md",
    ]
    bodies = {
        p.name: p.read_text() for p in AGENTS_DIR.glob("*.md")
    }
    unreferenced = []
    for protocol in imperative:
        if not any(protocol in body for body in bodies.values()):
            unreferenced.append(protocol)
    assert not unreferenced, (
        f"imperative protocols never referenced from any agent: {unreferenced}"
    )


def test_no_at_ref_in_agent_bodies_for_protocol_or_skill_paths():
    """`@`-refs were empirically falsified for hook additionalContext channel.
    Agent bodies must use plugin-relative markdown links, not `@`-refs.

    Invariant guard — must hold at every commit, pre- and post-v4.0.0.
    """
    at_ref_pattern = re.compile(r"@(?:~/.claude/plugins|\.\./protocols|\.\./skills)\S*\.md")
    failures = []
    for agent_path in sorted(AGENTS_DIR.glob("*.md")):
        text = agent_path.read_text()
        for m in at_ref_pattern.finditer(text):
            failures.append(f"{agent_path.name}: forbidden @-ref → {m.group()}")
    assert not failures, "forbidden @-refs in agent bodies:\n" + "\n".join(failures)
