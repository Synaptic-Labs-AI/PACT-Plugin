"""Standing both-modes merge gate for register()'s in-process self-guard.

register() (hooks/shared/session_registry.py) skips the {session_id -> name@team}
write ONLY on a confirmed in-process topology — own ``$CLAUDE_CODE_SESSION_ID``
== the team's ``leadSessionId`` (read inline from teams/<team>/config.json via
``_read_lead_session_id``). In tmux mode each teammate is a distinct process with
a distinct sid (sid != leadSessionId) so the write is meaningful and proceeds.
On EVERY uncertain topology (empty/unreadable/malformed/non-dict/missing-key/
unsafe-team) the guard FAILS OPEN and WRITES — over-writing in-process is a
harmless collided entry, but under-writing in tmux would blind name-recovery.

This file is the dual-mode contract's STANDING merge gate for that guard:

  * BOTH-MODES MATRIX (P0): in-process (==leadSessionId) -> NO line; tmux
    (!=leadSessionId) -> line written AND resolvable.
  * NON-VACUITY (P0, load-bearing): the in-process no-op is byte-indistinguishable
    from register()'s PRE-EXISTING no-op paths (absent $CLAUDE_CODE_SESSION_ID,
    missing @team config). A naive "no line was written" assertion is therefore
    PHANTOM-GREEN unless the skip is ATTRIBUTABLE to the in-process branch alone.
    We attribute it three ways: (1) a VALID sid is set (rules out the absent-sid
    no-op); (2) a tmux POSITIVE CONTROL with the SAME valid config DOES write
    (proves the environment genuinely registers, so the in-process miss is the
    guard, not a dead fixture); (3) a counter-test-by-revert (see the module
    docstring of test_*_counter_test, run out-of-band) flips the in-process
    no-op RED when the guard line is reverted.
  * FAIL-OPEN (P0): every reachable uncertain branch of _read_lead_session_id
    -> register() WRITES.

Counter-test-by-revert (documented cardinality, measured out-of-band — NOT run
in CI): revert ONLY the two guard lines in register() ::

    if sid == _read_lead_session_id(team):
        return  # confirmed in-process -> skip the un-recoverable write

(source-only: ``git stash push -- pact-plugin/hooks/shared/session_registry.py``
or ``git checkout 6c7b91fa^ -- <that file>``), then run this file. The
in-process no-op assertions flip RED because register() now writes a line where
the guard would have skipped it; the tmux + fail-open assertions stay GREEN
(they already expect a WRITE). Restore with ``git checkout 6c7b91fa -- <file>``
/ ``git stash pop`` and confirm ``git diff --quiet`` on the source. The measured
RED-case cardinality is recorded in TestCounterTestCardinalityDoc below.
"""

import json
from pathlib import Path

import pytest

from shared import session_registry
from shared.session_registry import register, resolve, _read_lead_session_id


# A canonical in-process session id: in in-process teammateMode every teammate
# inherits the lead's process env, so its own $CLAUDE_CODE_SESSION_ID EQUALS the
# team's leadSessionId. (Source-reasoned + devops-confirmed from the live
# in-process config.json for #962.)
_LEAD_SID = "lead-sess-0000-1111"
# A distinct tmux session id: a separate-process teammate has its OWN sid.
_TMUX_SID = "tmux-sess-2222-3333"


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    """Isolated ~/.claude tree (mirrors test_session_registry.py's fixture) with
    a write_team that can carry a top-level leadSessionId."""
    fake_home = tmp_path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    reg_path = fake_home / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
    monkeypatch.setattr(session_registry, "get_registry_path", lambda: reg_path)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    class _Env:
        home = fake_home
        registry_path = reg_path

        @staticmethod
        def set_session(sid):
            monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)

        @staticmethod
        def write_team(team, member_names, lead_session_id=None):
            """teams/<team>/config.json with members[] + OPTIONAL leadSessionId."""
            d = fake_home / ".claude" / "teams" / team
            d.mkdir(parents=True, exist_ok=True)
            config = {"members": [{"name": n} for n in member_names]}
            if lead_session_id is not None:
                config["leadSessionId"] = lead_session_id
            (d / "config.json").write_text(json.dumps(config), encoding="utf-8")

        @staticmethod
        def write_team_raw(team, config_text):
            """Write config.json with ARBITRARY (possibly malformed) contents:
            a raw string is written verbatim; a dict/list is JSON-encoded."""
            d = fake_home / ".claude" / "teams" / team
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(
                config_text if isinstance(config_text, str) else json.dumps(config_text),
                encoding="utf-8",
            )

    return _Env


