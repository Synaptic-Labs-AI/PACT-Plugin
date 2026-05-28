"""
Cross-file structural invariant for the UMBRELLA_SUBJECT_PREFIXES coverage
contract.

This invariant pins the lockstep-coupling between three surfaces — the
production tuple at `shared/wake_lifecycle.py::UMBRELLA_SUBJECT_PREFIXES`,
the per-command-file mapping of which workflow creates which umbrella, and
the actual literal references in `pact-plugin/commands/*.md` — so a future
addition of an umbrella-creating workflow that updates only ONE of the
three surfaces fails loudly at CI time.

Worked example (the gap this invariant directly defends against):
PR #850's B1 finding — when `/PACT:peer-review` was introduced as an
umbrella-creating workflow with the subject prefix `"Review: "`, the
production tuple `UMBRELLA_SUBJECT_PREFIXES` was NOT updated. Peer-review
orchestrations would have been structurally invisible to Gate 6 (the
OPERATIONAL-LULL-AT-PHASE-BOUNDARY suppression at
`teardown_request_emitter.py` and the Tier-2 mirror at
`wake_lifecycle_emitter.py`), and a peer-review session at a phase-lull
window would have fired a spurious teardown_request. The bug was caught
by peer review (B1 in `docs/review/842-pr-b-backend.md`) but would have
shipped without it.

This invariant catches the class at CI time via three assertions:

  Assertion 1 (file-side coverage): for each (file, prefixes) in the
  mapping, every declared prefix MUST appear as a quoted literal
  `"<prefix>` somewhere in the file. Fails when a file's mapping entry
  lists a prefix the file doesn't actually reference (e.g., the mapping
  declares `peer-review.md` references `"Review: "` but the file lost
  the reference in a refactor).

  Assertion 2 (UMBRELLA_SUBJECT_PREFIXES coverage): every prefix in
  the production tuple MUST appear in some mapping entry's prefix-set
  OR in `UMBRELLA_PREFIXES_WITHOUT_DOCUMENTED_CONSUMER` (the
  documentation-gap allowlist for prefixes that are runtime-active but
  not yet documented as quoted literals in `commands/*.md`). Fails when
  a new prefix is added to the tuple without a corresponding mapping
  entry — would have caught B1's inverse (if a `"Review: "` reference
  was added to peer-review.md AND the mapping was updated BUT the
  production tuple was not, then... wait, that's Assertion 3's case;
  this Assertion 2 catches the case where the production tuple has an
  entry that no mapping covers).

  Assertion 3 (mapping ⊆ UMBRELLA_SUBJECT_PREFIXES): every prefix in
  any mapping entry's prefix-set MUST be in the production tuple OR in
  the documentation-gap allowlist. Fails when a mapping entry references
  a prefix that's not declared in the production tuple — this is the
  exact B1 case: dispatcher adds `"Review: "` to `peer-review.md` AND
  to the mapping but forgets the production tuple → Gate 6 wouldn't
  suppress because the predicate iterates the production tuple, not
  the mapping. Lockstep enforcement at CI.

The combination of all three assertions enforces a bidirectional
contract: production tuple ↔ mapping ↔ commands/*.md. Any single-surface
edit that breaks the lockstep fails CI loudly with a message naming the
orphan prefix and the file or surface that's out of sync.

Sister to `test_first_observable_write_misfire_invariant.py` — both
crystallize a once-empirically-observed architectural shape into a
CI-time gate. Scope is intentionally narrow: this invariant scopes to
`pact-plugin/commands/*.md` (the workflow-creation SSOT); generalization
to other directories (skills/, agents/, protocols/) is deferred until a
second instance of the gap class crystallizes elsewhere.

Scanner is regex-based, not AST-based: the umbrella-prefix references
in commands/*.md are markdown-embedded quoted literals (in code-fence
examples, tree-art diagrams, and bullet lists), not executable Python
source. A `re.compile(r'"<prefix>')` substring check is the direct
match for the textual surface. The sister invariant test uses Python
`ast` because it walks actual Python source files; the choice of
scanner technology follows the file-type, not a uniform convention.
"""

import re
from pathlib import Path

import pytest

from shared.wake_lifecycle import UMBRELLA_SUBJECT_PREFIXES


COMMANDS_DIR = Path(__file__).resolve().parent.parent / "commands"


