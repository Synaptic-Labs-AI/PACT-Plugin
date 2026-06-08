"""
L1 resolver-unit tests for shared/paths.py::get_claude_config_dir().

Asserts the resolution contract directly (§A.2 of the architecture spec):
fail-loud env honoring, empty/whitespace==unset, exact-prefix ~ slicing with
NO expanduser (monkeypatch-safe), unresolved return, single-path.

Two drive modes are exercised:
  - DI mode (env=/home=): direct contract assertions (allowed at L1).
  - live-globals mode (monkeypatch.setenv + Path.home redirect): proves the
    real seam consumers depend on works without DI.
"""
import os
from pathlib import Path

import pytest

from shared.paths import get_claude_config_dir


FAKE_HOME = Path("/fake/home")


# --- DI mode: env unset --------------------------------------------------

def test_unset_falls_back_to_home_dotclaude():
    assert get_claude_config_dir(env={}, home=FAKE_HOME) == FAKE_HOME / ".claude"


def test_empty_string_is_unset():
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": ""}, home=FAKE_HOME) == FAKE_HOME / ".claude"


def test_whitespace_only_is_unset():
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "   "}, home=FAKE_HOME) == FAKE_HOME / ".claude"


def test_whitespace_trimmed_then_honored():
    # leading/trailing whitespace stripped, the remainder honored
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "  /abs  "}, home=FAKE_HOME) == Path("/abs")


# --- DI mode: env set ----------------------------------------------------

def test_absolute_path_honored():
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "/opt/cfg"}, home=FAKE_HOME) == Path("/opt/cfg")


def test_tilde_alone_maps_to_home():
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "~"}, home=FAKE_HOME) == FAKE_HOME


def test_tilde_slash_prefix_exact_slice():
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "~/x"}, home=FAKE_HOME) == FAKE_HOME / "x"


def test_tilde_slash_nested():
    assert get_claude_config_dir(
        env={"CLAUDE_CONFIG_DIR": "~/.claude-kimi"}, home=FAKE_HOME
    ) == FAKE_HOME / ".claude-kimi"


def test_relative_path_honored_as_is():
    # honored as-is (surfaced via observability at the consumer); NOT joined to home
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "rel/dir"}, home=FAKE_HOME) == Path("rel/dir")


def test_trailing_slash_normalizes_via_path():
    # Path() collapses a trailing slash; behavior is "honored as-is" then Path-normalized
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "/abs/"}, home=FAKE_HOME) == Path("/abs")


def test_exact_prefix_slice_not_lstrip():
    # A path like "~/~backup" must slice the EXACT 2-char "~/" prefix → home/"~backup".
    # str.lstrip("~/") would mangle this to home/"backup" (char-set strip). Guards A-no-expanduser.
    assert get_claude_config_dir(
        env={"CLAUDE_CONFIG_DIR": "~/~backup"}, home=FAKE_HOME
    ) == FAKE_HOME / "~backup"


def test_returns_unresolved_path():
    # The resolver must NOT call .resolve() (the single resolve stays at containment sites).
    # A symlink-bearing value comes back byte-identical, not dereferenced.
    result = get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "/a/../b"}, home=FAKE_HOME)
    assert result == Path("/a/../b")  # unresolved; ".." NOT collapsed by resolve()


# --- live-globals mode: the no-DI seam consumers use ---------------------

def test_live_globals_unset(monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: FAKE_HOME))
    assert get_claude_config_dir() == FAKE_HOME / ".claude"


def test_live_globals_env_set(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/run/cfg")
    assert get_claude_config_dir() == Path("/run/cfg")


def test_no_expanduser_uses_resolved_home(monkeypatch):
    # Critical seam test: with CLAUDE_CONFIG_DIR="~/sub", the resolver MUST expand
    # via the monkeypatched Path.home() (→ FAKE_HOME/"sub"), NOT via expanduser()
    # which reads $HOME directly and would bypass the monkeypatch seam.
    monkeypatch.setenv("HOME", "/real/home/should/not/be/used")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/sub")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: FAKE_HOME))
    assert get_claude_config_dir() == FAKE_HOME / "sub"


def _code_tokens(py_path: Path) -> str:
    """Return source CODE only (docstrings + comments stripped via tokenize).

    Lets the verbatim contract docstring legitimately MENTION the forbidden
    idioms ("NO expanduser/lstrip/removeprefix") while still catching any
    real CODE use of them.
    """
    import io
    import tokenize

    text = py_path.read_text(encoding="utf-8")
    pieces = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        pieces.append(tok.string)
    return " ".join(pieces)


def test_no_expanduser_idiom_in_code():
    # Structural guard (A-no-expanduser): forbid expanduser/lstrip/removeprefix
    # in executable CODE (docstring mentions of the contract are allowed).
    src = Path(__file__).parent.parent / "hooks" / "shared" / "paths.py"
    code = _code_tokens(src)
    for forbidden in ("expanduser", "lstrip", "removeprefix"):
        assert forbidden not in code, f"{forbidden} must not appear in paths.py code (A-no-expanduser)"


def test_resolver_has_no_logging_or_print_in_code():
    # DATA-G1: the pure resolver must not log/print the resolved root.
    src = Path(__file__).parent.parent / "hooks" / "shared" / "paths.py"
    code = _code_tokens(src)
    for forbidden in ("print", "logging", "logger", "stderr"):
        assert forbidden not in code, f"{forbidden!r} must not appear in the pure resolver code (DATA-G1)"


# ---------------------------------------------------------------------------
# C4 — the B1 accessors FOLLOW $CLAUDE_CONFIG_DIR at call time (proves the
# import-time freeze is gone). env-set → config root; env-unset → home/.claude.
# ---------------------------------------------------------------------------

def test_get_tracking_dir_follows_config_dir(tmp_path, monkeypatch):
    from track_files import get_tracking_dir
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert get_tracking_dir() == tmp_path / "pact-memory" / "session-tracking"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert get_tracking_dir() == tmp_path / ".claude" / "pact-memory" / "session-tracking"


def test_get_failure_log_path_follows_config_dir(tmp_path, monkeypatch):
    from shared.failure_log import get_failure_log_path
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert get_failure_log_path() == tmp_path / "pact-sessions" / "_session_init_failures.log"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert get_failure_log_path() == tmp_path / ".claude" / "pact-sessions" / "_session_init_failures.log"


def test_get_compact_summary_path_follows_config_dir(tmp_path, monkeypatch):
    from shared.constants import get_compact_summary_path
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert get_compact_summary_path() == tmp_path / "pact-sessions" / "compact-summary.txt"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert get_compact_summary_path() == tmp_path / ".claude" / "pact-sessions" / "compact-summary.txt"


def test_token_dir_b2_is_ssot_derived_not_rehardcoded():
    # B2 contract: TOKEN_DIR is an eager import-time constant, but it MUST derive
    # from the SSOT resolver (get_claude_config_dir()), NOT a re-hardcoded
    # Path.home()/".claude" (re-hardcode would re-open the drift the SSOT closes).
    # Structural check — runtime equality can't distinguish the two when the env
    # is unset (both yield home/.claude), so we assert on the source idiom.
    src = (Path(__file__).parent.parent / "hooks" / "shared" / "merge_guard_common.py").read_text(encoding="utf-8")
    assert "TOKEN_DIR = get_claude_config_dir()" in src
    assert 'TOKEN_DIR = Path.home()' not in src
