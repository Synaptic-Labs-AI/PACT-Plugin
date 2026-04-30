"""
Hook-side tests for the wake-arm directive emitted by peer_inject.py.

The teammate-side wake-arm directive is appended to additionalContext on
SubagentStart per architect §15.2 — Tier-0 hook delivery so the directive
is durable across compaction and bypasses the Read-tracker budget.

These tests focus on semantic-anchor invariants (skill slug, operation
name, agent_name interpolation, timing-gap-closure phrase distinct from
the lead-side directive). Existing tests/test_peer_inject.py already
covers chain-end positioning of _WAKE_ARM_TEMPLATE relative to the
completion-authority note across all spawnable pact-* roles; this file
fills the semantic-content gap without duplicating those positional
assertions.
"""
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


@pytest.fixture(scope="module")
def wake_arm_template() -> str:
    from peer_inject import _WAKE_ARM_TEMPLATE
    return _WAKE_ARM_TEMPLATE


class TestWakeArmTemplateSemanticAnchors:
    """Verbatim tokens that must appear in the rendered template."""

    def test_template_references_inbox_wake_skill_slug(self, wake_arm_template: str):
        assert 'Skill("PACT:inbox-wake")' in wake_arm_template, (
            "Teammate-side directive must reference exact slug Skill(\"PACT:inbox-wake\")"
        )

    def test_template_references_arm_operation(self, wake_arm_template: str):
        assert "Arm operation" in wake_arm_template, (
            "Teammate-side directive must reference the Arm operation by name"
        )

    def test_template_carries_teammate_side_timing_phrase(self, wake_arm_template: str):
        # Teammate-side timing is "before any tool call" — distinct from
        # lead-side "before any teammate dispatch". Teammates don't dispatch
        # other teammates, but they DO issue tool calls; the wake must be
        # armed before the first one. This anchor prevents copy-paste of
        # the lead template into the teammate site.
        assert "before any tool call" in wake_arm_template, (
            "Teammate-side directive must use 'before any tool call' timing phrase"
        )

    def test_template_does_not_use_lead_side_timing_phrase(self, wake_arm_template: str):
        # Negative anchor: ensures lead/teammate timing phrases stay distinct.
        assert "before any teammate dispatch" not in wake_arm_template, (
            "Teammate-side directive must NOT carry the lead-side "
            "'before any teammate dispatch' phrase — teammates don't dispatch teammates"
        )

    def test_template_carries_idempotency_phrase(self, wake_arm_template: str):
        assert "idempotent" in wake_arm_template.lower(), (
            "Teammate-side directive must carry an idempotency clause — "
            "guards against LLM-self-diagnosis re-introduction"
        )


class TestWakeArmAgentNameInterpolation:
    """The directive must parametrize on agent_name so each teammate watches its own inbox."""

    def test_template_contains_agent_name_placeholder(self, wake_arm_template: str):
        # The unrendered template must carry the {agent_name} placeholder so
        # the call site can interpolate the spawning teammate's name.
        assert "{agent_name}" in wake_arm_template, (
            "Template must contain {agent_name} placeholder for per-teammate interpolation"
        )

    def test_rendered_template_substitutes_agent_name(self, wake_arm_template: str):
        rendered = wake_arm_template.format(agent_name="architect")
        assert "{agent_name}" not in rendered, (
            "Rendered template must not retain unsubstituted {agent_name} placeholder"
        )
        assert "architect" in rendered, (
            "Rendered template must contain the substituted agent_name value"
        )

    def test_rendered_template_carries_distinct_agent_name(self, wake_arm_template: str):
        # Two different agent names yield two different rendered strings —
        # confirms the interpolation site is load-bearing, not decorative.
        a = wake_arm_template.format(agent_name="architect")
        b = wake_arm_template.format(agent_name="preparer")
        assert a != b, (
            "Rendered templates for different agent names must differ — "
            "agent_name interpolation must be in a content-bearing position"
        )
        assert "architect" in a and "architect" not in b
        assert "preparer" in b and "preparer" not in a
