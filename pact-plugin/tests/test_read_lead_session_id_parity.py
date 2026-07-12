"""Behavioral-parity DRIFT-GUARD for the two _read_lead_session_id
implementations.

shared.pact_context._read_lead_session_id is the SSOT implementation;
task_claim_gate consumes it via a from-import (a module-attribute binding, so
the established task_claim_gate._read_lead_session_id patch seam survives —
this file exercises the gate's binding, which IS the pact_context function).
session_registry._read_lead_session_id is the ONE remaining inline copy: that
module is a self-contained leaf that imports nothing from shared.*, so it
cannot be re-pointed to the SSOT. Its docstring claims LOGIC-PARITY with the
pact_context copy; this test makes that claim enforced — if a future edit
changes the leadSessionId-resolution behavior of either implementation without
the other, this test goes red. It mirrors the established inline-copy
parity-guard convention already used for _sanitize_agent_name
(session_registry vs peer_context) and _is_safe_team_segment
(session_registry vs session_end).

WHAT "PARITY" MEANS HERE — BEHAVIORAL, not structural. The two implementations
have a KNOWN, ACCEPTED STRUCTURAL DIVERGENCE that this test deliberately
accommodates:

  * session_registry._read_lead_session_id(team) — one positional arg; resolves
    the teams dir via the INLINED _config_root() (no parameter); gates the team
    segment with _is_safe_team_segment.
  * pact_context._read_lead_session_id(team_name, teams_dir=None) — the SSOT
    copy the gate binds; takes an optional teams_dir; resolves via
    get_claude_config_dir() when teams_dir is None; gates with
    is_safe_path_component (a positive-allowlist regex).

So this test does NOT assert the functions are byte-identical. It asserts they
return the SAME leadSessionId for the SAME LOGICAL INPUT — the same on-disk
teams/<team>/config.json under a shared Path.home() redirect (both config-root
mechanisms honor Path.home(): session_registry's _config_root() and
task_claim_gate's get_claude_config_dir() both derive from it).

KNOWN SAFE-SEGMENT BOUNDARY (intentional, NOT a parity violation): the two
segment guards reject DIFFERENT supersets of unsafe inputs. is_safe_path_component
uses a positive allowlist that ALSO rejects whitespace and uppercase-only-quirks,
while _is_safe_team_segment rejects only controls / NUL / path separators / dot
traversal. A team name containing a space (e.g. "team name") therefore reads the
config in session_registry but short-circuits to "" in task_claim_gate. This is a
real behavioral difference, but it is NOT in scope for the parity contract: both
still satisfy the fail-safe invariant (an "unsafe" segment -> ""), they merely
draw the "unsafe" line at different places. The parity cases below all use
segments BOTH guards agree on (valid pact-* names, and an unambiguously-unsafe
"../escape" / "a/b" that both reject), so the assertion is faithful. The space
divergence is pinned separately (test_known_segment_guard_boundary_is_documented)
so a future reader knows it is expected, not a missed drift.

Non-vacuity: the parity assertion compares the two LIVE implementations against
each other, so a drift in EITHER copy flips it red (verified by construction — if
one copy were edited to, say, read a different key, its return would diverge from
the other on the valid-config case). The valid-config case (Branch 8 / the only
non-"" return) is the positive control that makes the five ""-returning cases
non-vacuous: "" must mean a genuine miss, not that both copies always return "".
"""

import json
from pathlib import Path

import pytest

# session_registry is under hooks/shared/; task_claim_gate is under hooks/.
# tests/conftest.py puts both hooks/ and hooks/shared/ on sys.path.
from shared.session_registry import _read_lead_session_id as _sr_read_lead
import task_claim_gate as _tcg

_tcg_read_lead = _tcg._read_lead_session_id

_LEAD_SID = "lead-sess-parity-9999"