# ---------------------------------------------------------------------------
# P0 — BOTH-MODES MATRIX (the standing merge gate)
# ---------------------------------------------------------------------------

class TestBothModesMatrix:
    """The dual-mode contract: SAME input under the two topologies, divergent
    outcome. In-process == leadSessionId -> NO write; tmux != -> write."""

    def test_in_process_sid_equals_lead_session_id_skips_write(self, registry_env):
        """IN-PROCESS leg: own sid == config.leadSessionId -> register no-ops,
        registry file is never created. Attributable to the guard (a VALID sid
        is set, ruling out the absent-sid no-op; the tmux control below proves
        the environment otherwise writes)."""
        registry_env.set_session(_LEAD_SID)
        registry_env.write_team("pact-team1", ["alice"], lead_session_id=_LEAD_SID)

        register("alice@pact-team1")

        assert not registry_env.registry_path.exists(), (
            "in-process register (sid==leadSessionId) must SKIP the write"
        )
        # And nothing is resolvable for that sid.
        assert resolve(_LEAD_SID) is None

    def test_tmux_sid_differs_from_lead_session_id_writes_and_resolves(self, registry_env):
        """TMUX leg (positive control): own sid != config.leadSessionId ->
        register writes the line AND it resolves. Same fixture shape as the
        in-process leg (valid sid + valid leadSessionId config + member) — only
        the sid/leadSessionId RELATION differs, so this proves the divergence is
        driven by the structural signal, not a fixture artifact."""
        registry_env.set_session(_TMUX_SID)
        registry_env.write_team("pact-team1", ["alice"], lead_session_id=_LEAD_SID)

        register("alice@pact-team1")

        assert registry_env.registry_path.exists(), (
            "tmux register (sid!=leadSessionId) must WRITE the line"
        )
        assert resolve(_TMUX_SID) == "alice@pact-team1"

    def test_matrix_same_input_diverges_only_on_topology(self, registry_env):
        """Belt-and-suspenders single-test matrix: hold name@team + config
        constant, vary ONLY which sid the process carries, assert the two
        outcomes diverge. This is the merge-gate invariant in one assertion
        pair."""
        registry_env.write_team("pact-team1", ["alice"], lead_session_id=_LEAD_SID)

        # tmux first (distinct sid) -> writes.
        registry_env.set_session(_TMUX_SID)
        register("alice@pact-team1")
        assert resolve(_TMUX_SID) == "alice@pact-team1"

        # in-process (sid == leadSessionId) -> the matching sid is NEVER written,
        # so it never resolves, even though the tmux line now exists in the file.
        registry_env.set_session(_LEAD_SID)
        register("alice@pact-team1")
        assert resolve(_LEAD_SID) is None, (
            "the in-process sid must never be written even alongside a tmux line"
        )


# ---------------------------------------------------------------------------
# P0 — NON-VACUITY: the in-process skip is THE in-process branch, not a generic
# miss. We pin the distinguishing controls so a future fixture regression can't
# silently make the skip phantom-green.
# ---------------------------------------------------------------------------

