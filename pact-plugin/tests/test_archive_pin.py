"""
Tests for scripts/archive_pin.py — the archive-on-evict verification step.

Risk tier: CRITICAL. This script's verdict is what /PACT:prune-memory keys
its refuse-or-proceed decision on, and the pin being archived may be the
only remaining copy of the content (CLAUDE.md is gitignored, so there is no
git fallback). A FALSE `ARCHIVED` is the one error that destroys data: the
command acts on it by evicting. The inverse error (falsely refusing) costs
an over-block and loses nothing, so the two directions are NOT symmetric
and the tests here weight the false-positive direction accordingly.

Test strategy, stated because it is load-bearing:

  * At least one test drives the REAL memory CLI end-to-end against a temp
    `--db-path` database. A suite that stubs every subprocess would verify
    the stub, not the archive — the vacuous-verification defect this whole
    feature exists to remove, reproduced one layer down.
  * The failure matrix stubs `_run_memory_cli`, because a real store cannot
    be made to fail on demand in the specific ways that matter.
  * Temp DBs throughout — no test touches the shared store.

`db_path` IS REQUIRED AND KEYWORD-ONLY on `build_verdict`, so every call in
this file states an answer. Two answers are legal and they mean different
things:

  * `db_path=str(memory_store(...))` — this call CAN reach a real save, and is
    scoped to a temp store the fixture BROUGHT INTO EXISTENCE first. A bare
    `tmp_path / "x.db"` no longer reaches: it names a store that is ABSENT,
    and the CLI boundary refuses an absent caller path for each command other
    than `setup`. Such a call returns NOT_ARCHIVED, and an arm that reads only
    a pre-save field (`heading`, `delete_string`) stays GREEN while measuring
    nothing. Where the reach is the point, assert a POST-SAVE field as well,
    because no pre-save field can fail when the reach stops.
  * `db_path=None` — this call provably never reaches a store, because the
    enclosing test stubs `_run_memory_cli` (or `subprocess.run`) or
    short-circuits before the spawn (bad index, unresolvable CLAUDE.md,
    missing `_MEMORY_CLI`, a patched extractor). `None` still MEANS the
    live store; what makes it safe here is that nothing consumes it.

So `db_path=None` is a claim about the call site, not a shrug. If you delete
the stub or the short-circuit above such a call, that claim becomes false and
the call starts writing to the developer's real database — which is exactly
the defect this parameter was made required to surface. The mechanical
backstop is the guard in `_run_memory_cli`; this convention is what makes the
choice reviewable.
"""

import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402

import archive_pin  # noqa: E402
import pin_caps  # noqa: E402
from shared.project_scope import same_repository  # noqa: E402
import staleness  # noqa: E402


# Formats a curator can plausibly hand-write. B, D and E are the ones a
# part-reconstructing extractor mangles; they are here so a regression from
# slicing back to rebuilding fails loudly rather than silently archiving a
# whitespace-normalized variant.
PIN_FORMATS = {
    "canonical": "<!-- pinned: 2026-01-01 -->\n### Alpha\nbody alpha\n",
    "blank_line_before_heading": (
        "<!-- pinned: 2026-01-01 -->\n\n### Beta\nbody beta\n"
    ),
    "leading_ws_on_comment": (
        "  <!-- pinned: 2026-01-01 -->\n### Gamma\nbody gamma\n"
    ),
    "trailing_ws_on_comment": (
        "<!-- pinned: 2026-01-01 -->   \n### Delta\nbody delta\n"
    ),
    "two_blank_lines": (
        "<!-- pinned: 2026-01-01 -->\n\n\n### Epsilon\nbody epsilon\n"
    ),
}


@pytest.fixture
def claude_md(tmp_path, monkeypatch):
    """Write a CLAUDE.md and point the script's resolver at it."""
    def _write(content):
        path = tmp_path / "CLAUDE.md"
        path.write_text(content, encoding="utf-8")
        monkeypatch.setattr(
            archive_pin, "get_project_claude_md_path", lambda: path
        )
        return path
    return _write


def _pinned_body(content):
    """Extract the Pinned Context section body from a full CLAUDE.md."""
    parsed = staleness._parse_pinned_section(content)
    assert parsed is not None, "fixture has no Pinned Context section"
    return parsed[2]


def _two_pin_file():
    return make_claude_md_with_pins([
        make_pin_entry(title="First Pin", body_chars=40, date="2026-01-01"),
        make_pin_entry(title="Second Pin", body_chars=40, date="2026-02-02"),
    ])


class TestExtractPinBlock_Verbatim:
    """The extracted block must be a VERBATIM SLICE of CLAUDE.md.

    This is the property that makes the containment criterion meaningful.
    If the block is already a lossy rendering of the pin, then saving it and
    finding it again proves only that the round-trip preserved a variant —
    all three conjuncts hold while the verdict certifies something that is
    not the pin. The fidelity gap would simply relocate upstream of where
    the criterion looks.
    """

    @pytest.mark.parametrize("label", sorted(PIN_FORMATS))
    def test_slice_is_verbatim_in_source(self, label):
        source = PIN_FORMATS[label]
        pins = pin_caps.parse_pins(source)
        assert pins, f"fixture {label} parsed no pins — test would be vacuous"
        block = archive_pin.extract_pin_block(source, 0, pins)
        assert block in source, (
            f"format {label!r}: extracted block is not a verbatim substring "
            f"of the source. Block={block!r}"
        )

    @pytest.mark.parametrize("label", sorted(PIN_FORMATS))
    def test_slice_carries_the_substance(self, label):
        """Verbatim is necessary but not sufficient — the empty string is
        also a substring. Pin that the block actually carries the heading
        and the body, so a degenerate extractor cannot pass the test above."""
        source = PIN_FORMATS[label]
        pins = pin_caps.parse_pins(source)
        block = archive_pin.extract_pin_block(source, 0, pins)
        assert pins[0].heading in block
        assert pins[0].body.strip() in block
        assert "<!-- pinned:" in block

    def test_blank_line_between_comment_and_heading_survives(self):
        """The specific loss a part-reconstruction introduces. parse_pins
        walks BACKWARD over blank lines to find the date comment, so a
        rebuild drops them; a slice keeps them."""
        source = PIN_FORMATS["blank_line_before_heading"]
        pins = pin_caps.parse_pins(source)
        block = archive_pin.extract_pin_block(source, 0, pins)
        assert "-->\n\n### Beta" in block

    def test_slice_beats_naive_rebuild_on_the_same_input(self):
        """Direct comparison against the alternative implementation.

        This is the test that would have caught the original approach. It
        asserts BOTH that the slice is verbatim for every format AND that a
        rebuild is not — so if someone 'simplifies' the extractor back to
        concatenating parts, this fails with a message naming the formats
        that broke. The rebuild arm is also the non-vacuity control: if it
        ever passed for all five, the hazard would be gone and this whole
        test would be measuring nothing.
        """
        slice_ok, rebuild_ok, rebuild_failures = [], [], []
        for label, source in PIN_FORMATS.items():
            parsed = pin_caps.parse_pins(source)
            pin = parsed[0]
            block = archive_pin.extract_pin_block(source, 0, parsed)
            date_comment = pin.date_comment or ""
            rebuild = (
                f"{date_comment}\n{pin.heading}\n{pin.body}"
                if date_comment else f"{pin.heading}\n{pin.body}"
            )
            slice_ok.append(block in source)
            rebuild_ok.append(rebuild in source)
            if rebuild not in source:
                rebuild_failures.append(label)

        assert all(slice_ok), "slice must be verbatim for EVERY format"
        assert rebuild_failures, (
            "a naive rebuild is verbatim for all formats — the hazard this "
            "extractor defends against no longer exists, so this test is "
            "now vacuous and should be re-aimed or retired"
        )

    def test_slice_includes_the_comment_lines_leading_indentation(self):
        """The span rule starts at the comment LINE's first character, not at
        the comment text. Both are verbatim substrings, so containment alone
        cannot tell them apart — this pins the stricter of the two."""
        source = PIN_FORMATS["leading_ws_on_comment"]
        pins = pin_caps.parse_pins(source)
        block = archive_pin.extract_pin_block(source, 0, pins)
        assert block.startswith("  <!-- pinned:"), (
            f"slice dropped the comment line's indentation: {block[:40]!r}"
        )

    def test_slice_is_not_stripped(self):
        """The block is taken EXACTLY — no strip, rejoin or normalize.

        Dropping the strip is safe and was measured, not assumed: `context`
        is a scalar and escapes the string-list normalization, so a value
        with trailing newlines round-trips byte-exact. Had it been stripped
        in transit, containment would have failed for every archive — an
        over-block on the whole feature rather than an edge case.
        """
        source = (
            "<!-- pinned: 2026-01-01 -->\n### First\nbody one\n\n\n"
            "<!-- pinned: 2026-02-02 -->\n### Second\nbody two\n"
        )
        pins = pin_caps.parse_pins(source)
        block = archive_pin.extract_pin_block(source, 0, pins)
        assert block in source
        assert block.endswith("\n\n\n"), (
            f"separator blank lines were stripped from the span: "
            f"{block[-12:]!r}"
        )

    def test_index_out_of_range_is_unevaluable(self):
        source = PIN_FORMATS["canonical"]
        pins = pin_caps.parse_pins(source)
        with pytest.raises(archive_pin._Unevaluable):
            archive_pin.extract_pin_block(source, 5, pins)

    def test_second_pin_slice_does_not_bleed_into_the_first(self):
        """Block boundaries: pin 1's slice must not carry pin 0's content."""
        content = _two_pin_file()
        pinned = _pinned_body(content)
        pins = pin_caps.parse_pins(pinned)
        block = archive_pin.extract_pin_block(pinned, 1, pins)
        assert "Second Pin" in block
        assert "First Pin" not in block

    def test_first_pin_slice_does_not_swallow_the_next_pins_date_comment(self):
        """The FORWARD bleed, which is the easy one to miss.

        A span starts at its date-comment LINE, which sits BEFORE the heading.
        So ending pin N's block at the next `### ` heading — the obvious rule,
        and what the spec's `end` clause says literally — puts pin N+1's date
        comment inside pin N's archived block. The record then carries another
        pin's pinned-date.

        Containment can never catch this: the block is still a verbatim
        substring of CLAUDE.md and every conjunct of the criterion holds. The
        end boundary must therefore be the next pin's SPAN start, computed by
        the same rule as its own start.
        """
        content = _two_pin_file()
        pinned = _pinned_body(content)
        pins = pin_caps.parse_pins(pinned)
        block = archive_pin.extract_pin_block(pinned, 0, pins)

        assert "First Pin" in block
        assert pins[1].date_comment not in block, (
            f"pin 0's block swallowed pin 1's date comment "
            f"({pins[1].date_comment!r}) — the archived record would carry "
            f"another pin's pinned-date"
        )
        assert block.count("<!-- pinned:") == 1, (
            "this fixture's pins carry one date comment each, so a second "
            "one in pin 0's block means it swallowed pin 1's. NOT a general "
            "invariant: a pin whose body documents the pin format "
            "legitimately contains two, and that slice is correct. The "
            "fixture-independent property is the assertion above — a block "
            "contains its OWN pin's comment and not its successor's."
        )

    @pytest.mark.parametrize("decoy_placement", ["mid_body", "adjacent"])
    def test_span_start_takes_the_NEAREST_preceding_date_comment(
        self, decoy_placement
    ):
        """`_span_start` must resolve a pin's date comment by NEAREST
        preceding occurrence, not first.

        THE PROPERTY, stated so it survives a rewrite: pin 1's span begins
        at the comment ADJACENT TO ITS OWN HEADING. When an identical
        comment string also appears earlier — inside pin 0's body — the
        earlier one must not capture the span.

        WHY THIS TEST EXISTS AND NO OTHER COVERS IT. Every other fixture in
        this file has distinct date comments, so `rfind` and `find` return
        the same offset and the whole suite passes under either. Measured:
        swapping `preceding.rfind(date_comment)` for `.find(...)` leaves the
        suite at 55 passed. Under `find`, pin 1's span starts at the decoy
        and swallows the tail of pin 0's body, while pin 0's block truncates
        at the decoy — and the archived record still satisfies every
        containment conjunct, because a wrong span is still a verbatim
        substring. Boundaries are exactly what containment cannot see.

        `adjacent` is the sharper case: the decoy sits immediately before
        the real comment, so anything weaker than nearest-occurrence picks
        the wrong one by a single line.

        NOTE WHAT IS DELIBERATELY *NOT* ASSERTED. Pin 0's block here
        legitimately contains TWO date comments — the decoy is part of its
        body. `count("<!-- pinned:") == 1` is a property of the live file's
        current CONTENT, never an extractor invariant, and this fixture is
        the counterexample. Asserting it would manufacture a false RED.
        """
        decoy = "<!-- pinned: 2026-02-02 -->"
        # Text BETWEEN the decoy and the real comment. This is what a
        # first-occurrence resolver drags into pin 1's block, so it must sit
        # AFTER the decoy to discriminate — a marker placed before it is
        # invisible to the bug.
        between = (
            "\n" if decoy_placement == "adjacent"
            else "DRAGGED_IN_UNDER_FIND — still alpha's body.\n\n"
        )
        pinned = (
            "<!-- pinned: 2026-01-01 -->\n"
            "### Alpha\n"
            "Alpha's body documents the pin format:\n"
            f"{decoy}\n"
            f"{between}"
            f"{decoy}\n"
            "### Beta\n"
            "Beta's body.\n"
        )
        pins = pin_caps.parse_pins(pinned)
        assert len(pins) == 2, f"fixture must parse as 2 pins, got {len(pins)}"
        assert pinned.count(decoy) == 2, "fixture must contain the decoy twice"

        alpha = archive_pin.extract_pin_block(pinned, 0, pins)
        beta = archive_pin.extract_pin_block(pinned, 1, pins)

        # THE load-bearing assertions, and they are ABSOLUTE OFFSETS rather
        # than content checks. Pin 1 is last, so its span runs from the
        # NEAREST (last) decoy occurrence to the end of the section.
        #
        # Content checks alone are too weak in the `adjacent` case, and
        # PARTITION CANNOT CATCH THIS AT ALL: pin 0's end and pin 1's start
        # are computed by the SAME `_span_start` call, so they move together
        # and `alpha + beta == pinned` holds under both resolvers. Only an
        # assertion pinned to the expected OFFSET discriminates.
        nearest = pinned.rfind(decoy)
        assert beta == pinned[nearest:], (
            "pin 1's span did not begin at the NEAREST preceding date "
            "comment. `_span_start` resolved the FIRST matching occurrence "
            "instead, so an identical comment inside pin 0's body captured "
            f"the span.\n  expected: {pinned[nearest:]!r}\n  actual:   {beta!r}"
        )
        assert alpha == pinned[:nearest], (
            "pin 0's block did not end at pin 1's real span start — its END "
            "boundary resolved to the decoy inside its own body.\n"
            f"  expected: {pinned[:nearest]!r}\n  actual:   {alpha!r}"
        )

    def test_delete_to_next_heading_would_destroy_a_RETAINED_pins_date(self):
        """The archive rule and the next-`### `-heading rule are NOT
        interchangeable, and the difference is another pin's content.

        WHAT THIS PINS. `extract_pin_block` ends a pin at the NEXT PIN'S
        SPAN START (its date-comment line). A removal step that instead
        ran to the next `### ` HEADING would delete a strict SUPERSET —
        and the excess is the date comment belonging to the pin being
        RETAINED. Stripping it makes that pin undatable, so `age_days`
        becomes null, and by the three-state rule it drops out of the
        overdue ordering permanently. The prune corrupts a pin nobody
        chose to prune.

        WHY IT IS WORTH A TEST WHEN NO PYTHON RUNS THE REMOVAL. The
        removal is an LLM-executed Edit driven by prose, so what actually
        gets deleted is not observable here. This does not attempt to
        observe it. It pins the thing that IS observable and that makes
        the prose's wording load-bearing rather than stylistic: that the
        two candidate boundary rules diverge, and by exactly what. A
        reader who changes the removal wording meets a measured statement
        of what the other rule costs.

        HONEST BOUND, so this is not mistaken for coextensivity: this
        cannot catch the prose stating the wrong rule — that needs a
        prose-contract guard — and neither can observe the actual delete.
        Full span equality is only reachable by removing the SECOND
        DERIVATION: emit the block or its offsets in the verdict so the
        command has no boundaries to re-derive.
        """
        pinned = (
            "<!-- pinned: 2026-01-01 -->\n"
            "### Alpha\n"
            "Alpha's body.\n\n"
            "<!-- pinned: 2026-02-02 -->\n"
            "### Beta\n"
            "Beta's body.\n"
        )
        pins = pin_caps.parse_pins(pinned)
        assert len(pins) == 2, f"fixture must parse as 2 pins, got {len(pins)}"
        assert pins[1].date_comment, "the RETAINED pin must carry a date comment"

        heading_starts = [
            m.start() for m in pin_caps._PIN_HEADING_RE.finditer(pinned)
        ]
        archived = archive_pin.extract_pin_block(pinned, 0, pins)
        # The rule a removal step must NOT use.
        to_next_heading = pinned[pinned.index(archived):heading_starts[1]]

        excess = to_next_heading[len(archived):]
        assert excess, (
            "the two boundary rules produced identical spans — this fixture "
            "no longer discriminates them, so the test is measuring nothing. "
            "Re-aim it rather than deleting it."
        )
        assert pins[1].date_comment in excess, (
            "expected the excess to be the RETAINED pin's date comment; the "
            f"rules still differ but not in the way documented. excess={excess!r}"
        )
        assert pins[1].date_comment not in archived, (
            "the archived block already contains the next pin's date comment "
            "— the span END regressed to the next-heading rule"
        )

    def test_spans_partition_the_section_without_overlap(self):
        """Stronger than the pairwise checks: every pin's span is disjoint
        from every other's, and concatenating them in order reproduces a
        contiguous run of the source. A boundary error in either direction
        breaks one of these."""
        content = _two_pin_file()
        pinned = _pinned_body(content)
        pins = pin_caps.parse_pins(pinned)
        blocks = [
            archive_pin.extract_pin_block(pinned, i, pins)
            for i in range(len(pins))
        ]
        assert len(blocks) == 2, "fixture must have 2 pins or this is vacuous"
        assert "".join(blocks) in pinned, (
            "spans are not contiguous — they overlap or leave a gap"
        )
        for i, block in enumerate(blocks):
            others = [b for j, b in enumerate(blocks) if j != i]
            for other in others:
                assert other not in block, f"span {i} contains another span"


