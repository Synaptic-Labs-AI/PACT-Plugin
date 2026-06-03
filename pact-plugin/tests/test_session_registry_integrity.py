"""Integrity + fail-safe edge-DEPTH tests for the self-registration registry
(hooks/shared/session_registry.py) — #885.

The registry value is SELF-ASSERTED by the teammate and therefore FORGEABLE; the
registry is LABELING-ONLY. These tests harden the boundary against the inputs an
adversary or a corrupt disk would actually produce, beyond the happy-path +
basic-fail-safe rows in test_session_registry.py:

  * FORGED CROSS-TEAM: a value whose name is a real member of SOME team but NOT of
    the @team it claims -> members[]-validation rejects on read.
  * REAL O_NOFOLLOW SYMLINK DEFEAT (by construction, not via the _is_under helper):
    a planted symlink AT the registry path -> register is a no-op AND resolve
    returns None, both via the O_NOFOLLOW open (ELOOP), never following the link.
  * SANITIZE-ON-READ: a value written RAW (bypassing register's write-side
    sanitizer) with an embedded control char -> resolve neutralizes it on read.
  * MALFORMED TEAM CONFIG: members null / non-list / non-JSON / list-of-junk ->
    _name_is_team_member returns False, resolve returns None, never raises.
  * NON-@ + corrupt stored values on the READ path.

EVERY assertion is fail-safe BY CONSTRUCTION: the function under test must RETURN
None / no-op for the bad input. None of these tests wrap the call in try/except to
"prove no raise" — if a corrupt input raises, that is a fail-safe BREACH and the
test must FAIL loudly (a try/except would paper over the very defect being tested).

Also exercises the production register CLI (``python session_registry.py register
--name ...``) end-to-end as a real subprocess — the exact invocation the agent-def
first-action imperative runs, which no in-process test otherwise covers.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import session_registry
from shared.session_registry import register, resolve

_REGISTRY_MODULE = Path(session_registry.__file__)


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    """Isolated ~/.claude tree (mirrors test_session_registry.py's fixture)."""
    fake_home = tmp_path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    reg_path = fake_home / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
    monkeypatch.setattr(session_registry, "REGISTRY_PATH", reg_path)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    class _Env:
        home = fake_home
        registry_path = reg_path

        @staticmethod
        def set_session(sid):
            monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)

        @staticmethod
        def write_team(team, member_names):
            d = fake_home / ".claude" / "teams" / team
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(
                json.dumps({"members": [{"name": n} for n in member_names]}),
                encoding="utf-8",
            )

        @staticmethod
        def write_team_raw(team, config_obj):
            """Write a team config that is NOT the canonical members-list shape
            (for the malformed-config rejections)."""
            d = fake_home / ".claude" / "teams" / team
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(
                config_obj if isinstance(config_obj, str) else json.dumps(config_obj),
                encoding="utf-8",
            )

        @staticmethod
        def write_raw_line(session_id, value):
            """Append a registry line DIRECTLY (bypassing register's write-side
            sanitizer/guards) so the READ path can be tested against a value the
            writer would never have produced — e.g. an embedded control char."""
            reg_path.parent.mkdir(parents=True, exist_ok=True)
            with open(reg_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"session_id": session_id, "value": value}) + "\n")

    return _Env


# ===========================================================================
# FORGED CROSS-TEAM — name valid somewhere, NOT in the @team it claims
# ===========================================================================

class TestForgedCrossTeam:
    def test_name_member_of_other_team_but_not_claimed_team_rejected(self, registry_env):
        """alice is a real member of pact-real, but the forged value claims
        pact-victim (where alice is NOT a member) -> rejected on read. The
        members[]-validation keys on the value's OWN claimed @team, so being a
        member of a DIFFERENT team does not grant entry."""
        registry_env.set_session("sess-forge")
        registry_env.write_team("pact-real", ["alice"])
        registry_env.write_team("pact-victim", ["bob"])  # alice NOT here
        register("alice@pact-victim")  # forged: claims a team alice isn't in
        assert resolve("sess-forge") is None

    def test_forged_team_that_does_not_exist_rejected(self, registry_env):
        """A value claiming a @team with no config directory at all -> rejected
        (no members to validate against -> not a member)."""
        registry_env.set_session("sess-forge2")
        registry_env.write_team("pact-real", ["alice"])
        register("alice@pact-ghostteam")
        assert resolve("sess-forge2") is None


# ===========================================================================
# REAL O_NOFOLLOW SYMLINK DEFEAT — by construction (the actual open, not _is_under)
# ===========================================================================

