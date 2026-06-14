"""Structural prose-surface regression guard for the Task B claim-before-work discipline.

A teammate must flip its pre-assigned Task B from ``pending`` to ``in_progress``
BEFORE any implementation tool-use, so the lead retains the "work started" signal.
That discipline lives only in prose across several instruction surfaces a teammate
reads at execution time; a future edit could silently drop it. These guards make
that regression loud.

Keyed on STABLE structural tokens (semantic co-occurrence), not exact prose, so
benign rewording survives but dropping the discipline fails:

  G1 — pact-agent-teams "On Start" flow contains the claim step AND couples on
       the claim-BEFORE-work ORDERING (in_progress -> "before any" -> a tool, in
       sequence), so an inverted work-first/claim-after edit fails.
  G2 — pact-agent-teams post-acceptance paragraph carries the pre-assigned-Task-B
       reframe (positive token; the distinctive "status flip" phrase scopes the
       assert to the reframe paragraph).
  G3 — per-surface presence net across the reinforcement surfaces, plus a
       discovery floor so a renamed/trimmed surface set cannot false-pass.
"""
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"
COMMANDS_DIR = PLUGIN_ROOT / "commands"
AGENTS_DIR = PLUGIN_ROOT / "agents"

AGENT_TEAMS_SKILL = SKILLS_DIR / "pact-agent-teams" / "SKILL.md"

# G3 reinforcement surfaces — every surface OTHER than pact-agent-teams (which
# G1/G2 assert deeply). All 5 dispatch-template commands now carry the reminder
# (peer-review + rePACT added alongside the original orchestrate/comPACT/plan-mode),
# plus the teachback skill and the orchestrator persona. The discovery floor
# counts exactly what G3 scans.
REINFORCEMENT_SURFACES = [
    SKILLS_DIR / "pact-teachback" / "SKILL.md",
    COMMANDS_DIR / "comPACT.md",
    COMMANDS_DIR / "orchestrate.md",
    COMMANDS_DIR / "plan-mode.md",
    COMMANDS_DIR / "peer-review.md",
    COMMANDS_DIR / "rePACT.md",
    AGENTS_DIR / "pact-orchestrator.md",
]
DISCOVERY_FLOOR = 7


def _norm(text: str) -> str:
    """Lowercase + collapse whitespace so token checks survive line-wrapping and
    benign whitespace edits."""
    return re.sub(r"\s+", " ", text.lower())


def _on_start_section() -> str:
    """The normalized text of the pact-agent-teams '## On Start' section only
    (sliced to the next top-level '## ' header), so G1 cannot be satisfied by a
    claim token living in some unrelated part of the file."""
    text = AGENT_TEAMS_SKILL.read_text(encoding="utf-8")
    m = re.search(r"\n##\s+On Start\b", text)
    assert m, "pact-agent-teams SKILL.md has no '## On Start' section"
    start = m.end()
    nxt = re.search(r"\n##\s", text[start:])
    section = text[start : start + nxt.start()] if nxt else text[start:]
    return _norm(section)


# ── G1: On Start has the explicit, ordered claim-before-work step ──────────────
class TestG1OnStartClaimStep:
    """The 'On Start' flow must tell the teammate to flip Task B -> in_progress
    BEFORE Edit/Write/Bash.

    The load-bearing coupling is the ORDERING, not token presence. 'in_progress'
    survives step-5 removal (step 2 also claims in_progress); the Edit/Write/Bash
    trio survives too (step 4's "DO NOT call Edit/Write/Bash before your teachback"
    names all three). So a presence-only check false-passes an inverted edit
    (work-first, claim-after) — the exact regression this guards. The ordering
    test below instead requires the SEQUENCE in_progress -> "before any" -> a tool
    within one sentence, which an inverted/work-first reword breaks."""

    def test_on_start_references_task_b(self):
        assert "task b" in _on_start_section(), (
            "On Start flow no longer references Task B"
        )

    def test_on_start_has_in_progress_claim(self):
        assert "in_progress" in _on_start_section(), (
            "On Start flow no longer mentions the status=in_progress claim"
        )

    def test_on_start_claim_precedes_implementation_tools(self):
        section = _on_start_section()
        # ORDERING coupling: the claim (in_progress) must be gated AHEAD of the
        # implementation tools — in_progress, then "before any", then a tool, in
        # that order within one sentence (`[^.]` stays inside the sentence). A
        # work-first / claim-after reword (or a "before any HANDOFF" that binds
        # "before" to something other than the tools) breaks the sequence even
        # though every individual token still appears somewhere in On Start.
        assert re.search(
            r"in_progress[^.]*?before any[^.]*?\b(?:edit|write|bash)\b", section
        ), (
            "On Start step must gate Edit/Write/Bash BEHIND the in_progress claim "
            '(sequence: status="in_progress" -> "before any" -> Edit/Write/Bash). '
            "An inverted/work-first ordering reintroduces the #961 bug this guards."
        )


# ── G2: post-acceptance paragraph carries the pre-assigned reframe ─────────────
class TestG2PreAssignedReframe:
    """The post-acceptance claim guidance must cover the PRE-ASSIGNED Task B case
    (owner already set, still pending) rather than presupposing an unassigned
    task. Positive-token only. The reframe coupling rests on the distinctive
    "status flip, not only an ownership grab" phrase, which is unique to the
    post-acceptance paragraph ("pre-assigned task b" alone is insufficient — it
    also appears in the On Start step)."""

    @pytest.fixture
    def text(self) -> str:
        return _norm(AGENT_TEAMS_SKILL.read_text(encoding="utf-8"))

    def test_covers_pre_assigned_task_b(self, text):
        assert "pre-assigned task b" in text, (
            "agent-teams skill no longer covers the pre-assigned Task B claim case"
        )

    def test_reframes_claim_as_status_flip(self, text):
        # Distinctive reframe token, unique to the post-acceptance paragraph.
        assert "status flip, not only an ownership grab" in text, (
            "agent-teams skill lost the 'claiming is a status flip' reframe"
        )


# ── G3: per-surface presence net + discovery floor ────────────────────────────
class TestG3ReinforcementSurfaces:
    """Every reinforcement surface must carry the claim-before-work token, and the
    scanned set must not silently shrink (vacuous-sweep guard)."""

    def test_discovery_floor(self):
        scanned = [p for p in REINFORCEMENT_SURFACES if p.exists()]
        assert len(scanned) >= DISCOVERY_FLOOR, (
            f"G3 scanned only {len(scanned)} reinforcement surfaces "
            f"(floor {DISCOVERY_FLOOR}); a surface was renamed/removed/trimmed: "
            f"{[str(p) for p in REINFORCEMENT_SURFACES if not p.exists()]}"
        )

    @pytest.mark.parametrize(
        "surface", REINFORCEMENT_SURFACES, ids=lambda p: p.parent.name + "/" + p.name
    )
    def test_surface_carries_claim_token(self, surface):
        text = _norm(surface.read_text(encoding="utf-8"))
        # Each surface must mention Task B, the in_progress claim, and an
        # ordering token. plan-mode's Task-B reminder uses the adapted wording
        # "before beginning the consultation" instead of "before any
        # implementation tool-use", so the ordering token accepts either form.
        for token in ("task b", "in_progress"):
            assert token in text, (
                f"{surface.name} no longer reinforces claim-before-work "
                f"(missing '{token}')"
            )
        assert ("before any" in text) or ("before beginning" in text), (
            f"{surface.name} no longer reinforces claim-BEFORE-work ordering "
            "(missing both 'before any' and 'before beginning')"
        )
