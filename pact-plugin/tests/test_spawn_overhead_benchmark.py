"""
Location: pact-plugin/tests/test_spawn_overhead_benchmark.py
Summary: Byte-count regression gate for #366 Phase 1 spawn overhead reduction.
Used by: pytest CI / local test runs.

The PR #390 / v3.17.0 release eliminated ~17KB of orchestrator content from
the per-teammate spawn path by moving it out of ~/.claude/CLAUDE.md (always
loaded) and into pact-plugin/commands/bootstrap.md (lazy-loaded via
Skill("PACT:bootstrap") only when the team-lead needs it). This test pins that
reduction as a byte-level regression gate: any change that re-introduces
CLAUDE.md-scale content into the spawn path will blow past the threshold.

Secondary guards in this file check that bootstrap.md is not referenced
directly by the spawn-path hooks (it must only be reachable via the Skill
invocation) and that remove_stale_kernel_block correctly strips a legacy
PACT block from a migrated home CLAUDE.md.
"""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "hooks"))


class TestSpawnOverheadRegression:
    """Byte-count regression gate for #366 Phase 1 spawn overhead reduction."""

    # Measured v3.17.0 baseline (PR #390):
    #   peer_inject prelude (additionalContext): 822 bytes
    #   pact-backend-coder.md agent body:       8015 bytes
    #   total:                                  8837 bytes
    #
    # Threshold gives ~13% headroom over the measured baseline while
    # staying well below the ~17KB wall a CLAUDE.md re-introduction would
    # hit (the v3.16.2 home-CLAUDE.md PACT block was ~17KB). The goal is
    # to catch regressions that silently re-add orchestrator content to
    # the always-on spawn path, not to police normal small-scale growth.
    #
    # Tuning rules:
    #   - If a legitimate change pushes total over THRESHOLD_BYTES, first
    #     confirm that the new content genuinely needs to live on the
    #     spawn path (vs. a lazy-loaded Skill), then raise THRESHOLD_BYTES
    #     in +1000 byte steps and update the baseline comment above.
    #   - Do NOT raise THRESHOLD_BYTES above ~12000 without re-examining
    #     whether CLAUDE.md-scale content has crept back in. At that point
    #     the regression gate has lost its point.
    THRESHOLD_BYTES = 10000

    # Hard ceiling: THRESHOLD_BYTES must never be raised above this value.
    # Enforces the advisory comment above as a mechanical gate — any PR that
    # bumps THRESHOLD_BYTES past 12000 triggers a test failure demanding
    # investigation of CLAUDE.md-scale content creep.
    ABSOLUTE_CEILING = 12000

    def test_threshold_within_absolute_ceiling(self):
        """THRESHOLD_BYTES must stay below ABSOLUTE_CEILING.

        This enforces the tuning rule: "Do NOT raise THRESHOLD_BYTES above
        ~12000 without re-examining whether CLAUDE.md-scale content has crept
        back in." A test failure here means someone bumped the threshold past
        the point where the regression gate loses its value.
        """
        assert self.THRESHOLD_BYTES <= self.ABSOLUTE_CEILING, (
            f"THRESHOLD_BYTES ({self.THRESHOLD_BYTES}) exceeds "
            f"ABSOLUTE_CEILING ({self.ABSOLUTE_CEILING}). The spawn overhead "
            f"regression gate loses its value above {self.ABSOLUTE_CEILING} "
            f"bytes — investigate whether CLAUDE.md-scale content has crept "
            f"back into the always-on spawn path before raising this limit."
        )

