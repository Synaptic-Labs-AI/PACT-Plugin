"""
Tests for the `delete_string` emit contract and the uniqueness precondition.

Location: pact-plugin/tests/test_archive_pin_emit.py

WHAT THIS SUITE CLAIMS, AND WHAT IT DOES NOT. Every test here constrains
THE HANDLE the verdict emits — never THE EDIT the command performs. The
removal is an LLM-executed `Edit`, which pytest cannot observe. So the
claim is "the verdict emits a correct, unambiguous, verbatim handle bound
to a named file", NOT "the removal deletes the right bytes".

That distinction is the whole design. `delete_string` exists so the command
stops RE-DERIVING the span from prose: a second derivation is what let the
delete range and the archive range disagree. With one derivation there is
nothing left to disagree, so coextensivity is not VERIFIED here — it is made
UNFALSIFIABLE by removing the second derivation. No docstring in this file
may claim the stronger property.

The error directions are not symmetric. A false ARCHIVED destroys content
(CLAUDE.md is gitignored, so there is no git fallback); a false refusal
costs an over-block and loses nothing. Tests weight accordingly.

`db_path` IS REQUIRED AND KEYWORD-ONLY on `build_verdict`. `db_path=None`
here always means "this call provably never reaches a store" — the enclosing
test stubs `_run_memory_cli` or short-circuits before the spawn. It is a
claim about the call site, and deleting the stub above such a call makes the
claim false without changing the call.

ONE call in this file genuinely reaches the memory CLI:
`test_null_when_the_pin_does_not_resolve`'s resolving control. It is scoped
to a temp `db_path`. Before that scoping it ran with the default and wrote
into the developer's LIVE database, two rows per suite run, one per
parametrization. It must STAY reaching — it is a non-vacuity control, and a
control that stops reaching stops controlling — so it is scoped, not stubbed.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402

import archive_pin  # noqa: E402
import staleness  # noqa: E402
import os
from fixtures.hf_cache import hf_cache_env


@pytest.fixture(autouse=True)
def _pin_the_model_cache(monkeypatch):
    """Point this module's children at the operator cache, and refuse network.

    THE `_cli_get` BINDING BELOW IS NECESSARY AND NOT SUFFICIENT, which a
    forced-cold-cache run proved: with only that binding this module still
    pulled 14 files. `_cli_get` is one of TWO routes to a child here. The other
    is PRODUCTION code — `archive_pin._run_memory_cli` — which inherits this
    process's environment and has no `env` dict to bind. Fixing only the route
    visible in this file left the production route unprotected.

    MODULE-SCOPED rather than conftest-wide: a global autouse would also force
    offline on in-process loads elsewhere, where a fixture loads a real model on
    purpose.
    """
    for _k, _v in hf_cache_env().items():
        monkeypatch.setenv(_k, _v)


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


def _two_pin_file():
    return make_claude_md_with_pins([
        make_pin_entry(title="First Pin", body_chars=40, date="2026-01-01"),
        make_pin_entry(title="Second Pin", body_chars=40, date="2026-02-02"),
    ])


def _pinned_body(content):
    parsed = staleness._parse_pinned_section(content)
    assert parsed is not None, "fixture has no Pinned Context section"
    return parsed[2]


def _expected_block(content, index):
    """Compute the block INDEPENDENTLY of the verdict.

    Deliberately re-derived from the source rather than read back off the
    verdict — comparing a field against itself is the vacuity mode this
    row exists to avoid.
    """
    pinned = _pinned_body(content)
    pins = archive_pin.parse_pins(pinned)
    return archive_pin.extract_pin_block(pinned, index, pins)


def _ok_stub(context_value):
    """A save/get seam that succeeds and returns `context_value` on get."""
    def _stub(args, **kwargs):
        if args[0] == "save":
            return 0, json.dumps(
                {"ok": True, "result": {"memory_id": "a" * 32}}
            ), ""
        return 0, json.dumps(
            {"ok": True, "result": {"context": context_value}}
        ), ""
    return _stub


class TestDeleteStringEmitContract:
    """`delete_string` is a property of WHICH PIN, not of whether the
    archive worked — so it is emitted wherever the pin RESOLVES and is null
    only where it does not.

    BOUND: these tests pin the FIELD. They say nothing about whether the
    command uses it.
    """

    def test_present_and_non_null_on_archived(self, claude_md, monkeypatch):
        content = _two_pin_file()
        claude_md(content)
        block = _expected_block(content, 0)
        monkeypatch.setattr(archive_pin, "_run_memory_cli", _ok_stub(block))

        verdict = archive_pin.build_verdict(0, db_path=None)

        # Non-vacuity: prove the arm was actually taken. Without this the
        # assertion below passes on any outcome that happens to carry a key.
        assert verdict["outcome"] == "ARCHIVED"
        assert verdict["delete_string"] == block

    def test_present_on_not_archived_because_the_pin_still_resolved(
        self, claude_md, monkeypatch
    ):
        """The archive failed; the pin did not. The curator still needs the
        handle to know WHICH pin the refusal is about."""
        content = _two_pin_file()
        claude_md(content)
        monkeypatch.setattr(
            archive_pin, "_run_memory_cli",
            lambda *a, **k: (
                1, "", json.dumps({"ok": False, "error": "ValueError",
                                   "message": "bad field"})
            ),
        )

        verdict = archive_pin.build_verdict(0, db_path=None)

        assert verdict["outcome"] == "NOT_ARCHIVED"
        assert verdict["memory_id"] is None, "the archive must have failed"
        assert verdict["delete_string"] == _expected_block(content, 0)

    def test_present_on_a_located_pin_unevaluable(self, claude_md, monkeypatch):
        """UNEVALUABLE reached AFTER the pin resolved still carries it."""
        content = _two_pin_file()
        claude_md(content)

        def _refetch_fails(args, **kwargs):
            if args[0] == "save":
                return 0, json.dumps(
                    {"ok": True, "result": {"memory_id": "a" * 32}}
                ), ""
            return 1, "", json.dumps(
                {"ok": False, "error": "NOT_FOUND", "message": "gone"}
            )

        monkeypatch.setattr(archive_pin, "_run_memory_cli", _refetch_fails)
        verdict = archive_pin.build_verdict(0, db_path=None)

        assert verdict["outcome"] == "UNEVALUABLE"
        # heading non-null is what PROVES the pin resolved before the failure
        assert verdict["heading"] == "First Pin"
        assert verdict["delete_string"] == _expected_block(content, 0)

    @pytest.mark.parametrize("bad_index", [99, -1])
    def test_null_when_the_pin_does_not_resolve(
        self, claude_md, bad_index, memory_store
    ):
        """THE DISCRIMINATING ROW.

        If `delete_string` were merely "the block we happened to compute",
        a non-resolving verdict could carry a stale or defaulted value. Null
        here is what makes it a property of WHICH PIN.

        NON-VACUITY CONTROL IS LOAD-BEARING: a test asserting only `is None`
        passes trivially if the field is ALWAYS None. The resolving control
        below is what makes the null meaningful.

        The control REACHES A REAL SAVE — it is the one place in these three
        files where a bare `build_verdict` reached the memory CLI, and with
        `db_path` defaulting to None that save landed in the DEVELOPER'S
        LIVE DATABASE. Measured at two rows per suite run, one per
        parametrization. The control has to stay reaching to be a control, so
        the fix is to SCOPE it, never to stub it.

        SCOPING IS NECESSARY AND IT IS NOT SUFFICIENT, AND THAT COST THIS ARM
        ITS REACH ONCE. A bare temp path names a store that is ABSENT, and the
        CLI boundary refuses one for each command other than `setup`, so the
        save was refused and the control stopped reaching while staying green.
        The store must be PRESENT, which the fixture supplies. AND THE REACH
        NEEDS ITS OWN ASSERTION: `delete_string` is computed from CLAUDE.md
        BEFORE the save runs, so no assertion that reads it can fail when the
        call stops reaching. The outcome assertion below reads a POST-SAVE
        field, which is the only kind that can.
        """
        content = _two_pin_file()
        claude_md(content)

        unresolved = archive_pin.build_verdict(bad_index, db_path=None)
        assert unresolved["outcome"] == "UNEVALUABLE"
        assert unresolved["heading"] is None
        assert unresolved["delete_string"] is None

        # CONTROL: the same file, a resolving index, must be NON-null.
        # Without this the assertion above cannot distinguish "null because
        # the pin did not resolve" from "null always".
        resolving = archive_pin.build_verdict(
            0, db_path=str(memory_store("mem.db"))
        )
        assert resolving["outcome"] == "ARCHIVED", (
            f"the control did not reach a real save, so it is no longer a "
            f"control and the null above proves nothing: {resolving}"
        )
        assert resolving["delete_string"] is not None, (
            "delete_string is null even on a RESOLVING pin — the null above "
            "proves nothing"
        )

    def test_key_is_present_even_when_the_value_is_null(self, claude_md):
        """`null` and `absent` are different failures for a consumer.

        The contract says "null when the pin does not resolve" — a claim
        about the VALUE that is silent about the KEY. A consumer doing
        `verdict["delete_string"]` must not raise KeyError.
        """
        claude_md(_two_pin_file())
        verdict = archive_pin.build_verdict(99, db_path=None)
        assert "delete_string" in verdict
        assert "claude_md_path" in verdict

    def test_equals_an_independently_computed_extract_pin_block(
        self, claude_md, monkeypatch
    ):
        """A WIRING check, and deliberately named as one.

        BOUND, stated because the stronger reading is tempting: this proves
        the emitted field is the BLOCK — not the heading, not a truncation,
        not a reconstruction. It is NOT a coextensivity proof. Once the
        archiver emits the same string it archived, comparing the two is
        near-tautological; the property that matters is guaranteed by there
        being ONE derivation, not by this assertion.
        """
        content = _two_pin_file()
        claude_md(content)
        block = _expected_block(content, 1)
        monkeypatch.setattr(archive_pin, "_run_memory_cli", _ok_stub(block))

        verdict = archive_pin.build_verdict(1, db_path=None)

        assert verdict["delete_string"] == block
        assert verdict["delete_string"] != verdict["heading"]
        assert len(verdict["delete_string"]) > len("### Second Pin")

    def test_is_verbatim_in_the_file_at_the_emitted_path(
        self, claude_md, monkeypatch
    ):
        """The handle and its target must agree.

        A content handle without a bound target is the defect that put
        `claude_md_path` in the verdict: the archive read one file and the
        removal could edit another. Asserting the handle is verbatim IN the
        emitted path binds the two.
        """
        content = _two_pin_file()
        path = claude_md(content)
        block = _expected_block(content, 0)
        monkeypatch.setattr(archive_pin, "_run_memory_cli", _ok_stub(block))

        verdict = archive_pin.build_verdict(0, db_path=None)

        assert verdict["claude_md_path"] == str(path)
        on_disk = Path(verdict["claude_md_path"]).read_text(encoding="utf-8")
        assert verdict["delete_string"] in on_disk

    def test_retains_its_trailing_blank_line_for_a_non_last_pin(
        self, claude_md, monkeypatch
    ):
        """No-strip: the span is `source[start:end]` exactly.

        SCOPED TO A NON-LAST PIN ON PURPOSE. The last pin's span ends at
        the end of the section, so a blanket "ends with a blank line"
        assertion is FALSE for it. Asserting it universally would pin an
        invariant the code does not have — a guard that fossilizes a wrong
        rule is worse than no guard.

        The trailing blank line is what makes consecutive spans a
        PARTITION rather than an overlap.
        """
        content = _two_pin_file()
        claude_md(content)
        block = _expected_block(content, 0)
        monkeypatch.setattr(archive_pin, "_run_memory_cli", _ok_stub(block))

        verdict = archive_pin.build_verdict(0, db_path=None)

        assert verdict["delete_string"].endswith("\n\n"), (
            "the block was stripped in transit; consecutive spans no longer "
            "partition and the next pin's date comment is orphaned"
        )


class TestDeleteStringUniqueness:
    """A content handle is only usable if it identifies exactly one place.

    BOUND: uniqueness is verified AS OF THE VERDICT. Anything that writes
    CLAUDE.md between the verdict and the Edit re-opens the ambiguity, and
    that window is outside this script's control. No test here closes it.
    """

    def _duplicate_fixture(self, content, block):
        """Place a second verbatim copy of the block OUTSIDE the pinned
        section, which is what a Working Memory projection did before the
        sync was suppressed."""
        return content.replace(
            "## Working Memory", "## Working Memory\n\n" + block, 1
        ) if "## Working Memory" in content else content + "\n" + block

    def test_a_duplicated_block_is_archived_delete_unsafe(
        self, claude_md, monkeypatch
    ):
        """Archive SUCCEEDED, removal refused. A distinct outcome, because
        collapsing it into UNEVALUABLE would inherit the escape hatch and
        hand an ambiguous string back to a human with 'proceed anyway'
        attached."""
        base = _two_pin_file()
        block = _expected_block(base, 0)
        content = self._duplicate_fixture(base, block)

        # NON-VACUITY: the fixture must genuinely discriminate. If this is
        # 1, the test below measures nothing.
        assert content.count(block) == 2, (
            "duplicate fixture does not actually duplicate the block"
        )

        claude_md(content)
        monkeypatch.setattr(archive_pin, "_run_memory_cli", _ok_stub(block))
        verdict = archive_pin.build_verdict(0, db_path=None)

        assert verdict["outcome"] == "ARCHIVED_DELETE_UNSAFE"
        assert verdict["memory_id"] == "a" * 32, (
            "the archive SUCCEEDED — the id must be reported so the curator "
            "knows the content is safe before removing the pin by hand"
        )
        assert verdict["occurrences"] == 2

    def test_exactly_once_is_the_pass_condition(self, claude_md, monkeypatch):
        """`>= 1` would accept the very case the check exists to reject."""
        content = _two_pin_file()
        claude_md(content)
        block = _expected_block(content, 0)
        monkeypatch.setattr(archive_pin, "_run_memory_cli", _ok_stub(block))

        verdict = archive_pin.build_verdict(0, db_path=None)

        assert verdict["outcome"] == "ARCHIVED"
        assert verdict["occurrences"] == 1

    def test_a_vanished_block_is_also_delete_unsafe_not_just_a_duplicate(
        self, claude_md, monkeypatch
    ):
        """ZERO occurrences, reached by a CONCURRENT MODIFICATION.

        The predicate is `occurrences != 1`, so absence routes here too —
        and absence is a different hazard from duplication. A guard written
        as `occurrences > 1` would be FALSE on this verdict while looking
        correct, so the assertion is `!= 1`.

        Reachable without adversarial construction: the curator has
        CLAUDE.md open in an editor that writes during the archive.
        """
        content = _two_pin_file()
        path = claude_md(content)
        block = _expected_block(content, 0)

        def _writer_removes_the_pin(args, **kwargs):
            if args[0] == "save":
                path.write_text(content.replace(block, "", 1), encoding="utf-8")
                return 0, json.dumps(
                    {"ok": True, "result": {"memory_id": "a" * 32}}
                ), ""
            return 0, json.dumps(
                {"ok": True, "result": {"context": block}}
            ), ""

        monkeypatch.setattr(
            archive_pin, "_run_memory_cli", _writer_removes_the_pin
        )
        verdict = archive_pin.build_verdict(0, db_path=None)

        assert verdict["outcome"] == "ARCHIVED_DELETE_UNSAFE"
        assert verdict["occurrences"] == 0
        assert verdict["occurrences"] != 1
        # The two causes must be distinguishable by the curator: telling them
        # "ambiguous" when the block VANISHED sends them hunting a copy that
        # does not exist.
        assert "ambiguous" not in verdict["reason"].lower()

    def test_locations_are_absent_from_the_outcome_that_permits_deletion(
        self, claude_md, monkeypatch
    ):
        """`locations` are character offsets — DIAGNOSTIC, never a handle.

        A positional handle computed now and consumed later is silently
        wrong if the file moved. The strongest mechanical guard is that the
        field is not even PRESENT on ARCHIVED, the one outcome that permits
        the Edit, so it cannot be picked up and used as a delete target.

        BOUND: this cannot stop a consumer misusing `locations` on
        ARCHIVED_DELETE_UNSAFE, where removal is already refused.
        """
        content = _two_pin_file()
        claude_md(content)
        block = _expected_block(content, 0)
        monkeypatch.setattr(archive_pin, "_run_memory_cli", _ok_stub(block))

        verdict = archive_pin.build_verdict(0, db_path=None)

        assert verdict["outcome"] == "ARCHIVED"
        assert "locations" not in verdict

    def test_locations_actually_index_the_occurrences(
        self, claude_md, monkeypatch
    ):
        """Non-vacuity for the diagnostic itself: an offset list that does
        not point at the block would be a confident, wrong diagnostic."""
        base = _two_pin_file()
        block = _expected_block(base, 0)
        content = self._duplicate_fixture(base, block)
        claude_md(content)
        monkeypatch.setattr(archive_pin, "_run_memory_cli", _ok_stub(block))

        verdict = archive_pin.build_verdict(0, db_path=None)

        assert len(verdict["locations"]) == verdict["occurrences"]
        for offset in verdict["locations"]:
            assert content[offset:offset + len(block)] == block


class TestStartEdgeDoesNotOrphanTheDateComment:
    """The START edge is a PRIVILEGE boundary, not a tidiness one.

    WHICH CLASS THIS BELONGS TO — read this before deleting or citing it.

    This guards a WRONG SINGLE DERIVATION. It is NOT a divergence test.

    The divergence class — the command RE-DERIVING the span from prose and
    disagreeing with the archiver — was ELIMINATED by the emitted-handle
    design: the verdict emits `delete_string` and the removal is keyed on
    that content, so there is no second derivation left to disagree. This
    test is NOT evidence that the retired class is still live, and it must
    not be cited as such.

    What the emitted handle does NOT close is a span that is WRONG IN THE
    FIRST PLACE. One derivation cannot disagree with itself, but it can be
    incorrect, and the command then faithfully deletes exactly those wrong
    bytes — coextensivity holds while correctness does not. That residual is
    what this class covers.

    THE HARM. A span starting at the `### ` heading leaves the evicted pin's
    date comment behind. `parse_pins` walks backward to the nearest preceding
    non-blank line, so the orphan attaches to the FOLLOWING pin — and if it
    carried a `pin-size-override`, the retained pin INHERITS an override it
    was never granted. An override grants unlimited size, so an unrelated
    eviction silently bypasses the 1500-char cap on a pin nobody touched.

    RELATION TO test_archive_pin.py:172. That test
    (`block.startswith("  <!-- pinned:")`) goes red on the same regression,
    at the MECHANISM level. This one covers the CONSEQUENCE, in the
    vocabulary of the harm: an unearned override and a bypassed cap. Keep
    both — the mechanism test says WHAT broke, this says WHY IT MATTERS.

    BOUND: proves `delete_string`'s SPAN closes the hazard. Cannot prove an
    LLM honours `delete_string` rather than re-deriving a span of its own.
    """

    OVERRIDE = (
        "<!-- pinned: 2026-01-01, pin-size-override: load-bearing verbatim -->"
    )

    def _fixture(self):
        """Pin A carries an override; pin B has NO date comment of its own,
        so B is the pin that would inherit A's."""
        return (
            "# P\n\n## Pinned Context\n\n"
            f"{self.OVERRIDE}\n"
            "### Alpha Pin\nAlpha body.\n\n"
            "### Beta Pin\nBeta body.\n\n"
            "## Working Memory\n"
        )

    def test_removing_delete_string_does_not_grant_the_next_pin_an_override(
        self, claude_md, monkeypatch
    ):
        """The property, measured on the RESULT rather than the span.

        The counter-arm is what makes this non-vacuous: it applies the
        REJECTED start rule to the same fixture and proves the inheritance
        actually happens, so a pass on the shipped rule is a real
        discrimination and not a fixture that could never have failed.
        """
        content = self._fixture()
        claude_md(content)
        block = _expected_block(content, 0)
        monkeypatch.setattr(archive_pin, "_run_memory_cli", _ok_stub(block))
        verdict = archive_pin.build_verdict(0, db_path=None)

        after = content.replace(verdict["delete_string"], "", 1)
        pins = archive_pin.parse_pins(_pinned_body(after))
        assert [p.heading for p in pins] == ["### Beta Pin"]
        assert pins[0].override_rationale is None, (
            "Beta inherited Alpha's pin-size-override and can now exceed the "
            "size cap without the curator ever granting it"
        )
        assert pins[0].date_comment is None

        # COUNTER-ARM: the rejected rule, on the same fixture.
        heading_start = content.index("### Alpha Pin")
        orphaning = content[heading_start:content.index("### Beta Pin")]
        regressed = content.replace(orphaning, "", 1)
        regressed_pins = archive_pin.parse_pins(_pinned_body(regressed))
        assert regressed_pins[0].override_rationale == (
            "load-bearing verbatim"
        ), (
            "the fixture does not discriminate the two start rules, so the "
            "assertion above proves nothing"
        )


