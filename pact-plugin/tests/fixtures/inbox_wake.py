"""
Shared fixtures and constants for #591 inbox-monitor-wake test files.

Imported by the concern-split test_inbox_wake_*.py files. Owns:
  - Path constants (worktree-rooted, reused across all concerns).
  - Sentinel-heading constants (em-dash U+2014 byte-for-byte).
  - File-list constants (ARMING_FILES, TEARDOWN_FILES, NON_ARMING_FILES).
  - Read helpers (_read, _between).
  - Subprocess helpers for the verify-script counter-test
    (_build_repo_subset, _run_verify).
"""
import shutil
import subprocess
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _PLUGIN_ROOT.parent
COMMANDS_DIR = _PLUGIN_ROOT / "commands"
PROTOCOLS_DIR = _PLUGIN_ROOT / "protocols"
FIXTURES_DIR = _PLUGIN_ROOT / "tests" / "fixtures" / "inbox-wake-canonical"
SKILLS_DIR = _PLUGIN_ROOT / "skills"
HOOKS_DIR = _PLUGIN_ROOT / "hooks"
RUNBOOK_PATH = _PLUGIN_ROOT / "tests" / "runbooks" / "inbox-monitor-wake.md"
VERIFY_SCRIPT = _REPO_ROOT / "scripts" / "verify-protocol-extracts.sh"

ARMING_FILES = [
    "orchestrate.md",
    "comPACT.md",
    "rePACT.md",
    "plan-mode.md",
    "peer-review.md",
]
TEARDOWN_FILES = [
    "wrap-up.md",
    "pause.md",
]

# Files that MUST NOT contain wake-arming or cron-arming. Phantom-green guard:
# if a future refactor accidentally lands an arm step in any of these surfaces,
# the negative tests catch it. The strikethrough additions (pact-agent-teams,
# peer_inject) are post-spike additions to NON_ARMING — they were originally
# proposed as ARMING but the spike removed them.
NON_ARMING_FILES = [
    COMMANDS_DIR / "imPACT.md",
    COMMANDS_DIR / "bootstrap.md",
    SKILLS_DIR / "pact-agent-teams" / "SKILL.md",
    HOOKS_DIR / "peer_inject.py",
]

CHARTER_PATH = PROTOCOLS_DIR / "pact-communication-charter.md"

# Canonical sentinel pairs. The verify script reads these heading strings
# directly; they must match byte-for-byte (em-dash U+2014 preserved).
MONITOR_START = "## Inbox Wake — Arm Monitor (start)"
MONITOR_END = "## Inbox Wake — Arm Monitor (end)"
CRON_START = "## Inbox Wake — Arm Cron (start)"
CRON_END = "## Inbox Wake — Arm Cron (end)"
TEARDOWN_START = "## Inbox Wake — Teardown (start)"
TEARDOWN_END = "## Inbox Wake — Teardown (end)"

ALL_SENTINELS = (
    MONITOR_START, MONITOR_END,
    CRON_START, CRON_END,
    TEARDOWN_START, TEARDOWN_END,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    """Return the substring strictly between two literal markers.

    Used to scope assertions to the inline-prose region between sentinels
    (e.g., the STATE_FILE write step that lives between Monitor (end) and
    Cron (start)).
    """
    s = text.index(start) + len(start)
    e = text.index(end, s)
    return text[s:e]


# Source-tree spec for `_build_repo_subset`. Each entry is
# `(source_path, dst_relative_path, kind)` where `kind` is "tree" for
# directory copies (shutil.copytree) or "file" for single-file copies
# (shutil.copy2). UPDATE THIS LOCKSTEP with any extension to
# `scripts/verify-protocol-extracts.sh` that reads new sources — if the
# script grows a new input dependency and this list isn't updated, the
# counter-test will fail for the wrong reason (missing file in tempdir,
# not a real script bug).
VERIFY_SCRIPT_INPUTS = [
    (PROTOCOLS_DIR, "pact-plugin/protocols", "tree"),
    (COMMANDS_DIR, "pact-plugin/commands", "tree"),
    (FIXTURES_DIR, "pact-plugin/tests/fixtures/inbox-wake-canonical", "tree"),
    (VERIFY_SCRIPT, "scripts/verify-protocol-extracts.sh", "file"),
]


def _build_repo_subset(dst: Path) -> Path:
    """Copy the verify-script's source tree into a tempdir.

    Sources read by the script are listed in the module-level
    `VERIFY_SCRIPT_INPUTS` constant (see comment above the constant for
    update discipline). Returns the path to the copied repo root.
    """
    repo_dst = dst / "repo"
    repo_dst.mkdir(parents=True)
    for src, rel_dst, kind in VERIFY_SCRIPT_INPUTS:
        target = repo_dst / rel_dst
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "tree":
            shutil.copytree(src, target)
        elif kind == "file":
            shutil.copy2(src, target)
        else:
            raise ValueError(f"unknown VERIFY_SCRIPT_INPUTS kind: {kind!r}")
    return repo_dst


def _run_verify(repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "scripts/verify-protocol-extracts.sh"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