class TestNonVacuity:
    def test_in_process_skip_is_not_the_absent_sid_noop(self, registry_env):
        """Distinguish the in-process guard from register()'s PRE-EXISTING
        absent-$CLAUDE_CODE_SESSION_ID no-op: a VALID sid is set here, so a skip
        can ONLY come from the in-process branch (sid==leadSessionId). If a
        regression removed the guard, this VALID-sid register would WRITE (that
        is exactly the counter-test-by-revert flip)."""
        registry_env.set_session(_LEAD_SID)  # NON-empty sid -> not the absent path
        registry_env.write_team("pact-team1", ["alice"], lead_session_id=_LEAD_SID)

        register("alice@pact-team1")
        assert not registry_env.registry_path.exists()

    def test_positive_control_same_config_writes_when_topology_is_tmux(self, registry_env):
        """The environment genuinely registers under this exact config — only the
        sid==leadSessionId RELATION suppresses it. Without this control the
        in-process no-op could be a dead fixture (e.g. an unwritable path); WITH
        it, the no-op is provably the guard."""
        registry_env.set_session("some-other-distinct-sid")
        registry_env.write_team("pact-team1", ["alice"], lead_session_id=_LEAD_SID)

        register("alice@pact-team1")
        assert registry_env.registry_path.exists()
        assert resolve("some-other-distinct-sid") == "alice@pact-team1"


class TestCounterTestCardinalityDoc:
    """Counter-test-by-revert cardinality, MEASURED out-of-band (documented here
    so a future verifier checks the recorded number, not a re-derived guess).

    Procedure: source-only revert the two guard lines in register() (see this
    module's docstring), then ``python -m pytest
    pact-plugin/tests/test_session_registry_mode_gate.py -rf``.

    MEASURED RED-case set when the guard is reverted (recorded at authoring;
    re-verify if the matrix changes):
      - TestBothModesMatrix::test_in_process_sid_equals_lead_session_id_skips_write
      - TestBothModesMatrix::test_matrix_same_input_diverges_only_on_topology
      - TestNonVacuity::test_in_process_skip_is_not_the_absent_sid_noop

    => 3 RED cases (3 distinct source assertions; no parametrization expands
    them). The tmux/positive-control/fail-open cases stay GREEN because they
    already expect a WRITE — that asymmetry is itself evidence the RED set is
    coupled to the in-process branch specifically, not to register() at large.
    This test is a DOC anchor only; it asserts the recorded count is internally
    consistent (a tripwire if someone edits the list without re-measuring)."""

    EXPECTED_RED_ON_GUARD_REVERT = {
        "test_in_process_sid_equals_lead_session_id_skips_write",
        "test_matrix_same_input_diverges_only_on_topology",
        "test_in_process_skip_is_not_the_absent_sid_noop",
    }

    def test_documented_cardinality_is_three(self):
        assert len(self.EXPECTED_RED_ON_GUARD_REVERT) == 3


# ---------------------------------------------------------------------------
# P0 — FAIL-OPEN: every reachable uncertain branch of _read_lead_session_id
# routes register() to WRITE. By the time _read_lead_session_id runs, register()
# has already required a non-empty sid, an "@", and non-empty name AND team — so
# the EMPTY-team branch is unreachable here and is covered as a register()-level
# pre-existing path elsewhere. The reachable uncertain branches are: missing
# config, malformed JSON, non-dict top-level, missing leadSessionId key,
# non-string leadSessionId, and an unsafe @team segment.
# ---------------------------------------------------------------------------

