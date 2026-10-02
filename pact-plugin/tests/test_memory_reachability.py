"""Verification arms for the agent-memory reachability checker.

Not the comprehensive suite. Each arm here is tied to one load-bearing clause of
the reachability rule, and every fixture is synthetic — the repo
holds no agent-memory-shaped material and a test against a real tree would
assert a number that moves between sessions.
"""
import subprocess
import sys
from pathlib import Path

import pytest

import memory_reachability as mr  # noqa: E402


def _tree(root):
    """One index, one named satellite that names a second, one named archive.

    Seven leaves, each reachable by a DIFFERENT key or not at all, so an arm
    that drops one key reddens rather than passing on a sibling's evidence.

    THE POINTER TEXT MUST NOT CONTAIN ANY OTHER KEY OF THE LEAF IT NAMES. An
    earlier version pointed at the frontmatter leaf with prose containing the
    bare word `frontmatter`, which is that same leaf's prefix-stripped key -- so
    the frontmatter key had no coverage through `scan()` at all, and the
    prefix-strip arm was passing on the sibling's evidence this docstring warns
    about. Both now have their own leaf and their own non-overlapping pointer.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "MEMORY.md").write_text(
        "# Index\n"
        "> Satellites: `MEMORY-topics.md`; retired in `MEMORY-archive-old.md`.\n"
        "\n"
        "## Rules\n"
        "- [By filename](feedback_by_filename.md)\n"
        "- [[by_wikilink_stem]]\n",
        encoding="utf-8",
    )
    # The new pointer goes in a SATELLITE, not the index: anchor arms depend on
    # the index's last entry line, so adding one there moves their evidence.
    (root / "MEMORY-topics.md").write_text(
        "see also `MEMORY-second-hop.md`\nalso stripped_prefix\n"
        "- [[by-hyphen-spelling]]\n", encoding="utf-8")
    (root / "MEMORY-second-hop.md").write_text(
        "reached only at the second hop: a leaf named in its own header\n", encoding="utf-8")
    (root / "MEMORY-archive-old.md").write_text(
        "- [Retired](feedback_archived_only.md)\n", encoding="utf-8")

    (root / "feedback_by_filename.md").write_text("x", encoding="utf-8")
    (root / "feedback_by_wikilink_stem.md").write_text("x", encoding="utf-8")
    (root / "feedback_by_hyphen_spelling.md").write_text("x", encoding="utf-8")
    # Reached ONLY by its frontmatter name: neither its filename, its stem, nor
    # its prefix-stripped stem appears in any root.
    (root / "feedback_frontmatter.md").write_text(
        "---\nname: a leaf named in its own header\n---\nx", encoding="utf-8")
    # Reached ONLY by its prefix-stripped stem.
    (root / "feedback_stripped_prefix.md").write_text("x", encoding="utf-8")
    (root / "feedback_archived_only.md").write_text("x", encoding="utf-8")
    (root / "feedback_planted_orphan.md").write_text(
        "---\nname: nobody_points_here\n---\nx", encoding="utf-8")
    return root


def test_planted_orphan_is_the_only_unreachable(tmp_path):
    """The acceptance bar: it must find a planted orphan and nothing else."""
    result = mr.scan(_tree(tmp_path / "agent"))
    assert [p.name for p in result.unreachable] == ["feedback_planted_orphan.md"]


def test_archive_only_is_not_reported_as_an_orphan(tmp_path):
    """Collapsing the tiers condemns compaction for working correctly."""
    result = mr.scan(_tree(tmp_path / "agent"))
    assert [p.name for p in result.archive_only] == ["feedback_archived_only.md"]
    assert "feedback_archived_only.md" not in [p.name for p in result.unreachable]


def test_root_discovery_reaches_a_fixed_point(tmp_path):
    """A satellite naming another satellite. One level is not enough."""
    result = mr.scan(_tree(tmp_path / "agent"))
    assert "MEMORY-second-hop.md" in [p.name for p in result.roots]
    # And the leaf that only that satellite names is live, not an orphan.
    assert "feedback_frontmatter.md" in [p.name for p in result.live_reachable]


def test_an_unnamed_satellite_and_its_exclusive_leaf_are_both_orphans(tmp_path):
    """Globbing roots would silently promote it and certify its targets."""
    root = _tree(tmp_path / "agent")
    (root / "MEMORY-unnamed.md").write_text("- [Y](feedback_only_here.md)\n", encoding="utf-8")
    (root / "feedback_only_here.md").write_text("x", encoding="utf-8")
    names = [p.name for p in mr.scan(root).unreachable]
    assert "MEMORY-unnamed.md" in names and "feedback_only_here.md" in names


def test_a_pointer_past_the_cut_is_reported(tmp_path):
    """In the file and out of context is its own tier, invisible to a whole-file read."""
    root = tmp_path / "agent"
    root.mkdir(parents=True)
    filler = "\n".join("- filler line {0}".format(i) for i in range(mr.MAX_LINES + 5))
    (root / "MEMORY.md").write_text(
        "# Index\n" + filler + "\n- [Late](feedback_past_the_cut.md)\n", encoding="utf-8")
    (root / "feedback_past_the_cut.md").write_text("x", encoding="utf-8")
    result = mr.scan(root)
    assert [p.name for p in result.past_the_cut] == ["feedback_past_the_cut.md"]


def test_a_directory_with_no_index_is_not_applicable(tmp_path):
    """Not an error, and not a fully orphaned directory."""
    root = tmp_path / "empty"
    root.mkdir()
    (root / "feedback_stray.md").write_text("x", encoding="utf-8")
    result = mr.scan(root)
    assert result.not_applicable and result.unreachable == ()


def test_utf16_units_are_not_code_points(tmp_path):
    """`len()` undercounts an astral character by exactly one per occurrence."""
    assert mr.utf16_units("\U0001F511") == 2
    assert len("\U0001F511") == 1


def test_the_emitted_anchor_is_a_byte_exact_slice_of_the_raw_index(tmp_path):
    """The matching haystack is normalised; an anchor taken from it never matches.

    NARROWED once the anchor became a slice of `raw`: NO INPUT can make a
    byte-exact anchor fail this, so it no longer asserts "the guard catches a bad
    anchor from a real file". It asserts "an anchor built from the wrong SOURCE
    is caught" -- reachable only by mutating the construction, not the fixture.
    The first reading is the one everyone assumes; this is the second.
    """
    root = _tree(tmp_path / "agent")
    block = mr.emit_edit(mr.scan(root))
    raw = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert block is not None and not block.startswith("REFUSED")
    anchor = block.split("old_string:\n", 1)[1].split("\n\nnew_string:", 1)[0]
    assert anchor in raw, "anchor must be a raw slice, not normalised text"
    # The NEIGHBOUR, not the heading: the last entry line, so placement is exact.
    assert anchor.strip().startswith("-")


def test_a_named_section_anchors_inside_that_section(tmp_path):
    """The agent names the topic; the tool computes the anchor."""
    root = _tree(tmp_path / "agent")
    anchor, _ = _emitted(mr.emit_edit(mr.scan(root), "## Rules"))
    assert anchor.strip() == "- [[by_wikilink_stem]]"


def test_an_empty_named_section_anchors_on_its_heading(tmp_path):
    """With no entries to land in front of, heading and section-end coincide."""
    root = _tree(tmp_path / "agent")
    index = root / "MEMORY.md"
    index.write_text(index.read_text(encoding="utf-8") + "\n## Fresh\n", encoding="utf-8")
    anchor, _ = _emitted(mr.emit_edit(mr.scan(root), "## Fresh"))
    assert anchor.strip() == "## Fresh"


def test_a_section_past_the_cut_is_refused(tmp_path):
    """Placing a pointer there would create the condition this tool reports."""
    root = tmp_path / "agent"
    root.mkdir(parents=True)
    filler = "\n".join("- filler line {0}".format(i) for i in range(mr.MAX_LINES + 5))
    (root / "MEMORY.md").write_text("# Index\n" + filler + "\n## Late\n", encoding="utf-8")
    (root / "feedback_orphan.md").write_text("x", encoding="utf-8")
    block = mr.emit_edit(mr.scan(root), "## Late")
    assert block is not None
    assert block.startswith("REFUSED") and "past the loaded prefix" in block


def test_an_unknown_heading_is_refused(tmp_path):
    root = _tree(tmp_path / "agent")
    block = mr.emit_edit(mr.scan(root), "## Nope")
    assert block is not None
    assert block.startswith("REFUSED")


def test_emit_is_silent_on_a_clean_tree(tmp_path):
    root = _tree(tmp_path / "agent")
    (root / "feedback_planted_orphan.md").unlink()
    assert mr.emit_edit(mr.scan(root)) is None


def test_a_missing_directory_is_a_precondition_failure(tmp_path):
    with pytest.raises(mr.PreconditionError):
        mr.scan(tmp_path / "nope")


def test_the_report_refuses_to_write_into_the_scanned_directory(tmp_path):
    root = _tree(tmp_path / "agent")
    assert mr.main([str(root), "--quiet", "--report", str(root / "out.json")]) == 2
    assert not (root / "out.json").exists()


# ---------------------------------------------------------------------------
# Predicate-level arms.
#
# Every arm above drives scan() end to end, so a predicate detail no fixture
# happens to exercise is invisible to all of them. These four call the predicate
# directly. Measured against the 35 arms in this file: deleting re.escape,
# deleting re.IGNORECASE, widening the boundary classes to \b, dropping the
# frontmatter key, and dropping the prefix strip are EACH killed, every one by
# the arm written for it.
#
# State the arm count beside any such result. An earlier version of this block
# reported a 16-arm measurement as current fact, and it was still being read as
# current after the suite had grown past it.
#
# Mutating this module: disable bytecode caching (PYTHONDONTWRITEBYTECODE=1). A
# mutation that preserves the file's byte length, written in a fast loop, lands
# in the same mtime second and is served stale from __pycache__ -- reporting
# SURVIVED with the mutation sitting on disk. Kills are safe; survivors lie.


def test_the_key_is_escaped_rather_than_compiled():
    """Real frontmatter names carry '+'; unescaped it is a quantifier."""
    assert not mr.mentions("ab", "a+b")


def test_matching_ignores_case():
    """Roughly a fifth of real names are prose titles with capitals."""
    assert mr.mentions("SESSION_HOOK_PATTERNS", "session_hook_patterns")


def test_a_leading_dot_is_not_a_boundary():
    """\\b would accept it, and a key would match inside a longer dotted token."""
    assert not mr.mentions("other.key", "key")


def test_the_frontmatter_name_is_a_key(tmp_path):
    """The key that carries pointers written as bare prose."""
    leaf = tmp_path / "feedback_x.md"
    leaf.write_text("---\nname: A Prose Title\n---\nx", encoding="utf-8")
    assert mr.normalise("A Prose Title") in mr.keys_for(leaf)


def test_type_prefixes_are_the_four_memory_types():
    """The tuple's CLOSEDNESS is the whole argument for four keys over six.

    Nothing in this repo defines the memory-type vocabulary -- it comes from the
    platform -- so there is nothing to derive this from and this pins the
    literals instead. Weaker than a derivation, and it makes a fifth type a
    deliberate edit rather than silent drift.
    """
    assert mr.TYPE_PREFIXES == ("feedback_", "project_", "reference_", "user_")


def _emitted(block):
    # Assert the shape before splitting on it. A REFUSED block, or None, would
    # otherwise raise IndexError here -- which reddens the arm without telling
    # you whether the SUBJECT failed or this helper did. Measured: the
    # normalised-anchor mutant killed the round-trip arm by IndexError, and a
    # kill by the wrong mechanism is indistinguishable from a real one in a
    # pass/fail summary.
    assert block is not None and not block.startswith("REFUSED"), block
    assert "old_string:\n" in block and "\n\nnew_string:\n" in block, block
    anchor = block.split("old_string:\n", 1)[1].split("\n\nnew_string:\n", 1)[0]
    new = block.split("\n\nnew_string:\n", 1)[1].split("\n\nThe file may", 1)[0]
    return anchor, new


def test_applying_the_edit_closes_the_orphan_and_then_emits_nothing(tmp_path):
    """The only arm that exercises the emit path end to end rather than its string.

    Round trip and idempotence in one: everything else here asserts what the
    block SAYS, and a block can be well-formed and still not close the finding.
    """
    root = _tree(tmp_path / "agent")
    index = root / "MEMORY.md"
    raw = index.read_text(encoding="utf-8")
    anchor, new = _emitted(mr.emit_edit(mr.scan(root)))
    assert raw.count(anchor) == 1
    index.write_text(raw.replace(anchor, new), encoding="utf-8")

    assert mr.scan(root).unreachable == ()
    assert mr.emit_edit(mr.scan(root)) is None


def test_the_anchor_fixture_still_discriminates_a_normalised_anchor(tmp_path):
    """Pins the fixture property the raw-bytes guard's kill depends on.

    normalise() maps '-' to '_' and lowercases. If the anchor region ever loses
    every character normalise() changes -- switching the '-' bullets to '*' is
    enough -- then normalised text and raw text are identical there, a
    normalised anchor still matches, and the guard above silently stops being
    tested while staying green. Measured, not hypothetical.
    """
    root = _tree(tmp_path / "agent")
    anchor, _ = _emitted(mr.emit_edit(mr.scan(root)))
    assert mr.normalise(anchor) != anchor


def test_the_unnamed_anchor_is_the_LAST_entry_of_the_loaded_prefix(tmp_path):
    """Separates the heading-absent path from the heading-given one.

    Measured: mutating `entries[-1]` to `entries[0]` reddened nothing, while
    blanking the heading lookup reddened only the three heading arms. The two
    paths were separable in the code and not in the suite, which is the shape
    where a shared kill proves nothing. This is the missing half.
    """
    root = _tree(tmp_path / "agent")
    anchor, _ = _emitted(mr.emit_edit(mr.scan(root)))
    assert anchor.endswith("[[by_wikilink_stem]]")


def test_a_duplicated_section_entry_expands_the_anchor(tmp_path):
    """Uniqueness expansion on the heading-GIVEN path.

    The expansion loop is shared with the heading-absent path, but only a named
    section can put a duplicated last entry in front of it -- the absent path
    anchors on the tail of the whole prefix, where a duplicate downstream cannot
    occur. So this exercises the interaction, not new code.
    """
    root = tmp_path / "agent"
    root.mkdir()
    (root / "MEMORY.md").write_text(
        "# I\n"
        "## Rules\n"
        "- [a](feedback_a.md)\n"
        "- [dup](feedback_dup.md)\n"
        "## Other\n"
        "- [dup](feedback_dup.md)\n",
        encoding="utf-8",
    )
    for name in ("feedback_a.md", "feedback_dup.md", "feedback_orphan.md"):
        (root / name).write_text("x", encoding="utf-8")

    anchor, _ = _emitted(mr.emit_edit(mr.scan(root), "## Rules"))
    raw = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert "\n" in anchor, "a duplicated last entry must force expansion"
    assert raw.count(anchor) == 1


def test_the_size_cut_returns_a_line_aligned_prefix_within_the_cap():
    """The size branch: an index under the line cap but over the size cap.

    Kills two mutants no other arm sees -- dropping the size cut entirely, and
    dropping the newline snap-back. Both left every other arm green, because the
    filler fixtures above sit near 3.3k units against a 25000 ceiling, so the
    early return fires and this whole branch never executes.

    The snap-back is NOT diagnostic-only. emit_edit bounds anchor placement by
    len(truncate_as_loaded(raw).splitlines()), so a prefix ending mid-line makes
    that bound one too many and the emitted pointer lands past the cut -- the
    condition this tool exists to report.
    """
    raw = "# Index\n" + "".join(
        "- [entry {0}](feedback_e{0}.md) - {1}\n".format(i, "x" * 200) for i in range(120))
    assert len(raw.splitlines()) < mr.MAX_LINES, "must trip the SIZE cap, not the line cap"
    assert mr.utf16_units(raw) > mr.MAX_UNITS

    loaded = mr.truncate_as_loaded(raw)
    assert mr.utf16_units(loaded) <= mr.MAX_UNITS, "the size cut must actually cut"
    assert loaded.endswith("\n") and raw.startswith(loaded), "a line-aligned prefix of the raw text"


def test_the_emitted_anchor_stays_inside_the_loaded_prefix_under_a_size_cut(tmp_path):
    """The consequence the arm above protects: placement, not just truncation."""
    root = tmp_path / "agent"
    root.mkdir(parents=True)
    (root / "MEMORY.md").write_text("# Index\n" + "".join(
        "- [entry {0}](feedback_e{0}.md) - {1}\n".format(i, "x" * 200) for i in range(120)),
        encoding="utf-8")
    for i in range(120):
        (root / "feedback_e{0}.md".format(i)).write_text("x", encoding="utf-8")
    (root / "feedback_orphan.md").write_text("x", encoding="utf-8")

    raw = (root / "MEMORY.md").read_text(encoding="utf-8")
    anchor, _ = _emitted(mr.emit_edit(mr.scan(root)))
    through = raw[: raw.index(anchor) + len(anchor)] + "\n"
    assert mr.utf16_units(through) <= mr.MAX_UNITS, "a pointer placed here is written and never read"


def test_the_bare_token_diagnostic_counts_the_leaves_carried_by_prose(tmp_path):
    """Computed, rendered and serialised; asserted nowhere until now.

    feedback_stripped_prefix is carried by the bare token `stripped_prefix`, and
    feedback_frontmatter by its frontmatter name in a satellite's prose. Neither
    is reached through link syntax, which is what this diagnostic counts.
    """
    result = mr.scan(_tree(tmp_path / "agent"))
    assert result.bare_token_only == 2


def test_an_unnamed_satellite_is_listed_in_the_glob_versus_follow_diagnostic(tmp_path):
    """Zero is the expected reading, which is why a non-zero one needs an arm."""
    root = _tree(tmp_path / "agent")
    assert mr.scan(root).unreached_index_files == ()
    (root / "MEMORY-unnamed.md").write_text("- [Y](feedback_only_here.md)\n", encoding="utf-8")
    assert [p.name for p in mr.scan(root).unreached_index_files] == ["MEMORY-unnamed.md"]


def test_the_index_shaped_and_archive_vocabularies_are_closed():
    """Same argument as TYPE_PREFIXES: nothing here derives these, so pin them.

    The fixture exercises one member of each, so dropping any other member is
    invisible to every behavioural arm.
    """
    assert mr.INDEX_SHAPED == ("MEMORY-", "INDEX_", "ARCHIVE")
    assert mr.ARCHIVE_MARKERS == ("archive", "pre-compact", "precompact")


def test_the_size_cut_accounts_for_the_line_endings_actually_on_disk(tmp_path):
    """A CRLF index costs one more unit per line, so fewer lines fit under the cap.

    Only reachable now that _read is byte-faithful. While universal-newline
    translation ran, the cut was computed on text one character per line shorter
    than the file, so the anchor could sit past the very cap it respects --
    measured here at 25083 against a 25000 ceiling, versus 24897 byte-faithful.

    This is the only arm that exercises the size branch against line endings
    rather than against length alone.
    """
    root = tmp_path / "agent"
    root.mkdir(parents=True)
    body = "".join(
        "- [entry {0}](feedback_e{0}.md) - {1}\r\n".format(i, "x" * 150) for i in range(190))
    (root / "MEMORY.md").write_bytes(("# Index\r\n" + body).encode("utf-8"))
    for i in range(190):
        (root / "feedback_e{0}.md".format(i)).write_text("x", encoding="utf-8")
    (root / "feedback_orphan.md").write_text("x", encoding="utf-8")

    # `Path.read_text(newline=...)` is 3.13+; `Path.open(newline=...)` is not.
    # Reading here rather than through mr._read keeps the assertion independent
    # of the reader under test.
    with (root / "MEMORY.md").open(encoding="utf-8", newline="") as handle:
        raw = handle.read()
    assert len(raw.splitlines()) < mr.MAX_LINES, "must trip the SIZE cap, not the line cap"
    anchor, _ = _emitted(mr.emit_edit(mr.scan(root)))
    through = raw[: raw.index(anchor) + len(anchor)]
    assert mr.utf16_units(through) <= mr.MAX_UNITS


def _crlf_tree(root, duplicated):
    """A CRLF index whose last section entry is unique, or duplicated downstream."""
    root.mkdir(parents=True, exist_ok=True)
    tail = "## Other\r\n- [dup](feedback_dup.md)\r\n" if duplicated else ""
    (root / "MEMORY.md").write_bytes(
        ("# I\r\n## Rules\r\n- [a](feedback_a.md)\r\n- [dup](feedback_dup.md)\r\n"
         + tail).encode("utf-8"))
    for name in ("feedback_a.md", "feedback_dup.md", "feedback_orphan.md"):
        (root / name).write_text("x", encoding="utf-8")
    return root


def test_a_crlf_index_emits_appliable_anchors_and_keeps_its_terminator(tmp_path):
    """Both anchor shapes must emit an edit that APPLIES to the bytes on disk.

    A `\r` never appears inside a single-line anchor -- splitlines() consumes it
    as the terminator -- so that shape always round-tripped. The multi-line shape
    is the one that moved: it used to rejoin with a bare newline, was not a raw
    slice, and refused; it is now sliced from `raw` by offsets and emits.

    The terminator half is the reason this arm is worth more than the anchor
    check alone: an emitted pointer joined with a hardcoded newline would give a
    CRLF index one LF-only line, which is a terminator bug shipped by the fix for
    a terminator bug. Asserting the applied file has no bare newline catches it,
    and asserting the block APPLIES catches an anchor that merely looks right.
    """
    for duplicated in (False, True):
        root = _crlf_tree(tmp_path / ("dup" if duplicated else "unique"), duplicated)
        index = root / "MEMORY.md"
        raw = index.read_bytes()
        anchor, replacement = _emitted(mr.emit_edit(mr.scan(root), "## Rules"))

        assert anchor.encode("utf-8") in raw, "anchor must match the bytes an agent will edit"
        assert raw.count(anchor.encode("utf-8")) == 1, "and match exactly once"

        applied = raw.replace(anchor.encode("utf-8"), replacement.encode("utf-8"), 1)
        assert b"\n" not in applied.replace(b"\r\n", b""), (
            "the emitted pointer introduced an LF-only line into a CRLF index")

        index.write_bytes(applied)
        assert mr.scan(root).unreachable == (), "applying the block must close the finding"


def test_a_conforming_satellite_named_without_its_extension_is_still_a_root(tmp_path):
    """keys_for offers four keys for a leaf; find_roots matched satellites on one.

    A FULLY CONFORMING satellite referenced stem-only was never promoted, so its
    exclusive leaves came back as false orphans -- the direction --emit-edit acts
    on. The `.md` spelling is the control: it passed before the fix and must keep
    passing, so this arm catches an over-correction as well as the gap.
    """
    for i, reference in enumerate(("MEMORY-topics", "[[MEMORY-topics]]", "MEMORY-topics.md")):
        root = tmp_path / "agent{0}".format(i)
        root.mkdir(parents=True)
        (root / "MEMORY.md").write_text("# I\n- see {0}\n".format(reference), encoding="utf-8")
        (root / "MEMORY-topics.md").write_text("- [a](feedback_a.md)\n", encoding="utf-8")
        (root / "feedback_a.md").write_text("x", encoding="utf-8")

        result = mr.scan(root)
        assert "MEMORY-topics.md" in [p.name for p in result.roots], reference
        assert result.unreachable == (), reference


def _older_interpreter():
    """The FIRST candidate older than the one running the suite, or None.

    First, not oldest and not the declared floor: the walk returns as soon as a
    candidate is below the running version, so which interpreter a caller gets
    depends on what the host has installed.
    """
    for candidate in ("/usr/bin/python3", "python3.9", "python3.10", "python3.11"):
        try:
            out = subprocess.run(
                [candidate, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0:
            found = tuple(int(n) for n in out.stdout.split())
            if found < sys.version_info[:2]:
                return candidate, found
    return None, None


def test_the_checker_imports_and_scans_under_an_older_interpreter(tmp_path):
    """A syntax parse cannot see a keyword argument that only exists in a newer runtime.

    `Path.read_text(newline=...)` is 3.13+. It is valid 3.9 SYNTAX, so the
    annotation-compat gate -- which parses at feature_version 3.9 -- stayed green
    while every scan would have raised TypeError on the first file it read. This
    arm RUNS the module instead of parsing it, which is the only way to see that
    class of defect.

    WHAT A GREEN HERE COVERS. The probe runs under the first interpreter older
    than the running one, which is not the oldest available and not the declared
    floor, so the version actually exercised depends on the host's interpreter
    inventory. Read it off the failure message, which names the interpreter and
    the version it found; do not infer it from this arm passing.

    This arm is inert wherever the running interpreter is the oldest available,
    including a run AT the declared floor, where no candidate can be older. The
    skip is the correct outcome there.
    """
    interpreter, found = _older_interpreter()
    if interpreter is None:
        pytest.skip("no interpreter older than {0} available".format(sys.version_info[:2]))

    root = tmp_path / "agent"
    root.mkdir(parents=True)
    (root / "MEMORY.md").write_bytes(b"# I\r\n- [a](feedback_a.md)\r\n")
    (root / "feedback_a.md").write_text("x", encoding="utf-8")
    (root / "feedback_orphan.md").write_text("x", encoding="utf-8")

    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    probe = (
        "import sys, pathlib; sys.path.insert(0, {0!r})\n"
        "import memory_reachability as mr\n"
        "root = pathlib.Path({1!r})\n"
        "r = mr.scan(root)\n"
        "assert [p.name for p in r.unreachable] == ['feedback_orphan.md'], r.unreachable\n"
        "assert '\\r' in mr._read(root / 'MEMORY.md'), 'line endings were translated'\n"
        "mr.emit_edit(r); mr.render(r); mr.as_dict(r)\n"
    ).format(scripts, str(root))
    done = subprocess.run([interpreter, "-c", probe], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, "under {0} {1}:\n{2}".format(interpreter, found, done.stderr)


def test_the_one_root_warning_fires_only_when_nothing_was_followed(tmp_path):
    """The tool cannot tell a skipped hub from a leaf that links to an orphan.

    So the warning is conditional on having followed NOTHING, not on detecting a
    hub -- a predicate for hub-ness was measured and over-reports. Pins the two
    firing cases and the silence; NOT completeness. It deliberately does not fire
    when a hub is skipped alongside followed satellites, which is a known gap
    chosen over a rule that cries wolf on a healthy tree.
    """
    marker = "READ THIS BEFORE ACTING"

    # Skipped hub: named by the index, not index-shaped, sole pointer to its leaf.
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / "MEMORY.md").write_text("# I\n- see hub_topics.md\n", encoding="utf-8")
    (hub / "hub_topics.md").write_text("- [a](feedback_a.md)\n", encoding="utf-8")
    (hub / "feedback_a.md").write_text("x", encoding="utf-8")
    assert marker in mr.render(mr.scan(hub))

    # No satellite at all, one genuine orphan: same condition, still a warning.
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "MEMORY.md").write_text("# I\n- nothing here\n", encoding="utf-8")
    (bare / "feedback_orphan.md").write_text("x", encoding="utf-8")
    assert marker in mr.render(mr.scan(bare))

    # CONTROL -- the over-report direction. A conforming tree must stay silent,
    # and every leaf in it IS named by a root, which is how a too-broad
    # predicate passes unnoticed.
    assert marker not in mr.render(mr.scan(_tree(tmp_path / "agent")))

    # CONTROL -- one root is NOT enough on its own. A plain single-index
    # directory with nothing orphaned is the common healthy shape, and warning
    # there tells the user to rename satellites that do not exist.
    solo = tmp_path / "solo"
    solo.mkdir()
    (solo / "MEMORY.md").write_text("# I\n- [a](feedback_a.md)\n", encoding="utf-8")
    (solo / "feedback_a.md").write_text("x", encoding="utf-8")
    assert marker not in mr.render(mr.scan(solo))


def test_following_the_warnings_own_advice_does_not_lose_leaves(tmp_path):
    """The remedy the warning prints must restore the leaf, not retire it.

    `is_archive` treats any stem containing `archive` as RETIRED, so renaming a
    skipped satellite to ARCHIVE* moves its exclusive leaves to archive_only,
    where emit_edit no longer offers to restore them -- a live memory rendered
    indistinguishable from one deliberately retired. That is why ARCHIVE* is
    excluded from the advice, and this arm is the enforcement: the exclusion
    looks like an omission next to INDEX_SHAPED, which lists all three.

    Pins the round-trip, never the wording, so the prose can be rewritten.
    """
    def renamed(to):
        root = tmp_path / to.replace("*", "x").replace(".", "_")
        root.mkdir(parents=True)
        (root / "MEMORY.md").write_text("# I\n- see {0}\n".format(to), encoding="utf-8")
        (root / to).write_text("- [g](feedback_g.md)\n", encoding="utf-8")
        (root / "feedback_g.md").write_text("x", encoding="utf-8")
        return mr.scan(root)

    for target in ("MEMORY-t.md", "INDEX_t.md"):
        result = renamed(target)
        assert [p.name for p in result.unreachable] == [], target
        assert [p.name for p in result.archive_only] == [], target
        assert mr.emit_edit(result) is None, target

    # CONTROL -- the loss this exclusion exists to prevent. Renaming to ARCHIVE*
    # silently reclassifies a live leaf as retired and emit_edit goes quiet.
    lost = renamed("ARCHIVE_t.md")
    assert [p.name for p in lost.archive_only] == ["feedback_g.md"]
    assert mr.emit_edit(lost) is None, "and nothing is offered to restore it"


def test_a_usage_error_and_a_refusal_exit_DIFFERENTLY(tmp_path):
    """ONE RUN, VARIED INPUTS, BOTH CODES — and that is the whole design.

    argparse exits 2 by default and this script returns 2 for REFUSED, so a
    mistyped invocation was indistinguishable from a declined one. Two separate
    arms — one asserting usage returns 64, one asserting a refusal returns 2 —
    would BOTH pass against a collapsed axis, because each finds an input
    satisfying it in isolation. So the table is asserted whole.

    RED WHEN usage and refusal share a code again, in either direction.
    """
    def code(argv):
        try:
            return mr.main(argv)
        except SystemExit as exc:
            return exc.code

    observed = {
        "malformed flag":  code(["--nope", str(tmp_path)]),
        "missing operand": code([]),
        "refusal: absent": code([str(tmp_path / "not_here")]),
    }
    assert observed == {
        "malformed flag":  mr._EXIT_USAGE,
        "missing operand": mr._EXIT_USAGE,
        "refusal: absent": 2,
    }, "exit codes moved: {0}".format(observed)

    # The separation itself, stated rather than implied: routing everything
    # through one code would still satisfy a row-by-row reading.
    assert observed["malformed flag"] != observed["refusal: absent"], (
        "a mistyped command and a declined one are indistinguishable to a caller"
    )
