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

    def test_teammate_spawn_content_under_threshold(self, tmp_path):
        """The static content a freshly-spawned PACT teammate implicitly
        loads at spawn time must remain under THRESHOLD_BYTES.

        Measured:
          - peer_inject.get_peer_context() output (additionalContext —
            the routing prelude + peer list + teachback reminder)
          - The agent body file (pact-backend-coder.md as a
            representative — all agents share the same YOUR FIRST ACTION
            prelude and similar body envelope)

        Not measured (by design): lazy-loaded content reachable only
        via Skill() invocations (bootstrap.md, protocol files), and
        the project CLAUDE.md routing block which is separately
        size-gated by test_claude_md_manager tests.
        """
        teams_dir = tmp_path / "teams"
        team = "pact-bench"
        (teams_dir / team).mkdir(parents=True)
        (teams_dir / team / "config.json").write_text(json.dumps({
            "members": [
                {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
                {"name": "frontend-coder-1", "agentType": "pact-frontend-coder"},
                {"name": "test-engineer-1", "agentType": "pact-test-engineer"},
            ]
        }))

        from peer_inject import get_peer_context  # type: ignore
        peer_ctx = get_peer_context(
            agent_type="pact-backend-coder",
            team_name=team,
            agent_name="backend-coder-1",
            teams_dir=str(teams_dir),
        )
        assert peer_ctx is not None, (
            "peer_inject returned None for a valid team+member configuration"
        )

        agent_body = (
            _REPO_ROOT / "agents" / "pact-backend-coder.md"
        ).read_text(encoding="utf-8")

        peer_bytes = len(peer_ctx.encode("utf-8"))
        agent_bytes = len(agent_body.encode("utf-8"))
        total = peer_bytes + agent_bytes

        assert total < self.THRESHOLD_BYTES, (
            f"Spawn overhead regression: total static spawn-path content is "
            f"{total} bytes (peer_inject: {peer_bytes}, agent body: "
            f"{agent_bytes}), exceeds the {self.THRESHOLD_BYTES} byte "
            f"threshold set in PR #390 (#366 Phase 1). The v3.17.0 baseline "
            f"was a few KB. A CLAUDE.md re-introduction (~17KB) would cause "
            f"this. Investigate what grew and whether it should live in a "
            f"lazy-loaded Skill instead of the always-on spawn path."
        )

    def test_bootstrap_md_not_in_spawn_path(self):
        """Complementary guard: bootstrap.md content must not be referenced
        by any spawn-path hook in a way that causes it to be auto-loaded.

        session_init.py and peer_inject.py are the two hooks that run on
        SessionStart / SubagentStart and whose output lands in
        additionalContext. Neither should Read or embed bootstrap.md
        content — bootstrap.md must only be reachable via the
        Skill("PACT:bootstrap") invocation instruction the team-lead is
        told to issue.
        """
        session_init_src = (
            _REPO_ROOT / "hooks" / "session_init.py"
        ).read_text(encoding="utf-8")
        peer_inject_src = (
            _REPO_ROOT / "hooks" / "peer_inject.py"
        ).read_text(encoding="utf-8")

        assert "bootstrap.md" not in session_init_src, (
            "session_init.py references bootstrap.md directly — this is a "
            "spawn-path regression. bootstrap.md must only be loaded lazily "
            "via the Skill(\"PACT:bootstrap\") invocation by the team-lead."
        )
        assert "bootstrap.md" not in peer_inject_src, (
            "peer_inject.py references bootstrap.md directly — this is a "
            "spawn-path regression. bootstrap.md must only be loaded lazily "
            "via the Skill(\"PACT:bootstrap\") invocation by the team-lead."
        )

    def test_home_claude_md_template_has_no_pact_content_after_migration(
        self, tmp_path, monkeypatch
    ):
        """Complementary guard: after the one-shot v3.16.2 → v3.17.0
        migration, a home CLAUDE.md must not contain any PACT orchestrator
        content. Simulates a legacy v3.16.2 home CLAUDE.md with a
        PACT_START/PACT_END block and verifies remove_stale_kernel_block
        strips the PACT block cleanly without damaging user content.

        This closes the loop on the spawn overhead reduction: if migration
        regressed, the ~17KB of orchestrator content would remain loaded on
        every session start, defeating the purpose of the kernel split.
        """
        from shared.claude_md_manager import remove_stale_kernel_block  # type: ignore

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        (fake_home / ".claude").mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        claude_md = fake_home / ".claude" / "CLAUDE.md"
        claude_md.write_text(
            "# Personal Preferences\n"
            "\n"
            "Some user content I care about.\n"
            "\n"
            "<!-- PACT_START: Managed by pact-plugin - Do not edit this block -->\n"
            "# MISSION\n"
            "\n"
            "## S5 POLICY\n"
            "\n"
            "Non-Negotiables table with Security, Quality, Ethics rows...\n"
            "\n"
            "## Algedonic Signals\n"
            "\n"
            "HALT / ALERT catalog...\n"
            "<!-- PACT_END -->\n"
            "\n"
            "# My Other Notes\n"
            "\n"
            "More user content.\n",
            encoding="utf-8",
        )

        result = remove_stale_kernel_block()

        assert result is not None, (
            "remove_stale_kernel_block returned None for a CLAUDE.md that "
            "clearly contains a PACT_START/PACT_END block — migration "
            "did not run."
        )

        migrated = claude_md.read_text(encoding="utf-8")
        for forbidden in (
            "MISSION",
            "S5 POLICY",
            "Non-Negotiables",
            "Algedonic Signals",
            "PACT_START",
            "PACT_END",
        ):
            assert forbidden not in migrated, (
                f"After migration, '{forbidden}' is still present in home "
                f"CLAUDE.md — remove_stale_kernel_block regression. "
                f"Migrated content:\n{migrated}"
            )
        assert "Personal Preferences" in migrated, (
            "User content before the PACT block was lost by migration"
        )
        assert "My Other Notes" in migrated, (
            "User content after the PACT block was lost by migration"
        )
