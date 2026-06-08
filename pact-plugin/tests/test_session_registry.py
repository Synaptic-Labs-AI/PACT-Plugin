"""Unit tests for the self-registration registry (hooks/shared/session_registry.py).

Commit-A coverage: register/resolve round-trip, self-lookup-only, members[]-
validation (integrity), fail-safe everywhere, $CLAUDE_CODE_SESSION_ID-absent
no-op, the <=512B single-write bound, last-wins-per-session_id, the global
fixed team-agnostic path, sanitize-on-write-and-read, and the symlink/path
guards. (The both-modes matrix, AST no-import, and sanitizer-parity structural
tests live in commit G's test files; this file is the module's own unit suite.)
"""

import json
import os
from pathlib import Path

import pytest

from shared import session_registry
from shared.session_registry import register, resolve


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    """Redirect REGISTRY_PATH + Path.home() into tmp_path so register/resolve
    operate on an isolated fake ~/.claude tree, and patch the
    $CLAUDE_CODE_SESSION_ID env var. Returns a small helper namespace.

    The path-containment check resolves against ``Path.home()/.claude/
    pact-sessions``; we point Path.home() at tmp_path so the fixed registry
    path passes the containment guard inside the sandbox.
    """
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
        def write_team(team, member_names):
            team_dir = fake_home / ".claude" / "teams" / team
            team_dir.mkdir(parents=True, exist_ok=True)
            (team_dir / "config.json").write_text(
                json.dumps({"members": [{"name": n} for n in member_names]}),
                encoding="utf-8",
            )

    return _Env


# ---------------------------------------------------------------------------
# register/resolve round-trip + self-lookup
# ---------------------------------------------------------------------------

def test_register_then_resolve_roundtrip(registry_env):
    registry_env.set_session("sess-abc")
    registry_env.write_team("pact-team1", ["alice", "bob"])

    register("alice@pact-team1")
    assert resolve("sess-abc") == "alice@pact-team1"


def test_resolve_self_lookup_only_other_session_misses(registry_env):
    """resolve keys on the passed session_id; another agent's session_id does
    NOT return this agent's value (self-lookup-only — no cross-team forge)."""
    registry_env.set_session("sess-alice")
    registry_env.write_team("pact-team1", ["alice"])
    register("alice@pact-team1")

    assert resolve("sess-alice") == "alice@pact-team1"
    assert resolve("sess-someone-else") is None


def test_no_name_keyed_lookup_api_exists(registry_env):
    """Structural: the module exposes resolve(session_id) and register only —
    NO name-keyed lookup function (a name-keyed scan would enable forging)."""
    public = {n for n in dir(session_registry) if not n.startswith("_")}
    # Allowed public surface: the two operations + the path/bound constants.
    assert "resolve" in public
    assert "register" in public
    forbidden = {"resolve_by_name", "lookup_name", "find_by_name", "scan"}
    assert not (forbidden & public), f"name-keyed lookup leaked: {forbidden & public}"


# ---------------------------------------------------------------------------
# last-wins-per-session_id
# ---------------------------------------------------------------------------

def test_last_wins_per_session_id(registry_env):
    registry_env.set_session("sess-1")
    registry_env.write_team("pact-team1", ["alice"])
    registry_env.write_team("pact-team2", ["alice"])

    register("alice@pact-team1")
    register("alice@pact-team2")  # later write wins on read
    assert resolve("sess-1") == "alice@pact-team2"


# ---------------------------------------------------------------------------
# members[]-validation (integrity)
# ---------------------------------------------------------------------------

def test_resolve_rejects_name_not_in_team_members(registry_env):
    """A self-supplied name that is NOT a member of its @team config is
    rejected on read (returns None) — the forge-blunting integrity check."""
    registry_env.set_session("sess-x")
    registry_env.write_team("pact-team1", ["alice", "bob"])

    register("mallory@pact-team1")  # mallory not a member
    assert resolve("sess-x") is None


def test_resolve_rejects_when_team_config_missing(registry_env):
    registry_env.set_session("sess-y")
    # no team config written for pact-ghost
    register("alice@pact-ghost")
    assert resolve("sess-y") is None


