"""
Location: pact-plugin/tests/test_spawn_overhead_benchmark.py
Summary: Byte-count regression gate for per-teammate spawn overhead.
Used by: pytest CI / local test runs.

The per-teammate spawn path delivers ONLY the teammate's agent body plus
the spawn-time skills: frontmatter preload. This test pins the per-
teammate body as a byte-level regression gate: any change that bleeds
orchestrator-scale content into a teammate body will blow past the
ceiling.

Companion guard: orchestrator persona content must NOT be referenced from
any teammate spawn-path artifact. `agents/pact-orchestrator.md` is delivered
via `claude --agent` for the team-lead session ONLY. A regression that
points teammate frontmatter or body at orchestrator content would re-
introduce the per-teammate-spawn cost the v4.0.0 cutover eliminated.
"""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "hooks"))

# Sentinel teammate for the regression check. backend-coder is one of the
# larger teammate bodies; if it stays under THRESHOLD_BYTES the rest do
# too.
SENTINEL_TEAMMATE = "pact-backend-coder.md"


class TestSpawnOverheadRegression:
    """Byte-count regression gate for v4.0.0 spawn overhead."""

    # Measured v4.0.0 baseline:
    #   pact-backend-coder.md (largest typical teammate body):  ~7800 bytes
    #
    # THRESHOLD_BYTES gives ~28% headroom over the sentinel body
    # measurement while staying well below ABSOLUTE_CEILING. Goal: catch
    # regressions that silently re-add orchestrator-scale content to a
    # teammate body, not to police normal small-scale growth.
    #
    # Tuning rules:
    #   - If a legitimate change pushes a teammate body over THRESHOLD_BYTES,
    #     first confirm the content genuinely belongs in the per-teammate
    #     spawn path (vs. a lazy-loaded skill or the orchestrator agent
    #     body), then raise THRESHOLD_BYTES in +1000-byte steps and update
    #     the baseline comment above.
    #   - Do NOT raise THRESHOLD_BYTES above ABSOLUTE_CEILING without
    #     re-examining whether orchestrator-scale content has crept in.
    THRESHOLD_BYTES = 10000

    # Hard ceiling: THRESHOLD_BYTES must never be raised above this value.
    # An orchestrator-body-scale teammate (~40KB) would mean the spawn-time
    # cost the v4.0.0 cutover removed has been re-introduced.
    ABSOLUTE_CEILING = 15000

    def test_threshold_within_absolute_ceiling(self):
        """THRESHOLD_BYTES must stay below ABSOLUTE_CEILING.

        Enforces the tuning rule mechanically — any PR that bumps
        THRESHOLD_BYTES past ABSOLUTE_CEILING triggers a test failure
        demanding investigation of orchestrator-scale content creep.
        """
        assert self.THRESHOLD_BYTES <= self.ABSOLUTE_CEILING, (
            f"THRESHOLD_BYTES ({self.THRESHOLD_BYTES}) exceeds "
            f"ABSOLUTE_CEILING ({self.ABSOLUTE_CEILING}). The spawn overhead "
            f"regression gate loses its value above {self.ABSOLUTE_CEILING} "
            f"bytes — investigate whether orchestrator-scale content has "
            f"crept back into the per-teammate spawn path before raising "
            f"this limit."
        )

    def test_sentinel_teammate_body_under_threshold(self):
        """The sentinel teammate body must stay under THRESHOLD_BYTES.

        Direct measurement of the per-teammate spawn cost: the agent
        body IS the spawn-time delivery.
        """
        body_path = _REPO_ROOT / "agents" / SENTINEL_TEAMMATE
        body_bytes = len(body_path.read_bytes())
        assert body_bytes <= self.THRESHOLD_BYTES, (
            f"{SENTINEL_TEAMMATE} body is {body_bytes} bytes, exceeds "
            f"THRESHOLD_BYTES ({self.THRESHOLD_BYTES}). Investigate whether "
            f"orchestrator-scale content was added to the teammate body, "
            f"or move the new content to a lazy-loaded skill."
        )

    def test_orchestrator_body_not_in_spawn_path(self):
        """Orchestrator persona content must NOT be referenced from any
        teammate frontmatter or body in a way that would deliver the
        orchestrator body at teammate spawn.

        Under v4.0.0 `agents/pact-orchestrator.md` is delivered via
        `claude --agent PACT:pact-orchestrator` for the team-lead session
        ONLY. If a teammate frontmatter `skills:` list or body cross-
        reference pointed at orchestrator content, every teammate spawn
        would carry the orchestrator's body — the exact cost the v4.0.0
        cutover eliminated.
        """
        agents_dir = _REPO_ROOT / "agents"
        offenders = []
        forbidden_substrings = (
            "pact-orchestrator.md",
            "PACT:pact-orchestrator",
            "../agents/pact-orchestrator",
        )
        for agent_path in sorted(agents_dir.glob("*.md")):
            if agent_path.name == "pact-orchestrator.md":
                continue  # the orchestrator file itself may self-reference
            text = agent_path.read_text(encoding="utf-8")
            for substr in forbidden_substrings:
                if substr in text:
                    offenders.append(
                        f"{agent_path.name}: contains {substr!r}"
                    )
        assert not offenders, (
            "teammate agents must not reference orchestrator content in a "
            "spawn-path-loaded surface (frontmatter or body):\n"
            + "\n".join(offenders)
        )

    def test_peer_inject_prelude_plus_agent_body_under_threshold(self, tmp_path):
        """The full per-teammate spawn-time delivery (peer_inject prelude +
        agent body) must remain under THRESHOLD_BYTES.

        Measured:
          - peer_inject.get_peer_context() output (additionalContext
            injected at SubagentStart — the routing prelude + peer list +
            plugin banner + teachback reminder + completion-authority
            note + charter cross-ref)
          - The agent body file (sentinel teammate as representative)

        Not measured (by design): lazy-loaded content reachable only via
        Skill() invocations (bootstrap.md, protocol files), and the
        project CLAUDE.md routing block (separately size-gated).
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
            _REPO_ROOT / "agents" / SENTINEL_TEAMMATE
        ).read_text(encoding="utf-8")

        peer_bytes = len(peer_ctx.encode("utf-8"))
        agent_bytes = len(agent_body.encode("utf-8"))
        total = peer_bytes + agent_bytes

        assert total < self.THRESHOLD_BYTES, (
            f"Spawn overhead regression: total static spawn-path content is "
            f"{total} bytes (peer_inject: {peer_bytes}, agent body: "
            f"{agent_bytes}), exceeds THRESHOLD_BYTES "
            f"({self.THRESHOLD_BYTES}). Investigate what grew and whether "
            f"it should live in a lazy-loaded skill instead of the always-"
            f"on spawn path."
        )

    def test_bootstrap_md_not_in_spawn_path(self):
        """session_init.py and peer_inject.py are the two hooks whose
        output lands in additionalContext at session/teammate spawn. Neither
        may directly reference bootstrap.md content — bootstrap.md must
        only be reachable via the Skill("PACT:bootstrap") invocation.

        A direct reference (Read or string-embed) would deliver
        bootstrap.md content on every teammate spawn, defeating the
        lazy-load contract.
        """
        session_init_src = (
            _REPO_ROOT / "hooks" / "session_init.py"
        ).read_text(encoding="utf-8")
        peer_inject_src = (
            _REPO_ROOT / "hooks" / "peer_inject.py"
        ).read_text(encoding="utf-8")

        assert "bootstrap.md" not in session_init_src, (
            "session_init.py references bootstrap.md directly — this is a "
            "spawn-path regression. bootstrap.md must only be loaded "
            "lazily via the Skill(\"PACT:bootstrap\") invocation."
        )
        assert "bootstrap.md" not in peer_inject_src, (
            "peer_inject.py references bootstrap.md directly — this is a "
            "spawn-path regression. bootstrap.md must only be loaded "
            "lazily via the Skill(\"PACT:bootstrap\") invocation."
        )