class TestSymlinkDefeatByConstruction:
    def test_register_does_not_follow_symlink_at_registry_path(self, registry_env, tmp_path):
        """A symlink planted AT the registry path -> register is a no-op via
        O_NOFOLLOW (the open raises ELOOP, swallowed) and the link TARGET is never
        written through. fail-safe by construction: register returns, never
        raises."""
        target = tmp_path / "attacker_target.jsonl"
        target.write_text("", encoding="utf-8")
        registry_env.registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_env.registry_path.symlink_to(target)

        registry_env.set_session("sess-sym")
        registry_env.write_team("pact-team1", ["alice"])
        register("alice@pact-team1")  # must not raise, must not write the target

        # The attacker-controlled target was NOT written through the link.
        assert target.read_text(encoding="utf-8") == ""

    def test_resolve_does_not_follow_symlink_at_registry_path(self, registry_env, tmp_path):
        """A symlink at the registry path whose target holds a VALID-looking line
        -> resolve returns None (O_NOFOLLOW open raises ELOOP -> miss), never
        reading through the link. Defeats a redirect-to-attacker-file read."""
        target = tmp_path / "attacker_target.jsonl"
        registry_env.write_team("pact-team1", ["alice"])
        target.write_text(
            json.dumps({"session_id": "sess-sym", "value": "alice@pact-team1"}) + "\n",
            encoding="utf-8",
        )
        registry_env.registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_env.registry_path.symlink_to(target)

        # Even though the link target has a valid line, resolve must NOT follow it.
        assert resolve("sess-sym") is None


# ===========================================================================
# SANITIZE-ON-READ — a raw value with an embedded control char is neutralized
# ===========================================================================

class TestSanitizeOnRead:
    def test_control_char_in_stored_value_neutralized_on_read(self, registry_env):
        """A value written RAW with an embedded newline-class control char (e.g.
        U+0085 NEL, which str.splitlines() and tokenizers treat as a line break)
        is sanitized on READ -> the control char is replaced before the resolved
        name reaches a caller / a role marker. Proves read-side sanitization is
        not skipped just because the writer would normally have sanitized."""
        registry_env.set_session("sess-ctl")
        registry_env.write_team("pact-team1", ["a_b"])  # the sanitized form is a member
        registry_env.write_raw_line("sess-ctl", "a\x85b@pact-team1")  # NEL injected
        resolved = resolve("sess-ctl")
        assert resolved == "a_b@pact-team1"
        # No raw control char survives into the resolved value.
        assert "\x85" not in resolved

    def test_close_paren_in_stored_value_neutralized_on_read(self, registry_env):
        """A close-paren (which could early-close a parenthetical role marker)
        in a raw stored value is replaced on read."""
        registry_env.set_session("sess-paren")
        registry_env.write_team("pact-team1", ["a_b"])  # ')' sanitizes to '_'
        registry_env.write_raw_line("sess-paren", "a)b@pact-team1")
        assert resolve("sess-paren") == "a_b@pact-team1"


# ===========================================================================
# MALFORMED TEAM CONFIG — members null / non-list / junk -> reject, never raise
# ===========================================================================

class TestMalformedTeamConfig:
    @pytest.mark.parametrize("config_obj", [
        {"members": None},                       # null members
        {"members": "alice"},                    # members is a string, not a list
        {"members": 42},                         # members is a number
        {"no_members_key": True},                # members key absent
        "[]",                                    # top-level list, not a dict
        "not json at all",                       # non-JSON
        {"members": [{"no_name": "x"}, "junk"]}, # list of entries with no usable name
    ])
    def test_malformed_config_rejects_resolve_never_raises(self, registry_env, config_obj):
        """Every malformed team-config shape -> resolve returns None (the name
        cannot be validated as a member) and NEVER raises. fail-safe by
        construction: no try/except here — a raise fails the test."""
        registry_env.set_session("sess-malformed")
        registry_env.write_team_raw("pact-malformed", config_obj)
        register("alice@pact-malformed")
        assert resolve("sess-malformed") is None

    def test_member_name_validation_is_fail_safe_unit(self, registry_env):
        """Direct unit on the validator: a malformed config -> False, never
        raises (the integrity check is the load-bearing forge-blunt)."""
        registry_env.write_team_raw("pact-bad", {"members": None})
        assert session_registry._name_is_team_member("alice", "pact-bad") is False
        # Missing config dir entirely -> False, never raises.
        assert session_registry._name_is_team_member("alice", "pact-absent") is False

    def test_unsafe_team_segment_rejected_before_path_build(self, registry_env):
        """Containment parity with the session_end prune: an @team that is not a
        single safe path segment is rejected BEFORE the teams/<team>/config.json
        path is built, not merely caught after.

        NON-VACUITY: './pact-real' resolves to the real pact-real config whose
        members include 'alice', so PRE-fix _name_is_team_member returned True —
        the containment hole where a traversal resolving into a live team wrongly
        validates a forged name@team. POST-fix the segment guard rejects the '/'
        and returns False. The legit 'pact-real' control still returns True (the
        guard does not break real single-segment teams). The NUL case
        (open/read_text raises ValueError on every Python version) returns False
        without raising; built via chr(0) so the test file holds no literal null
        byte.
        """
        registry_env.write_team("pact-real", ["alice"])
        m = session_registry._name_is_team_member
        # Control: legit single-segment team with a real member → True.
        assert m("alice", "pact-real") is True
        # Lever: a traversal resolving into the real team dir was wrongly KEPT
        # pre-fix; the segment guard now rejects it before any path is built.
        assert m("alice", "./pact-real") is False
        # NUL / traversal-segment @team → rejected, never raises.
        assert m("alice", "pact-" + chr(0) + "real") is False
        assert m("alice", "..") is False