def test_resolve_accepts_member_after_sanitization(registry_env):
    """members[]-validation compares SANITIZED names on both sides, so a member
    stored with a sanitizable char still validates."""
    registry_env.set_session("sess-z")
    registry_env.write_team("pact-team1", ["weird)name"])  # ) sanitizes to _

    register("weird)name@pact-team1")
    assert resolve("sess-z") == "weird_name@pact-team1"


# ---------------------------------------------------------------------------
# sanitize on write and read
# ---------------------------------------------------------------------------

def test_name_sanitized_on_write(registry_env):
    registry_env.set_session("sess-s")
    registry_env.write_team("pact-team1", ["a_b"])  # ) and newline strip to _

    register("a)b@pact-team1")
    # the stored + resolved value is sanitized
    assert resolve("sess-s") == "a_b@pact-team1"


# ---------------------------------------------------------------------------
# $CLAUDE_CODE_SESSION_ID absent → no-op
# ---------------------------------------------------------------------------

def test_register_noop_when_session_id_absent(registry_env):
    """No $CLAUDE_CODE_SESSION_ID → register is a no-op, writes nothing, never
    raises."""
    # registry_env deletes the env var by default
    register("alice@pact-team1")
    assert not registry_env.registry_path.exists()


def test_register_noop_when_value_has_no_at(registry_env):
    registry_env.set_session("sess-1")
    register("alice-no-team")  # no @ → nothing resolvable → no-op
    assert not registry_env.registry_path.exists()


# ---------------------------------------------------------------------------
# <=512B single-write bound
# ---------------------------------------------------------------------------

def test_register_skips_oversize_line(registry_env):
    """A line over the portable 512B atomicity bound is skipped (no partial
    write) rather than risking a torn append."""
    registry_env.set_session("s" * 600)  # forces the JSON line over 512B
    registry_env.write_team("pact-team1", ["alice"])
    register("alice@pact-team1")
    assert not registry_env.registry_path.exists()


def test_realistic_line_is_well_under_bound(registry_env):
    registry_env.set_session("sess-abc-123")
    registry_env.write_team("pact-team1", ["alice"])
    register("alice@pact-team1")
    raw = registry_env.registry_path.read_bytes()
    assert len(raw) <= session_registry._MAX_LINE_BYTES


# ---------------------------------------------------------------------------
# fail-safe: corrupt / missing → resolve returns None, never raises
# ---------------------------------------------------------------------------

def test_resolve_missing_file_returns_none(registry_env):
    assert resolve("sess-anything") is None


def test_resolve_empty_session_id_returns_none(registry_env):
    assert resolve("") is None


def test_resolve_tolerates_torn_lines(registry_env):
    """A garbage / partially-written line is skipped; a valid later line for the
    session still resolves (last-wins over good lines, garbage ignored)."""
    registry_env.set_session("sess-ok")
    registry_env.write_team("pact-team1", ["alice"])
    register("alice@pact-team1")
    # splice a torn line in
    with open(registry_env.registry_path, "a", encoding="utf-8") as fh:
        fh.write('{"session_id": "sess-ok", "value": "alic\n')  # torn
    assert resolve("sess-ok") == "alice@pact-team1"


def test_register_never_raises_on_bad_input(registry_env):
    registry_env.set_session("sess-1")
    # None / empty / weird — none raise
    register(None)  # type: ignore[arg-type]
    register("")
    register("@onlyteam")
    register("name@")
    # nothing valid was written
    assert not registry_env.registry_path.exists()


# ---------------------------------------------------------------------------
# global fixed team-agnostic path
# ---------------------------------------------------------------------------

def test_registry_path_is_under_pact_sessions_not_teams():
    """The path is under pact-sessions/ (PACT-owned), NOT under teams/
    (shared, not PACT-owned). LOCKED team-agnostic global fixed path."""
    p = session_registry.get_registry_path()
    parts = p.parts
    assert "pact-sessions" in parts
    assert "teams" not in parts
    assert p.name == ".teammate-registry.jsonl"