class TestArchiveMarker:
    """The structured archive marker written into `entities[].type`.

    SCOPE, stated so no reader infers more: this marker makes archives
    DISCOVERABLE GOING FORWARD. It does not make them enumerable. Archives
    written before it shipped carry no marker at all — including ones folded
    into an existing record rather than saved standalone — so any count
    obtained by scanning for it is a FLOOR over future archives, never a
    total over all of them.
    """

    def test_marker_value_is_pinned(self):
        """The value is pinned because `entities[].type` is unconstrained
        free text — structured by POSITION, not by SCHEMA.

        Nothing in the store validates the field, so a typo or a rename
        ships SILENTLY: a scanner keyed on the old value returns a smaller
        set and raises nothing. A confident undercount is worse than a loud
        failure, and this test is what converts a silent drift into a red.
        """
        assert archive_pin.ARCHIVE_ENTITY_TYPE == "pact_memory_archive"

    def test_record_uses_the_constant_not_a_second_literal(self):
        """Any code that writes or scans the marker must read THIS constant.

        Two copies of a free-text value drift, and the drift is invisible in
        exactly the way a schema violation would not be. Asserting the
        record's value IS the constant object (rather than equal to a literal
        retyped here) is what makes this test detect a divergence instead of
        silently agreeing with a hardcoded copy.
        """
        record = archive_pin._build_record("### Pin\nbody", "Pin")
        entity = record["entities"][0]
        assert entity["type"] == archive_pin.ARCHIVE_ENTITY_TYPE
        assert entity["name"] == "Pin"
        assert "demoted from CLAUDE.md" in entity["notes"]

    def test_no_second_copy_of_the_literal_in_this_module(self):
        """Guards the drift directly: the literal may appear exactly once in
        archive_pin.py — at the constant's definition. A scan site or a
        second writer that re-types it would be invisible to the test above,
        because a hardcoded duplicate agrees with the constant right up until
        someone changes one of them."""
        source = Path(archive_pin.__file__).read_text(encoding="utf-8")
        occurrences = source.count('"pact_memory_archive"')
        assert occurrences == 1, (
            f"the marker literal appears {occurrences} times in "
            f"archive_pin.py; it must appear once (the constant) and every "
            f"other site must reference ARCHIVE_ENTITY_TYPE"
        )


class TestStandaloneOnlyInvariant:
    """Archival MUST create a standalone record, never update an existing one.

    This is asserted directly rather than via a downstream symptom, because
    the violation is INVISIBLE AT RUNTIME. An additive `update` creates no new
    record and runs no marker code, so a folded archive is silently absent
    from any marker scan — that already happened to prior demotions. A future
    maintainer adding a fold path for a perfectly good reason (dedup,
    retrieval quality, avoiding near-duplicates) would void the audit property
    WITHOUT TOUCHING THE MARKER CODE, and nothing would fail; the symptom is a
    missing record in somebody's later audit.

    If a fold path is ever genuinely wanted, the correct move is to write the
    marker on the update path too, or to withdraw the auditability claim in
    the same change — not to relax this test.
    """

    def test_archival_calls_save_and_never_update(
        self, claude_md, monkeypatch
    ):
        claude_md(_two_pin_file())
        subcommands = []

        def _spy(args, **kwargs):
            subcommands.append(args[0])
            if args[0] == "save":
                return 0, json.dumps(
                    {"ok": True, "result": {"memory_id": "d" * 32}}
                ), ""
            return 0, json.dumps({"ok": True, "result": {"context": "x"}}), ""

        monkeypatch.setattr(archive_pin, "_run_memory_cli", _spy)
        archive_pin.build_verdict(0, db_path=None)

        assert "save" in subcommands, (
            "no save was attempted — the spy never saw the create path, so "
            "this test would pass vacuously"
        )
        assert "update" not in subcommands
        assert set(subcommands) <= {"save", "get"}, (
            f"archival used an unexpected subcommand: {subcommands}"
        )

    def test_archive_subcommand_constant_is_save(self):
        """The subcommand is a named constant so a fold path cannot be
        introduced by editing a bare string at the call site."""
        assert archive_pin._ARCHIVE_SUBCOMMAND == "save"

    def test_no_update_subcommand_anywhere_in_the_module(self):
        """Structural backstop for the spy above, which can only observe the
        paths a test actually drives."""
        source = Path(archive_pin.__file__).read_text(encoding="utf-8")
        assert '"update"' not in source
        assert "'update'" not in source


class TestProjectDirResolution:
    """WHERE the archive is filed.

    The memory layer detects project_id from CLAUDE_PROJECT_DIR, else git,
    else by walking UP from the CWD to the nearest project marker — and
    `.claude` is such a marker. Since the installed plugin lives beneath
    `~/.claude/`, running the memory CLI from the plugin's own directory
    files a consumer's archived pin under project `.claude`. Deriving the
    directory from the CLAUDE.md actually read is what prevents that.
    """

    def test_dot_claude_layout_resolves_to_project_root(self):
        got = archive_pin.project_dir_for(Path("/tmp/proj/.claude/CLAUDE.md"))
        assert got.name == "proj"

    def test_legacy_layout_resolves_to_project_root(self):
        got = archive_pin.project_dir_for(Path("/tmp/proj/CLAUDE.md"))
        assert got.name == "proj"

    def test_project_dir_is_never_the_plugin_directory(self, tmp_path):
        """The regression this guards: a CWD anywhere under the plugin tree
        would resolve project_id to `.claude` for every consumer."""
        claude_md = tmp_path / "myproject" / ".claude" / "CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text("x", encoding="utf-8")
        resolved = archive_pin.project_dir_for(claude_md)
        plugin_root = Path(archive_pin.__file__).resolve().parent.parent
        assert resolved.name == "myproject"
        assert plugin_root not in resolved.parents
        assert resolved != plugin_root


