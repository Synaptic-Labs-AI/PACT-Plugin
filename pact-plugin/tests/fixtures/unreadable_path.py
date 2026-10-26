"""
Location: pact-plugin/tests/fixtures/unreadable_path.py
Summary: Make a file path unreadable without mode bits, by putting a DIRECTORY
         in the file's place. Reading it raises IsADirectoryError, a real
         OSError, and unlike `chmod 000` that also happens when the suite runs
         as root (see tests/fixtures/root_guard.py for why mode bits do not).
Used by: tests/test_pin_caps_gate.py, tests/test_pin_caps_gate_frames.py and
         tests/test_pin_caps_gate_paths.py (the "unreadable CLAUDE.md" arms).
         Direct import, per the thin-conftest rule.
"""

from pathlib import Path


def replace_with_directory(path: Path) -> None:
    """Put an empty directory at `path`, removing a file already there.

    The suite-wide CLAUDE.md redirect seeds some of these paths with a file, so
    it is removed first. Idempotent: a directory already at `path` is kept, so
    a test can call this once per frame it runs.
    """
    if path.is_dir() and not path.is_symlink():
        return
    if path.is_symlink() or path.exists():
        path.unlink()
    path.mkdir()
