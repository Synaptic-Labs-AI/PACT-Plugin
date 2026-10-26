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
        # Not valid UTF-8 (iconv), including the lone 8-bit CSI byte.
        ("lone 8-bit CSI 0x9b", b"# v1.2.3: a \x9b31m\n\nbody\n", "not valid UTF-8"),
        ("byte 0xff", b"# v1.2.3: a \xff\n\nbody\n", "not valid UTF-8"),
        ("truncated sequence", b"# v1.2.3: a \xe2\x80\n\nbody\n", "not valid UTF-8"),
        ("overlong encoding", b"# v1.2.3: a \xc0\xaf\n\nbody\n", "not valid UTF-8"),
        ("surrogate", b"# v1.2.3: a \xed\xa0\x80\n\nbody\n", "not valid UTF-8"),
        ("past U+10FFFF", b"# v1.2.3: a \xf4\x90\x80\x80\n\nbody\n", "not valid UTF-8"),
        # Invisible and reordering format characters.
        ("RLO U+202E", "# v1.2.3: a \u202e b\n\nbody\n".encode(), "format character"),
        ("LRE U+202A", "# v1.2.3: a \u202a b\n\nbody\n".encode(), "format character"),
        ("LRI U+2066", "# v1.2.3: a \u2066 b\n\nbody\n".encode(), "format character"),
        ("PDI U+2069", "# v1.2.3: a \u2069 b\n\nbody\n".encode(), "format character"),
        ("LRM U+200E", "# v1.2.3: a \u200e b\n\nbody\n".encode(), "format character"),
        ("RLM U+200F", "# v1.2.3: a \u200f b\n\nbody\n".encode(), "format character"),
        ("ALM U+061C", "# v1.2.3: a \u061c b\n\nbody\n".encode(), "format character"),
        ("ZWSP U+200B", "# v1.2.3: a \u200b b\n\nbody\n".encode(), "format character"),
        ("ZWJ U+200D", "# v1.2.3: a \u200d b\n\nbody\n".encode(), "format character"),
        ("word joiner U+2060", "# v1.2.3: a \u2060 b\n\nbody\n".encode(), "format character"),
        ("BOM U+FEFF", "# v1.2.3: a \ufeff b\n\nbody\n".encode(), "format character"),
        ("line separator U+2028", "# v1.2.3: a \u2028 b\n\nbody\n".encode(), "format character"),
        ("paragraph separator U+2029", "# v1.2.3: a \u2029 b\n\nbody\n".encode(), "format character"),
        ("soft hyphen U+00AD", "# v1.2.3: a \u00ad b\n\nbody\n".encode(), "format character"),
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
    """Ordinary UTF-8 is not a control or format character.

    The neighbours of the rejected ranges (U+2010 hyphen, U+2026 ellipsis,
    U+202F narrow no-break space) are here so an off-by-one in a byte range
    shows up as a failure.
    """
    root = _require_checkout()
    notes = tmp_path / "v1.2.3.md"
    summary = "em \u2014 dash caf\u00e9 \u00a0nbsp \u2010 \u2026 \u202f \u4e2d \U0001f600"
    notes.write_bytes(_GOOD.replace(b"short summary", summary.encode("utf-8")))
    code, reason = _check(root, "1.2.3", notes)
    assert code == 0, reason


def test_checker_accepts_a_large_body(tmp_path):
    """A body far past the 64 KB pipe buffer must be accepted, every time.

    The body check once piped `tail` into `grep -q` under pipefail; grep
    exiting on its first match sent tail SIGPIPE, and the check then
    rejected valid notes at random (a 200 KB body: 20 rejections in 20
    runs). Ten runs here make a regression all but certain to show.
    """
    root = _require_checkout()
    notes = tmp_path / "v1.2.3.md"
    notes.write_bytes(b"# v1.2.3: big\n\n" + (b"x" * 79 + b"\n") * 2600)
    assert notes.stat().st_size > 200_000
    for attempt in range(10):
        code, reason = _check(root, "1.2.3", notes)
        assert code == 0, f"attempt {attempt}: {reason}"


def test_checker_refuses_a_version_that_is_not_semver(tmp_path):
    root = _require_checkout()
    notes = tmp_path / "v1.2.md"
    notes.write_bytes(_GOOD)
    code, reason = _check(root, "1.2", notes)
    assert code == 1 and "not X.Y.Z" in reason