class TestSyncSuppressionSeam:
    """The ALWAYS-RUNS half of the suppression guard.

    BOUND, stated plainly: this asserts THE CALL, not THE EFFECT. It cannot
    tell whether `--no-sync` actually suppressed anything. Its job is to be
    PRESENT when the effect-level test is deselected — a conditionally
    deselected guard is ABSENT, not weak, and absence has no symptom.
    """

    def test_archival_save_passes_no_sync(self, claude_md, monkeypatch):
        content = _two_pin_file()
        claude_md(content)
        block = _expected_block(content, 0)
        seen = []

        def _record(args, **kwargs):
            seen.append(list(args))
            if args[0] == "save":
                return 0, json.dumps(
                    {"ok": True, "result": {"memory_id": "a" * 32}}
                ), ""
            return 0, json.dumps(
                {"ok": True, "result": {"context": block}}
            ), ""

        monkeypatch.setattr(archive_pin, "_run_memory_cli", _record)
        archive_pin.build_verdict(0, db_path=None)

        save_calls = [a for a in seen if a and a[0] == "save"]
        assert len(save_calls) == 1, "expected exactly one save call"
        assert "--no-sync" in save_calls[0], (
            "the archival save would write the record back into CLAUDE.md, "
            "duplicating the very block used as the removal handle"
        )