def test_path_containment_rejects_outside_tree(registry_env, tmp_path):
    """The inline containment check rejects a path outside pact-sessions."""
    outside = tmp_path / "evil" / "registry.jsonl"
    assert session_registry._is_under_pact_sessions(outside) is False
    inside = registry_env.home / ".claude" / "pact-sessions" / "x.jsonl"
    assert session_registry._is_under_pact_sessions(inside) is True


def test_path_containment_rejects_sibling_prefix_of_root(registry_env):
    """F4: a SIBLING of the sessions root whose name is a string-PREFIX of it
    (``pact-sessions-evil`` vs ``pact-sessions``) must NOT count as 'under' the
    root. The check uses Path.parents containment, NOT a string prefix — a naive
    ``str(candidate).startswith(str(root))`` would WRONGLY accept the sibling and
    let register()/resolve() operate on an attacker-planted sibling tree
    (``~/.claude/pact-sessions-evil/...``). test_path_containment_rejects_outside_tree
    only covers a generic outside path, not this prefix-collision sibling — this
    row pins the parents-vs-prefix distinction.

    Non-vacuity (counter-test, isolated worktree): replacing _is_under_pact_sessions
    with a str-prefix containment makes THIS test FAIL (the sibling is wrongly
    accepted) while test_path_containment_rejects_outside_tree still passes.
    """
    claude = registry_env.home / ".claude"
    # sibling dir whose name is a string-prefix of the real root
    sibling = claude / "pact-sessions-evil" / "x.jsonl"
    assert session_registry._is_under_pact_sessions(sibling) is False
    # control: the real root itself and a child under it ARE contained
    root = claude / "pact-sessions"
    child = root / "sub" / "x.jsonl"
    assert session_registry._is_under_pact_sessions(root) is True
    assert session_registry._is_under_pact_sessions(child) is True


# ---------------------------------------------------------------------------
# C4b — inline config-root resolver parity (session_registry is a standalone-
# script leaf that can't import shared.paths, so it inlines the resolver). These
# parity tests enforce the inline copy never drifts from the canonical resolver,
# and guard the silent-bail trap. Mirrors the _is_safe_team_segment dual-copy
# parity precedent (test_session_registry_trust_partition.py).
# ---------------------------------------------------------------------------

class TestConfigRootInlineParity:

    @pytest.mark.parametrize("env_value", [
        None,                 # unset -> home/.claude
        "",                   # empty == unset
        "   ",                # whitespace == unset
        "/abs/config",        # absolute
        "~",                  # home
        "~/.claude-kimi",     # ~/x exact-prefix slice
        "rel/dir",            # relative, honored as-is
    ])
    def test_inline_config_root_matches_canonical_resolver(self, env_value, monkeypatch, tmp_path):
        # (a) the inline _config_root() MUST be byte-equivalent to the canonical
        # shared.paths.get_claude_config_dir() across every env scenario.
        import shared.session_registry as sr
        from shared.paths import get_claude_config_dir
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        if env_value is None:
            monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        else:
            monkeypatch.setenv("CLAUDE_CONFIG_DIR", env_value)
        assert sr._config_root() == get_claude_config_dir(), (
            f"inline _config_root drifted from get_claude_config_dir for "
            f"CLAUDE_CONFIG_DIR={env_value!r}"
        )

    def test_is_under_pact_sessions_holds_under_nondefault_config_dir(self, monkeypatch, tmp_path):
        # (b) THE SILENT-BAIL TRAP GUARD: under a non-default CLAUDE_CONFIG_DIR,
        # the registry path AND the _is_under_pact_sessions anchor must resolve
        # through the SAME inline root, or register()/resolve() fail-closed at the
        # containment gate (teammate-name recovery silently dies). NON-VACUOUS:
        # if the anchor lagged on Path.home() the candidate (under $CONFIG) would
        # not be under sessions_root (under ~/.claude) -> False -> this FAILS.
        import shared.session_registry as sr
        config_dir = tmp_path / "kimi-config"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        assert sr._is_under_pact_sessions(sr.get_registry_path()) is True, (
            "_is_under_pact_sessions rejected the registry path under a non-default "
            "CLAUDE_CONFIG_DIR — the containment anchor is not co-routed through the "
            "inline _config_root() (register() would silently bail)"
        )