# ===========================================================================
# CORRUPT READ-PATH VALUES — non-@, non-str, non-dict lines on resolve
# ===========================================================================

class TestCorruptReadPath:
    def test_stored_value_without_at_returns_none(self, registry_env):
        registry_env.write_team("pact-team1", ["alice"])
        registry_env.write_raw_line("sess-noat", "alice-no-team")  # no @ in value
        assert resolve("sess-noat") is None

    def test_stored_value_non_string_returns_none(self, registry_env):
        """A line whose value is a non-string (e.g. a number) is ignored on read
        -> None, never raises (isinstance(value, str) guard)."""
        registry_env.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with open(registry_env.registry_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"session_id": "sess-nonstr", "value": 12345}) + "\n")
        assert resolve("sess-nonstr") is None

    def test_non_dict_json_line_skipped(self, registry_env):
        """A JSON line that parses to a non-dict (e.g. a bare list/number) is
        skipped, not crashed on -> resolve misses cleanly."""
        registry_env.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with open(registry_env.registry_path, "a", encoding="utf-8") as fh:
            fh.write("[1, 2, 3]\n")            # non-dict JSON
            fh.write("42\n")                    # bare number
        assert resolve("sess-anything") is None

    def test_empty_name_half_in_stored_value_returns_none(self, registry_env):
        """A value like '@team' (empty name half) -> rejected on read (the name
        half is empty), never resolves to a bare '@team'."""
        registry_env.write_team("pact-team1", ["alice"])
        registry_env.write_raw_line("sess-emptyname", "@pact-team1")
        assert resolve("sess-emptyname") is None


# ===========================================================================
# REGISTER CLI ROUND-TRIP — the production __main__ invocation as a subprocess
# ===========================================================================

class TestRegisterCliRoundTrip:
    """The agent-def first-action imperative runs
    ``python3 <plugin_root>/hooks/shared/session_registry.py register --name 'n@t'``
    in SCRIPT MODE by direct path. No in-process test exercises that __main__
    path; this runs the REAL module as a subprocess from an UNRELATED cwd (proving
    the self-containment claim: zero shared.* deps, works from any cwd) and then
    resolves the result IN-PROCESS to confirm the round-trip."""

    def _run_cli(self, name_at_team, *, home, session_id, cwd):
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["CLAUDE_CODE_SESSION_ID"] = session_id
        return subprocess.run(
            [sys.executable, str(_REGISTRY_MODULE), "register", "--name", name_at_team],
            capture_output=True, text=True, env=env, cwd=str(cwd), timeout=30,
        )

    def test_cli_register_then_in_process_resolve(self, registry_env, tmp_path):
        """register via the real CLI subprocess (from a foreign cwd) -> the line
        lands -> in-process resolve recovers it. The fixture's REGISTRY_PATH +
        Path.home point into tmp_path, and the CLI inherits HOME, so both sides
        agree on the file location."""
        # The CLI child uses Path.home()/.claude/... so set HOME to the fixture home.
        registry_env.write_team("pact-cli", ["devops"])
        foreign_cwd = tmp_path / "some" / "unrelated" / "place"
        foreign_cwd.mkdir(parents=True)

        proc = self._run_cli(
            "devops@pact-cli", home=registry_env.home,
            session_id="sess-cli", cwd=foreign_cwd,
        )
        assert proc.returncode == 0, (
            f"register CLI exited {proc.returncode}; stderr=\n{proc.stderr}"
        )
        # In-process resolve (fixture REGISTRY_PATH == HOME/.claude/...) recovers it.
        assert resolve("sess-cli") == "devops@pact-cli"

    def test_cli_runs_from_foreign_cwd_no_shared_import_error(self, registry_env, tmp_path):
        """Self-containment: the script-mode invocation must NOT fail with a
        ModuleNotFoundError for shared.* — it imports nothing from shared. Run it
        from a deep unrelated cwd and assert a clean exit + empty stderr."""
        foreign_cwd = tmp_path / "x" / "y" / "z"
        foreign_cwd.mkdir(parents=True)
        proc = self._run_cli(
            "devops@pact-cli", home=registry_env.home,
            session_id="sess-cli2", cwd=foreign_cwd,
        )
        assert proc.returncode == 0
        assert "ModuleNotFoundError" not in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_cli_missing_session_id_is_clean_noop(self, registry_env, tmp_path):
        """No $CLAUDE_CODE_SESSION_ID in the CLI's env -> register is a clean
        no-op (exit 0, nothing written), never an error exit."""
        env = dict(os.environ)
        env["HOME"] = str(registry_env.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        proc = subprocess.run(
            [sys.executable, str(_REGISTRY_MODULE), "register", "--name", "devops@pact-cli"],
            capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=30,
        )
        assert proc.returncode == 0, f"stderr=\n{proc.stderr}"
        assert not registry_env.registry_path.exists()