@pytest.mark.requires_embedding_backend
class TestSyncSuppressionEffect:
    """The EFFECT-level half — three arms, none of which is optional.

    DECLARED EXTERNAL DEPENDENCY: inherits `requires_embedding_backend`,
    so `-m 'not requires_embedding_backend'` DESELECTS this class. That
    shows in the pytest header as `(N deselected)` and NEVER in the skip
    count, so a gate watching skips reports normal while this is gone.
    Gate on `0 deselected`. `TestSyncSuppressionSeam` above is what remains
    when this is absent.

    WHY THREE ARMS. A crashed save and a working suppression produce
    IDENTICAL EVIDENCE: both leave CLAUDE.md untouched. Asserting only that
    the file is unchanged would certify suppression by observing a crash —
    which has already happened once, from a save that died on a str/Path
    mismatch and reported exactly the number the design predicted.
    """

    def test_suppressed_save_leaves_the_file_byte_identical(
        self, claude_md, tmp_path, memory_store
    ):
        content = _two_pin_file()
        path = claude_md(content)
        before = path.read_bytes()

        verdict = archive_pin.build_verdict(
            0, db_path=str(memory_store("mem.db"))
        )

        # ARM 1 — THE EFFECT. Byte-identity, deliberately not
        # `count(block) == 1`: a count is conditioned on the hypothesis it
        # tests (it only sees a writer that duplicates THIS block), while
        # byte-identity fails on ANY writer, including one nobody imagined.
        assert path.read_bytes() == before, (
            "the archival save modified CLAUDE.md"
        )

        # ARM 2 — THE OPERATION SUCCEEDED. Without this, ARM 1 passes on a
        # save that crashed before writing anything.
        assert verdict["outcome"] == "ARCHIVED"
        memory_id = verdict["memory_id"]
        assert memory_id and len(memory_id) == 32
        record = _cli_get(memory_id, str(memory_store("mem.db")))
        assert verdict["delete_string"] in record["context"], (
            "the record did not persist — ARM 1 is satisfied by failure"
        )

    def test_unsuppressed_control_proves_the_harness_can_see_a_write(
        self, claude_md, tmp_path, memory_store
    ):
        """ARM 3 — the negative control.

        Without it, arms 1 and 2 can both pass against a harness that is
        blind to writes in BOTH directions, and the suite would report
        suppression working in a world where nothing ever writes.

        This test FAILS LOUDLY if the sync stops writing for an unrelated
        reason — at which point arm 1 has stopped measuring suppression and
        this file must be revisited.
        """
        content = _two_pin_file()
        path = claude_md(content)
        block = _expected_block(content, 0)
        before = path.read_bytes()

        payload = json.dumps(
            archive_pin._build_record(block, "First Pin")
        )
        rc, stdout, stderr = archive_pin._run_memory_cli(
            ["save", "--stdin"],                      # NO --no-sync
            db_path=str(memory_store("mem.db")),
            stdin_data=payload,
            cwd=archive_pin.project_dir_for(path),
        )
        assert rc == 0, f"unsuppressed save failed: {stderr[:300]}"

        assert path.read_bytes() != before, (
            "an UNSUPPRESSED save left CLAUDE.md unchanged — the harness "
            "cannot detect a write at all, so the suppression assertions "
            "prove nothing"
        )


def _cli_get(memory_id, db_path):
    """Fetch a record via the real CLI, streams kept separate."""
    cli = (
        Path(archive_pin.__file__).resolve().parent.parent
        / "skills" / "pact-memory" / "scripts" / "cli.py"
    )
    # THIS CHILD RUNS `get`, NOT `save`, AND IT STILL LOADS THE MODEL —
    # measured at 8 attempts from this one helper. `_ensure_ready()` runs on
    # every verb and calls the embedding catch-up, which loads the model
    # whenever the store holds an unembedded row. So "only saves download" is
    # false, and binding the cache env here is not belt-and-braces.
    proc = subprocess.run(
        [sys.executable, str(cli), "get", memory_id, "--db-path", db_path],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, **hf_cache_env()},
    )
    assert proc.returncode == 0, f"get failed: {proc.stderr[:400]}"
    return json.loads(proc.stdout)["result"]
