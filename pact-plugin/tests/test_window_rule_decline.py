"""Arms for the DECLINE path of the three-step write-window rule.

THE MECHANISM. `_resolve_write_scope` picks the window a section write may
search, in three steps. STEP 1 uses the memory marker pair. STEP 2 derives the
window from the session-block end to the managed end. STEP 3 DECLINES, and the
two sync writers turn that decline into `SyncResult.NO_WINDOW`.

WHY THESE ARMS ARE HERE. The decline shipped with no arm. The new result
reason, the two sync mappings and step 3 itself were all green with none of
them exercised, so nothing reddened if the decline broke.

HOW EACH ARM IS SHOWN TO REACH THE DECLINE, and not a neighbour. A document
that declines is malformed in more than one way at once, so a red alone does
not say which cause fired. The answer is a ONE-AXIS FAMILY: one document that
resolves at step 1, the same document less ONLY the memory marker pair, and
that one less ONLY the session block. The three give three different answers,
so the family reads the STEP SELECTION rather than a malformation.

ASSERTION ORDER IS LOAD-BEARING IN EACH ARM BELOW. More than one leg reddens
when the decline breaks, and the leg asserted first names the defect. The
coarse fact goes first, the finer discrimination second.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# The one-axis document family
# ---------------------------------------------------------------------------

def _family():
    """Build the three documents, each differing from the last by ONE removal.

    Returns (step1_doc, step2_doc, step3_doc, memory_pair_text, session_text).

    THE LITERALS COME FROM THE MODULE UNDER TEST, never spelled again here. A
    twin literal in a test goes stale without a gate, which is the shape this
    branch keeps removing.
    """
    from scripts.working_memory import (
        MEMORY_END_MARKER,
        MEMORY_START_MARKER,
        _MANAGED_END_MARKER,
        _MANAGED_START_MARKER,
        _SESSION_END_MARKER,
    )

    session_text = (
        "<!-- SESSION_START -->\n"
        "- Resume: `claude --resume abc`\n"
        f"{_SESSION_END_MARKER}\n"
    )
    memory_pair_text = (
        f"{MEMORY_START_MARKER}\n"
        "## Working Memory\n"
        f"{MEMORY_END_MARKER}\n"
    )

    step1 = (
        "# Project\n"
        "\n"
        f"{_MANAGED_START_MARKER}\n"
        "# PACT Framework and Managed Project Memory\n"
        "\n"
        f"{session_text}"
        "\n"
        f"{memory_pair_text}"
        "\n"
        f"{_MANAGED_END_MARKER}\n"
    )
    # ONE REMOVAL: the memory marker pair. Nothing else changes.
    step2 = step1.replace(memory_pair_text, "")
    # ONE MORE REMOVAL: the session block. Nothing else changes.
    step3 = step2.replace(session_text, "")
    return step1, step2, step3, memory_pair_text, session_text


class TestTheWindowFamilySelectsThreeDifferentSteps:
    """Step 3 itself, proven to be step 3 and not a malformed document."""

    def test_the_family_gives_step_1_step_2_and_a_decline(self):
        """The three documents select three different windows.

        WHAT THIS CATCHES IF STEP 3 IS DELETED: the third row. With the decline
        removed the resolver falls through to whatever the caller had before,
        so `step3` stops returning `None` and this arm reddens on that row.

        WHAT IT STOPS CATCHING: nothing about steps 1 and 2. Rows one and two
        assert the OTHER branches, and they are here as the control that makes
        row three mean something: a family that answered the same way at each
        row would separate no states at all.
        """
        from scripts.working_memory import (
            MEMORY_START_MARKER,
            _SESSION_END_MARKER,
            _resolve_write_scope,
            parse,
        )

        step1, step2, step3, memory_pair_text, session_text = _family()

        # THE AXIS IS ONE, AND THIS IS MEASURED RATHER THAN ASSERTED. Each
        # neighbour differs from the last by exactly the named removal.
        assert memory_pair_text in step1 and memory_pair_text not in step2
        assert step1.replace(memory_pair_text, "") == step2
        assert session_text in step2 and session_text not in step3
        assert step2.replace(session_text, "") == step3

        def window_rows(doc):
            """The contents of the rows the resolver picks, or None."""
            parsed = parse(doc)
            scope = _resolve_write_scope(parsed)
            if scope is None:
                return None
            first, last = scope
            return [line.content for line in parsed.lines[first:last + 1]]

        w1 = window_rows(step1)
        w2 = window_rows(step2)
        w3 = window_rows(step3)

        # COARSE FIRST: the decline is the property this file exists for.
        assert w3 is None, (
            "a document with neither a memory marker pair nor a session block "
            "resolved a window instead of declining"
        )
        # THEN THE CONTROLS. Without these the arm above passes for a resolver
        # that declines on everything, which is a different defect.
        assert w1 is not None, "the memory marker pair no longer resolves a window"
        assert w2 is not None, "the session-block window no longer resolves"

        # 🔴 EACH CONTROL ROW ASSERTS THE IDENTITY OF ITS WINDOW, NOT MERELY
        # THAT THE TWO DIFFER. A `w1 != w2` check is satisfied when BOTH rows
        # took step 2, because the two documents differ in text, so it does
        # not show that step 1 ran at all. MEASURED: this arm survived a
        # mutant that made step 1 unavailable, until these two lines existed.
        # The step-1 window IS the memory region, so the start marker bounds
        # it from outside. With step 1 unavailable this document falls to
        # step 2, whose window opens above the pair and CONTAINS that marker.
        assert MEMORY_START_MARKER not in w1, (
            "the step-1 window contains the memory start marker, so it is the "
            "session-block window and step 1 did not run"
        )
        assert "## Working Memory" in w1, (
            "the step-1 window does not hold the memory section it should be"
        )
        # The step-2 window opens BELOW the session block, so the end marker
        # of that block bounds it from outside.
        assert _SESSION_END_MARKER not in w2, (
            "the step-2 window contains the session-end marker, so it is the "
            "wide region rather than the window below the session block"
        )


# ---------------------------------------------------------------------------
# The two sync mappings, and the reason each one carries
# ---------------------------------------------------------------------------

def _declining_project(tmp_path, monkeypatch):
    """Write the declining document to a CLAUDE.md under a fresh root.

    THE PROJECT DIR IS DECLARED, AND WITHOUT IT THESE ARMS MEASURE THE WRONG
    THING. The write path refuses a target outside the project containment
    boundary, and that refusal produces `failed` BEFORE the window rule is
    reached. MEASURED: with the variable unset each arm below returned
    `failed`, which is a non-write for a cause that has nothing to do with the
    decline. An arm asserting only `it did not write` passes in that state.
    """
    _, _, step3, _, _ = _family()
    root = tmp_path / "declined-project"
    root.mkdir()
    (root / "CLAUDE.md").write_text(step3, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


class TestTheSyncMappingsCarryTheNewReason:
    """Both None-to-reason paths, one arm each, and neither shares a killer."""

    def test_the_working_memory_sync_declines_with_the_new_reason(
        self, tmp_path, monkeypatch
    ):
        """`sync_to_claude_md` maps the decline to `NO_WINDOW`.

        WHAT THIS CATCHES IF STEP 3 IS DELETED: the whole arm, because no
        decline reaches this mapping at all.

        WHAT IT STOPS CATCHING: the SIBLING mapping below. A change at this
        call site alone leaves the sibling arm green, which is what makes the
        two arms different rather than one arm written twice.
        """
        from scripts.working_memory import SyncResult, sync_to_claude_md

        root = _declining_project(tmp_path, monkeypatch)
        result = sync_to_claude_md(
            {"context": "c", "goal": "g"}, None, "mem-decline-1",
            claude_md_root=root,
        )

        # COARSE FIRST: it did not write.
        assert bool(result) is False, (
            f"the write was reported as landed on a declining document: {result!r}"
        )
        # THEN THE DISCRIMINATION, which is the point of a new reason.
        assert result.reason == SyncResult.NO_WINDOW, (
            f"the decline carried {result.reason!r} rather than the reason that "
            f"names this class of document"
        )
        assert result.reason != SyncResult.REFUSED, (
            "the decline collapsed into the pre-existing refusal reason, so a "
            "reader can no longer tell the two causes apart"
        )

    def test_the_retrieved_sync_declines_with_the_new_reason(
        self, tmp_path, monkeypatch
    ):
        """`sync_retrieved_to_claude_md` maps the decline to `NO_WINDOW`.

        WHAT THIS CATCHES IF STEP 3 IS DELETED: the whole arm, as above.

        WHAT IT STOPS CATCHING: the sibling mapping. The two call sites are
        separate lines and a repair to one does not reach the other.

        ITS VALUE IS NOT USER-VISIBLE TODAY, AND THAT IS WHY THE ARM IS HERE.
        The only caller of this function discards its return value, so this
        reason reaches no status field and no log line. An arm at the function
        is the only thing that holds the mapping in place.
        """
        from scripts.working_memory import SyncResult, sync_retrieved_to_claude_md

        root = _declining_project(tmp_path, monkeypatch)
        result = sync_retrieved_to_claude_md(
            [{"id": "m1", "context": "c", "goal": "g"}], "a query", None, ["m1"],
            claude_md_root=root,
        )

        assert bool(result) is False, (
            f"the write was reported as landed on a declining document: {result!r}"
        )
        assert result.reason == SyncResult.NO_WINDOW, (
            f"the decline carried {result.reason!r} rather than the reason that "
            f"names this class of document"
        )
        assert result.reason != SyncResult.REFUSED, (
            "the decline collapsed into the pre-existing refusal reason, so a "
            "reader can no longer tell the two causes apart"
        )


# ---------------------------------------------------------------------------
# The command-line status field, driven by a RUN
# ---------------------------------------------------------------------------

class TestTheDeclineReachesTheStatusField:
    """The transport from the sync result to the field a caller reads."""

    def test_a_declining_document_puts_the_new_reason_in_sync_status(
        self, tmp_path, capsys, monkeypatch
    ):
        """A save through the command line reports the decline to its caller.

        THIS ARM EXISTS TO REPLACE A READ WITH A RUN. That the reason reaches
        the status field was asserted by reading the code. Three separate
        pieces of plumbing sit between the sync result and the field, and a
        read cannot show that all three carry the value.

        WHAT THIS CATCHES IF STEP 3 IS DELETED: the whole arm.

        WHAT IT STOPS CATCHING: nothing the two mapping arms cover. Its own
        subject is the TRANSPORT, so it is the only arm that reddens if the
        mapping is correct and the field stops carrying the value.

        WHAT IS REAL AND WHAT IS STUBBED, stated so the word `end to end` is
        checkable. REAL: the argument parse, the memory-store resolution, the
        sync call, the reason assignment and the envelope build. STUBBED: the
        embedding generator, because it spins a backend that reads a model
        from a cache and downloads it when the cache is cold.
        """
        # 🔴 THE ISOLATION PROOF, AND IT RUNS BEFORE ANY WRITE. The harness
        # redirects the store, and that redirect is a PREMISE. An arm that
        # writes first and trusts the premise is the shape that put a test
        # suite on the live store once before.
        from scripts import config

        resolved_store = config.resolve_db_path()
        real_home = Path(os.path.expanduser("~")).resolve()

        assert str(resolved_store).startswith(str(tmp_path)), (
            f"the store this arm would write to is {resolved_store}, which is "
            f"not under this test's tmp_path {tmp_path}. REFUSING to run."
        )
        assert real_home not in resolved_store.resolve().parents, (
            f"the store this arm would write to is {resolved_store}, which is "
            f"under the real home {real_home}. REFUSING to run."
        )

        from scripts import cli

        root = _declining_project(tmp_path, monkeypatch)
        payload = json.dumps({
            "context": "decline arm",
            "goal": "drive the decline to the status field",
        })

        with patch("scripts.memory_api.generate_embedding", return_value=[0.0] * 8):
            with pytest.raises(SystemExit) as exit_info:
                cli.main([
                    "save", payload, "--claude-md-root", str(root),
                ])

        assert exit_info.value.code == 0, "the save command did not succeed"
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["ok"] is True

        result = envelope["result"]
        # PRESENT, and asserted BEFORE the value. An absent field satisfies
        # `!= 'refused'` on its own, so a bare difference check would pass on
        # a save that reported nothing at all.
        assert "sync_status" in result, (
            f"the save envelope carries no sync_status field at all: {result}"
        )
        assert result["sync_status"] == "no_window", (
            f"the caller-visible status is {result['sync_status']!r}, so the "
            f"decline did not reach the field a caller reads"
        )
