"""Release notes must be valid before they merge, not after.

WHAT THIS EXISTS TO STOP
------------------------
``.github/workflows/release.yml`` tags and releases each version bump on main,
taking the tag message and release body from
``.github/release-notes/v<version>.md``. If a malformed notes file merges, the
release job refuses it after the fact, and every later release waits behind it
until main is fixed. This test runs on every PR (tests.yml runs the suite), so
the current version's notes file is checked before it can merge.

ONE DEFINITION OF THE RULES
---------------------------
The rules live only in ``.github/scripts/check-release-notes.sh``. The release
workflow calls that script, and so does this test, so the PR-time check and
the release-time check cannot drift apart. This module does not restate the
rules; the negative table below only proves the script still rejects each
class of mistake (a script that accepts everything would otherwise pass the
positive checks).
"""

import json
import subprocess
from pathlib import Path

import pytest

_CHECKER_REL = Path(".github") / "scripts" / "check-release-notes.sh"
_NOTES_DIR_REL = Path(".github") / "release-notes"
_PLUGIN_JSON_REL = Path("pact-plugin") / ".claude-plugin" / "plugin.json"


def _source_repo_root():
    """Repo root by a positive marker, or None outside a checkout.

    Same marker as test_local_lint_parity.py and test_ci_collection_scope.py,
    so "not a checkout" (skip) is never confused with "notes file missing"
    (failure).
    """
    for base in Path(__file__).resolve().parents:
        if (base / ".claude-plugin" / "marketplace.json").exists():
            return base
    return None


def _require_checkout():
    root = _source_repo_root()
    if root is None:
        pytest.skip("not running from a repository checkout")
    return root


def _check(root, version, path):
    """Run the shared checker; return (exit code, reason on stdout)."""
    result = subprocess.run(
        ["/bin/bash", str(root / _CHECKER_REL), version, str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout.strip()


def test_current_version_has_valid_release_notes():
    """The version this PR ships must have a notes file the release job accepts."""
    root = _require_checkout()
    version = json.loads((root / _PLUGIN_JSON_REL).read_text(encoding="utf-8"))["version"]
    notes = root / _NOTES_DIR_REL / f"v{version}.md"
    assert notes.is_file(), (
        f"plugin.json is at {version}, so this PR must add {notes.relative_to(root)} "
        f"(see the header of .github/workflows/release.yml for the format)"
    )
    code, reason = _check(root, version, notes)
    assert code == 0, f"{notes.relative_to(root)} is not valid release notes: {reason}"


def test_every_committed_notes_file_is_valid():
    """A later fix to an older file must also stay valid: the release job may read it."""
    root = _require_checkout()
    files = sorted((root / _NOTES_DIR_REL).glob("v*.md"))
    assert files, "expected at least one committed release-notes file"
    for notes in files:
        version = notes.stem[1:]
        code, reason = _check(root, version, notes)
        assert code == 0, f"{notes.relative_to(root)}: {reason}"


_GOOD = b"# v1.2.3: short summary\n\n## Summary\n\nText.\n"


@pytest.mark.parametrize(
    "label, content, expected_reason",
    [
        ("missing colon", b"# v1.2.3 short summary\n\nbody\n", "line 1 must start"),
        ("wrong version", b"# v1.2.4: summary\n\nbody\n", "line 1 must start"),
        ("no heading", b"v1.2.3: summary\n\nbody\n", "line 1 must start"),
        ("empty summary", b"# v1.2.3:    \n\nbody\n", "empty summary"),
        ("carriage return", b"# v1.2.3: summary\r\n\nbody\n", "control character"),
        ("escape and bell", b"# v1.2.3: \x1b]0;x\x07t \x1b[31mred\n\nbody\n", "control character"),
        ("tab", b"# v1.2.3: a\tb\n\nbody\n", "control character"),
        ("C1 CSI as UTF-8", b"# v1.2.3: a \xc2\x9b31m\n\nbody\n", "control character"),
        ("line 2 not blank", b"# v1.2.3: summary\nbody\nmore\n", "line 2 must be blank"),
        ("no body", b"# v1.2.3: summary\n\n   \n", "no release notes"),
        ("empty file", b"", "line 1 must start"),
    ],
)
def test_checker_rejects(tmp_path, label, content, expected_reason):
    root = _require_checkout()
    notes = tmp_path / "v1.2.3.md"
    notes.write_bytes(content)
    code, reason = _check(root, "1.2.3", notes)
    assert code == 1, f"{label}: expected rejection, got exit {code} ({reason})"
    assert expected_reason in reason, f"{label}: unexpected reason {reason!r}"


def test_checker_accepts_well_formed_and_non_ascii(tmp_path):
    """UTF-8 text such as an em dash is not a control character."""
    root = _require_checkout()
    notes = tmp_path / "v1.2.3.md"
    notes.write_bytes(_GOOD.replace(b"short summary", "em — dash café".encode("utf-8")))
    code, reason = _check(root, "1.2.3", notes)
    assert code == 0, reason


def test_checker_refuses_a_version_that_is_not_semver(tmp_path):
    root = _require_checkout()
    notes = tmp_path / "v1.2.md"
    notes.write_bytes(_GOOD)
    code, reason = _check(root, "1.2", notes)
    assert code == 1 and "not X.Y.Z" in reason
