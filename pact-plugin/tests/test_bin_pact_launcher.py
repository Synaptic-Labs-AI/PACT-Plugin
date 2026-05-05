"""
Structural guard for the bundled bin/pact launcher script.

v4.0.0 ships a ready-to-symlink shell script at pact-plugin/bin/pact that
wraps `claude --agent PACT:pact-orchestrator "$@"`. The script is the
recommended global-use installation pattern documented in README §"How to
use" (alongside the per-project .claude/settings.json convention).

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
        f"pact-plugin/bin/pact missing — README §'How to use' documents "
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


def test_bin_pact_invokes_canonical_agent_form():
    """The script's load-bearing line: must invoke claude with the canonical
    agent-flag form. This is what makes the wrapper a PACT launcher rather
    than a plain claude alias."""
    text = BIN_PACT.read_text(encoding="utf-8")
    assert "claude --agent PACT:pact-orchestrator" in text, (
        "pact-plugin/bin/pact must invoke `claude --agent "
        "PACT:pact-orchestrator`. Without this exact form, the wrapper "
        "would not load the PACT orchestrator persona."
    )


def test_bin_pact_passes_through_user_arguments():
    """The wrapper must forward any additional flags to claude (e.g.,
    --resume, --plugin-dir, --print). Without `"$@"`, users couldn't
    pass through claude's own flags through the wrapper."""
    text = BIN_PACT.read_text(encoding="utf-8")
    assert '"$@"' in text, (
        "pact-plugin/bin/pact must forward arguments via `\"$@\"`. "
        "Without this, the wrapper consumes claude's flags and breaks "
        "documented usage like `pact --resume <session>`."
    )