class TestEnvPropagation_ExplicitCwdBeatsAmbient:
    """WHICH project the child process is TOLD it is in.

    `_run_memory_cli` hands the child an explicit CLAUDE_PROJECT_DIR derived
    from the caller's `cwd`. It used `env.setdefault`, which is a no-op when
    the variable is already set — so an AMBIENT value beat an EXPLICIT caller
    argument. The fail direction was inverted.

    This is a PRODUCTION property, not test hygiene. `resolve_claude_md`
    deliberately permits a worktree fall-through: the env dir is a worktree
    carrying no CLAUDE.md, so resolution lands on the MAIN repo's file. Under
    `setdefault` the archive was then filed under the WORKTREE while the pin
    lived in the MAIN repo — precisely the pin/archive disagreement
    `project_dir_for` exists to prevent.

    Both tests fire in EVERY regime. A trigger conditioned on the ambient
    variable already naming a CLAUDE.md-bearing directory would fire only in
    that one cell and sit green everywhere else, which is how a control stops
    meaning anything without anyone noticing.
    """

    @staticmethod
    def _init_repo_with_worktree(root):
        """Create a REAL git repo and a REAL linked worktree under `root`.

        Real rather than simulated on purpose: `resolve_claude_md` refuses a
        cross-project fall-through by asking git whether the env dir and the
        resolved base share a repository. Stubbing that seam would patch out
        the very permissiveness that makes this defect reachable in
        production, and the test would then prove nothing about it.
        """
        main = root / "mainrepo"
        subprocess.run(["git", "init", "-q", str(main)],
                       capture_output=True, text=True, check=True)

        def _git(*args):
            return subprocess.run(["git", "-C", str(main), *args],
                                  capture_output=True, text=True, check=True)

        _git("config", "user.email", "t@example.com")
        _git("config", "user.name", "t")
        (main / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git("add", "seed.txt")
        _git("commit", "-qm", "seed")
        worktree = root / "linked-worktree"
        _git("worktree", "add", "-q", "-b", "probe", str(worktree))
        return main, worktree

    @staticmethod
    def _spy_memory_cli_spawns(monkeypatch, context=""):
        """Capture the memory-CLI spawns AT THE PROCESS BOUNDARY.

        Only the CLI spawns are intercepted. Git probes are delegated to the
        real `subprocess.run`, because `resolve_claude_md` asks git whether
        two paths share a repository — stubbing that would defeat the
        fall-through under test. The boundary is the right observation point:
        it is the last place the parent controls before the child re-imports
        and re-resolves everything for itself.
        """
        real_run = subprocess.run
        calls = []

        class _Proc:
            def __init__(self, stdout):
                self.returncode = 0
                self.stdout = stdout
                self.stderr = ""

        def _fake_run(argv, **kwargs):
            if not (argv and argv[0] == sys.executable):
                return real_run(argv, **kwargs)
            calls.append((list(argv), kwargs))
            subcommand = argv[2] if len(argv) > 2 else ""
            if subcommand == "save":
                return _Proc(json.dumps(
                    {"ok": True, "result": {"memory_id": "e" * 32}}
                ))
            return _Proc(json.dumps(
                {"ok": True, "result": {"context": context}}
            ))

        monkeypatch.setattr(archive_pin.subprocess, "run", _fake_run)
        return calls

    def test_archive_is_filed_under_the_repo_whose_claude_md_was_read(
        self, tmp_path, monkeypatch
    ):
        """The production case: worktree env dir, main-repo CLAUDE.md."""
        main, worktree = self._init_repo_with_worktree(tmp_path)
        claude_md_path = main / "CLAUDE.md"
        claude_md_path.write_text(_two_pin_file(), encoding="utf-8")
        monkeypatch.setattr(
            archive_pin, "get_project_claude_md_path", lambda: claude_md_path
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))

        # PRECONDITIONS, asserted separately and FIRST. A fixture that drifts
        # out of the regime under test then fails with its own message,
        # instead of presenting as a regression in the one load-bearing
        # assertion below and inviting someone to "fix" that instead.
        assert not (worktree / "CLAUDE.md").exists(), (
            "fixture drift: the worktree must carry NO CLAUDE.md, or the "
            "fall-through this test depends on never happens"
        )
        assert same_repository(worktree, main), (
            "fixture drift: git does not consider the worktree part of the "
            "main repo, so resolve_claude_md would REFUSE rather than fall "
            "through, and this test would measure the refusal path"
        )

        calls = self._spy_memory_cli_spawns(
            monkeypatch, context=claude_md_path.read_text(encoding="utf-8")
        )
        verdict = archive_pin.build_verdict(0, db_path=str(tmp_path / "m.db"))

        assert verdict["outcome"] == "ARCHIVED", verdict
        assert calls, (
            "the memory CLI was never spawned — nothing was measured, and a "
            "per-call assertion over an empty list passes vacuously"
        )
        for argv, kwargs in calls:
            assert kwargs["env"]["CLAUDE_PROJECT_DIR"] == str(main), (
                f"the child was told it is in {kwargs['env']['CLAUDE_PROJECT_DIR']!r}, "
                f"but the CLAUDE.md actually read lives in {str(main)!r}. The "
                f"ambient value (the worktree) beat the resolved base, so the "
                f"archive files under a different project than the pin. "
                f"argv={argv[2:4]}"
            )
            assert kwargs["cwd"] == str(main)

    def test_explicit_cwd_overwrites_an_ambient_project_dir(
        self, tmp_path, monkeypatch
    ):
        """The fail-direction, isolated from resolution entirely."""
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        intended = tmp_path / "intended"
        intended.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(decoy))

        # PRECONDITION FIRST: without a live ambient value this test cannot
        # tell `setdefault` from assignment, and would pass under both.
        assert os.environ.get("CLAUDE_PROJECT_DIR") == str(decoy), (
            "precondition: the ambient decoy is not set, so this test cannot "
            "distinguish setdefault from explicit assignment"
        )

        calls = self._spy_memory_cli_spawns(monkeypatch)
        # Scoped db_path, though the spy means no child is ever launched:
        # this call enters the real `_run_memory_cli`, and naming a temp store
        # keeps it correct under the live-DB guard rather than relying
        # on the spy sitting downstream of it. None of the assertions below
        # read db_path, so it cannot perturb the property under test.
        archive_pin._run_memory_cli(
            ["get", "x" * 32], db_path=str(tmp_path / "mem.db"), cwd=intended
        )

        assert len(calls) == 1, (
            f"expected exactly one CLI spawn, captured {len(calls)}"
        )
        env = calls[0][1]["env"]
        assert env["CLAUDE_PROJECT_DIR"] == str(intended), (
            "the ambient CLAUDE_PROJECT_DIR beat the caller's explicit cwd. "
            "`env.setdefault` is a no-op when the variable is already set, "
            "which inverts the fail direction: the general answer wins over "
            "the specific one."
        )
        assert env["CLAUDE_PROJECT_DIR"] != str(decoy)
        assert calls[0][1]["cwd"] == str(intended)


class TestArchiveRecordPredicate:
    """The marker predicate, in code rather than reconstructed from prose.

    Every prior audit hand-rolled this match, and the version people reach for
    first is a substring test over the serialised blob. That is not equivalent:
    it also matches a record that merely MENTIONS the marker. On a live store
    the substring form returned 19 where the type-anchored form returned 18,
    and the extra row was a memory ABOUT the marker — a document describing the
    audit joining the population it was counting.
    """

    def test_matches_an_entity_carrying_the_marker_as_a_type(self):
        entities = [{"name": "x", "type": archive_pin.ARCHIVE_ENTITY_TYPE}]
        assert archive_pin.is_archive_record(entities) is True
        assert archive_pin.is_archive_record(json.dumps(entities)) is True, (
            "the stored form is a JSON string; a predicate that only accepts "
            "decoded lists forces every caller to decode first"
        )

    def test_does_not_match_a_record_that_merely_mentions_the_marker(self):
        """THE DISCRIMINATING CASE — this is the false positive measured live."""
        mentions = [{
            "name": f"note about {archive_pin.ARCHIVE_ENTITY_TYPE}",
            "type": "observation",
        }]
        blob = json.dumps(mentions)
        assert archive_pin.ARCHIVE_ENTITY_TYPE in blob, (
            "fixture does not contain the marker at all — the substring form "
            "would not match either, so this proves nothing"
        )
        assert archive_pin.is_archive_record(mentions) is False
        assert archive_pin.is_archive_record(blob) is False

    @pytest.mark.parametrize("bad", [
        None, "", "not json", "{}", '"a string"', "[1, 2, 3]", '["text"]', 42,
    ])
    def test_is_total_and_never_raises(self, bad):
        """An audit that dies on one malformed row reports nothing."""
        assert archive_pin.is_archive_record(bad) is False

    def test_sql_predicate_agrees_with_the_python_one(self, tmp_path):
        db = tmp_path / "probe.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, entities TEXT)")
        rows = [
            ("carrier", [{"name": "n", "type": archive_pin.ARCHIVE_ENTITY_TYPE}]),
            ("mentions", [{"name": f"about {archive_pin.ARCHIVE_ENTITY_TYPE}",
                           "type": "observation"}]),
            # A BARE STRING member. Real stores hold these — 63 among 14092 on
            # the live one — and they are what breaks the naive spelling.
            ("has_text_member", ["a bare string", {"name": "n", "type": "other"}]),
        ]
        for rid, ents in rows:
            con.execute("INSERT INTO memories VALUES (?, ?)", (rid, json.dumps(ents)))
        con.commit()

        got = con.execute(
            archive_pin.ARCHIVE_RECORD_COUNT_SQL,
            (archive_pin.ARCHIVE_ENTITY_TYPE,),
        ).fetchone()[0]
        expected = sum(
            1 for _, ents in rows if archive_pin.is_archive_record(ents)
        )
        assert expected == 1, "fixture drift: exactly one row should carry the marker"
        assert got == expected, (
            f"SQL predicate counted {got}, Python predicate {expected}"
        )
        con.close()

    def test_the_naive_sql_spellings_raise_on_a_text_member(self, tmp_path):
        """PIN AGAINST 'SIMPLIFICATION'. Both obvious forms fail on real data.

        `json_extract` on a bare string raises `malformed JSON` and kills the
        whole query. The natural defence — `json_type(j.value) = 'object'` —
        raises IDENTICALLY, because `json_type` re-parses the value, so the
        guard throws before the AND can short-circuit. Only `j.type`, the
        column `json_each` already computed, filters without re-parsing.

        Without this test, someone tidies the WHERE clause, every fixture here
        happens to hold only objects, and the query dies on the first real
        store it meets.
        """
        db = tmp_path / "naive.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, entities TEXT)")
        con.execute(
            "INSERT INTO memories VALUES (?, ?)",
            ("has_text_member", json.dumps(["a bare string"])),
        )
        con.commit()

        for label, where in (
            ("json_extract alone", "json_extract(j.value, '$.type') = ?"),
            ("json_type guard", "json_type(j.value) = 'object' "
                                "AND json_extract(j.value, '$.type') = ?"),
        ):
            sql = ("SELECT COUNT(DISTINCT m.id) FROM memories m, "
                   f"json_each(m.entities) j WHERE {where}")
            with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
                con.execute(sql, (archive_pin.ARCHIVE_ENTITY_TYPE,)).fetchone()

        # The shipped spelling survives the same row.
        assert con.execute(
            archive_pin.ARCHIVE_RECORD_COUNT_SQL,
            (archive_pin.ARCHIVE_ENTITY_TYPE,),
        ).fetchone()[0] == 0
        con.close()