@pytest.fixture
def parity_env(tmp_path, monkeypatch):
    """Redirect Path.home() into tmp_path so BOTH copies' config-root mechanisms
    (_config_root and get_claude_config_dir) resolve the SAME teams/<team>/
    config.json. Returns a helper to write team configs of arbitrary shape."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    class _Env:
        home = tmp_path

        @staticmethod
        def write_config(team, config_obj):
            d = tmp_path / ".claude" / "teams" / team
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(
                config_obj if isinstance(config_obj, str) else json.dumps(config_obj),
                encoding="utf-8",
            )

    return _Env


def _assert_parity(team):
    """Both copies return the SAME value for the SAME logical input."""
    sr = _sr_read_lead(team)
    tcg = _tcg_read_lead(team)
    assert sr == tcg, (
        f"_read_lead_session_id DRIFT on team={team!r}: "
        f"session_registry returned {sr!r} but task_claim_gate returned {tcg!r}. "
        f"The two inline copies must stay behaviorally parity (same logical input "
        f"-> same leadSessionId)."
    )
    return sr


class TestReadLeadSessionIdParity:
    """The six dispatch-named cases — both copies must agree on each."""

    def test_valid_config_both_return_same_lead_session_id(self, parity_env):
        """POSITIVE CONTROL (makes the five ""-cases non-vacuous): a well-formed
        config -> BOTH copies return the SAME leadSessionId STRING."""
        parity_env.write_config(
            "pact-parity",
            {"members": [{"name": "alice"}], "leadSessionId": _LEAD_SID},
        )
        assert _assert_parity("pact-parity") == _LEAD_SID

    def test_missing_config_both_return_empty(self, parity_env):
        """No teams/<team>/config.json -> both copies -> ""."""
        assert _assert_parity("pact-nodir") == ""

    def test_malformed_json_both_return_empty(self, parity_env):
        """config.json is not valid JSON -> both copies -> ""."""
        parity_env.write_config("pact-bad", "{ not valid json ]")
        assert _assert_parity("pact-bad") == ""

    def test_non_dict_top_level_both_return_empty(self, parity_env):
        """config.json top-level is a JSON list -> both copies -> ""."""
        parity_env.write_config("pact-list", ["alice", "bob"])
        assert _assert_parity("pact-list") == ""

    def test_missing_lead_session_id_key_both_return_empty(self, parity_env):
        """Valid object, members[] but NO leadSessionId key -> both copies -> ""."""
        parity_env.write_config("pact-nokey", {"members": [{"name": "alice"}]})
        assert _assert_parity("pact-nokey") == ""

    def test_non_string_lead_session_id_both_return_empty(self, parity_env):
        """leadSessionId present but not a string (int) -> both copies -> ""."""
        parity_env.write_config(
            "pact-int", {"members": [{"name": "alice"}], "leadSessionId": 12345}
        )
        assert _assert_parity("pact-int") == ""

    @pytest.mark.parametrize("unsafe_team", ["../escape", "a/b", "..", "."])
    def test_unsafe_team_segment_both_return_empty(self, parity_env, unsafe_team):
        """An unambiguously-unsafe @team segment (path separator / dot traversal)
        that BOTH guards reject -> both copies short-circuit to "" before any FS
        read."""
        assert _assert_parity(unsafe_team) == ""


class TestKnownSegmentGuardBoundary:
    """Pin the ONE documented behavioral divergence so it is not mistaken for a
    missed drift: the two segment guards draw the "unsafe" line differently, so a
    team name with an internal SPACE is read by session_registry but rejected by
    task_claim_gate. Both still honor the fail-safe invariant (unsafe -> "").
    This is OUT of the parity contract by design; pinning it keeps the divergence
    visible and intentional rather than surprising."""

    def test_space_in_team_name_is_the_documented_boundary(self, parity_env):
        parity_env.write_config(
            "team name", {"members": [{"name": "x"}], "leadSessionId": _LEAD_SID}
        )
        sr = _sr_read_lead("team name")
        tcg = _tcg_read_lead("team name")
        # session_registry's _is_safe_team_segment admits the space -> reads the
        # config; task_claim_gate's positive-allowlist is_safe_path_component
        # rejects it -> "". Documented, intentional boundary.
        assert sr == _LEAD_SID
        assert tcg == ""
        # Both still satisfy the fail-safe contract (never raise; tcg fails safe).
