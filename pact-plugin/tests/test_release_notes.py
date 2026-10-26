"""Release notes must be valid before they merge, not after.

WHAT THIS EXISTS TO STOP
------------------------
``.github/workflows/release.yml`` tags and releases each version bump on main,
taking the tag message and release body from that version's section of the
root ``CHANGELOG.md``. If a malformed section merges, the release job refuses
it after the fact, and every later release waits behind it until main is
fixed. This test runs on every PR (tests.yml runs the suite), so the current
version's section, and every other version section, is checked before it can
merge.

ONE DEFINITION OF THE RULES
---------------------------
The rules and the extraction live only in
``.github/scripts/check-release-notes.sh``. The release workflow calls that
script, and so does this test, so the PR-time check and the release-time
extraction cannot drift apart. This module does not restate the rules; the
negative table below only proves the script still rejects each class of
mistake (a script that accepts everything would otherwise pass the positive
checks).
"""

import json
import subprocess
from pathlib import Path

import pytest

_CHECKER_REL = Path(".github") / "scripts" / "check-release-notes.sh"
_CHANGELOG_REL = Path("CHANGELOG.md")
_PLUGIN_JSON_REL = Path("pact-plugin") / ".claude-plugin" / "plugin.json"


def _source_repo_root():
    """Repo root by a positive marker, or None outside a checkout.

    Same marker as test_local_lint_parity.py and test_ci_collection_scope.py,
    so "not a checkout" (skip) is never confused with "changelog missing"
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


def _check(root, version, path, out_dir=None):
    """Run the shared checker; return (exit code, reason, message, body).

    With ``out_dir`` the checker also extracts, and message and body are the
    bytes it wrote; otherwise they are None.
    """
    args = ["/bin/bash", str(root / _CHECKER_REL), version, str(path)]
    if out_dir is not None:
        args += [str(out_dir / "msg"), str(out_dir / "body")]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    message = body = None
    if out_dir is not None and result.returncode == 0:
        message = (out_dir / "msg").read_bytes()
        body = (out_dir / "body").read_bytes()
    return result.returncode, result.stdout.strip(), message, body


def _list(root, path):
    """Run the checker's --list mode; return (exit code, versions or reason)."""
    result = subprocess.run(
        ["/bin/bash", str(root / _CHECKER_REL), "--list", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout.split()


def test_current_version_has_a_valid_changelog_section():
    """The version this PR ships must have a section the release job accepts."""
    root = _require_checkout()
    version = json.loads((root / _PLUGIN_JSON_REL).read_text(encoding="utf-8"))["version"]
    changelog = root / _CHANGELOG_REL
    assert changelog.is_file(), "the repository must have a root CHANGELOG.md"
    code, reason, _, _ = _check(root, version, changelog)
    assert code == 0, (
        f"plugin.json is at {version}, so CHANGELOG.md needs a valid "
        f"'## v{version}: <summary>' section (see the header of "
        f".github/workflows/release.yml): {reason}"
    )


def test_every_changelog_version_section_is_valid():
    """A later edit to an older section must stay valid: the release job may read it.

    The sections are enumerated by the checker itself (--list), so this test
    shares the release job's parser instead of re-implementing it.
    """
    root = _require_checkout()
    code, versions = _list(root, root / _CHANGELOG_REL)
    assert code == 0, f"CHANGELOG.md fails the whole-file checks: {' '.join(versions)}"
    assert versions, "expected at least one '## v<version>: ' section"
    for version in versions:
        code, reason, _, _ = _check(root, version, root / _CHANGELOG_REL)
        assert code == 0, f"CHANGELOG.md v{version}: {reason}"


# v4.8.12's release body as it will be published: the per-version notes file
# before the move to CHANGELOG.md (.github/release-notes/v4.8.12.md at
# b505d81, line 3 on), with one intended wording change (the release.yml
# bullet now names CHANGELOG.md sections instead of that file). Extraction
# from CHANGELOG.md must reproduce it byte for byte.
_V4812_MESSAGE = (
    "v4.8.12: backlog ref checks over REST when GraphQL is blocked, "
    "and releases cut by CI (issue #1667)\n"
)
_V4812_BODY = (
    '## Summary\n'
    '\n'
    'In Claude Code cloud sessions the session proxy refuses GitHub '
    'GraphQL, so every ref in `/PACT:next` read `unverifiable` '
    '(#1667). `hooks/shared/backlog.py` keeps GraphQL as the fast path '
    'and adds REST, both as a fallback and for links to other '
    'repositories. This release also moves tagging and releasing into '
    'CI, because cloud sessions get HTTP 403 when they push a tag or '
    'create a release — which is why v4.8.9–v4.8.11 were merged '
    'without tags or releases.\n'
    '\n'
    '## What landed\n'
    '\n'
    '- Repo slug without GraphQL: `GH_REPO`, then the raw `origin` '
    'URL, then `gh repo view` run inside the project and used only '
    'when it answers github.com. Ambiguous remotes are refused and the '
    'remote URL is never echoed.\n'
    '- REST fallback on a whole-request GraphQL error body: refs '
    'de-duplicated and checked over `gh api --hostname github.com`, up '
    'to 8 in parallel and at most 25, under the same 5 s deadline as '
    'GraphQL. Pending or capped refs read `unverifiable` with a '
    'reason; `GH_HOST` is removed from every child environment.\n'
    '- REST bodies are converted to the GraphQL node shape, so '
    '`_ref_outcome` decides every bucket on both paths; one pure '
    '`_classify_ref` drives read, write and abandoned-work checks, '
    'with bounded issue numbers and linear-time link detection.\n'
    '- Tests: tracker tests moved to `test_backlog_rest_fallback.py` '
    'behind a `_backlog_tracker_guard.py` that fails any real `gh` '
    'call; 155 → 214 tests, 100% line and branch coverage of the new '
    'tracker functions, 43 of 44 mutations caught.\n'
    '- `.github/workflows/release.yml`: on every push to main it gives '
    'each `plugin.json` version bump since v4.8.8 an annotated tag and '
    'a release — tag message and notes from '
    "that version's section of `CHANGELOG.md`, only the highest version "
    'marked Latest. It is idempotent, stops on a tag pointing at the '
    'wrong commit or a missing or invalid `CHANGELOG.md` section, and '
    'authors as the owner of an optional `RELEASE_TOKEN`.\n'
    '\n'
    '## Why\n'
    '\n'
    'A tracker check that is always `unverifiable` in the cloud gives '
    '`/PACT:next` nothing to reconcile. The REST path was checked live '
    'in a cloud session: real refs, including other-repo and comment '
    'links, resolved in about 1 s with correct outcomes. Release '
    'automation closes the other cloud gap for good: the first run '
    'after this merge creates v4.8.9, v4.8.10 and v4.8.11, then '
    'v4.8.12 as Latest.\n'
)


def test_v4812_extraction_matches_the_published_notes(tmp_path):
    root = _require_checkout()
    code, reason, message, body = _check(root, "4.8.12", root / _CHANGELOG_REL, tmp_path)
    assert code == 0, reason
    assert message == _V4812_MESSAGE.encode("utf-8")
    assert body == _V4812_BODY.encode("utf-8")


_GOOD = b"## v1.2.3: short summary\n\n### Summary\n\nText.\n"

# The reason the checker gives for any byte outside the line-1 allowlist.
_ALLOW = "may contain only printable ASCII, em dash or en dash"


@pytest.mark.parametrize(
    "label, content, expected_reason",
    [
        ("missing colon", b"## v1.2.3 short summary\n\nbody\n", "not in the form '## v1.2.3: <summary>'"),
        ("heading names another version", b"## v1.2.4: summary\n\nbody\n", "no section for v1.2.3"),
        ("no heading", b"v1.2.3: summary\n\nbody\n", "no section for v1.2.3"),
        ("empty summary", b"## v1.2.3:    \n\nbody\n", "empty summary"),
        ("carriage return", b"## v1.2.3: summary\r\n\nbody\n", "carriage return"),
        ("escape and bell", b"## v1.2.3: \x1b]0;x\x07t \x1b[31mred\n\nbody\n", _ALLOW),
        ("tab", b"## v1.2.3: a\tb\n\nbody\n", _ALLOW),
        ("C1 CSI as UTF-8", b"## v1.2.3: a \xc2\x9b31m\n\nbody\n", _ALLOW),
        # Not valid UTF-8, including the lone 8-bit CSI byte.
        ("lone 8-bit CSI 0x9b", b"## v1.2.3: a \x9b31m\n\nbody\n", _ALLOW),
        ("byte 0xff", b"## v1.2.3: a \xff\n\nbody\n", _ALLOW),
        ("truncated sequence", b"## v1.2.3: a \xe2\x80\n\nbody\n", _ALLOW),
        ("overlong encoding", b"## v1.2.3: a \xc0\xaf\n\nbody\n", _ALLOW),
        ("surrogate", b"## v1.2.3: a \xed\xa0\x80\n\nbody\n", _ALLOW),
        ("past U+10FFFF", b"## v1.2.3: a \xf4\x90\x80\x80\n\nbody\n", _ALLOW),
        # Invisible and reordering format characters.
        ("RLO U+202E", "## v1.2.3: a \u202e b\n\nbody\n".encode(), _ALLOW),
        ("LRE U+202A", "## v1.2.3: a \u202a b\n\nbody\n".encode(), _ALLOW),
        ("LRI U+2066", "## v1.2.3: a \u2066 b\n\nbody\n".encode(), _ALLOW),
        ("PDI U+2069", "## v1.2.3: a \u2069 b\n\nbody\n".encode(), _ALLOW),
        ("LRM U+200E", "## v1.2.3: a \u200e b\n\nbody\n".encode(), _ALLOW),
        ("RLM U+200F", "## v1.2.3: a \u200f b\n\nbody\n".encode(), _ALLOW),
        ("ALM U+061C", "## v1.2.3: a \u061c b\n\nbody\n".encode(), _ALLOW),
        ("ZWSP U+200B", "## v1.2.3: a \u200b b\n\nbody\n".encode(), _ALLOW),
        ("ZWJ U+200D", "## v1.2.3: a \u200d b\n\nbody\n".encode(), _ALLOW),
        ("word joiner U+2060", "## v1.2.3: a \u2060 b\n\nbody\n".encode(), _ALLOW),
        ("BOM U+FEFF", "## v1.2.3: a \ufeff b\n\nbody\n".encode(), _ALLOW),
        ("line separator U+2028", "## v1.2.3: a \u2028 b\n\nbody\n".encode(), _ALLOW),
        ("paragraph separator U+2029", "## v1.2.3: a \u2029 b\n\nbody\n".encode(), _ALLOW),
        ("soft hyphen U+00AD", "## v1.2.3: a \u00ad b\n\nbody\n".encode(), _ALLOW),
        ("past U+10FFFF, lead F5", b"## v1.2.3: a \xf5\x80\x80\x80\n\nbody\n", _ALLOW),
        ("past U+10FFFF, 5-byte F8", b"## v1.2.3: a \xf8\x88\x80\x80\x80\n\nbody\n", _ALLOW),
        ("TAG character U+E0041", "## v1.2.3: a \U000e0041 b\n\nbody\n".encode(), _ALLOW),
        ("DEL", b"## v1.2.3: a\x7fb\n\nbody\n", _ALLOW),
        # Ordinary non-ASCII text is outside the allowlist too.
        ("accented letter", "## v1.2.3: caf\u00e9\n\nbody\n".encode(), _ALLOW),
        ("no-break space U+00A0", "## v1.2.3: a\u00a0b\n\nbody\n".encode(), _ALLOW),
        ("CJK", "## v1.2.3: a \u4e2d\n\nbody\n".encode(), _ALLOW),
        ("emoji", "## v1.2.3: a \U0001f600\n\nbody\n".encode(), _ALLOW),
        ("narrow no-break space U+202F", "## v1.2.3: a\u202fb\n\nbody\n".encode(), _ALLOW),
        # Byte neighbours of the two allowed dashes (E2 80 93 / E2 80 94),
        # and the two-byte prefix they share left on its own.
        ("hyphen U+2010", "## v1.2.3: a \u2010 b\n\nbody\n".encode(), _ALLOW),
        ("figure dash U+2012", "## v1.2.3: a \u2012 b\n\nbody\n".encode(), _ALLOW),
        ("horizontal bar U+2015", "## v1.2.3: a \u2015 b\n\nbody\n".encode(), _ALLOW),
        ("stray E2 80", b"## v1.2.3: a \xe2\x80 b\n\nbody\n", _ALLOW),
        ("stray E2", b"## v1.2.3: a \xe2 b\n\nbody\n", _ALLOW),
        # NUL anywhere in the file.
        ("NUL in line 1", b"## v1.2.3: a\x00b\n\nbody\n", "NUL byte"),
        ("NUL in the body", b"## v1.2.3: summary\n\nbody\x00more\n", "NUL byte"),
        ("NUL-only body", b"## v1.2.3: summary\n\n\x00\n", "NUL byte"),
        ("no body", b"## v1.2.3: summary\n\n   \n", "no release notes"),
        ("empty file", b"", "no section for v1.2.3"),
        # Section structure.
        (
            "duplicate section",
            b"## v1.2.3: one\n\nbody\n\n## v1.2.3: two\n\nbody\n",
            "duplicate section",
        ),
        (
            "missing section among others",
            b"# Changelog\n\n## v1.2.4: newer\n\nx\n\n## v1.2.2: older\n\ny\n",
            "no section for v1.2.3",
        ),
        ("version prefix only (v1.2.30)", b"## v1.2.30: other\n\nbody\n", "no section for v1.2.3"),
        (
            "empty body before the next heading",
            b"## v1.2.3: summary\n\n\n## v1.2.2: older\n\nbody\n",
            "no release notes",
        ),
        # Code fences (CommonMark). Reviewer repro (a): a ~~~ fence shown
        # inside a ``` block, with an example version heading in it.
        (
            "version heading inside nested example fences",
            b"## v1.2.3: a\n\n```\n~~~\n## v0.0.1: example heading\n~~~\n```\n\n"
            b"### What landed\n\n- IMPORTANT BULLET\n",
            "heading inside the code fence",
        ),
        # Repro (c): an indented fence around a column-0 version heading.
        (
            "indented fence around a version heading",
            b"## v1.2.3: a\n\n  ```\n## v1.2.2: inside\n  ```\n\nafter\n",
            "heading inside the code fence",
        ),
        (
            "closing line with an info string does not close",
            b"## v1.2.3: a\n\n```\ncode\n``` not-a-close\n\n## v1.2.2: b\n\nx\n",
            "heading inside the code fence",
        ),
        ("unclosed fence", b"## v1.2.3: a\n\nbody\n\n```\ncode\n", "never closed"),
        (
            "unclosed fence before the next section",
            b"## v1.2.3: a\n\n```\ncode\n\n## v1.2.2: b\n\nx\n",
            "heading inside the code fence",
        ),
        (
            "version heading indented 1-3 spaces",
            b"## v1.2.3: a\n\nbody\n\n  ## v1.2.2: b\n\nx\n",
            "must start at column 0",
        ),
        # HTML comments: GitHub hides their text.
        (
            "section hidden in an HTML comment",
            b"<!-- draft\n## v1.2.3: HIDDEN\n\nHIDDEN BODY\n-->\n\n## v1.2.2: b\n\nx\n",
            "HTML comment",
        ),
        (
            "visible malformed heading plus a hidden valid one",
            b"## v1.2.3 visible looks fine\n\nnotes\n<!--\n## v1.2.3: HIDDEN\n\nHIDDEN\n-->\n",
            "HTML comment",
        ),
        (
            "malformed heading beside a valid one",
            b"## v1.2.3: valid\n\nnotes\n\n## v1.2.3 also this\n\nmore\n",
            "not in the form",
        ),
        ("CRLF in the body", b"## v1.2.3: a\n\nbody line\r\nmore\n", "carriage return"),
    ],
)
def test_checker_rejects(tmp_path, label, content, expected_reason):
    root = _require_checkout()
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_bytes(content)
    code, reason, _, _ = _check(root, "1.2.3", changelog)
    assert code == 1, f"{label}: expected rejection, got exit {code} ({reason})"
    assert expected_reason in reason, f"{label}: unexpected reason {reason!r}"


def test_checker_accepts_ascii_and_the_two_dashes(tmp_path):
    """The allowlist: printable ASCII (all of it), em dash and en dash."""
    root = _require_checkout()
    changelog = tmp_path / "CHANGELOG.md"
    summary = "plain ASCII !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ 0-9 \u2014 em \u2013 en \u2014\u2013"
    changelog.write_bytes(_GOOD.replace(b"short summary", summary.encode("utf-8")))
    code, reason, message, _ = _check(root, "1.2.3", changelog, tmp_path)
    assert code == 0, reason
    assert message == f"v1.2.3: {summary}\n".encode("utf-8")


def test_extraction_picks_one_section_trims_and_promotes(tmp_path):
    """Only this version's section; blank edges trimmed; ### promoted to ##.

    A body may follow its heading without a blank line, and the neighbouring
    sections (one newer above, one older below) contribute nothing.
    """
    root = _require_checkout()
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_bytes(
        b"# Changelog\n\nPreamble.\n\n"
        b"## v1.2.4: newer\n\n### Summary\n\nnewer body\n\n"
        b"## v1.2.3: this one\n### Summary\n\nline\n\n#### Detail\n\nmore\n\n\n"
        b"## v1.2.2: older\n\n### Summary\n\nolder body\n"
    )
    code, reason, message, body = _check(root, "1.2.3", changelog, tmp_path)
    assert code == 0, reason
    assert message == b"v1.2.3: this one\n"
    assert body == b"## Summary\n\nline\n\n### Detail\n\nmore\n"


def test_section_ends_only_at_the_next_version_heading(tmp_path):
    """A `## Something` line, and anything inside a fence, stay in the body.

    Inside a fence nothing is promoted. (A `## v<digit>` line inside a fence
    is rejected instead: see the reject table.)
    """
    root = _require_checkout()
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_bytes(
        b"## v1.2.3: boundaries\n\n### Summary\n\n## Something\n\ntext\n\n"
        b"```\n### not promoted\n## not a heading in a fence\n```\n\n"
        b"~~~\n## Also inside\n~~~\n\nend\n\n"
        b"## v1.2.2: older\n\nolder\n"
    )
    code, reason, _, body = _check(root, "1.2.3", changelog, tmp_path)
    assert code == 0, reason
    assert body == (
        b"## Summary\n\n## Something\n\ntext\n\n"
        b"```\n### not promoted\n## not a heading in a fence\n```\n\n"
        b"~~~\n## Also inside\n~~~\n\nend\n"
    )


@pytest.mark.parametrize(
    "label, fenced_block",
    [
        # Reviewer repro (b): before CommonMark tracking, each of these left
        # the parser "inside a fence", so the next version's heading was
        # ignored and later sections were absorbed. They are valid nested
        # fences, accepted and published exactly as GitHub renders them.
        ("backtick block showing a lone ~~~", b"```\n~~~\n```\n"),
        ("four-backtick block showing a lone ```", b"````\n```\n````\n"),
        ("backtick block showing a tilde fence", b"```markdown\n~~~\ncode\n~~~\n```\n"),
        ("longer closing run", b"```\ncode\n`````\n"),
        ("indented fence", b"  ~~~\n### not promoted\n  ~~~\n"),
    ],
)
def test_valid_nested_fences_are_published_exactly(tmp_path, label, fenced_block):
    root = _require_checkout()
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_bytes(
        b"## v1.2.3: fences\n\nbefore\n\n" + fenced_block + b"\nafter\n\n"
        b"## v1.2.2: older\n\nolder body\n"
    )
    code, reason, _, body = _check(root, "1.2.3", changelog, tmp_path)
    assert code == 0, f"{label}: {reason}"
    assert body == b"before\n\n" + fenced_block + b"\nafter\n", label
    code, versions = _list(root, changelog)
    assert (code, versions) == (0, ["1.2.3", "1.2.2"]), f"{label}: {versions}"


def test_list_item_fence_indented_four_spaces_is_published_as_written(tmp_path):
    """Pins the one documented divergence from CommonMark.

    A fence indented 4+ spaces inside a list item is code on GitHub but plain
    text to the checker. Its lines are never boundaries or promoted, so the
    body is published byte for byte as written, which is how GitHub shows it.
    """
    root = _require_checkout()
    block = b"1. Item:\n\n    ```\n    ### looks like a heading\n    ## v9.9.9: and a version\n    ```\n"
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_bytes(b"## v1.2.3: list fence\n\n" + block + b"\n## v1.2.2: older\n\nx\n")
    code, reason, _, body = _check(root, "1.2.3", changelog, tmp_path)
    assert code == 0, reason
    assert body == block


def test_list_mode_rejects_a_malformed_version_heading(tmp_path):
    root = _require_checkout()
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_bytes(b"## v1.2.3: fine\n\nx\n\n## v1.2 broken\n\ny\n")
    code, output = _list(root, changelog)
    assert code == 1 and "must be" in " ".join(output)


def test_checker_accepts_a_large_body(tmp_path):
    """A body far past the 64 KB pipe buffer must be accepted, every time.

    The body check once piped `tail` into `grep -q` under pipefail; grep
    exiting on its first match sent tail SIGPIPE, and the check then
    rejected valid notes at random (a 200 KB body: 20 rejections in 20
    runs). Ten runs here make a regression all but certain to show.
    """
    root = _require_checkout()
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_bytes(
        b"## v1.2.3: big\n\n" + (b"x" * 79 + b"\n") * 2600 + b"\n## v1.2.2: older\n\ny\n"
    )
    assert changelog.stat().st_size > 200_000
    for attempt in range(10):
        code, reason, _, body = _check(root, "1.2.3", changelog, tmp_path)
        assert code == 0, f"attempt {attempt}: {reason}"
        assert body == (b"x" * 79 + b"\n") * 2600


def test_checker_refuses_a_version_that_is_not_semver(tmp_path):
    root = _require_checkout()
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_bytes(_GOOD)
    code, reason, _, _ = _check(root, "1.2", changelog)
    assert code == 1 and "not X.Y.Z" in reason
