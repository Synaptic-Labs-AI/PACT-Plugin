"""
Negative tests for the inbox-wake surface.

NON_ARMING_FILES must NOT contain Monitor( arming or sentinel headings.
Phantom-green guard: if a future refactor accidentally lands an arm step
in the wrong file, these tests catch it.

NON_ARMING set:
  - commands/imPACT.md (inherits parent workflow's wake; prose-only note)
  - commands/bootstrap.md (no workflow-arming responsibility)
  - skills/pact-agent-teams/SKILL.md (post-spike removal — teammate-side
    arming was originally proposed but the spike confirmed it's
    unnecessary; this is the negative pin so it never reappears silently)
  - hooks/peer_inject.py (post-spike removal — the original proposal had a
    teammate-side `_INBOX_MONITOR_DIRECTIVE` injection; the spike removed it)
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
