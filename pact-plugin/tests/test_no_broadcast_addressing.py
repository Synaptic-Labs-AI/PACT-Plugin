"""
Regression: no SendMessage broadcast addressing in plugin source.

The Claude Code Agent Teams runtime does not support broadcast addressing.
This test asserts no plugin-source file references the prior broadcast form.
See skills/orchestration/SKILL.md::Lead-Side HALT Fan-Out for the
iterate-and-send replacement.
"""
import re
from pathlib import Path

import pytest


_PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # pact-plugin/
_BROADCAST_RE = re.compile(r"""\bto\s*[=:]\s*['"]\*['"]""")
_SCAN_SUFFIXES = (".md", ".py", ".json", ".sh")
# Allowlist entries: (relative_path, line_substring). Empty line_substring
# means "exempt every match in this path" (whole-file exemption). The test
# file itself defines the regex and carries positive-shape synthetics
# inside `test_regex_catches_known_shapes`; whole-file exemption is the
# only honest way to express that. A non-empty substring is required for
# any future entry — see test_allowlist_only_contains_test_file.
_ALLOWLIST = (
    ("tests/test_no_broadcast_addressing.py", ""),
)


def _iter_scan_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in _SCAN_SUFFIXES:
            continue
        yield path


def _is_allowlisted(rel_path: str, line_text: str) -> bool:
    for allow_path, allow_substring in _ALLOWLIST:
        if rel_path != allow_path:
            continue
        if allow_substring == "" or allow_substring in line_text:
            return True
    return False


def _scan_for_broadcast(root: Path):
    """Return list of (rel_path, line_no, line_text) tuples for matches
    not covered by the allowlist."""
    hits = []
    for path in _iter_scan_files(root):
        rel_path = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not _BROADCAST_RE.search(line):
                continue
            if _is_allowlisted(rel_path, line):
                continue
            hits.append((rel_path, line_no, line.strip()))
    return hits


class TestNoBroadcastAddressing:
    """Asserts the plugin source contains no SendMessage broadcast form
    (`to="*"` / `to: "*"`) outside the allowlist."""

    def test_no_broadcast_in_plugin_source(self):
        hits = _scan_for_broadcast(_PLUGIN_ROOT)
        if hits:
            formatted = "\n".join(
                f"  pact-plugin/{path}:{line_no} — `{text}`"
                for path, line_no, text in hits
            )
            pytest.fail(
                "SendMessage broadcast form found at:\n"
                f"{formatted}\n"
                "The runtime does not support broadcast addressing. Replace "
                "with iterate-and-send;\n"
                "see skills/orchestration/SKILL.md::Lead-Side HALT Fan-Out."
            )

    def test_allowlist_only_contains_test_file(self):
        assert len(_ALLOWLIST) == 1, (
            f"Allowlist has {len(_ALLOWLIST)} entries; only the test-self "
            "exemption is permitted. A real exemption requires explicit "
            "rationale in the commit and a deliberate update of this "
            "meta-invariant."
        )
        path, _ = _ALLOWLIST[0]
        assert path == "tests/test_no_broadcast_addressing.py", (
            f"Sole allowlist entry must be the test file itself; got {path!r}."
        )

    def test_regex_catches_known_shapes(self):
        positives = [
            'to="*"',
            "to='*'",
            'to: "*"',
            "to: '*'",
        ]
        negatives = [
            'to="lead"',
            'to: "secretary"',
            'to_star_field = "*"',
        ]
        for sample in positives:
            assert _BROADCAST_RE.search(sample), (
                f"_BROADCAST_RE failed to match known broadcast shape: {sample!r}"
            )
        for sample in negatives:
            assert not _BROADCAST_RE.search(sample), (
                f"_BROADCAST_RE incorrectly matched non-broadcast shape: {sample!r}"
            )