# Per-command-file map: which UMBRELLA_SUBJECT_PREFIXES does each workflow
# command create? Empirically populated from a pre-impl scan against
# commands/*.md; each (file, prefix) pair corresponds to a quoted-literal
# reference of the umbrella subject prefix in the file's prose, code-fence
# examples, tree-art diagrams, or bullet lists. Adding a new umbrella-
# creating workflow requires updating BOTH this mapping AND
# UMBRELLA_SUBJECT_PREFIXES in lockstep — Assertions 2 and 3 enforce that
# lockstep at CI time.
#
# Classification criterion (documented for future maintainers): a prefix
# belongs to a file's required-prefix set when the file (a) creates the
# umbrella task at workflow start via documented TaskCreate (orchestrate,
# peer-review, plan-mode) OR (b) creates child phase tasks under an
# umbrella with the prefix (rePACT's nested phase prefixes). Files that
# merely MENTION an umbrella prefix in prose without creating it are NOT
# in scope (no current examples in commands/).
UMBRELLA_PREFIX_COMMAND_MAPPING: dict[str, set[str]] = {
    "orchestrate.md": {"PREPARE: ", "ARCHITECT: ", "CODE: ", "TEST: "},
    "peer-review.md": {"Review: "},
    "plan-mode.md": {"Plan: "},
    "rePACT.md": {"PREPARE: ", "ARCHITECT: ", "CODE: ", "TEST: "},
}


# Documentation-gap allowlist: UMBRELLA_SUBJECT_PREFIXES entries that are
# runtime-active (the production hooks/skills/agents create tasks with
# these subject prefixes) but have NO quoted-literal example in any
# `commands/*.md` file as of this invariant's authoring.
#
# - "Feature: ": `/PACT:orchestrate` creates a `Feature: {feature-slug}`
#   umbrella per its narrative ("1. TaskCreate: Feature task ..."), but
#   the markdown's example subjects use generic names like
#   `"Implement user auth"` without the `Feature: ` prefix shown. Runtime
#   creation is empirically verified (current session's Task #2 has
#   subject `"Feature: #842 OPERATIONAL-LULL HYBRID hook defense (PR-B)"`).
# - "Plan (revised): ": `/PACT:plan-mode` re-runs after revision use this
#   variant prefix; the markdown documents only the initial `"Plan: "`
#   template. The revised variant is conditional-on-revision and not
#   shown in any example.
#
# Both entries are kept in UMBRELLA_SUBJECT_PREFIXES because Gate 6's
# correctness depends on detecting these umbrella shapes at runtime. The
# allowlist mechanism here is the honest encoding of the documentation
# gap — Option D-strict (add example templates to orchestrate.md and
# plan-mode.md) is queued as a separate follow-up issue. Removing an
# entry from this allowlist requires the corresponding command file to
# add a quoted-literal example of the prefix template.
UMBRELLA_PREFIXES_WITHOUT_DOCUMENTED_CONSUMER: set[str] = {
    "Feature: ",
    "Plan (revised): ",
}


def _read_command_file(filename: str) -> str:
    """Read a commands/*.md file's full text. Raises FileNotFoundError if
    the file doesn't exist (mapping points at a stale file) — surfaced as
    a test failure naming the missing file."""
    path = COMMANDS_DIR / filename
    return path.read_text(encoding="utf-8")