class TestLiveDbGuard:
    """The MECHANICAL closure for the live-database leak.

    A required `db_path` makes the choice visible; it does not make it safe.
    `None` still satisfies the signature and still means the real store, and
    `""` is falsy so it takes the same branch as an omission. This class pins
    the two guards that turn those into failures.

    The guards sit on OPPOSITE SIDES of the process boundary and that split is
    the design, not an accident:

      * PARENT (`_run_memory_cli`) rejects falsy-BUT-PRESENT db_path. It sees
        the caller's intent, so it can tell `""` from `None`.
      * CHILD (`cli.py`) refuses the live store outright when no
        --db-path arrived. It sees the actual spawn, so it catches routes that
        never touch `archive_pin` at all -- including a raw
        `subprocess.run([sys.executable, cli.py, ...])`.

    Neither covers the other's cases. The parent guard cannot see a spawn that
    bypasses it; the child guard cannot see intent that never crossed.

    A SPAWN THAT RESOLVES THE STORE FROM `HOME` SANDBOXES IT. That is not
    decoration. `config.py` resolves the database path at USE time, and a child
    is a fresh interpreter, so it reads `HOME` and lands in the sandbox.

    TWO KINDS OF SPAWN HERE NEED NO SANDBOX, AND THIS IS MEASURED RATHER THAN
    ASSUMED. A child given `--db-path` opens the store that path names and
    resolves nothing from `HOME`, which is how the `memory_store` fixture
    works. A child pointed at a PROBE cannot open the store at all, which is
    how the tripwire arm works. AN INSTRUCTION DEMANDING A SANDBOX FROM EACH
    SPAWN WOULD CONDEMN THE TWO OF THEM, so the rule is narrower than that.

    `_refuse_a_store_reaching_spawn` below holds the rule as a mechanism rather
    than as a request: a child that can reach the pact-memory CLI with no
    `--db-path` must carry a `HOME` that does not resolve the operator's home.

    IN THIS PROCESS THE LEVER IS DIFFERENT AND `HOME` IS INERT. The autouse
    fixture `_isolate_config_root_to_tmp` patches `Path.home` and states that it
    deliberately does NOT set `HOME`. So an in-process `HOME` change moves no
    store, and the in-process protection comes from that fixture rather than
    from anything this class does. Do not read the child sandbox as in-process
    protection, and do not build an arm on an in-process `HOME` change: it
    measures nothing. `test_the_store_path_resolves_at_use_time` below pins the
    use-time property that the child leg rests on.

    These tests exercise the exact production shape (no --db-path)
    with zero risk to the live store, which is only possible because the
    boundary that makes the defect hard to guard is the same boundary that
    makes it safe to test.
    """

    @staticmethod
    def _spawn_cli(tmp_path, *, with_pytest_var, extra_argv=()):
        """Run the memory CLI as the curator's production shape: no --db-path.

        HOME points into tmp_path, so the child resolves a temp database even
        on the branch that would otherwise select the live store.
        """
        cli = (
            Path(archive_pin.__file__).resolve().parent.parent
            / "skills" / "pact-memory" / "scripts" / "cli.py"
        )
        assert cli.exists(), f"CLI not found at {cli} — this test measures nothing"
        home = tmp_path / "sandbox-home"
        home.mkdir(exist_ok=True)
        env = dict(os.environ)
        env["HOME"] = str(home)
        if with_pytest_var:
            env["PYTEST_CURRENT_TEST"] = "sentinel::test (call)"
        else:
            env.pop("PYTEST_CURRENT_TEST", None)
        return subprocess.run(
            [sys.executable, str(cli), "get", "f" * 32, *extra_argv],
            capture_output=True, text=True, timeout=120, env=env,
        )

    @staticmethod
    def _operator_home() -> Path:
        """The home the OPERATOR resolves, read from the password database.

        NOT `Path.home()`, which the autouse `_isolate_config_root_to_tmp`
        fixture patches, and NOT `HOME`, which that fixture deliberately leaves
        alone. The password database is the one source that neither lever
        moves, so it is what says whether a child reaches the real store.
        """
        import pwd  # POSIX. This suite does not run where it is absent.

        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()

    @pytest.fixture(autouse=True)
    def _refuse_a_store_reaching_spawn(self, monkeypatch):
        """Make a spawn that can reach the live store FAIL rather than ask.

        The class docstring tells the next author to sandbox `HOME`. AN
        INSTRUCTION THAT REQUESTS COMPLIANCE IS NOT A MECHANISM. This observes
        each spawn that runs and refuses the one that can reach the store.

        WHY THE PREDICATE IS THE SAFETY PROPERTY RATHER THAN `tmp_path`. A
        sandbox below `tmp_path` is the CONVENTION here. The property that
        keeps the store safe is different: the child must not resolve the
        OPERATOR'S home. A spawn that points `HOME` at another temporary root
        is safe, and a `tmp_path`-shaped assertion reddens it, which taxes an
        author who did the safe thing in an unexpected place.

        WHY IT IS SCOPED TO STORE-REACHING SPAWNS. Not each child here is a
        hazard. `test_tripwire_the_child_actually_receives_pytest_current_test`
        spawns a PROBE written into `tmp_path` and passes the inherited
        environment, which carries the operator's `HOME`. That is safe, because
        the probe cannot open the store. An arm that demanded a sandbox from
        EACH spawn reddens that test, which is an over-block on correct work.

        WHY AN EXPLICIT `--db-path` EXEMPTS A SPAWN, AND THE ARM MEASURED THIS
        RATHER THAN ASSUMED IT. A child given `--db-path` opens the store it
        names and resolves nothing from `HOME`, so its `HOME` cannot reach the
        operator. The `memory_store` fixture spawns `setup --db-path` with the
        inherited environment, and an arm without this exemption reddens it.
        THE HAZARD IS THE PRODUCTION SHAPE WITH NO `--db-path`, which is the
        shape this class exercises on purpose.

        ITS BOUND, STATED: this sees the spawns that RUN. A skipped test
        escapes it, and so does a spelling it does not wrap.
        `test_no_spawn_spelling_escapes_the_guard` closes the second half.
        """
        real_run = subprocess.run
        memory_package = (
            Path(archive_pin.__file__).resolve().parent.parent
            / "skills" / "pact-memory"
        )
        operator_home = self._operator_home()

        def guarded(argv, *args, **kwargs):
            words = [
                str(word)
                for word in (argv if isinstance(argv, (list, tuple)) else [argv])
            ]
            reaches_store = any(str(memory_package) in word for word in words)
            resolves_from_home = "--db-path" not in words
            if reaches_store and resolves_from_home:
                env = kwargs.get("env")
                assert env is not None, (
                    "this spawn can reach the pact-memory CLI and passes no "
                    "env, so the child inherits HOME and resolves the store "
                    f"below {operator_home}. Pass an env with a sandboxed HOME."
                )
                home = env.get("HOME")
                assert home, (
                    "this spawn can reach the pact-memory CLI and its env "
                    "carries no HOME, so the child falls back to the password "
                    f"database and resolves the store below {operator_home}."
                )
                assert Path(home).resolve() != operator_home, (
                    f"this spawn sets HOME to {home}, which resolves the "
                    f"operator's own home at {operator_home}, so the child "
                    "opens the LIVE memory store. Point HOME into a sandbox. "
                    "Below tmp_path is the shape used here, and any directory "
                    "other than the operator's home satisfies this arm."
                )
            return real_run(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", guarded)

    def test_no_spawn_spelling_escapes_the_guard(self):
        """The fixture above wraps `subprocess.run` and nothing else.

        So a spawn reached by another spelling leaves its sight. This REFUSES
        those spellings in this class rather than widening the wrapper, because
        a refusal has a fixed alphabet where a wrapper list grows forever.
        """
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        cls = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == type(self).__name__
        )

        def dotted(node):
            parts = []
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
                return ".".join(reversed(parts))
            return None

        wrapped = "subprocess.run"
        unwrapped = {
            "subprocess.Popen", "subprocess.check_output", "subprocess.call",
            "subprocess.check_call", "os.system", "os.popen", "os.execv",
            "os.execve", "os.spawnv", "os.posix_spawn",
        }
        seen = [
            dotted(node.func) for node in ast.walk(cls) if isinstance(node, ast.Call)
        ]
        escaping = sorted({name for name in seen if name in unwrapped})
        assert not escaping, (
            f"{escaping} spawn a child in this class and the guard fixture "
            f"wraps {wrapped!r} alone, so those calls reach a child unobserved. "
            f"Route them through `_spawn_cli`, or wrap them in the fixture too."
        )

        # THE FLOOR. Without it a renamed class or a broken walk makes the
        # assertion above pass over an empty set, which is the shape that ships
        # a guard reporting green because it read nothing.
        assert seen.count(wrapped) >= 1, (
            f"the walk over {type(self).__name__} found no {wrapped!r} call, so "
            f"the refusal above passed over an empty set and proves nothing."
        )

    def test_the_store_path_resolves_at_use_time(self, tmp_path, monkeypatch):
        """The class docstring above states a BEHAVIOUR. This asserts it.

        WHY AN ARM RATHER THAN A TEXT PIN ON THAT SENTENCE. A text pin goes red
        when the WORDS change. This goes red when the BEHAVIOUR changes, and the
        behaviour is the condition that makes the sentence incorrect, so it is
        what the guard must watch. A pin on the prose is green on the day the
        resolver changes, which is the one day it is needed.

        THE CLAIM THIS DISCHARGES: `config.py` resolves the store path at USE
        time. Under the refuted claim it bound the path once at import, and the
        second resolution below would then repeat the first.

        WHY THE LEVER IS `Path.home` AND NOT THE `HOME` VARIABLE. The autouse
        fixture `_isolate_config_root_to_tmp` patches `Path.home` and states
        that it deliberately does NOT set `HOME`, so an in-process `HOME` change
        moves nothing here and an arm built on one measures nothing. This drives
        the lever the harness leaves live.

        `resolve_db_path` CREATES NO DIRECTORY, so this arm reads a location and
        leaves no tree behind, in the sandbox or in the live store.
        """
        from pathlib import Path as _Path

        from scripts.config import (  # the pytest harness is the stated carve-out
            MEMORY_DIR_ENV,
            STORE_ORIGIN_HOME,
            resolve_db_path,
            store_path_origin,
        )

        # THE VARIABLE OUTRANKS THE HOME LEG AND THE SUITE SETS IT FOR EACH TEST.
        # Without this the two resolutions below agree for a reason that has
        # nothing to do with use-time resolution, and the arm proves nothing.
        monkeypatch.delenv(MEMORY_DIR_ENV, raising=False)
        assert store_path_origin() == STORE_ORIGIN_HOME, (
            "the resolver is not on its home leg, so this arm would measure an "
            "override rather than the behaviour the class docstring claims"
        )

        first = tmp_path / "first-home"
        monkeypatch.setattr(_Path, "home", lambda: first)
        from_first = resolve_db_path()

        second = tmp_path / "second-home"
        monkeypatch.setattr(_Path, "home", lambda: second)
        from_second = resolve_db_path()

        assert from_first != from_second, (
            "the resolved store did not follow the second change, so "
            "`config.py` bound the path once instead of resolving it at use "
            "time. The class docstring above is then incorrect, and the child "
            "sandbox rests on a property that no longer holds."
        )
        assert first in from_first.parents, from_first
        assert second in from_second.parents, from_second

    def test_child_refuses_the_live_db_when_spawned_under_pytest(
        self, tmp_path
    ):
        proc = self._spawn_cli(tmp_path, with_pytest_var=True)
        assert proc.returncode != 0, (
            "the child exited 0 with no --db-path under pytest — it would "
            "have used the live database"
        )
        assert "UNSCOPED_TEST_DB" in proc.stderr, (
            f"guard did not fire; stderr={proc.stderr[:400]}"
        )

    def test_refusal_states_the_observation_and_not_an_inference(self, tmp_path):
        """The guard sees a VARIABLE; it does not see a pytest run.

        Those come apart — an exported or inherited PYTEST_CURRENT_TEST reaches
        a plain shell with no test anywhere — and that is exactly the case a
        curator hits. Asserting the inference makes the message false precisely
        when someone is relying on it to understand what happened.
        """
        proc = self._spawn_cli(tmp_path, with_pytest_var=True)
        stderr = proc.stderr
        assert "PYTEST_CURRENT_TEST" in stderr, (
            "the refusal does not name the variable it keyed on, so the "
            f"reader cannot check or clear it: {stderr[:400]}"
        )
        assert "spawned from a pytest run" not in stderr, (
            "the refusal asserts it was spawned from a pytest run — an "
            "inference the guard cannot make and which is false when the "
            f"variable was exported or inherited: {stderr[:400]}"
        )

    def test_refusal_does_not_claim_the_write_would_reach_the_real_store(
        self, tmp_path
    ):
        """The guard never resolves the default, so it cannot name the store.

        SIBLING TO THE TEST ABOVE, AND THE SAME DEFECT ONE INFERENCE OVER. That
        one pins the absence of "spawned from a pytest run". This pins the
        absence of the OTHER inference the message used to draw -- that a write
        would land in the real store -- which is false whenever the default has
        already been redirected.

        IT IS FALSE HERE, WHICH IS WHY THIS TEST BELONGS IN THIS CLASS. conftest
        sets PACT_TEST_MEMORY_DIR for the whole suite, and the child inherits the
        environment, so the write this refusal prevents would have reached a tmp
        store and not the operator's. The message asserted the real store in the
        one place it was reliably wrong.

        The uncovered inference outlived the covered one because the test named
        for this property pinned only the spelling that had already been caught.
        """
        proc = self._spawn_cli(tmp_path, with_pytest_var=True)
        stderr = proc.stderr
        # NON-VACUITY ARM, and it is not optional. The assertion below is an
        # ABSENCE check, which an empty stderr satisfies for free -- a failed
        # spawn would pass it while measuring nothing. This proves the refusal
        # text is actually present before anything asserts on what it omits.
        assert "UNSCOPED_TEST_DB" in stderr, (
            "the guard did not fire, so the absence assertion below would "
            f"measure an empty stderr rather than the refusal: {stderr[:400]}"
        )
        assert "would land in the real store" not in stderr, (
            "the refusal asserts the write would reach the real store -- an "
            "inference the guard cannot make, because it never resolves the "
            f"default location: {stderr[:400]}"
        )

    def test_refusal_does_not_advise_a_curator_to_scope_the_archive(
        self, tmp_path
    ):
        """THE REMEDY IS AUDIENCE-SPECIFIC AND THE ANSWERS ARE OPPOSITE.

        For a test, `--db-path` is right. For a curator archiving a pin it is
        destructive: the archive lands in a throwaway database, the verdict
        reports success, and the pin becomes eligible for deletion with its
        only copy in a file about to be discarded. A correct guard with the
        wrong remedy can destroy exactly what the guard protected.

        So the message must carry BOTH branches and must tell the curator NOT
        to pass --db-path. A message offering only the test remedy passes a
        bare "mentions --db-path" check, which is why this asserts the
        curator's branch specifically.
        """
        stderr = self._spawn_cli(tmp_path, with_pytest_var=True).stderr
        assert "do NOT pass --db-path" in stderr, (
            "the refusal does not warn a curator away from --db-path; "
            "followed literally its advice archives the pin into a throwaway "
            f"database and marks it deletion-eligible: {stderr[:400]}"
        )
        assert "unset PYTEST_CURRENT_TEST" in stderr, (
            f"the refusal never states the fix that preserves the pin: {stderr[:400]}"
        )

    def test_no_clause_sits_between_the_remedy_and_the_observed_value(
        self, tmp_path
    ):
        """The remedy is followed by the observed value and nothing else.

        WHY THIS IS STRUCTURAL RATHER THAN A BANNED PHRASE, and the reasoning
        is the point. The clause removed here claimed two things the guard
        never observed: that no test was in progress, and where the variable
        came from. A pin on either wording is a pin on a SPELLING -- reword it
        to say the same thing differently and the pin passes, which is the
        exact defect the sibling above exists to catch. So this keys on
        POSITION: the remedy sentence must be followed immediately by the
        observed value, with only whitespace between. Any explanatory clause
        inserted there fails, however it is worded.

        ITS BOUND, STATED BECAUSE IT IS NOT THE FULL PROPERTY. This closes the
        POSITION, not the CLASS. An unobservable claim placed elsewhere in the
        message still passes. Making the class unrepresentable takes assembling
        the message from the guard's observation set, so an unobserved claim
        has no source to come from; that is a change to the guard rather than
        to its message, and it is not what this test is.
        """
        proc = self._spawn_cli(tmp_path, with_pytest_var=True)
        stderr = proc.stderr
        # NON-VACUITY ARM. The assertion below is satisfied for free by an
        # empty stderr from a failed spawn, so prove the refusal is present
        # before asserting on the shape of its tail.
        assert "UNSCOPED_TEST_DB" in stderr, (
            "the guard did not fire, so the structural assertion below would "
            f"measure an empty stderr rather than the refusal: {stderr[:400]}"
        )
        assert re.search(
            r"unset PYTEST_CURRENT_TEST and run again\.\s*PYTEST_CURRENT_TEST=",
            stderr,
        ), (
            "something sits between the remedy and the observed value. That "
            "position held two claims the guard cannot make -- whether a test "
            "is in progress, and where the variable came from -- and it is "
            f"reserved so neither can return in any wording: {stderr[:400]}"
        )

    def test_the_curator_production_path_is_not_blocked(self, tmp_path):
        """OVER-BLOCK CONTROL, and the reason the gate exists.

        `archive_pin --index N` (commands/prune-memory.md) passes no
        --db-path, and /PACT:prune-memory keys its refuse-or-proceed decision
        on that command. An ungated guard breaks it. This asserts the guard is
        SILENT with the variable absent — the same spawn, one variable apart.
        """
        proc = self._spawn_cli(tmp_path, with_pytest_var=False)
        assert "UNSCOPED_TEST_DB" not in proc.stderr, (
            "the guard fired OUTSIDE pytest — this is the cardinal over-block: "
            f"`archive_pin --index N` would stop working. stderr={proc.stderr[:400]}"
        )

    def test_an_explicit_db_path_is_accepted_under_pytest(
        self, tmp_path, memory_store
    ):
        """The guard must block only the UNSCOPED case, not every spawn.

        THE STORE IS PRESENT SO THE SPAWN IS GENUINELY ACCEPTED. `main` refuses
        a `--db-path` naming a store that is absent. An absent path here would
        leave the arm green while the command was refused at the boundary,
        because the refusal carries a different error name and the assertion
        below only looks for `UNSCOPED_TEST_DB`. The exit-code assertion is what
        makes acceptance observable rather than merely un-refuted.
        """
        proc = self._spawn_cli(
            tmp_path, with_pytest_var=True,
            extra_argv=("--db-path", str(memory_store("scoped.db"))),
        )
        assert "UNSCOPED_TEST_DB" not in proc.stderr, (
            f"guard fired despite an explicit --db-path; stderr={proc.stderr[:400]}"
        )
        # NON-VACUITY: prove the command REACHED the store rather than merely
        # avoided one named error. `NOT_FOUND` is a store-level answer, so it
        # can only come from a lookup that ran. The exit code is 1 on that
        # answer, which is correct and is why this arm reads the error name.
        assert "DB_PATH_NOT_FOUND" not in proc.stderr, (
            f"an explicit --db-path at a store that is present was refused at "
            f"the boundary; stderr={proc.stderr[:400]}"
        )
        assert json.loads(proc.stderr)["error"] == "NOT_FOUND", (
            f"the spawn did not reach a store lookup, so this arm did not show "
            f"that an explicit --db-path is accepted; stderr={proc.stderr[:400]}"
        )

    @pytest.mark.parametrize("pass_cwd", [True, False])
    def test_tripwire_the_child_actually_receives_pytest_current_test(
        self, tmp_path, monkeypatch, pass_cwd
    ):
        """NON-OPTIONAL TRIPWIRE. The guard's fail direction is ALLOW.

        The child-side guard works only because `_run_memory_cli` hands the
        child a FULL copy of this process's environment. Hardening that to a
        minimal allowlist is plausible, otherwise desirable, and would disable
        the guard SILENTLY — every other test in this file would still pass,
        because they assert on the guard's behaviour given the variable rather
        than on the variable arriving.

        So this asserts the delivery itself, on BOTH branches of the env
        construction: `cwd` passed (env is a dict copy) and `cwd` omitted
        (env stays None, so the child inherits verbatim).

        It also records WHY the signal must be inherited rather than detected:
        `"pytest" in sys.modules` is False in the child, measured here rather
        than asserted in a comment. A future reader proposing an in-process
        check can see it is not available.
        """
        probe = tmp_path / "probe.py"
        probe.write_text(
            "import os, sys, json\n"
            "print(json.dumps({\n"
            "    'seen': os.environ.get('PYTEST_CURRENT_TEST'),\n"
            "    'pytest_importable_here': 'pytest' in sys.modules,\n"
            "}))\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(archive_pin, "_MEMORY_CLI", probe)
        rc, stdout, stderr = archive_pin._run_memory_cli(
            [], db_path=None, cwd=(tmp_path if pass_cwd else None)
        )
        assert rc == 0, f"probe child failed: {stderr[:300]}"
        report = json.loads(stdout)

        assert report["seen"], (
            "PYTEST_CURRENT_TEST did NOT reach the child. The child-side "
            "live-DB guard in cli.py is therefore INERT, and its fail "
            "direction is ALLOW — unscoped spawns will silently use the real "
            "database. Most likely cause: _run_memory_cli stopped passing a "
            "full env copy."
        )
        # THIS BLOCK PINS NOTHING IN `cli.py`. It probes the CHILD environment
        # and compares nothing against that module, so the docstring named below
        # can be rewritten with this suite green. A citation inside an assertion
        # MESSAGE renders only when the assertion fails. A reader who searches
        # for what guards a `cli.py` docstring must not stop at a hit like this
        # one: right file, incorrect mechanism.
        assert report["pytest_importable_here"] is False, (
            "pytest IS visible in the child, so the guard could have used an "
            "in-process check. Re-read the forced-choice reasoning recorded "
            "with the child-side live-store refusal."
        )

    def test_parent_rejects_falsy_but_present_db_path(self, tmp_path):
        """`db_path=""` is falsy, so without this it routes to the live store.

        The real `_MEMORY_CLI` is left in place. Repointing it at a
        non-existent file to prevent a spawn does not work here and is worth
        recording: the existence check runs BEFORE this guard, so the call
        raises `_Unevaluable` for the WRONG REASON and a test asserting only
        on the exception TYPE would pass while measuring the missing-CLI path.
        No spawn happens regardless, because the guard raises before argv is
        built. Assert on the reason, not just the type.
        """
        with pytest.raises(archive_pin._Unevaluable) as excinfo:
            archive_pin._run_memory_cli(["get", "x" * 32], db_path="", cwd=tmp_path)
        assert "empty value" in str(excinfo.value.reason), (
            f"raised for the wrong reason: {excinfo.value.reason!r}"
        )

    def test_parent_rejects_falsy_db_path_with_the_marker_REMOVED(
        self, tmp_path, monkeypatch
    ):
        """The guard holds when the environment marker is GONE, not present.

        THIS ARM DELETES THE VARIABLE THE GUARD KEYS ON. Its sibling above
        relies on the marker pytest sets for it, so that sibling can only ever
        see the guard succeed for the reason it was going to succeed anyway.
        An arm that supplies the precondition it is testing cannot observe the
        blindness, and that is the shape this arm exists to avoid.

        WHAT IT MEASURES: the in-process half of the predicate. `pytest` is in
        `sys.modules` here and no environment edit can remove it, so the guard
        holds for a caller who cleared the environment to isolate a probe.

        THE STUB IS A SAFETY BOUND, NOT A CONVENIENCE, and it is the reason
        this arm is safe to run at all. With the in-process half removed the
        guard does not fire, the call builds argv and spawns a child with NO
        `--db-path`. That child is a fresh interpreter, it resolves the store
        from `HOME`, and the autouse config-root fixture patches `Path.home`
        while deliberately leaving `HOME` alone. So the child would reach the
        OPERATOR'S LIVE DATABASE. The stub makes the failure land as a missing
        exception rather than as a live-store spawn.
        """
        spawned = []
        monkeypatch.setattr(
            archive_pin.subprocess,
            "run",
            lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(
                AssertionError("a spawn was attempted; the guard did not fire")
            ),
        )
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        assert not os.environ.get("PYTEST_CURRENT_TEST"), (
            "the marker is present at call time, so this arm would pass "
            "through the environment half and measure nothing new"
        )

        with pytest.raises(archive_pin._Unevaluable) as excinfo:
            archive_pin._run_memory_cli(["get", "x" * 32], db_path="", cwd=tmp_path)
        assert "empty value" in str(excinfo.value.reason), (
            f"raised for the wrong reason: {excinfo.value.reason!r}"
        )
        assert not spawned, "the guard raised but a spawn was attempted first"

    def test_parent_allows_none_so_non_spawning_paths_keep_working(
        self, tmp_path, monkeypatch
    ):
        """None is the not-scoping sentinel, and must NOT be rejected here.

        Six tests reach `_run_memory_cli` with `db_path=None` while stubbing
        `subprocess.run` or `_MEMORY_CLI`; none can touch a store. Rejecting
        plain falsiness rather than falsy-but-present would redden all six for
        a hazard they do not have — and the cheap-looking repair would be to
        weaken the guard.
        """
        calls = []
        monkeypatch.setattr(
            archive_pin.subprocess, "run",
            lambda argv, **kw: calls.append(argv) or _CompletedStub(),
        )
        archive_pin._run_memory_cli(["get", "x" * 32], db_path=None, cwd=tmp_path)
        assert calls, "the spawn never happened — this test proves nothing"
        assert "--db-path" not in calls[0], (
            "a None db_path must not synthesize a --db-path argument"
        )


class TestExplicitTargetBoundary:
    """The no-`cwd` population — the one that reached OUTSIDE the sandbox.

    `cwd` is an ordinary optional argument. When it was omitted the env block
    never ran, `env` stayed None, and `subprocess.run(env=None)` handed the
    child the parent environment VERBATIM — so the child resolved a CLAUDE.md
    from whatever ambient variable, git anchor or working directory was in
    scope. Measured before the fix: variable unset and `cwd` omitted, the
    child wrote to the invoking repository's REAL CLAUDE.md. Three
    configurations, two destinations, all of them outside the intended target.

    The contract now: no target means the ambient value is REMOVED rather than
    inherited, and the projection that would consume it is SUPPRESSED. Not a
    guess, not a fallback — a skip.

    THE TWO EXISTING LEGS MUST NOT MOVE. Both `archive_pin`'s save and get
    calls already pass `cwd`, so they are in the closed population and this
    change must leave them byte-identical. Their argv is pinned below: a shift
    there is a REGRESSION, not an improvement, and would otherwise read as one.
    """

    @staticmethod
    def _capture(monkeypatch):
        real_run = subprocess.run
        calls = []

        class _Proc:
            returncode = 0
            stderr = ""
            def __init__(self, stdout=""):
                self.stdout = stdout

        def _fake(argv, **kwargs):
            if not (argv and argv[0] == sys.executable):
                return real_run(argv, **kwargs)
            calls.append((list(argv), kwargs))
            return _Proc('{"ok": true, "result": {}}')

        monkeypatch.setattr(archive_pin.subprocess, "run", _fake)
        return calls

    def test_no_cwd_strips_the_ambient_project_and_suppresses_the_sync(
        self, tmp_path, monkeypatch
    ):
        decoy = tmp_path / "ambient"
        decoy.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(decoy))
        assert os.environ.get("CLAUDE_PROJECT_DIR") == str(decoy), (
            "precondition: no ambient value is set, so this test cannot show "
            "that one is removed"
        )

        calls = self._capture(monkeypatch)
        archive_pin._run_memory_cli(
            ["save", "--stdin"], db_path=str(tmp_path / "m.db"),
            stdin_data="{}", cwd=None,
        )

        assert len(calls) == 1, f"expected one spawn, captured {len(calls)}"
        argv, kwargs = calls[0]
        assert kwargs["env"] is not None, (
            "env is None, so the child inherits the parent environment "
            "verbatim — the exact hole this closes"
        )
        assert "CLAUDE_PROJECT_DIR" not in kwargs["env"], (
            "the ambient project survived into a call that named no project; "
            "an absent target must not fall back to an ambient one"
        )
        assert "--no-sync" in argv, (
            "the projection was not suppressed on a call with no target — it "
            "would resolve a destination ambiently and write there"
        )
        assert kwargs["cwd"] is None

    def test_no_sync_is_not_added_to_subcommands_that_reject_it(
        self, tmp_path, monkeypatch
    ):
        """OVER-BLOCK CONTROL. `--no-sync` is declared on `save` only, so
        appending it to a `get` is an argparse error — a failure the fix would
        have manufactured."""
        calls = self._capture(monkeypatch)
        archive_pin._run_memory_cli(
            ["get", "a" * 32], db_path=str(tmp_path / "m.db"), cwd=None
        )
        argv = calls[0][0]
        assert "--no-sync" not in argv, (
            f"--no-sync was appended to a subcommand that does not accept it: {argv[2:]}"
        )

    def test_sync_capable_set_matches_the_cli_parser(self):
        """DRIFT DETECTOR, mechanical rather than conventional.

        The suppression fires only for subcommands in
        `_SYNC_CAPABLE_SUBCOMMANDS`. If a future subcommand gains a sync and a
        `--no-sync` flag but is not added there, calls with no target silently
        stop being suppressed — the leak returns with nothing failing.

        So the constant is checked against the CLI's REAL parser rather than
        against a second hand-written list, which would only ever contain what
        someone already remembered.
        """
        cli_path = (
            Path(archive_pin.__file__).resolve().parent.parent
            / "skills" / "pact-memory" / "scripts" / "cli.py"
        )
        source = cli_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Which `<name>_parser.add_argument("--no-sync", ...)` calls exist.
        declaring = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                continue
            if not (node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "--no-sync"):
                continue
            recv = node.func.value
            assert isinstance(recv, ast.Name), (
                f"unrecognised --no-sync receiver at line {node.lineno}; the "
                "detector must be updated rather than the assertion relaxed"
            )
            declaring.add(recv.id.removesuffix("_parser"))

        assert declaring, (
            "no --no-sync declaration found in cli.py at all — the detector "
            "found nothing to compare and would pass vacuously"
        )
        assert declaring == set(archive_pin._SYNC_CAPABLE_SUBCOMMANDS), (
            f"cli.py declares --no-sync on {sorted(declaring)} but "
            f"_SYNC_CAPABLE_SUBCOMMANDS is "
            f"{sorted(archive_pin._SYNC_CAPABLE_SUBCOMMANDS)}. A subcommand "
            f"that syncs but is missing from the constant loses the "
            f"suppression on no-target calls."
        )

    def test_both_existing_legs_keep_their_argv_unchanged(
        self, claude_md, monkeypatch, tmp_path
    ):
        """Both archive_pin legs pass `cwd`, so neither may shift.

        The save leg carries `--no-sync` EXPLICITLY and must not gain a second
        one; the get leg carries none and must not gain one at all.
        """
        content = _two_pin_file()
        path = claude_md(content)
        calls = []
        real_run = archive_pin._run_memory_cli

        def _spy(args, **kwargs):
            calls.append(list(args))
            if args[0] == "save":
                return 0, json.dumps(
                    {"ok": True, "result": {"memory_id": "f" * 32}}
                ), ""
            return 0, json.dumps({"ok": True, "result": {"context": content}}), ""

        monkeypatch.setattr(archive_pin, "_run_memory_cli", _spy)
        verdict = archive_pin.build_verdict(0, db_path=str(tmp_path / "m.db"))
        assert verdict["outcome"] == "ARCHIVED", verdict
        assert len(calls) == 2, f"expected save+get, got {calls}"

        save_args, get_args = calls
        assert save_args == ["save", "--stdin", "--no-sync"], (
            f"the save leg's argv changed: {save_args}"
        )
        assert save_args.count("--no-sync") == 1, "double-injected --no-sync"
        assert get_args[0] == "get" and "--no-sync" not in get_args, (
            f"the get leg's argv changed: {get_args}"
        )
        assert path.exists()
        assert real_run is not None


