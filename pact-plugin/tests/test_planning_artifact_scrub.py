"""
Tests for planning-artifact scrub discipline in LLM-loaded markdown.

Background: Planning-artifact patterns (PR/issue refs, SHAs, version markers,
in-session task IDs, letter-cycle labels) are forbidden in files that load
into the LLM's context at runtime — agents/*.md, skills/*/SKILL.md,
protocols/*.md, commands/*.md — per the LLM-load distinction pin in
user CLAUDE.md.

Rationale (from the pin): these files load every session; planning artifacts
become dead pointers (no tracker access), consume context, and undermine
project-agnostic posture. The repo must self-describe through behavioral
language; provenance refs belong in commit messages and PR descriptions.

This test walks the four LLM-loaded directories and scans each .md file
for planning-artifact regex patterns. Legitimate retentions (fictional
example data, external-tracker refs, markdown anchor links, operational
IDs, behavioral provenance via memory IDs) are exempt via either:

  1. Same-line trailing `<!-- planning-artifact-exempt: <reason> -->` marker.
  2. Preceding-line `<!-- planning-artifact-exempt: <reason> -->` marker.
  3. Block-bracketed pair:
     `<!-- planning-artifact-exempt-block: <reason> -->` ...
     `<!-- planning-artifact-exempt-block-end -->`
     exempts every line between the two markers (inclusive of neither —
     the markers themselves don't contain artifacts). Useful for fenced
     code blocks demonstrating operational sample output where each line
     might mention an exempt operational ID.
  4. Whole-file `<!-- planning-artifact-exempt-file: <reason> -->` marker
     at the top of the file (for files that intentionally demonstrate
     planning-artifact-shaped example data, like pact-memory's example
     memory entries).

Pattern catalog (regex strings are raw to silence escape warnings):

  - PR/issue refs:        ``#\\d{2,4}`` not preceded by ``&`` (HTML entities
                          like ``&#039;``) and not followed by ``-`` or
                          a letter (markdown anchors like
                          ``#12-completion-authority`` or
                          ``#section-heading``).
  - SHA-looking:          7-12 hex chars with at least one letter AND one
                          digit (skips phone numbers, timestamps, message
                          IDs that are pure-digit; skips English words
                          that are pure-letter).
  - Version markers:      ``\\bv\\d+\\.\\d+\\.\\d+\\b``.
  - In-session task IDs:  ``\\bTask #\\d+``.
  - Letter-cycle labels:  ``\\bCommit [A-Z]\\b`` and
                          ``\\bBug [A-Z] dispatch\\b``.

The catalog is conservative: bias toward false-negative (some patterns
may slip through) over false-positive (block legitimate retentions).
False-positives ARE catchable via the explicit exempt marker; over-firing
on legitimate content would block routine doc work.

If a future PR re-introduces an unmarked planning artifact, this test
fails with file:line + matched pattern + suggested action, surfacing
the regression at PR time rather than at next-session context-load.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest


# ─── Directory scope ─────────────────────────────────────────────────────────

PACT_PLUGIN_ROOT = Path(__file__).parent.parent

LLM_LOADED_DIRECTORIES = (
    PACT_PLUGIN_ROOT / "agents",
    PACT_PLUGIN_ROOT / "skills",
    PACT_PLUGIN_ROOT / "protocols",
    PACT_PLUGIN_ROOT / "commands",
)


# ─── Pattern catalog ─────────────────────────────────────────────────────────

# PR/issue refs: digits-only after `#`, 2-4 digits, with two false-positive
# guards:
#   - Negative lookbehind for `&` to skip HTML entities like `&#039;`.
#   - Negative lookahead for `-` or alpha to skip markdown anchor links
#     like `#12-completion-authority` and `#section-heading`.
# The negative-lookahead `(?![\w-])` after the digit run forbids any
# word-character (letter, digit, underscore) or hyphen continuation —
# legitimate PR refs are bounded by whitespace, punctuation, or end-of-line.
_RE_PR_ISSUE = re.compile(r"(?<!&)#\d{2,4}(?![\w-])")

# SHA-looking: 7-12 hex chars, must contain BOTH at least one letter AND
# at least one digit. Skips pure-digit runs (phone numbers, timestamps,
# message IDs) and pure-letter runs (English words).
_RE_SHA_LOOKING = re.compile(
    r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,12}\b"
)

# Version markers: literal vX.Y.Z with word boundary.
_RE_VERSION = re.compile(r"\bv\d+\.\d+\.\d+\b")

# In-session task IDs: `Task #N` with at least one digit.
_RE_TASK_ID = re.compile(r"\bTask #\d+")

# Letter-cycle labels: `Commit A`/`Bug A dispatch` shape.
_RE_COMMIT_LETTER = re.compile(r"\bCommit [A-Z]\b")
_RE_BUG_LETTER = re.compile(r"\bBug [A-Z] dispatch\b")


@dataclass(frozen=True)
class PatternSpec:
    name: str
    pattern: re.Pattern[str]
    suggestion: str


PATTERN_CATALOG = (
    PatternSpec(
        name="pr_or_issue_ref",
        pattern=_RE_PR_ISSUE,
        suggestion=(
            "Strip the planning artifact and keep only the behavioral "
            "description. If the reference is a legitimate external-tracker "
            "ref (e.g., upstream-project issue) or fictional example data, "
            "add `<!-- planning-artifact-exempt: <reason> -->` on the "
            "preceding line or trailing same-line."
        ),
    ),
    PatternSpec(
        name="sha_looking",
        pattern=_RE_SHA_LOOKING,
        suggestion=(
            "Strip the commit SHA and reference the behavioral provenance by "
            "name instead. If the value is a memory ID, cron entry ID, or "
            "other operational identifier, add an exempt marker."
        ),
    ),
    PatternSpec(
        name="version_marker",
        pattern=_RE_VERSION,
        suggestion=(
            "Strip the version pin. Plugin version lives in the canonical "
            "4 files (plugin.json, marketplace.json, README.md, "
            "pact-plugin/README.md); LLM-loaded docs should be version-"
            "agnostic. Exempt-mark legitimate external-tool versions "
            "(e.g., GitHub Action refs)."
        ),
    ),
    PatternSpec(
        name="in_session_task_id",
        pattern=_RE_TASK_ID,
        suggestion=(
            "In-session task IDs are ephemeral; strip and describe the role "
            "(e.g., 'the architect task') instead. Exempt-mark sample-output "
            "blocks demonstrating task-tool shape."
        ),
    ),
    PatternSpec(
        name="commit_letter",
        pattern=_RE_COMMIT_LETTER,
        suggestion=(
            "Letter-cycle labels (Commit A/B/...) are planning artifacts. "
            "Describe the change by its behavioral content instead."
        ),
    ),
    PatternSpec(
        name="bug_letter",
        pattern=_RE_BUG_LETTER,
        suggestion=(
            "Letter-cycle bug labels are planning artifacts. Describe the "
            "bug by its symptom or behavioral signature instead."
        ),
    ),
)


# ─── Exempt-marker shapes ────────────────────────────────────────────────────

# Inline marker (same-line or preceding-line). Matches the bare form
# `planning-artifact-exempt:` and the file-level form
# `planning-artifact-exempt-file:` — both shapes carry an exemption.
# The block-start / block-end markers are matched separately so the
# block-spanning semantic does not bleed into single-line scope.
_EXEMPT_INLINE = re.compile(
    r"<!--\s*planning-artifact-exempt(?:-file)?:\s*[^>]*-->"
)
_EXEMPT_FILE_HEADER = re.compile(
    r"<!--\s*planning-artifact-exempt-file:\s*[^>]*-->"
)
_EXEMPT_BLOCK_START = re.compile(
    r"<!--\s*planning-artifact-exempt-block:\s*[^>]*-->"
)
_EXEMPT_BLOCK_END = re.compile(
    r"<!--\s*planning-artifact-exempt-block-end\s*-->"
)


def _file_is_whole_exempt(text: str) -> bool:
    """A file is whole-exempt if a `planning-artifact-exempt-file` HTML
    comment appears anywhere in the first 30 lines. The 30-line window
    covers frontmatter (YAML) + initial doc header + intro paragraph."""
    head = "\n".join(text.splitlines()[:30])
    return _EXEMPT_FILE_HEADER.search(head) is not None


def _block_exempt_indices(lines: list[str]) -> set[int]:
    """Return the set of 0-based line indices that fall between a
    `planning-artifact-exempt-block:` start marker and its matching
    `planning-artifact-exempt-block-end` marker.

    Lines containing the start/end markers themselves are NOT in the set;
    only lines BETWEEN them are exempt. Unmatched block-start (no closing
    marker before end-of-file) raises no error and exempts to EOF — the
    permissive choice given the scanner's overall lean toward false-
    negative over false-positive."""
    exempt: set[int] = set()
    in_block = False
    for idx, line in enumerate(lines):
        if _EXEMPT_BLOCK_START.search(line):
            in_block = True
            continue
        if _EXEMPT_BLOCK_END.search(line):
            in_block = False
            continue
        if in_block:
            exempt.add(idx)
    return exempt


def _line_is_exempt(
    lines: list[str], idx: int, block_exempt: set[int]
) -> bool:
    """A match on `lines[idx]` is exempt if:
      - The same line carries an inline `planning-artifact-exempt` marker, OR
      - The same line IS itself a block-start or block-end marker (the
        marker's reason text may legitimately quote an exempt token), OR
      - The preceding line carries an inline marker, OR
      - The line falls inside a `planning-artifact-exempt-block: ... -end`
        pair.
    `idx` is 0-based."""
    if idx in block_exempt:
        return True
    line = lines[idx]
    if _EXEMPT_INLINE.search(line):
        return True
    if _EXEMPT_BLOCK_START.search(line) or _EXEMPT_BLOCK_END.search(line):
        return True
    if idx > 0 and _EXEMPT_INLINE.search(lines[idx - 1]):
        return True
    return False


# ─── Scan logic ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Violation:
    file_path: Path
    line_number: int  # 1-based
    pattern_name: str
    match_text: str
    line_text: str
    suggestion: str

    def format(self, root: Path) -> str:
        try:
            relpath = self.file_path.relative_to(root)
        except ValueError:
            relpath = self.file_path
        return (
            f"  {relpath}:{self.line_number} [{self.pattern_name}] "
            f"matched {self.match_text!r}\n"
            f"    line: {self.line_text.strip()[:160]}\n"
            f"    fix:  {self.suggestion}"
        )


def _scan_file(path: Path) -> list[Violation]:
    """Scan one .md file for unmarked planning-artifact patterns."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if _file_is_whole_exempt(text):
        return []
    lines = text.splitlines()
    block_exempt = _block_exempt_indices(lines)
    violations: list[Violation] = []
    for idx, line in enumerate(lines):
        if _line_is_exempt(lines, idx, block_exempt):
            continue
        for spec in PATTERN_CATALOG:
            for m in spec.pattern.finditer(line):
                violations.append(
                    Violation(
                        file_path=path,
                        line_number=idx + 1,
                        pattern_name=spec.name,
                        match_text=m.group(0),
                        line_text=line,
                        suggestion=spec.suggestion,
                    )
                )
    return violations


def _scan_directory(directory: Path) -> list[Violation]:
    """Walk a directory recursively for .md files and scan each."""
    if not directory.is_dir():
        return []
    violations: list[Violation] = []
    for md_path in sorted(directory.rglob("*.md")):
        violations.extend(_scan_file(md_path))
    return violations


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestPlanningArtifactScrub:
    """Structural enforcement of the planning-artifact scrub discipline
    across the four LLM-loaded directories.

    Per-directory tests give a focused failure surface (one failing
    directory does not mask violations in others). The detailed assertion
    message names every violation with file:line + pattern + suggestion
    so a contributor can fix or exempt without re-running locally.
    """

    @pytest.mark.parametrize(
        "directory",
        LLM_LOADED_DIRECTORIES,
        ids=lambda p: p.name,
    )
    def test_directory_is_clean(self, directory: Path):
        violations = _scan_directory(directory)
        if not violations:
            return
        joined = "\n".join(v.format(PACT_PLUGIN_ROOT) for v in violations)
        pytest.fail(
            f"\n{len(violations)} unmarked planning-artifact pattern(s) "
            f"found in {directory.name}/:\n{joined}\n\n"
            "Either strip the artifact or add an exempt marker:\n"
            "  Same-line: trailing `<!-- planning-artifact-exempt: "
            "<reason> -->`\n"
            "  Preceding-line: `<!-- planning-artifact-exempt: "
            "<reason> -->`\n"
            "  Whole-file:  `<!-- planning-artifact-exempt-file: "
            "<reason> -->` in first 30 lines."
        )


# ─── Self-tests for the scanner itself ───────────────────────────────────────


class TestScannerSelfDiscipline:
    """The scanner is itself logic — pin its behavior with synthetic
    fixtures so future maintenance edits to the regex catalog or exempt
    semantics fail loudly rather than silently weaken enforcement."""

    def _write_md(self, tmp_path: Path, body: str) -> Path:
        md = tmp_path / "fixture.md"
        md.write_text(body, encoding="utf-8")
        return md

    def test_pr_ref_unmarked_fires(self, tmp_path):
        md = self._write_md(tmp_path, "This relates to #1234 work.\n")
        violations = _scan_file(md)
        assert any(
            v.pattern_name == "pr_or_issue_ref" and v.match_text == "#1234"
            for v in violations
        ), f"expected pr_or_issue_ref hit, got: {violations}"

    def test_pr_ref_with_inline_exempt_silent(self, tmp_path):
        md = self._write_md(
            tmp_path,
            "This relates to #1234 work. <!-- planning-artifact-exempt: "
            "fictional example -->\n",
        )
        assert _scan_file(md) == []

    def test_pr_ref_with_preceding_exempt_silent(self, tmp_path):
        md = self._write_md(
            tmp_path,
            "<!-- planning-artifact-exempt: fictional example -->\n"
            "This relates to #1234 work.\n",
        )
        assert _scan_file(md) == []

    def test_whole_file_exempt_silent(self, tmp_path):
        md = self._write_md(
            tmp_path,
            "<!-- planning-artifact-exempt-file: example fixture -->\n"
            "Mentions #1234 and `bef7f24` and v1.2.3 freely.\n",
        )
        assert _scan_file(md) == []

    def test_block_exempt_silent_between_markers(self, tmp_path):
        """Block-bracketed exempt covers every line between the start and
        end markers."""
        md = self._write_md(
            tmp_path,
            "Before block: #1234 fires here.\n"
            "<!-- planning-artifact-exempt-block: sample output demo -->\n"
            "Line A: #5678 inside.\n"
            "Line B: bef7f24 inside.\n"
            "Line C: v1.2.3 inside.\n"
            "<!-- planning-artifact-exempt-block-end -->\n"
            "After block: #9876 fires here.\n",
        )
        violations = _scan_file(md)
        # Only the lines outside the block fire.
        match_texts = sorted(v.match_text for v in violations)
        assert match_texts == ["#1234", "#9876"], (
            f"block-exempt leaked or over-fired: {violations}"
        )

    def test_block_exempt_unmatched_start_exempts_to_eof(self, tmp_path):
        """Unmatched block-start (no closing marker) exempts to EOF.
        Permissive by design — bias toward false-negative over blocking
        legitimate doc work."""
        md = self._write_md(
            tmp_path,
            "Before: #1234 fires.\n"
            "<!-- planning-artifact-exempt-block: no closer demo -->\n"
            "After: #5678 does NOT fire.\n",
        )
        violations = _scan_file(md)
        match_texts = [v.match_text for v in violations]
        assert match_texts == ["#1234"], (
            f"unmatched block-start leak: {violations}"
        )

    def test_anchor_link_does_not_fire(self, tmp_path):
        """`#section-heading` is a markdown anchor, not an issue ref."""
        md = self._write_md(
            tmp_path,
            "See [the section](#section-heading) for more.\n",
        )
        violations = [v for v in _scan_file(md) if v.pattern_name == "pr_or_issue_ref"]
        assert violations == [], f"anchor link false-positive: {violations}"

    def test_numeric_anchor_link_does_not_fire(self, tmp_path):
        """`#12-completion-authority` is an in-doc anchor link to a numbered
        section, NOT a PR/issue ref. The negative-lookahead for `[\\w-]`
        after the digit run excludes it."""
        md = self._write_md(
            tmp_path,
            "[Authority Protocol §12](pact-completion-authority.md#12-completion-authority)\n",
        )
        violations = [v for v in _scan_file(md) if v.pattern_name == "pr_or_issue_ref"]
        assert violations == [], (
            f"numeric anchor link false-positive: {violations}"
        )

    def test_html_entity_does_not_fire(self, tmp_path):
        """`&#039;` is the HTML entity for apostrophe, not an issue ref.
        The negative-lookbehind for `&` excludes HTML entities."""
        md = self._write_md(
            tmp_path,
            "Replace ` ` with `&#039;` to escape.\n",
        )
        violations = [v for v in _scan_file(md) if v.pattern_name == "pr_or_issue_ref"]
        assert violations == [], f"HTML entity false-positive: {violations}"

    def test_sha_looking_fires_on_realistic_sha(self, tmp_path):
        md = self._write_md(tmp_path, "From commit bef7f24 onward.\n")
        violations = _scan_file(md)
        assert any(
            v.pattern_name == "sha_looking" and v.match_text == "bef7f24"
            for v in violations
        ), f"expected sha_looking hit, got: {violations}"

    def test_sha_looking_skips_pure_digits(self, tmp_path):
        """Phone numbers, timestamps, message IDs are pure-digit and exempt
        by the at-least-one-letter constraint."""
        md = self._write_md(
            tmp_path,
            "Phone: 1234567890. Timestamp: 1737384600. msgId: 1234567890.\n",
        )
        violations = [v for v in _scan_file(md) if v.pattern_name == "sha_looking"]
        assert violations == [], f"pure-digit false-positive: {violations}"

    def test_sha_looking_skips_pure_letters(self, tmp_path):
        """English words are pure-letter and exempt by the at-least-one-
        digit constraint."""
        md = self._write_md(
            tmp_path,
            "The deafened cabbage faced bedded effaced facade.\n",
        )
        violations = [v for v in _scan_file(md) if v.pattern_name == "sha_looking"]
        assert violations == [], f"pure-letter false-positive: {violations}"

    def test_version_marker_fires(self, tmp_path):
        md = self._write_md(tmp_path, "Released as v4.2.15 last week.\n")
        violations = _scan_file(md)
        assert any(
            v.pattern_name == "version_marker" for v in violations
        ), f"expected version_marker hit, got: {violations}"

    def test_task_id_fires(self, tmp_path):
        md = self._write_md(tmp_path, "Per Task #14 the architect ...\n")
        violations = _scan_file(md)
        assert any(
            v.pattern_name == "in_session_task_id" for v in violations
        ), f"expected in_session_task_id hit, got: {violations}"

    def test_commit_letter_fires(self, tmp_path):
        md = self._write_md(tmp_path, "Commit B added the field.\n")
        violations = _scan_file(md)
        assert any(
            v.pattern_name == "commit_letter" for v in violations
        ), f"expected commit_letter hit, got: {violations}"

    def test_bug_letter_fires(self, tmp_path):
        md = self._write_md(tmp_path, "Bug C dispatch was routed back.\n")
        violations = _scan_file(md)
        assert any(
            v.pattern_name == "bug_letter" for v in violations
        ), f"expected bug_letter hit, got: {violations}"

    def test_pattern_catalog_is_non_empty(self):
        """Defensive — guards against a future refactor that drops all
        patterns and renders the test vacuous."""
        assert len(PATTERN_CATALOG) >= 5
        names = {spec.name for spec in PATTERN_CATALOG}
        # Pin the canonical pattern names so a rename surfaces here.
        assert {
            "pr_or_issue_ref",
            "sha_looking",
            "version_marker",
            "in_session_task_id",
            "commit_letter",
            "bug_letter",
        } <= names