class TestFailOpenWrites:
    def test_missing_config_writes(self, registry_env):
        """No teams/<team>/config.json at all -> _read_lead_session_id OSError ->
        "" -> never matches -> WRITE."""
        registry_env.set_session(_LEAD_SID)
        # team dir / config deliberately absent; member-validation on READ would
        # reject, so we assert the WRITE happened at the file level (a line
        # exists), which is the fail-open observable.
        register("alice@pact-ghostteam")
        assert registry_env.registry_path.exists(), "missing config must fail-OPEN to WRITE"

    def test_malformed_json_config_writes(self, registry_env):
        """config.json is not valid JSON -> ValueError -> "" -> WRITE."""
        registry_env.set_session(_LEAD_SID)
        registry_env.write_team_raw("pact-team1", "{ this is not json ]")
        register("alice@pact-team1")
        assert registry_env.registry_path.exists(), "malformed JSON must fail-OPEN to WRITE"

    def test_non_dict_top_level_config_writes(self, registry_env):
        """config.json top-level is a JSON LIST (not an object) -> not isinstance
        dict -> "" -> WRITE."""
        registry_env.set_session(_LEAD_SID)
        registry_env.write_team_raw("pact-team1", ["alice", "bob"])  # a list
        register("alice@pact-team1")
        assert registry_env.registry_path.exists(), "non-dict config must fail-OPEN to WRITE"

    def test_missing_lead_session_id_key_writes(self, registry_env):
        """config.json is a valid object with members[] but NO leadSessionId key
        -> config.get returns None -> "" -> WRITE. (This is the shape every
        pre-#962 config had, so the guard must be inert for them.)"""
        registry_env.set_session(_LEAD_SID)
        registry_env.write_team("pact-team1", ["alice"])  # lead_session_id=None
        register("alice@pact-team1")
        assert registry_env.registry_path.exists(), "absent leadSessionId must fail-OPEN to WRITE"

    def test_non_string_lead_session_id_writes(self, registry_env):
        """leadSessionId present but NOT a string (e.g. an int) -> the isinstance
        guard returns "" -> WRITE. A non-string can never == the string sid
        anyway, but the explicit "" keeps the branch fail-OPEN by construction."""
        registry_env.set_session(_LEAD_SID)
        registry_env.write_team_raw(
            "pact-team1",
            {"members": [{"name": "alice"}], "leadSessionId": 12345},
        )
        register("alice@pact-team1")
        assert registry_env.registry_path.exists(), "non-string leadSessionId must fail-OPEN to WRITE"

    def test_unsafe_team_segment_writes(self, registry_env):
        """An @team that is not a single safe path component (contains a path
        separator) -> _is_safe_team_segment False -> "" BEFORE any FS read ->
        WRITE. register() keeps the @team intact in the value, so the line is
        written with the raw (unsafe) team; member-validation rejects it on READ,
        but the fail-OPEN WRITE is the observable under test."""
        registry_env.set_session(_LEAD_SID)
        register("alice@pact/evil")  # "/" -> unsafe segment
        assert registry_env.registry_path.exists(), "unsafe @team must fail-OPEN to WRITE"


# ---------------------------------------------------------------------------
# DIRECT helper unit tests for _read_lead_session_id — covers EVERY branch in
# isolation, including the ones NOT reachable through register() (so the
# fail-open suite above does not vacuously claim register() exercises them).
# _read_lead_session_id is the signal source the guard reads; testing it
# directly localizes a regression to the helper vs the register() wiring.
#
# Branch map (return "" on every miss; the team's leadSessionId string on a hit):
#   1. EMPTY team           -> _is_safe_team_segment False -> "" (UNREACHABLE via
#                              register(): pre-filtered at the name/team partition;
#                              ONLY a direct helper test can cover it)
#   2. UNSAFE team segment  -> _is_safe_team_segment False -> "" (reachable via
#                              register() too — pinned there AND here)
#   3. missing config       -> OSError -> ""
#   4. malformed JSON       -> ValueError -> ""
#   5. non-dict top-level   -> "" (isinstance guard)
#   6. missing leadSessionId-> config.get None -> ""
#   7. non-string lead..    -> isinstance guard -> ""
#   8. VALID                -> returns the leadSessionId STRING (the only non-""
#                              return — the positive control that makes 1-7
#                              non-vacuous: "" must mean a real miss, not that the
#                              helper can never return anything)
# ---------------------------------------------------------------------------