class _CompletedStub:
    """Minimal stand-in for CompletedProcess on a path that must not spawn."""
    returncode = 0
    stdout = '{"ok": true, "result": {}}'
    stderr = ""


@pytest.mark.requires_embedding_backend
class TestArchivePin_RealCLI:
    """End-to-end against the REAL memory CLI and a temp database.

    Without at least one of these, the whole suite would be verifying its
    own stubs. These exercise the actual save/get envelope handling, the
    real embedding backend, and real byte round-tripping.

    DECLARED EXTERNAL DEPENDENCY (marker registered in conftest.py). The
    `save` path spins the embedding backend, which reads its model from a
    local cache. Every measurement behind these tests was taken against a
    WARM cache; cold-cache behaviour is UNMEASURED, and on a cold cache
    that read becomes a network download. In a container with no network
    it is not a slower test, it is a DIFFERENT FAILURE.

    The marker does not skip. These run by default, so the dependency
    cannot be satisfied by quietly not exercising the one path that keeps
    the suite from verifying its own stubs; a constrained environment
    deselects with `-m 'not requires_embedding_backend'`, which shows up
    in the pytest header rather than inside the skip count. The point is
    that the dependency is now NAMED: if it bites, it presents as a
    deselected marker or a legible failure, not as unexplained flakiness.
    """

    def test_archives_and_verifies_containment(self, claude_md, tmp_path, memory_store):
        claude_md(_two_pin_file())
        verdict = archive_pin.build_verdict(
            0, db_path=str(memory_store("mem.db"))
        )
        assert verdict["outcome"] == "ARCHIVED"
        assert verdict["heading"] == "First Pin"
        assert verdict["contained"] is True
        assert len(verdict["memory_id"]) == 32
        assert verdict["chars"] > 0

    def test_archived_record_carries_the_block_in_context_not_a_list_field(
        self, claude_md, tmp_path, memory_store
    ):
        """D3, verified at the destination rather than asserted in a comment.

        String-list fields `.strip()` and NFC-normalize their items, so a pin
        stored there comes back ALTERED — containment returns FALSE for a
        perfectly good archive, which is an OVER-BLOCK refusing a legitimate
        eviction. This is not hypothetical: the one pin-archive record
        already in the store claims verbatim preservation while holding its
        content in `lessons_learned`.
        """
        content = _two_pin_file()
        claude_md(content)
        db = str(memory_store("mem.db"))
        verdict = archive_pin.build_verdict(0, db_path=db)
        assert verdict["outcome"] == "ARCHIVED"

        fetched = _cli_get(verdict["memory_id"], db)
        pinned = _pinned_body(content)
        block = archive_pin.extract_pin_block(
            pinned, 0, pin_caps.parse_pins(pinned)
        )

        assert block in fetched["context"], "block must land in `context`"
        for list_field in ("lessons_learned", "reasoning_chains", "decisions"):
            assert not fetched.get(list_field), (
                f"{list_field} must stay empty — a list field cannot "
                f"preserve the block byte-exact"
            )

    def test_adversarial_body_round_trips_byte_exact(
        self, claude_md, tmp_path, memory_store
    ):
        """Apostrophes, backticks, tabs, CRLF and trailing whitespace all
        survive. These are the classes that break shell-quoted or
        normalizing storage paths."""
        content = (
            "<!-- PACT_MANAGED_START -->\n## Pinned Context\n\n"
            "<!-- pinned: 2026-01-01 -->\n"
            "### Adversarial\n"
            "has 'apostrophes' and `backticks`\n"
            "\ttab-indented, trailing spaces here   \n"
            "#### not a pin heading\n\n"
            "<!-- PACT_MANAGED_END -->\n"
        )
        claude_md(content)
        db = str(memory_store("mem.db"))
        verdict = archive_pin.build_verdict(0, db_path=db)
        assert verdict["outcome"] == "ARCHIVED"

        fetched = _cli_get(verdict["memory_id"], db)
        block = archive_pin.extract_pin_block(
            _pinned_body(content), 0,
            pin_caps.parse_pins(_pinned_body(content)),
        )
        assert block in fetched["context"]
        assert "'apostrophes'" in fetched["context"]
        assert "`backticks`" in fetched["context"]
        assert "\t" in fetched["context"]

    def test_save_stdout_is_a_clean_json_envelope(self, claude_md, tmp_path, memory_store):
        """The real CLI's stdout parses, on any interpreter.

        WHAT THIS COVERS. `save` succeeds and its stdout is a well-formed
        envelope. If the streams were ever merged AND stderr happened to carry
        anything, the parse below would break — so this catches the merged
        stream OPPORTUNISTICALLY, whenever there is something to merge.

        WHAT IT DELIBERATELY NO LONGER ASSERTS. It used to require non-empty
        stderr as its non-vacuity control, on the grounds that `save` writes an
        embedding progress bar. That bar is not a property of this code. It is
        a property of the interpreter's dependency set and the model cache,
        because `_run_memory_cli` spawns `sys.executable` — so the child
        inherits whatever packages the parent has.

        MEASURED, and the direction is the opposite of the obvious guess: with
        `sentence_transformers` and `torch` PRESENT the warm-cache path prints
        NOTHING and the old assertion FAILED; with them ABSENT the
        `huggingface_hub` fallback prints `Fetching 10 files` even from cache
        and it passed. Both runs returned 0 with a byte-identical stdout
        envelope — nothing the test guards differed. So "install the missing
        dependency to fix it" is backwards; installing more is what removes the
        signal.

        The guarantee therefore moved to `TestArchivePin_StreamSeparation`
        below, which manufactures its own stderr payload and so does not depend
        on any of that. Do NOT re-add a bare `assert stderr.strip()` here.
        """
        claude_md(_two_pin_file())
        pinned = _pinned_body(_two_pin_file())
        block = archive_pin.extract_pin_block(
            pinned, 0, pin_caps.parse_pins(pinned)
        )
        payload = json.dumps(archive_pin._build_record(block, "First Pin"))

        rc, stdout, stderr = archive_pin._run_memory_cli(
            ["save", "--stdin"],
            db_path=str(memory_store("mem.db")),
            stdin_data=payload,
            cwd=tmp_path,
        )
        assert rc == 0
        # A merged stream returns stderr as None, not as "". Assert it before
        # touching it: `stderr.strip()` on None raises AttributeError, and a
        # crash and a detection must not look the same in the report.
        assert stderr is not None, (
            "stderr came back None — the child's streams are merged, so there "
            "is no separate stderr to return"
        )
        envelope = json.loads(stdout)  # would raise if streams were merged
        assert envelope["ok"] is True
        # Not conditional on stderr being non-empty: when it IS empty this is
        # trivially true, and when it is not, it is the real check.
        assert not stderr.strip() or stderr.strip() not in stdout


