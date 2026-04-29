"""
Charter-anchor tests for the inbox-wake mechanism.

The Communication Charter Part I has a `### Wake Mechanism (Monitor + Cron)`
subsection that documents path-1 vs path-2 delivery-model asymmetry +
file-based-registry rationale. Cross-refs from
`skills/orchestration/SKILL.md` depend on the subsection's stable presence.
"""
from fixtures.inbox_wake import CHARTER_PATH, SKILLS_DIR, _read


class TestWakeMechanismCharterAnchor:
    """The `### Wake Mechanism (Monitor + Cron)` subsection in Charter Part I
    documents the delivery-model context that justifies the wake mechanism.
    Tests pin: presence, Part-I scoping, load-bearing content, and the
    cross-ref discipline from skills/orchestration/SKILL.md.
    """

    def test_wake_mechanism_subsection_exists(self):
        text = _read(CHARTER_PATH)
        assert "### Wake Mechanism (Monitor + Cron)" in text, (
            "charter Part I missing `### Wake Mechanism (Monitor + Cron)` "
            "subsection"
        )

    def test_wake_mechanism_in_part_i(self):
        """The subsection must appear inside Part I (between
        `## Part I` and `## Part II`)."""
        text = _read(CHARTER_PATH)
        part_i = text.index("## Part I")
        part_ii = text.index("## Part II")
        wake = text.index("### Wake Mechanism (Monitor + Cron)")
        assert part_i < wake < part_ii, (
            "Wake Mechanism subsection is not inside Part I — delivery "
            "mechanics belong in Part I, not Part II (written-output norms)"
        )

    def test_wake_mechanism_documents_path_asymmetry(self):
        """The subsection's load-bearing content is the path-1 / path-2
        asymmetry: in-process teammates wake via `waitForNextPromptOrShutdown`;
        the lead session's idle-boundary delivery is gated by `useInboxPoller`.
        """
        text = _read(CHARTER_PATH)
        wake_idx = text.index("### Wake Mechanism (Monitor + Cron)")
        # Subsection ends at the next `### ` heading.
        next_h3 = text.index("\n### ", wake_idx + 1)
        section = text[wake_idx:next_h3]
        assert "Path-1" in section or "path-1" in section, (
            "Wake Mechanism subsection missing path-1 framing"
        )
        assert "Path-2" in section or "path-2" in section, (
            "Wake Mechanism subsection missing path-2 framing"
        )
        assert "useInboxPoller" in section, (
            "Wake Mechanism subsection missing useInboxPoller reference — "
            "the gate that motivates the wake mechanism"
        )

    def test_orchestration_skill_references_charter_subsection(self):
        """Cross-ref discipline: skills/orchestration/SKILL.md's Inbox Wake
        pointer must link back to the charter file so an LLM reading
        the skill can find the delivery-model context."""
        skill_text = _read(SKILLS_DIR / "orchestration" / "SKILL.md")
        charter_ref = "pact-communication-charter.md"
        assert charter_ref in skill_text, (
            "skills/orchestration/SKILL.md missing reference to "
            f"{charter_ref} — cross-ref to Wake Mechanism subsection"
        )