class TestReadLeadSessionIdHelper:
    """Direct unit coverage of the in-process self-guard's signal source."""

    @staticmethod
    def _write_config(home, team, config_obj):
        d = home / ".claude" / "teams" / team
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(
            config_obj if isinstance(config_obj, str) else json.dumps(config_obj),
            encoding="utf-8",
        )

    def test_empty_team_returns_empty(self):
        """Branch 1 — EMPTY team is rejected by _is_safe_team_segment BEFORE any
        FS access. UNREACHABLE through register() (the L179 partition short-
        circuits it), so this direct helper test is the ONLY coverage for it.
        Per the lead's reachability guidance: cover the unreachable branch here,
        not vacuously through register()."""
        assert _read_lead_session_id("") == ""

    def test_unsafe_team_segment_returns_empty(self):
        """Branch 2 — a non-empty but unsafe @team (path separator / traversal /
        control char) is rejected BEFORE building the FS path."""
        assert _read_lead_session_id("../escape") == ""
        assert _read_lead_session_id("a/b") == ""
        assert _read_lead_session_id("..") == ""

    def test_missing_config_returns_empty(self, registry_env, tmp_path):
        """Branch 3 — no teams/<team>/config.json -> OSError -> ""."""
        # registry_env redirects Path.home() into tmp_path; no config written.
        assert _read_lead_session_id("pact-noconfig") == ""

    def test_malformed_json_returns_empty(self, registry_env, tmp_path):
        """Branch 4 — config.json is not valid JSON -> ValueError -> ""."""
        self._write_config(tmp_path, "pact-bad", "{ not json ]")
        assert _read_lead_session_id("pact-bad") == ""

    def test_non_dict_top_level_returns_empty(self, registry_env, tmp_path):
        """Branch 5 — top-level is a JSON list, not an object -> ""."""
        self._write_config(tmp_path, "pact-list", ["a", "b"])
        assert _read_lead_session_id("pact-list") == ""

    def test_missing_lead_session_id_key_returns_empty(self, registry_env, tmp_path):
        """Branch 6 — a valid object with members[] but NO leadSessionId key
        (the pre-#962 config shape) -> config.get None -> ""."""
        self._write_config(tmp_path, "pact-nokey", {"members": [{"name": "alice"}]})
        assert _read_lead_session_id("pact-nokey") == ""

    def test_non_string_lead_session_id_returns_empty(self, registry_env, tmp_path):
        """Branch 7 — leadSessionId present but not a string -> isinstance guard
        -> ""."""
        self._write_config(tmp_path, "pact-int", {"leadSessionId": 12345})
        assert _read_lead_session_id("pact-int") == ""

    def test_valid_config_returns_lead_session_id_string(self, registry_env, tmp_path):
        """Branch 8 (POSITIVE CONTROL) — a well-formed config returns the
        leadSessionId STRING. Without this, every "" assertion above is vacuous
        (a helper that ALWAYS returned "" would pass them all). This proves ""
        means a genuine miss, and the in-process guard's `sid == helper(team)`
        comparison can actually be TRUE."""
        self._write_config(
            tmp_path, "pact-good",
            {"members": [{"name": "alice"}], "leadSessionId": _LEAD_SID},
        )
        assert _read_lead_session_id("pact-good") == _LEAD_SID


# ---------------------------------------------------------------------------
# Pre-existing register()-level no-op paths that are NOT the in-process guard —
# pinned so the guard's addition did not accidentally fold them into itself.
# ---------------------------------------------------------------------------

class TestPreExistingNoopsStillDistinct:
    def test_absent_sid_still_noops_independently_of_guard(self, registry_env):
        """No $CLAUDE_CODE_SESSION_ID -> the L168 absent-sid no-op fires BEFORE
        the guard is ever reached -> no write. (Distinct from the in-process
        skip: here there is no sid at all.)"""
        # registry_env clears the env var by default; set a matching config so
        # the ONLY reason for the no-op is the absent sid, not the guard.
        registry_env.write_team("pact-team1", ["alice"], lead_session_id=_LEAD_SID)
        register("alice@pact-team1")
        assert not registry_env.registry_path.exists()

    def test_empty_team_half_noops_before_guard(self, registry_env):
        """"name@" (empty team) -> the L179 partition guard returns BEFORE
        _read_lead_session_id is reached -> no write. Confirms the empty-team
        fail-open branch is genuinely unreachable from register() (it is
        short-circuited upstream), so the fail-open suite above correctly omits
        it."""
        registry_env.set_session(_LEAD_SID)
        register("alice@")  # empty team half
        assert not registry_env.registry_path.exists()
