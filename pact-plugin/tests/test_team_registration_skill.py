"""N1 smoke for the pact-team-registration skill's register command.

The skill (skills/pact-team-registration/SKILL.md) directs every spawned
teammate to run, as its FIRST action, a SYMLINK-RELATIVE register command:

    python3 ~/.claude/protocols/pact-plugin/../hooks/shared/session_registry.py register --name '<name>@<team>'

``~/.claude/protocols/pact-plugin`` is a RUNTIME symlink to the plugin's
``protocols/`` dir (created by SessionStart setup_plugin_symlinks), so
``../hooks/shared/session_registry.py`` hops protocols -> plugin-root ->
hooks/shared. Because that symlink only exists at runtime, this smoke does NOT
assert on-disk symlink resolution (CI has no symlink); instead it pins two
CI-stable invariants the runtime command depends on:

  1. COMMAND SHAPE: SKILL.md carries a register command whose path ends in
     ``hooks/shared/session_registry.py`` and which passes the ``register``
     subcommand and the ``--name`` flag (matching the module's argparse CLI).
  2. PATH ARITHMETIC: from the plugin's REAL ``protocols/`` dir,
     ``../hooks/shared/session_registry.py`` resolves to the real, EXISTING
     session_registry module — proving the ``..`` hop the runtime symlink relies
     on is correct.

Non-vacuity: dropping ``register``/``--name`` from SKILL.md fails invariant (1)
(verified by isolated-worktree counter-test); the no-``..`` negative control
(test_missing_dotdot_does_not_resolve) guards invariant (2) so the positive path
test is discriminating, not trivially true of any path.
"""

import re
from pathlib import Path

from shared import session_registry

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SKILL = _PLUGIN_ROOT / "skills" / "pact-team-registration" / "SKILL.md"

# python3 <path ending in hooks/shared/session_registry.py> register --name <arg>
_REGISTER_CMD_RE = re.compile(
    r"python3\s+\S*hooks/shared/session_registry\.py\s+register\s+--name\s+\S+"
)


def _skill_text() -> str:
    return _SKILL.read_text(encoding="utf-8")


class TestRegisterCommandShape:
    """Invariant 1 — SKILL.md carries a well-formed register command."""

    def test_skill_file_exists(self):
        assert _SKILL.is_file(), f"pact-team-registration SKILL.md missing at {_SKILL}"

    def test_register_command_has_expected_shape(self):
        """SKILL.md must carry a register command of shape `python3 <path>/hooks/
        shared/session_registry.py register --name '<name>@<team>'`. Pins the cmd
        shape so a future SKILL.md edit can't silently break the first-action
        register-delivery (the LEG-4 failure class, skill-side)."""
        assert _REGISTER_CMD_RE.search(_skill_text()), (
            "SKILL.md is missing a well-formed register command of shape "
            "`python3 <path>/hooks/shared/session_registry.py register --name "
            "'<name>@<team>'`. A spawned teammate would have no correct command to "
            "run as its first action — register-delivery would dead-end."
        )

    def test_register_subcommand_matches_module_cli(self):
        """The 'register' subcommand the skill invokes must exist in the module's
        CLI surface (guards skill/CLI drift): the module dispatches `register` to
        a register(...) callable."""
        assert "register --name" in _skill_text()
        assert hasattr(session_registry, "register"), (
            "session_registry has no register() — the skill's `register` "
            "subcommand would have nothing to dispatch to."
        )


class TestRegisterPathArithmetic:
    """Invariant 2 — the symlink-relative ``..`` hop resolves to the real module.

    The ``~/.claude/protocols/pact-plugin`` symlink targets the plugin's
    ``protocols/`` dir, so ``../hooks`` from there == ``<plugin>/hooks``. We model
    that with the plugin's REAL protocols/ dir (no runtime symlink needed)."""

    def test_protocols_dotdot_hooks_resolves_to_real_module(self):
        protocols = _PLUGIN_ROOT / "protocols"
        assert protocols.is_dir(), (
            f"plugin protocols/ dir missing at {protocols} — the skill's "
            f"~/.claude/protocols/pact-plugin/../hooks path arithmetic assumes the "
            f"runtime symlink targets it."
        )
        arith = (protocols / ".." / "hooks" / "shared" / "session_registry.py").resolve()
        real = (_PLUGIN_ROOT / "hooks" / "shared" / "session_registry.py").resolve()
        assert arith == real, f"path arithmetic resolved to {arith}, expected {real}"
        assert arith.exists(), "the resolved register-script path does not exist"
        # It is the same module the rest of the suite imports.
        assert arith == Path(session_registry.__file__).resolve()

    def test_missing_dotdot_does_not_resolve(self):
        """Negative control (non-vacuity): WITHOUT the ``..`` hop, the path
        (protocols/hooks/shared/...) must NOT resolve to an existing file — so the
        positive test is discriminating the correct arithmetic, not trivially true
        of any path."""
        no_dotdot = _PLUGIN_ROOT / "protocols" / "hooks" / "shared" / "session_registry.py"
        assert not no_dotdot.exists()