_SEPARATION_CANARY = "STREAM-SEPARATION-CANARY-2f8c1d"

# A stand-in memory CLI that writes a KNOWN payload to each stream. The canary
# is deliberately not valid JSON and not a JSON fragment, so if it ever reaches
# stdout the envelope parse fails as well as the containment assertion — two
# independent detections of one regression.
_STUB_CLI = f'''import sys, json
sys.stderr.write({_SEPARATION_CANARY!r} + "\\n")
sys.stderr.flush()
sys.stdout.write(json.dumps({{"ok": True, "result": {{"id": "stub"}}}}))
'''


class TestArchivePin_StreamSeparation:
    """`_run_memory_cli` must never merge the child's streams.

    WHY THIS EXISTS SEPARATELY FROM THE REAL-CLI TESTS. The guard is
    `capture_output=True` in `_run_memory_cli` — separate pipes. The regression
    is someone replacing it with `stderr=subprocess.STDOUT`, which splices
    stderr into the JSON envelope and breaks every consumer.

    To detect that, a test needs stderr to actually CARRY something. The
    previous guard borrowed that from the real backend's embedding progress
    bar, which made its verdict depend on the interpreter's dependency set and
    the model cache rather than on this code — it failed on an interpreter with
    a MORE complete stack, and passed on a barer one, while the behaviour under
    test was byte-identical in both.

    So the payload is manufactured here instead. A stub CLI writes a canary to
    stderr and an envelope to stdout. The control is non-vacuous BY
    CONSTRUCTION — this test wrote the canary, so it cannot be absent — and it
    is identical on every interpreter, with no network, no model and no cache.
    """

    def _stub(self, tmp_path, monkeypatch):
        stub = tmp_path / "stub_cli.py"
        stub.write_text(_STUB_CLI, encoding="utf-8")
        monkeypatch.setattr(archive_pin, "_MEMORY_CLI", stub)
        return stub

    def test_stderr_payload_never_reaches_stdout(self, tmp_path, monkeypatch):
        """The guard: a canary on stderr must not appear on stdout."""
        self._stub(tmp_path, monkeypatch)

        rc, stdout, stderr = archive_pin._run_memory_cli(
            ["save"], db_path=str(tmp_path / "mem.db"), cwd=tmp_path
        )

        assert rc == 0
        # FIRST detection, and the one the mutation arm found: merging makes
        # `proc.stderr` None rather than empty, so every later assertion would
        # raise AttributeError/TypeError instead of failing. A crash and a
        # detection must not look alike in the report.
        assert stderr is not None, (
            "stderr came back None — the streams are merged. _run_memory_cli "
            "must keep separate pipes; there is no stderr to return once the "
            "child's stderr is redirected into stdout."
        )
        # Non-vacuity, and it cannot silently lapse: the canary is written by
        # the stub above, so an empty stderr here means the plumbing broke, not
        # that the environment changed.
        assert _SEPARATION_CANARY in stderr, (
            "the stub CLI's stderr payload did not arrive — _run_memory_cli is "
            "no longer capturing stderr, so this guard is measuring nothing"
        )
        assert _SEPARATION_CANARY not in stdout, (
            "stderr content reached stdout — the streams are merged. Restore "
            "separate pipes in _run_memory_cli; every consumer parses stdout "
            "as JSON and a merged stream corrupts it."
        )
        assert json.loads(stdout)["ok"] is True

    def test_a_merged_stream_would_be_caught(self, tmp_path, monkeypatch):
        """Non-vacuity twin: prove the predicate above CAN fail.

        Runs the same stub with the streams deliberately merged and asserts
        that both detections fire — the canary lands in stdout AND the envelope
        stops parsing. Without this, the test above is a predicate nobody has
        ever seen return False, and a guard that has never failed is a guard
        whose discrimination is unmeasured.
        """
        stub = self._stub(tmp_path, monkeypatch)

        merged = subprocess.run(
            [sys.executable, str(stub)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,      # the regression, made explicit
            text=True,
            timeout=30,
        )

        assert _SEPARATION_CANARY in merged.stdout
        with pytest.raises(json.JSONDecodeError):
            json.loads(merged.stdout)


def _cli_get(memory_id, db_path):
    """Fetch a record via the real CLI, streams kept separate."""
    cli = (
        Path(archive_pin.__file__).resolve().parent.parent
        / "skills" / "pact-memory" / "scripts" / "cli.py"
    )
    proc = subprocess.run(
        [sys.executable, str(cli), "get", memory_id, "--db-path", db_path],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"get failed: {proc.stderr[:400]}"
    return json.loads(proc.stdout)["result"]


class TestArchivePin_FailureMatrix:
    """Stubbed-CLI failure paths. Real stores cannot be made to fail on
    demand in these specific ways, so the seam is stubbed here — while
    TestArchivePin_RealCLI keeps the unstubbed path covered."""

    def test_save_returning_no_id_is_not_archived(self, claude_md, monkeypatch):
        """The store was reachable and said no. Definite failure, so
        NOT_ARCHIVED rather than UNEVALUABLE."""
        claude_md(_two_pin_file())
        monkeypatch.setattr(
            archive_pin, "_run_memory_cli",
            lambda *a, **k: (
                1, "", json.dumps({"ok": False, "error": "ValueError",
                                   "message": "bad field"})
            ),
        )
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "NOT_ARCHIVED"
        assert verdict["memory_id"] is None
        assert verdict["heading"] == "First Pin"
        assert "ValueError" in verdict["reason"]

    def test_error_envelope_key_is_error_not_error_type(
        self, claude_md, monkeypatch
    ):
        """cli.py's envelope key is `error`. A reader keyed on `error_type`
        would silently render every failure as an empty reason."""
        claude_md(_two_pin_file())
        monkeypatch.setattr(
            archive_pin, "_run_memory_cli",
            lambda *a, **k: (
                1, "", json.dumps({"ok": False, "error": "NOT_FOUND",
                                   "message": "Memory 'x' not found"})
            ),
        )
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert "NOT_FOUND" in verdict["reason"]

    def test_containment_failure_is_not_archived(self, claude_md, monkeypatch):
        """THE criterion. A record that exists but does not carry the pin is
        NOT archived — this is precisely the case the old memory_id-only
        check passed, and it is the case the founding data loss was."""
        claude_md(_two_pin_file())
        calls = []

        def _stub(args, **kwargs):
            calls.append(args[0])
            if args[0] == "save":
                return 0, json.dumps(
                    {"ok": True, "result": {"memory_id": "a" * 32}}
                ), ""
            return 0, json.dumps({
                "ok": True,
                "result": {"context": "a summary that lost the pin body"},
            }), ""

        monkeypatch.setattr(archive_pin, "_run_memory_cli", _stub)
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "NOT_ARCHIVED"
        assert verdict["memory_id"] == "a" * 32, (
            "the id must still be reported so the orphan record is findable"
        )
        assert "fidelity" in verdict["reason"]
        assert calls == ["save", "get"], "must re-fetch before judging"

    def test_containment_is_substring_not_equality(
        self, claude_md, monkeypatch
    ):
        """`context` may legitimately carry MORE than the block — a
        provenance preamble, for instance. An equality assertion would
        return false for a correct archive: an over-block in the one control
        whose entire purpose is not destroying content."""
        content = _two_pin_file()
        claude_md(content)
        pinned = _pinned_body(content)
        block = archive_pin.extract_pin_block(
            pinned, 0, pin_caps.parse_pins(pinned)
        )

        def _stub(args, **kwargs):
            if args[0] == "save":
                return 0, json.dumps(
                    {"ok": True, "result": {"memory_id": "b" * 32}}
                ), ""
            return 0, json.dumps({
                "ok": True,
                "result": {
                    "context": (
                        "Archive of CLAUDE.md pin, demoted 2026-07-25 to "
                        "free a slot.\n\n" + block + "\n\ntrailing note"
                    )
                },
            }), ""

        monkeypatch.setattr(archive_pin, "_run_memory_cli", _stub)
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "ARCHIVED", (
            "a record wrapping the block in provenance is a GOOD archive"
        )

    def test_refetch_failure_is_unevaluable_not_not_archived(
        self, claude_md, monkeypatch
    ):
        """The id came back but the record will not re-read. We cannot tell
        whether the bytes are safe — so we must claim neither. Collapsing
        this into NOT_ARCHIVED would be defensible; collapsing it into
        ARCHIVED would destroy content."""
        claude_md(_two_pin_file())

        def _stub(args, **kwargs):
            if args[0] == "save":
                return 0, json.dumps(
                    {"ok": True, "result": {"memory_id": "c" * 32}}
                ), ""
            return 1, "", json.dumps(
                {"ok": False, "error": "NOT_FOUND", "message": "gone"}
            )

        monkeypatch.setattr(archive_pin, "_run_memory_cli", _stub)
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert "c" * 32 in verdict["reason"]
        assert verdict["heading"] == "First Pin"

    @pytest.mark.parametrize("stdout", ["", "   ", "not json", "[]",
                                        '{"ok": false}'])
    def test_unparseable_save_stdout_never_reads_as_success(
        self, claude_md, monkeypatch, stdout
    ):
        """NOT_FOUND, PREFIX_TOO_SHORT and an outright crash all present as
        empty or unusable stdout. None may be mistaken for a save."""
        claude_md(_two_pin_file())
        monkeypatch.setattr(
            archive_pin, "_run_memory_cli", lambda *a, **k: (0, stdout, "")
        )
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] != "ARCHIVED"


class TestArchivePin_Unevaluable:
    """Every path where the pin's state cannot be established."""

    def test_missing_claude_md(self, monkeypatch):
        monkeypatch.setattr(
            archive_pin, "get_project_claude_md_path", lambda: None
        )
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert verdict["heading"] is None

    def test_no_pinned_section(self, claude_md):
        claude_md("# Project\n\n## Working Memory\n\n")
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"

    def test_index_beyond_pin_count(self, claude_md):
        claude_md(_two_pin_file())
        verdict = archive_pin.build_verdict(99, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert "out of range" in verdict["reason"]

    def test_missing_memory_cli(self, claude_md, monkeypatch):
        claude_md(_two_pin_file())
        monkeypatch.setattr(archive_pin, "_MEMORY_CLI", Path("/nonexistent/cli.py"))
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert "not found" in verdict["reason"]

    def test_cli_timeout(self, claude_md, monkeypatch):
        """A hang must refuse (pin survives), never proceed."""
        claude_md(_two_pin_file())

        def _boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="save", timeout=1)

        monkeypatch.setattr(subprocess, "run", _boom)
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert "timed out" in verdict["reason"]

    def test_unreadable_claude_md(self, claude_md, monkeypatch):
        claude_md(_two_pin_file())

        def _raise(*a, **k):
            raise IOError("simulated")

        monkeypatch.setattr(Path, "read_text", _raise)
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"

    def test_lossy_extractor_is_caught_before_any_write(
        self, claude_md, monkeypatch
    ):
        """The source-side tripwire.

        The slice makes 'block is verbatim in CLAUDE.md' true BY
        CONSTRUCTION, so this check costs nothing in normal operation. It
        exists to fail loudly if the extractor ever regresses into a lossy
        rebuild — and it fires BEFORE the save, so a broken extractor writes
        nothing rather than persisting a variant and certifying it.
        """
        claude_md(_two_pin_file())
        monkeypatch.setattr(
            archive_pin, "extract_pin_block",
            lambda *a, **k: "### Not from the source at all\nfabricated",
        )
        called = []
        monkeypatch.setattr(
            archive_pin, "_run_memory_cli",
            lambda *a, **k: called.append(a) or (0, "", ""),
        )
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert "not verbatim" in verdict["reason"]
        assert called == [], "must not save when the block is not verbatim"


class TestArchivePin_CliContract:
    """main() surface: always exit 0, always a parseable verdict."""

    @pytest.mark.parametrize(
        "index, expected",
        [(0, "ARCHIVED"), (1, "ARCHIVED"), (99, "UNEVALUABLE"), (-1, "UNEVALUABLE")],
        ids=["0", "1", "99", "-1"],
    )
    def test_always_exits_zero(self, claude_md, capsys, index, expected,
                               memory_store):
        """SACROSANCT in-band degradation: the script reports, the command
        decides. A non-zero exit would turn a measurement into a decision.

        THE ROW SET SPANS SUCCESS AND FAILURE, AND EACH ROW PINS WHICH ONE IT
        IS. An acceptance set of three outcome names cannot say that, and it
        already failed to: rows 0 and 1 were the success rows, the boundary
        refusal moved them to NOT_ARCHIVED, and the set silently collapsed onto
        the failure branch while staying green. The exit contract then had
        nothing behind it on the side a curator relies on, which
        `commands/prune-memory.md` tells them to rely on.

        THE STORE MUST BE PRESENT FOR THE SUCCESS ROWS TO REACH A SAVE. The
        fixture supplies that. The per-row expected outcome is what HOLDS it
        there afterwards: without it the set can collapse a second time and
        nothing turns red.
        """
        claude_md(_two_pin_file())
        rc = archive_pin.main(
            ["--index", str(index), "--db-path", str(memory_store("m.db"))]
        )
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["outcome"] == expected, (
            f"row {index} reached {payload['outcome']!r} rather than "
            f"{expected!r}. If this is the success row, the call no longer "
            f"reaches a save and the exit contract is pinned on the failure "
            f"branch only."
        )

    def test_every_verdict_carries_outcome_and_heading_keys(
        self, claude_md, capsys, monkeypatch, tmp_path, memory_store
    ):
        """`heading` is present in ALL THREE verdicts so a consumer never
        has to distinguish an absent key from a null value."""
        claude_md(_two_pin_file())
        db = str(memory_store("m.db"))

        seen = {}
        # ARCHIVED (real CLI)
        archive_pin.main(["--index", "0", "--db-path", db])
        seen["ARCHIVED"] = json.loads(capsys.readouterr().out)
        # UNEVALUABLE
        archive_pin.main(["--index", "99", "--db-path", db])
        seen["UNEVALUABLE"] = json.loads(capsys.readouterr().out)
        # NOT_ARCHIVED
        monkeypatch.setattr(
            archive_pin, "_run_memory_cli", lambda *a, **k: (1, "", "boom")
        )
        archive_pin.main(["--index", "0", "--db-path", db])
        seen["NOT_ARCHIVED"] = json.loads(capsys.readouterr().out)

        assert set(seen) == {"ARCHIVED", "UNEVALUABLE", "NOT_ARCHIVED"}, (
            "all three verdicts must be reachable or this test is partial"
        )
        for outcome, payload in seen.items():
            assert payload["outcome"] == outcome
            assert "heading" in payload, f"{outcome} dropped the heading key"

    def test_heading_is_the_real_heading_not_an_index_echo(
        self, claude_md, capsys, memory_store
    ):
        """The caller cross-checks this value against the curator's
        selection to catch an index shift between listing and archival —
        which would otherwise archive one pin and evict another while
        containment still passed, the right property measured on the wrong
        object. An index echo would make that check compare the index
        against itself and pass unconditionally.

        THE OUTCOME ASSERTION IS WHAT KEEPS THIS ARM ON THE ARCHIVED PATH.
        `heading` resolves BEFORE the save, so it stays correct when the save
        is refused. Without the outcome the arm measures heading resolution on
        a verdict that never archived, which is not the case the curator
        cross-checks.
        """
        claude_md(_two_pin_file())
        archive_pin.main(
            ["--index", "1", "--db-path", str(memory_store("m.db"))]
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["outcome"] == "ARCHIVED", (
            f"the call did not reach a save, so this arm no longer measures "
            f"the heading on the path the curator cross-checks: {payload}"
        )
        assert payload["heading"] == "Second Pin"
        assert "1" != payload["heading"]

    def test_internal_error_degrades_to_unevaluable(
        self, claude_md, capsys, monkeypatch
    ):
        """An unexpected exception must never crash the curator's flow."""
        claude_md(_two_pin_file())

        def _boom(*a, **k):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(archive_pin, "archive_pin", _boom)
        rc = archive_pin.main(["--index", "0"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["outcome"] == "UNEVALUABLE"
        assert "RuntimeError" in payload["reason"]


# THE ALPHABET IS SPELLED, NOT READ OFF `SyncResult`, matching the convention
# `test_sync_result_contract.py` states at length: a test whose input alphabet
# comes from the implementation cannot falsify the implementation's choice of
# alphabet. A rename fails loudly over there, in
# `test_constants_match_the_declared_alphabet`, rather than silently shrinking
# this sweep. Do not "tidy" these into imports.
_NON_SUPPRESSED_STATUSES = ("wrote", "refused", "failed", "unresolved", "missing")

# The two halves of the scope-presence rule, SPELLED for the same reason the
# alphabet above is: a set read off `archive_pin._WRITE_ATTEMPTED_STATUSES`
# would FOLLOW a mutation of that constant and pass over it. Spelled here, a
# mutation reddens. `test_the_write_attempted_constant_matches_the_spelled_set`
# below is the tie-back that turns a deliberate rename into a loud failure
# rather than a silent divergence.
_WRITE_ATTEMPTED_LITERAL = ("wrote", "failed")
_NO_WRITE_ATTEMPTED_STATUSES = tuple(
    s for s in _NON_SUPPRESSED_STATUSES if s not in _WRITE_ATTEMPTED_LITERAL
)


class TestArchivePin_SyncSuppressionBreach:
    """The archival path CONSUMES `sync_status`, which nothing read before.

    The save leg passes `--no-sync` because projecting the record would write
    the pin block back into the file the archive exists to remove it from. So
    `suppressed` is the only status this route asks for, and anything else
    means the suppression did not take effect.

    THESE ARE REGRESSION ARMS, NOT A LIVE-BUG REPRODUCTION. While `--no-sync`
    works the status is `suppressed` and none of the refusing arms can fire in
    production. They exist so that a change which breaks the flag fails HERE
    rather than reaching a curator as a clean archive.

    WHY EVERY FIXTURE BELOW LEAVES `occurrences == 1`. That is the whole
    discrimination. The file is clean and the block is unique, so the existing
    occurrence check returns "safe" -- and before this consumer these verdicts
    were `ARCHIVED`. Any arm that let the block duplicate would pass through
    the OLD check and prove nothing about the new one.
    """

    @staticmethod
    def _drive(claude_md, monkeypatch, sync_status, content=None):
        """Run one archival with a stubbed save envelope carrying `sync_status`.

        `sync_status=None` omits the key entirely -- the absent-field case.
        The `get` leg echoes the real block, so conjunct 3 (fidelity) PASSES
        and the verdict turns solely on the sync field.
        """
        content = content or _two_pin_file()
        claude_md(content)
        pinned = _pinned_body(content)
        block = archive_pin.extract_pin_block(
            pinned, 0, pin_caps.parse_pins(pinned)
        )

        def _stub(args, **kwargs):
            if args[0] == "save":
                result = {"memory_id": "a" * 32}
                if sync_status is not None:
                    result["sync_status"] = sync_status
                return 0, json.dumps({"ok": True, "result": result}), ""
            return 0, json.dumps(
                {"ok": True, "result": {"context": block}}
            ), ""

        monkeypatch.setattr(archive_pin, "_run_memory_cli", _stub)
        return archive_pin.build_verdict(0, db_path=None), block

    @pytest.mark.parametrize("status", _NON_SUPPRESSED_STATUSES)
    def test_a_non_suppressed_status_never_reads_as_a_clean_archive(
        self, claude_md, monkeypatch, status
    ):
        """EVERY reason but `suppressed`, because the predicate is `!=`.

        Keyed on the value this route REQUESTS rather than on a list of
        unwanted ones, so a seventh `SyncResult` reason is refused the day it
        is added instead of falling through an enumeration that never heard
        of it.
        """
        verdict, _ = self._drive(claude_md, monkeypatch, status)
        assert verdict["outcome"] == "ARCHIVED_DELETE_UNSAFE", (
            f"sync_status={status!r} reported as {verdict['outcome']} -- the "
            f"suppression this save requested did not take effect, so the "
            f"removal must not proceed automatically"
        )
        assert verdict["occurrences"] == 1, (
            "the fixture must leave the block UNIQUE, or this arm would pass "
            "through the pre-existing occurrence check and prove nothing"
        )
        assert verdict["memory_id"] == "a" * 32, (
            "the archive succeeded, so the id must still be reported"
        )

    def test_suppressed_archives_cleanly(self, claude_md, monkeypatch):
        """POSITIVE ARM -- and it is what makes the refusals above meaningful.

        Every arm above is a refusal, and a refusal is indistinguishable from
        a driver that never ran at all. This proves the fixture DOES reach a
        clean `ARCHIVED` when the status is the production-normal one, so the
        refusals are attributable to the status and not to a broken harness.

        It is also the over-block guard: `suppressed` is what the curator's
        own routine path reports on EVERY archive. Firing on it would refuse
        every legitimate eviction.
        """
        verdict, _ = self._drive(claude_md, monkeypatch, "suppressed")
        assert verdict["outcome"] == "ARCHIVED", (
            "`suppressed` is the PRODUCTION-NORMAL value on this path; "
            "refusing it is a cardinal over-block on the curator's own path"
        )
        assert "sync_status" not in verdict, (
            "a clean archive must not carry the breach-only diagnostic keys"
        )

    def test_an_absent_sync_status_does_not_fire(self, claude_md, monkeypatch):
        """PRESENT-VALUE-ONLY. Absence is no evidence, not a violation.

        Nothing here reads absence as a SUCCESSFUL sync -- the guard simply
        has no value to disagree with. What makes that safe at this call site
        is that `_MEMORY_CLI` is a sibling path of `archive_pin.py`, so parent
        and child are the same tree; the real-CLI arm below pins that premise
        instead of leaving it assumed.
        """
        verdict, _ = self._drive(claude_md, monkeypatch, None)
        assert verdict["outcome"] == "ARCHIVED"

    def test_wrote_names_where_a_stray_projection_can_be(
        self, claude_md, monkeypatch
    ):
        """The payload must NAME the stray projection, not just carry a reason.

        `occurrences` and `locations` describe the TARGET file, which in the
        different-file case is CLEAN. A verdict whose only evidence pointed
        there would be true about a narrower subject than the one it is read
        for -- the exact defect this consumer exists to remove, reproduced
        inside its own fix.
        """
        verdict, _ = self._drive(claude_md, monkeypatch, "wrote")
        assert verdict["sync_status"] == "wrote"
        assert verdict["sync_scope"], (
            "the verdict must bound WHERE a stray projection can be; the "
            "declared anchor is that bound"
        )
        assert verdict["sync_scope"] in verdict["reason"], (
            "the scope must be legible in the reason the curator reads, not "
            "only in a key they may not inspect"
        )
        assert verdict["claude_md_path"] in verdict["reason"], (
            "the reason must say which file `occurrences`/`locations` describe"
        )

    @pytest.mark.parametrize("status", _NO_WRITE_ATTEMPTED_STATUSES)
    def test_a_status_that_never_reached_the_write_carries_no_scope(
        self, claude_md, monkeypatch, status
    ):
        """THE SPLIT IS "WAS A WRITE ATTEMPTED", NOT "DID ONE LAND".

        These three exit BEFORE the write is attempted, so there is no
        projection from this save to bound. Their scope would be TRUE but
        VACUOUS, and a scope that is unconditionally true names nothing worth
        searching -- so it is omitted for VACUITY, never for falsehood.

        `failed` is deliberately NOT in this set. An earlier design put it
        here on the reasoning that no write had landed; that reasoning was
        wrong, because the `except` producing `failed` wraps the atomic
        rename. See the sibling arm below.
        """
        verdict, _ = self._drive(claude_md, monkeypatch, status)
        assert verdict["outcome"] == "ARCHIVED_DELETE_UNSAFE"
        assert "sync_scope" not in verdict, (
            f"status={status!r} exits before the write is attempted, so there "
            f"is no projection from this save to bound -- offering a scope "
            f"sends the curator searching for something this save never wrote"
        )
        assert "NO PROJECTION WAS ATTEMPTED" in verdict["reason"], (
            "this arm knows more than `failed` does and must say so: no write "
            "was attempted at all, which is stronger than 'cannot tell'"
        )

    def test_failed_carries_the_scope_because_a_write_may_have_landed(
        self, claude_md, monkeypatch
    ):
        """`failed` IS THE STATUS WHERE A STRAY COPY IS MOST PLAUSIBLE.

        The `except` that produces it wraps the atomic rename, and a lock
        release and a log call run after that rename inside the same `try`.
        So a durable, completed write can still report `failed` -- and the
        bound still holds, because a write outside the declared anchor is
        refused and also yields `failed`, with no write.

        An earlier design dropped the scope here, which removed it from
        precisely the status that most needed it. This arm is what stops that
        from being reintroduced.
        """
        verdict, _ = self._drive(claude_md, monkeypatch, "failed")
        assert verdict["sync_scope"], (
            "a write was ATTEMPTED and may have completed, so the bound is "
            "non-vacuous and must be reported"
        )
        assert verdict["sync_scope"] in verdict["reason"], (
            "a payload carrying a scope its prose never explains is the same "
            "disclosure mismatch this consumer exists to remove"
        )
        reason = verdict["reason"]
        assert "neither that a copy exists nor that none does" in reason, (
            "the `failed` arm must claim nothing in either direction"
        )
        assert "NO PROJECTION WAS ATTEMPTED" not in reason, (
            "that is the no-write arm's stronger claim and is false here -- a "
            "write WAS attempted and may have completed"
        )

    def test_exactly_the_write_attempting_statuses_carry_a_scope(
        self, claude_md, monkeypatch
    ):
        """DIFFERENTIAL over the whole alphabet, in one arm.

        The arms above assert each side separately. This records the
        INVARIANT -- the scope appears for exactly the statuses that attempted
        a write -- so an edit that adds or drops the key on any status fails
        here rather than being noticed by whoever reads the verdict second.

        COMPARED AGAINST THE SPELLED `_WRITE_ATTEMPTED_LITERAL`, NEVER AGAINST
        `archive_pin._WRITE_ATTEMPTED_STATUSES`. A set read off the module would
        FOLLOW a mutation of that constant and pass over it, which is the one
        failure this arm exists to catch. `test_the_write_attempted_constant_
        matches_the_spelled_set` is the tie-back that keeps the two honest.

        DO NOT "SIMPLIFY" THIS TO IMPORT THE CONSTANT. An earlier draft of this
        docstring recommended exactly that, describing a design that was
        rejected before the code was written -- so the comment licensed the
        edit the code below it forbids. A docstring that recommends disarming
        its own test is worse than a missing one, because it reads as
        permission rather than as description.
        """
        carriers = {
            status
            for status in _NON_SUPPRESSED_STATUSES
            if "sync_scope" in self._drive(claude_md, monkeypatch, status)[0]
        }
        assert carriers == set(_WRITE_ATTEMPTED_LITERAL), (
            f"the scope must be carried by exactly the statuses that attempted "
            f"a write; got {carriers}"
        )

    def test_the_write_call_is_inside_the_handler_that_returns_failed(self):
        """STRUCTURAL LICENCE for the `failed` arm's central claim.

        `_suppression_breach_reason` tells a curator that a `failed` sync "can
        be raised AFTER the write completed", and the whole reason `failed`
        carries `sync_scope` rests on that. Until now the claim was established
        by READING `sync_to_claude_md`, which is exactly the kind of premise
        that goes stale silently when someone restructures the function.

        PINS CONTAINMENT, NOT A SYNTHETIC FAULT. A fault-injection arm would
        prove the same thing more narrowly AND has a silent-rot mode this does
        not: a refactor could lift the write out from under the post-write
        statements while the injected fault still fired, leaving the test green
        over a dead property. Containment is the property the prose actually
        depends on, so containment is what is asserted.

        If this fails, do NOT relax it -- the `failed` arm's reason string and
        its membership in `_WRITE_ATTEMPTED_STATUSES` both stop being true.
        """
        wm_path = (
            Path(archive_pin.__file__).resolve().parent.parent
            / "skills" / "pact-memory" / "scripts" / "working_memory.py"
        )
        tree = ast.parse(wm_path.read_text(encoding="utf-8"))

        func = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "sync_to_claude_md"),
            None,
        )
        assert func is not None, "sync_to_claude_md not found -- test is blind"

        def _returns_failed(handler):
            return any(
                isinstance(n, ast.Attribute) and n.attr == "FAILED"
                for n in ast.walk(handler)
            )

        tries = [
            n for n in ast.walk(func)
            if isinstance(n, ast.Try)
            and any(_returns_failed(h) for h in n.handlers)
        ]
        assert tries, (
            "no `try` in sync_to_claude_md has a handler returning FAILED -- "
            "the `failed` status no longer originates where the reason string "
            "claims, so that prose and the scope-presence rule are both stale"
        )

        def _calls_atomic_write(node):
            return any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "_atomic_write_text"
                for n in ast.walk(node)
            )

        assert any(_calls_atomic_write(t) for t in tries), (
            "`_atomic_write_text` is NOT inside the try whose handler returns "
            "FAILED. A completed write can then no longer surface as `failed`, "
            "so the `failed` arm must stop claiming it may have landed -- and "
            "`failed` must leave _WRITE_ATTEMPTED_STATUSES"
        )

    def test_the_write_attempted_constant_matches_the_spelled_set(self):
        """TIE-BACK for the spelled literals above.

        The arms in this class sweep SPELLED status sets so that a mutation of
        the module constant cannot quietly shrink what they cover. That leaves
        one gap: a deliberate, correct change to the constant would look like
        a test failure with no explanation. This arm is the explanation -- it
        fails HERE, naming the constant, rather than inside a parametrised
        sweep whose set silently stopped matching the code.
        """
        assert set(archive_pin._WRITE_ATTEMPTED_STATUSES) == set(
            _WRITE_ATTEMPTED_LITERAL
        ), (
            "the module's scope-presence constant and this file's spelled set "
            "have diverged; update the literals deliberately, and check every "
            "arm that sweeps them"
        )

    def test_wrote_is_attributed_to_this_run_not_a_concurrent_editor(
        self, claude_md, monkeypatch
    ):
        """`wrote` and a duplicate block are DIFFERENT causes.

        A same-file projection is already caught downstream by
        `occurrences != 1`, but `_unsafe_reason` then blames "an editor or
        another process" -- when THIS RUN made the write. Right outcome,
        wrong cause. The two reason strings must not be confusable.
        """
        verdict, _ = self._drive(claude_md, monkeypatch, "wrote")
        reason = verdict["reason"]
        assert "THIS RUN" in reason, (
            "a projection made by this archival must be attributed to it"
        )
        assert "check for an editor or another process" not in reason, (
            "this is the concurrent-editor prose from the DUPLICATION cause; "
            "using it here misdescribes a write this run performed"
        )


@pytest.mark.requires_embedding_backend
class TestArchivePin_SyncStatusReachesTheArchive:
    """The premise the consumer rests on, MEASURED against the real CLI.

    Everything in the stubbed class above assumes `sync_status` is actually
    present in the envelope the archival path receives ON THE `--no-sync`
    ROUTE. `cmd_save` emits the field and this path parses that envelope, but
    those are two separate readings; a stub cannot join them. If the field is
    absent on this route the consumer has nothing to consume, and the stubs
    would keep passing while it did.
    """

    def test_the_archives_own_argv_yields_suppressed(self, tmp_path, memory_store):
        """Drives the REAL `_run_memory_cli` with the module's OWN constant."""
        project = tmp_path / "proj"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "CLAUDE.md").write_text(
            "# P\n\n## Working Memory\n\n", encoding="utf-8"
        )
        payload = json.dumps({"context": "real-cli arm", "goal": "observe"})

        _, stdout, _ = archive_pin._run_memory_cli(
            [archive_pin._ARCHIVE_SUBCOMMAND, "--stdin", "--no-sync"],
            db_path=str(memory_store("m.db")),
            stdin_data=payload,
            cwd=str(project),
        )
        result = archive_pin._parse_envelope(stdout)
        assert isinstance(result, dict), f"no success envelope: {stdout!r}"
        assert "sync_status" in result, (
            "the archival route's envelope carries NO sync_status -- the "
            "consumer in archive_pin() has nothing to read, and every stubbed "
            "arm above is testing a field production never sends"
        )
        assert result["sync_status"] == "suppressed"

    def test_dropping_the_flag_changes_the_status_and_writes_the_file(
        self, tmp_path, memory_store
    ):
        """CONTROL for the arm above, and it earns its place twice.

        Without it, `suppressed` is indistinguishable from a field that never
        varies -- a constant would satisfy the assertion above forever. It
        also measures the claim `_suppression_breach_reason` makes about the
        SAME-FILE case: that a projection lands the block where the occurrence
        check can see it. `_apply_token_budget` keeps the NEWEST entry in
        full, so the text arrives verbatim rather than compressed.
        """
        project = tmp_path / "proj"
        (project / ".claude").mkdir(parents=True)
        target = project / ".claude" / "CLAUDE.md"
        target.write_text("# P\n\n## Working Memory\n\n", encoding="utf-8")
        marker = "VERBATIM-PROJECTION-MARKER with spaces and `backticks`"
        payload = json.dumps({"context": marker, "goal": "observe"})

        _, stdout, _ = archive_pin._run_memory_cli(
            [archive_pin._ARCHIVE_SUBCOMMAND, "--stdin"],
            db_path=str(memory_store("m.db")),
            stdin_data=payload,
            cwd=str(project),
        )
        result = archive_pin._parse_envelope(stdout)
        assert isinstance(result, dict), f"no success envelope: {stdout!r}"
        assert result["sync_status"] == "wrote", (
            "without --no-sync the save must report a real projection; if "
            "this stays 'suppressed' the field is a constant and the arm "
            "above proves nothing"
        )
        assert marker in target.read_text(encoding="utf-8"), (
            "the projection must land the context VERBATIM -- that is what "
            "lets the occurrence check catch a same-file write-back"
        )
