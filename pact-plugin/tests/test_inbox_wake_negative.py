"""
Negative tests for the inbox-wake surface.

NON_ARMING_FILES must NOT contain `Monitor(` arming or sentinel headings.
Phantom-green guard: if a future refactor accidentally lands an arm step
in the wrong file, these tests catch it.

ARCHITECTURAL ANCHOR — the NON_ARMING set is bound to a specific design
decision, not a convention. The wake mechanism is **lead-side only**:
- Path-1 (in-process teammates dispatched as Agent Teams subagents) wake
  via the Claude Code harness's `waitForNextPromptOrShutdown` reactive
  event-loop. No external wake mechanism is needed for them; idle-boundary
  delivery is in-process.
- Path-2 (the lead's own session) is gated by `useInboxPoller`'s
  `!isLoading && !focusedInputDialog` precondition, and THAT is what the
  Monitor + Cron pair bypasses via stdout-line-as-event delivery.

The original PR proposal floated teammate-side arming (an
`_INBOX_MONITOR_DIRECTIVE` injection in `peer_inject.py` and an arm-step
in `pact-agent-teams/SKILL.md`); the design spike empirically refuted
that — path-1 doesn't need it, and adding it would create double-arming
for any in-process teammate. The post-spike scope removal made
teammate-side arming a NEGATIVE invariant: it must not reappear in any
of the surfaces a teammate's bootstrap might pull in.

NON_ARMING set (the 4 surfaces a teammate is most likely to read at
spawn time, plus 2 inherit/no-arm command files):
  - commands/imPACT.md — inherits parent workflow's wake; prose-only note.
  - commands/bootstrap.md — no workflow-arming responsibility.
  - skills/pact-agent-teams/SKILL.md — post-spike removal; teammate-side
    arming was the rejected proposal; this is the negative pin so it
    never reappears silently.
  - hooks/peer_inject.py — post-spike removal; the original proposal had
    a teammate-side `_INBOX_MONITOR_DIRECTIVE` injection; the spike
    removed it.
"""
import pytest

from fixtures.inbox_wake import (
    NON_ARMING_FILES, ALL_SENTINELS,
    _read,
)


class TestNonArmingFilesAreClean:
    @pytest.mark.parametrize("path", NON_ARMING_FILES, ids=lambda p: p.name)
    def test_no_monitor_invocation(self, path):
        assert path.exists(), f"NON_ARMING surface missing: {path}"
        text = _read(path)
        assert "Monitor(" not in text, (
            f"{path.name} unexpectedly contains 'Monitor(' — wake-arming "
            "must not land in this surface (phantom-green guard)"
        )

    @pytest.mark.parametrize("path", NON_ARMING_FILES, ids=lambda p: p.name)
    def test_no_arm_sentinels(self, path):
        text = _read(path)
        for sentinel in ALL_SENTINELS:
            assert sentinel not in text, (
                f"{path.name} contains '{sentinel}' — wake-arming sentinels "
                "must not land in this surface"
            )