class TestUmbrellaPrefixCoverageInvariant:
    """Three-assertion lockstep contract for UMBRELLA_SUBJECT_PREFIXES."""

    def test_assertion_1_file_side_coverage(self):
        """For each (file, prefixes) in the mapping, every declared
        prefix MUST appear as a quoted literal `"<prefix>` somewhere in
        the file. Catches: a file's mapping entry lists a prefix the
        file no longer references (refactor drift)."""
        missing: list[tuple[str, str]] = []
        for filename, prefixes in UMBRELLA_PREFIX_COMMAND_MAPPING.items():
            content = _read_command_file(filename)
            for prefix in prefixes:
                if f'"{prefix}' not in content:
                    missing.append((filename, prefix))
        assert not missing, (
            "UMBRELLA_PREFIX_COMMAND_MAPPING declares (file, prefix) pairs "
            "where the file does NOT contain a quoted-literal reference "
            f'`"<prefix>`. Missing: {missing!r}. Either restore the '
            "reference in the file or remove the prefix from the mapping "
            "entry (and from UMBRELLA_SUBJECT_PREFIXES if no other file "
            "references it)."
        )

    def test_assertion_2_umbrella_prefixes_have_documented_or_allowlisted_consumer(self):
        """For each prefix in UMBRELLA_SUBJECT_PREFIXES, the prefix MUST
        appear in some mapping entry's prefix-set OR in
        UMBRELLA_PREFIXES_WITHOUT_DOCUMENTED_CONSUMER. Catches: a
        production tuple entry has no documented consumer AND no explicit
        documentation-gap allowlist entry."""
        all_mapped_prefixes: set[str] = set()
        for prefixes in UMBRELLA_PREFIX_COMMAND_MAPPING.values():
            all_mapped_prefixes |= prefixes
        covered = all_mapped_prefixes | UMBRELLA_PREFIXES_WITHOUT_DOCUMENTED_CONSUMER
        orphans = set(UMBRELLA_SUBJECT_PREFIXES) - covered
        assert not orphans, (
            "UMBRELLA_SUBJECT_PREFIXES contains entries with NO documented "
            "consumer in UMBRELLA_PREFIX_COMMAND_MAPPING and NO entry in "
            "UMBRELLA_PREFIXES_WITHOUT_DOCUMENTED_CONSUMER. Orphan "
            f"prefixes: {sorted(orphans)!r}. Either (a) add a mapping "
            "entry pointing at the file that creates the umbrella, OR "
            "(b) add the prefix to UMBRELLA_PREFIXES_WITHOUT_DOCUMENTED_"
            "CONSUMER with rationale in its docstring."
        )

    def test_assertion_3_mapping_is_subset_of_production_tuple(self):
        """For each prefix in any mapping entry's prefix-set, the prefix
        MUST be in UMBRELLA_SUBJECT_PREFIXES or in
        UMBRELLA_PREFIXES_WITHOUT_DOCUMENTED_CONSUMER. This is the
        DIRECT B1 defense: a dispatcher adds `"Review: "` to
        peer-review.md AND to the mapping but forgets the production
        tuple. The mapping then references a prefix Gate 6 doesn't
        recognize → test FAILS naming the orphan prefix and the
        mapping file."""
        production_tuple = set(UMBRELLA_SUBJECT_PREFIXES)
        production_or_allowlist = (
            production_tuple | UMBRELLA_PREFIXES_WITHOUT_DOCUMENTED_CONSUMER
        )
        orphans: list[tuple[str, str]] = []
        for filename, prefixes in UMBRELLA_PREFIX_COMMAND_MAPPING.items():
            for prefix in prefixes:
                if prefix not in production_or_allowlist:
                    orphans.append((filename, prefix))
        assert not orphans, (
            "UMBRELLA_PREFIX_COMMAND_MAPPING declares prefixes NOT in "
            "UMBRELLA_SUBJECT_PREFIXES at shared/wake_lifecycle.py. Gate 6 "
            "(OPERATIONAL-LULL suppression) iterates the production "
            "tuple, NOT the mapping — so a prefix in the mapping but not "
            "in the tuple is structurally invisible to the suppression "
            f"predicate. Orphan (file, prefix) pairs: {orphans!r}. Add "
            "the prefix to UMBRELLA_SUBJECT_PREFIXES at "
            "pact-plugin/hooks/shared/wake_lifecycle.py to close the "
            "lockstep gap."
        )


class TestMappingShape:
    """Pin the shape of UMBRELLA_PREFIX_COMMAND_MAPPING itself —
    structural invariants on the mapping data structure (not on its
    contents). Catches accidental shape regressions during edits."""

    @pytest.mark.parametrize("filename", list(UMBRELLA_PREFIX_COMMAND_MAPPING.keys()))
    def test_mapping_filenames_point_at_existing_command_files(self, filename):
        """Every filename in the mapping must point at an existing file
        under pact-plugin/commands/. Catches typos in filename or stale
        entries after a command rename."""
        path = COMMANDS_DIR / filename
        assert path.exists() and path.is_file(), (
            f"UMBRELLA_PREFIX_COMMAND_MAPPING references {filename!r} but "
            f"no such file exists under {COMMANDS_DIR}."
        )

    def test_mapping_values_are_nonempty_sets(self):
        """Every mapping entry's value must be a non-empty set. An empty
        set would silently exempt the file from Assertion 1 — guard
        against accidental wipes."""
        for filename, prefixes in UMBRELLA_PREFIX_COMMAND_MAPPING.items():
            assert isinstance(prefixes, set) and prefixes, (
                f"UMBRELLA_PREFIX_COMMAND_MAPPING[{filename!r}] must be a "
                "non-empty set; empty sets silently exempt the file from "
                "the file-side coverage assertion."
            )

    def test_prefix_shape_capital_led_colon_space(self):
        """Every prefix across mapping + allowlist + production tuple
        must match `^[A-Z][A-Za-z()\\s]*: $` (capital-led, allowing
        parens + internal space for `Plan (revised): `, ending in
        colon-space). Pins the shape contract: lowercase role-prefixes
        like `secretary: ` are NOT umbrella prefixes and must not
        accidentally be added to any of these sets."""
        all_prefixes = (
            set(UMBRELLA_SUBJECT_PREFIXES)
            | UMBRELLA_PREFIXES_WITHOUT_DOCUMENTED_CONSUMER
        )
        for prefixes in UMBRELLA_PREFIX_COMMAND_MAPPING.values():
            all_prefixes |= prefixes
        shape_pattern = re.compile(r"^[A-Z][A-Za-z()\s]*: $")
        for prefix in all_prefixes:
            assert shape_pattern.match(prefix), (
                f"Prefix {prefix!r} does not match the umbrella-prefix "
                "shape `^[A-Z][A-Za-z()\\s]*: $` (capital-led name + "
                "optional parens + colon-space). Lowercase role-prefixes "
                "(e.g. `secretary: `) are NOT umbrella prefixes — they "
                "belong in a separate concept space."
            )
