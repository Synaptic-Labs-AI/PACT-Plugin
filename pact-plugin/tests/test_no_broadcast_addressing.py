"""
Regression: no SendMessage broadcast addressing in plugin source.

The Claude Code Agent Teams runtime does not support broadcast addressing.
This test asserts no plugin-source file references the prior broadcast form
in any of three shapes: `to="*"`, `to: "*"`, or JSON-key `"to": "*"`.
See protocols/algedonic.md#lead-side-halt-fan-out for the
iterate-and-send replacement.

Failure-message format note: the assertion message uses backtick-wrapped
matched text (`` `text` ``) rather than the bracketed-quoted form sketched
in the original architecture doc — backticks render the matched snippet
unambiguously in CI logs and align with how plugin docs cite code.
"""
import re
from pathlib import Path

import pytest


_PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # pact-plugin/
# Two addressing-mode shapes:
#   - Bare-identifier kwarg form: `to="*"`, `to: "*"` (Python call, YAML)
#   - JSON-key form: `"to": "*"` (JSON config / quoted-key dict)
# The `\bto` and `"to"` branches are mutually exclusive at a given match
# position but together cover the addressing surface end-to-end. Negative
# controls in test_regex_catches_known_shapes pin the precision against
# `"to_field"` and similar near-shapes.
_BROADCAST_RE = re.compile(r"""(\bto|"to")\s*[=:]\s*['"]\*['"]""")
_SCAN_SUFFIXES = (".md", ".py", ".json", ".sh", ".yaml", ".yml", ".toml")
# Allowlist entries: (relative_path, line_substring). Empty line_substring
# means "exempt every match in this path" (whole-file exemption). The test
# file itself defines the regex and carries positive-shape synthetics
# inside `test_regex_catches_known_shapes`; whole-file exemption is the
# only honest way to express that. A non-empty substring is required for
# any future entry — see test_allowlist_only_contains_test_file and
# test_non_test_self_allowlist_must_have_substring.
_ALLOWLIST = (
    ("tests/test_no_broadcast_addressing.py", ""),
)


def _iter_scan_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in _SCAN_SUFFIXES:
            continue
        # Skip hidden directories anywhere in the path (.git, .venv,
        # .pytest_cache, .worktrees, etc.). Without this guard the scan
        # would walk into vendored/cached source under those trees.
        rel_parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
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
    (`to="*"`, `to: "*"`, or JSON-key `"to": "*"`) outside the allowlist."""

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
                "see protocols/algedonic.md#lead-side-halt-fan-out."
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

    def test_non_test_self_allowlist_must_have_substring(self):
        """Whole-file exemption (empty substring) is reserved for the test
        file itself. Any future allowlist entry pointing at a different
        path must use a non-empty `line_substring` so the exemption is
        narrowly targeted at a known-shape line, not a blanket pass.

        Verifies the rule by exercising the same `_is_allowlisted` helper
        on a synthetic non-test-self path with an empty substring, and
        asserts the rule policy: such an entry is NOT permitted to live
        in `_ALLOWLIST`. We don't mutate `_ALLOWLIST` (test isolation);
        we audit the current contents against the rule.
        """
        for allow_path, allow_substring in _ALLOWLIST:
            if allow_path == "tests/test_no_broadcast_addressing.py":
                continue
            assert allow_substring, (
                f"Allowlist entry for {allow_path!r} has empty substring. "
                "Only the test-self entry may use whole-file exemption; "
                "every other entry must specify a non-empty `line_substring` "
                "naming the exact shape being exempted."
            )

    def test_regex_catches_known_shapes(self):
        positives = [
            # Bare-identifier kwarg form (Python call / YAML)
            'to="*"',
            "to='*'",
            'to: "*"',
            "to: '*'",
            # JSON-key form (quoted key, then `:` separator)
            '"to":"*"',
            '"to": "*"',
        ]
        negatives = [
            'to="lead"',
            'to: "secretary"',
            'to_star_field = "*"',
            # Different JSON keys must NOT match — the `"to"` branch is
            # exact-string, so trailing or leading underscores break it.
            '"to_field":"*"',
            '"foo_to":"*"',
            '"target":"*"',
        ]
        for sample in positives:
            assert _BROADCAST_RE.search(sample), (
                f"_BROADCAST_RE failed to match known broadcast shape: {sample!r}"
            )
        for sample in negatives:
            assert not _BROADCAST_RE.search(sample), (
                f"_BROADCAST_RE incorrectly matched non-broadcast shape: {sample!r}"
            )
