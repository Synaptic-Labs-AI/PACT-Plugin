"""THE MARKER CLEAR, DRIVEN THROUGH A STATE ORACLE OVER THE MARKER FILE.

`track_files.clear_pin_staleness_marker_if_resolved` is what the shipped deny
text PROMISES a user: archive the stale pins and the gate clears inside the
same session. If it stops working, a user who OBEYS the refusal stays denied
on their own Pinned Context for the rest of the session. That is the cardinal
over-block.

WHY EVERY ARM HERE READS THE MARKER FILE AND NOT THE RETURN VALUE. Each exit
of that function returns None, and the body ends in a bare
`except Exception: return`. So an arm that calls it and asserts a quiet RESULT
passes when the function-local imports fail, when the session directory does
not resolve, and when the marker is not there. Three broken states and one
green arm. THE RETURN VALUE CARRIES NO INFORMATION, so the oracle is the
marker file and, where the marker cannot separate two states, a recorder on
the staleness signal.

THE SEAM, STATED BECAUSE PICKING THE OTHER ONE GIVES A GREEN ARM AND NO
SIGNAL. The function imports `check_pinned_block_signal` and `get_session_dir`
INSIDE its body, at call time. So the patch must land on the SOURCE modules,
`staleness` and `shared.pact_context`. There is no `track_files` attribute to
patch, and a patch aimed at one would not bind, would raise nothing the caller
sees, and would be swallowed by the bare except.

THE THREE CASES AND THE ONE MUTANT EACH ANSWERS TO. The three are separated on
purpose: each dies under a mutant that leaves the other two green, so no arm
here is riding another arm's decision.

  (a) the signal STILL reports stale pins, so the marker STAYS.
      Dies when the signal re-read is removed. THIS IS THE LOAD-BEARING ONE:
      removing the re-read is WORSE than removing the whole function, because
      it drops the marker while stale pins remain, which makes the trap vanish
      by deleting the evidence of it. Neutering the whole function does NOT
      redden this arm, because a function that does nothing also leaves the
      marker in place. So (a) is the only guard against that direction.
  (b) the signal has CLEARED on an archive Bash call, so the marker is REMOVED.
      Dies when the unlink is neutered, and dies when the whole function is
      neutered.
  (c) an unrelated Bash command touches nothing.
      Dies when the archive-token gate is removed.

CASE (c) CANNOT USE THE MARKER ALONE AS ITS ORACLE. "The marker is still
there" is also what a totally broken function produces, so that assertion on
its own is the quiet-return trap in a new place. (c) therefore records whether
the staleness signal was CONSULTED, and asserts the opposite answer for the
two commands under one fixture: an ordinary command must not reach the
re-read, and the archive command must reach it.
"""

import pytest

# The archive command shape the deny text tells a user to run. The token the
# Bash leg tests for is `archive_pin.py`, so this string must carry it.
ARCHIVE_COMMAND = "python3 archive_pin.py --index 1"

# A shell call with nothing to do with the archive. It must cost one substring
# test and reach no file.
UNRELATED_COMMAND = "ls -la"

STALE_SIGNAL = "2 stale pins remain in Pinned Context"


def _patch_the_resolver(monkeypatch, staleness, replacement):
    """Rebind EVERY module name that points at the managed-file resolver.

    `staleness` binds the resolver under TWO names: the canonical
    `get_project_claude_md_path`, and an alias assigned at import time,
    `_get_project_claude_md_path = get_project_claude_md_path`. THE TWO NAMES
    HOLD ONE OBJECT, AND REBINDING ONE LEAVES THE OTHER POINTING AT THE
    ORIGINAL.

    THIS COST ME A FALSE RESULT AND IT IS RECORDED SO IT IS NOT REPEATED. The
    fixtures patched the ALIAS. The conservative repair imports the CANONICAL
    name, so it took the real resolver, walked to the real managed file, and
    the arm reported the repair as absent while it was present. A pending arm
    that cannot see its own subject land is worse than no arm: it holds the
    team at a state the tree left.

    A caller takes whichever name it imports, so a fixture must cover each of
    them. `raising=True` is the default and it is wanted: if a name goes, this
    raises rather than patch nothing in silence.
    """
    for name in ("get_project_claude_md_path", "_get_project_claude_md_path",
                 "resolve_session_claude_md_path"):
        monkeypatch.setattr(staleness, name, replacement)


class _SignalRecorder:
    """A stand-in for `check_pinned_block_signal` that records its calls.

    The recorder is the POSITIVE CONTROL for the two cases whose expected
    marker state is "unchanged". Without it, "the marker is still there" is
    satisfied by a function that never ran at all.
    """

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.answer


@pytest.fixture
def marker_bed(tmp_path, monkeypatch):
    """A session directory holding a marker, with the two seams redirected.

    Returns a helper that runs the function under one signal answer and
    reports the marker state and the recorder afterwards.
    """
    import shared.pact_context
    import staleness
    # TAKE THE NAME FROM THE MODULE THE CODE UNDER TEST READS, which is
    # `shared.constants`. `pin_staleness_gate` RE-EXPORTS the same name, and a
    # fixture that read the re-export would build its marker at one spelling
    # while the code looked for another if the two ever diverged. Cases (a) and
    # (c) expect the marker to SURVIVE, so a divergence would leave them green
    # for the wrong reason. The agreement arm below holds the two spellings
    # together, and NEVER type this filename as a literal.
    from shared.constants import PIN_STALENESS_MARKER_NAME

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    marker = session_dir / PIN_STALENESS_MARKER_NAME

    monkeypatch.setattr(
        shared.pact_context, "get_session_dir",
        lambda *a, **k: str(session_dir),
    )

    # POINT THE MANAGED-FILE RESOLVER AT A READABLE FILE, even though these
    # three cases stub the signal and do not read it today. A repair that
    # makes the clear CONSERVATIVE will add a read of this file, and a
    # fixture that leaves the resolver unpatched would then take whatever the
    # host machine resolves. Case (b) requires a REMOVAL, so an unresolvable
    # or unreadable target would fail it FOR A FIXTURE REASON rather than for
    # a behaviour reason, and the failure would read as a defect in the code.
    readable = tmp_path / "CLAUDE.md"
    readable.write_text("# Project Memory\n\n## Working Memory\n")
    _patch_the_resolver(monkeypatch, staleness, lambda *a, **k: readable)

    def drive(tool_name, tool_input, signal_answer):
        """Re-arm the marker, run the function, report the state after."""
        import track_files

        marker.write_text("")
        recorder = _SignalRecorder(signal_answer)
        monkeypatch.setattr(
            staleness, "check_pinned_block_signal", recorder)
        track_files.clear_pin_staleness_marker_if_resolved(
            tool_name, tool_input)
        return marker.exists(), recorder

    return drive


class TestTheMarkerSurvivesWhileThePinsAreStale:
    """CASE (a). The re-read is the whole mechanism, and this is its only arm."""

    def test_an_archive_call_leaves_the_marker_while_the_signal_stays_stale(
        self, marker_bed
    ):
        """An archive run is a REASON TO LOOK, never proof the condition cleared.

        The command carries the archive token, so the Bash leg admits it and
        the function reaches the re-read. The re-read reports stale pins, so
        the marker must SURVIVE.

        NON-VACUITY: the recorder proves the re-read was actually consulted.
        Without that assertion this arm would pass for a function that
        returned at its first line, and the marker would be untouched for a
        reason that has nothing to do with the guard under test.
        """
        present, recorder = marker_bed(
            "Bash", {"command": ARCHIVE_COMMAND}, STALE_SIGNAL)

        assert recorder.calls == 1, (
            "the staleness signal was consulted "
            f"{recorder.calls} time(s), and the archive command must reach it "
            "exactly once. A count of zero means the function returned before "
            "the re-read, so the marker state below proves nothing about the "
            "re-read"
        )
        assert present, (
            "THE MARKER WAS REMOVED WHILE THE SIGNAL REPORTS STALE PINS. "
            "This is the direction that is WORSE than the mechanism being "
            "absent: the trap disappears by deletion of the evidence of it, "
            "while the stale pins remain. The re-read of the staleness signal "
            "before the unlink is what stops this, and it is gone or bypassed"
        )


class TestTheMarkerGoesWhenTheConditionHasCleared:
    """CASE (b). The promise the shipped deny text makes to a user."""

    def test_an_archive_call_removes_the_marker_once_the_signal_clears(
        self, marker_bed
    ):
        """The archive route is `Bash`, and it is the route the deny text names.

        The archive command runs a script that writes the file directly, so it
        emits no `Edit` and no `Write` event. A clear bound to the edit route
        alone would miss the very route the message recommends.

        NON-VACUITY: the removal of a file is a POSITIVE state change, so this
        assertion cannot be satisfied by a function that did nothing.
        """
        present, recorder = marker_bed(
            "Bash", {"command": ARCHIVE_COMMAND}, None)

        assert recorder.calls == 1, (
            "the archive command must reach the staleness re-read exactly "
            f"once, and it was consulted {recorder.calls} time(s)"
        )
        assert not present, (
            "THE MARKER SURVIVED AN ARCHIVE RUN THAT CLEARED THE CONDITION. "
            "A user who obeys the refusal, archives the stale pins, and then "
            "edits again stays DENIED for the rest of the session. That is "
            "the cardinal over-block this mechanism exists to remove"
        )


class TestAnUnrelatedShellCallIsNotAnArchive:
    """CASE (c). The token gate, held by a two-command discrimination."""

    def test_an_ordinary_command_does_not_reach_the_clear_and_an_archive_does(
        self, marker_bed
    ):
        """One fixture, two commands, opposite answers.

        THE MARKER ALONE CANNOT HOLD THIS CASE. Under an ordinary command the
        marker is untouched, which is also what a broken function produces. So
        the oracle is the RECORDER: an ordinary command must not reach the
        staleness re-read at all, and the archive command under the identical
        fixture must reach it. The pair is what separates "the token gate
        refused this command" from "nothing ran".

        The signal answers None on both halves on purpose. If the token gate
        is removed, the ordinary command proceeds to the re-read, finds the
        condition clear, and unlinks a marker that no archive earned.
        """
        present_ordinary, recorder_ordinary = marker_bed(
            "Bash", {"command": UNRELATED_COMMAND}, None)
        present_archive, recorder_archive = marker_bed(
            "Bash", {"command": ARCHIVE_COMMAND}, None)

        assert recorder_archive.calls == 1, (
            "CONTROL HALF FAILED: the archive command did not reach the "
            "staleness re-read, so this fixture cannot tell a refused command "
            "from a function that never runs. Repair this half before you "
            "read the assertion below"
        )
        assert not present_archive, (
            "CONTROL HALF FAILED: the archive command left the marker in "
            "place under a cleared signal"
        )

        assert recorder_ordinary.calls == 0, (
            f"an ordinary shell call reached the staleness re-read "
            f"{recorder_ordinary.calls} time(s). The `Bash` leg must test the "
            "command string FIRST, so an ordinary shell call costs one "
            "substring test and no file read. The archive-token gate is gone"
        )
        assert present_ordinary, (
            "AN ORDINARY SHELL CALL CLEARED THE PIN STALENESS MARKER. "
            f"The command was {UNRELATED_COMMAND!r}, which archives nothing. "
            "The gate that admits only the archive script has been removed, "
            "so any shell call now retires the marker"
        )


class TestTheMarkerNameHasOneSpelling:
    """THE DEFINITION AND THE RE-EXPORT MUST STAY THE SAME STRING.

    `PIN_STALENESS_MARKER_NAME` is defined in `shared/constants.py`. Three
    frames read it: this gate, the PostToolUse clear, and SessionStart.
    `pin_staleness_gate` RE-EXPORTS it, and other modules and arms take it from
    there, so two spellings of one filename are now reachable.

    WHY THIS ARM EXISTS AND WHY IT IS NOT DECORATION. A divergence between the
    two is SILENT in the worst direction. A fixture that builds its marker at
    one spelling, against code that looks for the other, gets the QUIET exit:
    the code finds no marker and returns, so an arm expecting the marker to
    SURVIVE passes while measuring nothing. Two of the three cases in this file
    expect exactly that, so a divergence would leave them green.

    A CONTROL FOR THE SAME HAZARD IS BUILT INTO CASE (b) AS WELL, and the two
    are complementary. Case (b) requires the shipped code to REMOVE a marker
    this file created, which no name mismatch can satisfy, so it fails loudly
    where cases (a) and (c) would fail silently.
    """

    def test_the_gate_reexport_agrees_with_the_definition(self):
        """One filename, one string, read through the two routes that exist."""
        from shared.constants import PIN_STALENESS_MARKER_NAME as defined
        from pin_staleness_gate import PIN_STALENESS_MARKER_NAME as reexported

        assert defined == reexported, (
            f"THE MARKER FILENAME HAS TWO SPELLINGS. `shared.constants` "
            f"defines {defined!r} and `pin_staleness_gate` re-exports "
            f"{reexported!r}. Any reader of the second builds or looks for a "
            f"file the readers of the first do not touch. The failure is "
            f"SILENT wherever the expected outcome is that the marker stays "
            f"where it is"
        )
        assert defined, "the marker filename is empty, so every path resolves to the session directory itself"


@pytest.fixture
def real_signal_bed(tmp_path, monkeypatch):
    """A bed that drives the REAL `check_pinned_block_signal`.

    THE OTHER FIXTURE IN THIS FILE STUBS THE SIGNAL, and that is correct for
    the three readable cases, because those ask what the clear does with a
    given answer. THIS ONE ASKS WHAT THE SIGNAL ITSELF PRODUCES from a broken
    managed file, so a stub here would make the arm assert its own input.

    Returns a helper taking the marker state and the managed-file state.
    """
    import shared.pact_context
    import staleness
    from shared.constants import PIN_STALENESS_MARKER_NAME

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    marker = session_dir / PIN_STALENESS_MARKER_NAME

    monkeypatch.setattr(
        shared.pact_context, "get_session_dir",
        lambda *a, **k: str(session_dir),
    )

    def drive(*, marker_present, readable):
        """Run one token-bearing Bash call and report the marker state after.

        A DIRECTORY IS THE UNREADABLE TARGET, and that is deliberate.
        `Path.read_text` on a directory raises `IsADirectoryError`, which is an
        `OSError`, on each platform and for each user. A file at mode 000 is
        the obvious alternative and it is NOT deterministic: a run as root
        reads it, so the arm would pass on a workstation and go quiet in a
        container.
        """
        import track_files

        if marker_present:
            marker.write_text("")
        elif marker.exists():
            marker.unlink()

        target = tmp_path / f"claude_md_{marker_present}_{readable}"
        if readable:
            target.write_text("# Project Memory\n\n## Working Memory\n")
        else:
            target.mkdir()

        _patch_the_resolver(monkeypatch, staleness, lambda *a, **k: target)
        track_files.clear_pin_staleness_marker_if_resolved(
            "Bash", {"command": ARCHIVE_COMMAND})
        return marker.exists()

    return drive


class TestTheClearIsConservativeWhenItCannotTell:
    """CANNOT-TELL MUST NOT ROUTE TO CLEARED.

    `check_pinned_block_signal` returns None for a cleared condition AND for
    an unreadable managed file, because its own contract is fail-open: it
    serves SessionStart, where None means DO NOT BLOCK and ambiguity is safe.
    THE CLEAR READS THE SAME None AND ACTS ON IT IN THE OPPOSITE DIRECTION,
    where None means DROP THE MARKER and disarm the gate. One value, two
    callers, opposite safety.

    WHY THE DROP IS WORSE THAN IT LOOKS, and this is the half that was
    inferred before it was measured: THE DROP PERSISTS FOR THE SESSION. The
    marker is written at SessionStart, and no path re-creates it. So a single
    transient read fault disarms the gate until the next session, and the gate
    then allows an add-shaped edit while the stale pins remain. The arm below
    holds that persistence, and it is what turns a momentary fault into a
    session-long hole.
    """

    def test_an_unreadable_managed_file_leaves_the_marker_in_place(
        self, real_signal_bed
    ):
        """The conservative route, and the one a later refactor would undo.

        THIS ARM WAS A STRICT XFAIL WHILE THE REPAIR WAS PENDING, and the
        marker came off when the repair landed and the test XPASSED. It is a
        live arm now. Do not re-add the marker: a failure here reports that
        the conservative routing went, rather than that it has not arrived.
        """
        present = real_signal_bed(marker_present=True, readable=False)

        assert present, (
            "THE MARKER DROPPED WHILE THE MANAGED FILE WAS UNREADABLE. "
            "The clear cannot tell whether the stale pins went, so it must "
            "leave the marker alone. A drop here disarms the gate for the "
            "REST OF THE SESSION, because nothing re-creates the marker "
            "before the next SessionStart"
        )

    def test_the_clear_is_one_way_and_no_later_call_restores_the_marker(
        self, real_signal_bed
    ):
        """THE PERSISTENCE, held by a pair rather than by one assertion.

        NON-VACUITY. "The marker is absent afterwards" is what a function that
        does nothing produces, so that assertion alone is the quiet-return
        trap. The CONTROL HALF runs the identical call with the marker
        PRESENT and requires a REMOVAL, which no broken path can fake. The
        pair then says: the clear acts, and its action runs one way only.

        THIS IS WHY THE CONSERVATIVE ROUTE MATTERS. A drop is not a state the
        next call repairs.
        """
        control = real_signal_bed(marker_present=True, readable=True)
        assert not control, (
            "CONTROL HALF FAILED: a readable call with a cleared condition "
            "left the marker in place, so this fixture does not reach the "
            "clear and the assertion below proves nothing"
        )

        restored = real_signal_bed(marker_present=False, readable=True)
        assert not restored, (
            "THE CLEAR RE-CREATED THE MARKER. This mechanism removes and "
            "never writes. If it gains a write path, the persistence "
            "argument for conservative routing changes and the reasoning at "
            "this class must be re-read"
        )


# A path that the managed-file predicate admits, and one it refuses. The two
# are sentinels rather than files on disk: route 1 tests the PATH through
# `match_project_claude_md` before it opens anything.
MANAGED_EDIT_PATH = "/tmp/project/.claude/CLAUDE.md"
UNRELATED_EDIT_PATH = "/tmp/project/docs/notes.txt"


@pytest.fixture
def edit_route_bed(tmp_path, monkeypatch):
    """A bed that drives ROUTE 1, the `Edit` and `Write` leg of the clear.

    THE FOUR ARMS ABOVE DRIVE ROUTE 2, THE `Bash` LEG. Route 1 is the other
    member of the trigger union, and it carries its own gate:
    `match_project_claude_md` on the edited path. That gate has no arm, so a
    later edit that breaks path resolution, or that returns early from this
    leg, ships green.

    WHICH NAME THIS PATCHES, AND WHY ONE NAME IS SUFFICIENT HERE. Route 1 runs
    `from shared import match_project_claude_md` inside the function, so it
    reads the name in the `shared` package namespace at call time. A PATCH
    BINDS TO A NAME RATHER THAN TO A FUNCTION, so the correct name is the one
    THE CALLER IMPORTS. An AST walk of module-level `name = name` bindings
    across `hooks/` finds ONE alias in the tree, `_get_project_claude_md_path`
    in `staleness`, and NONE for this predicate. The walk reports that known
    alias, which is the control that the walk is live. If a second binding
    appears later, this fixture wants the same treatment
    `_patch_the_resolver` gives the other resolver.

    THE PREDICATE IS A FUNCTION OF THE PATH rather than a constant, because
    the two cases below differ ONLY in the path they supply. A stub that
    answered the same way for each path would make the refusal case pass for
    a fixture reason.
    """
    import shared
    import shared.pact_context
    import staleness
    from shared.constants import PIN_STALENESS_MARKER_NAME

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    marker = session_dir / PIN_STALENESS_MARKER_NAME

    monkeypatch.setattr(
        shared.pact_context, "get_session_dir",
        lambda *a, **k: str(session_dir),
    )

    # The managed file the clear reads after route 1 admits the edit. It must
    # be READABLE, because the conservative repair returns on a read fault and
    # case (d) requires a REMOVAL. An unreadable target would fail case (d)
    # for a fixture reason rather than for a behaviour reason.
    readable = tmp_path / "CLAUDE.md"
    readable.write_text("# Project Memory\n\n## Working Memory\n")
    _patch_the_resolver(monkeypatch, staleness, lambda *a, **k: readable)

    def _predicate(file_path, *a, **k):
        return readable if file_path == MANAGED_EDIT_PATH else None

    monkeypatch.setattr(shared, "match_project_claude_md", _predicate)

    def drive(tool_name, file_path, signal_answer):
        """Re-arm the marker, run one tool call, report the state after."""
        import track_files

        marker.write_text("")
        recorder = _SignalRecorder(signal_answer)
        monkeypatch.setattr(staleness, "check_pinned_block_signal", recorder)
        track_files.clear_pin_staleness_marker_if_resolved(
            tool_name, {"file_path": file_path})
        return marker.exists(), recorder

    return drive


class TestTheEditRouteReachesTheClear:
    """CASE (d). ROUTE 1 CARRIES A HAND EDIT TO THE MANAGED FILE.

    A user who repairs the pinned region by hand arrives as `Edit` or `Write`
    rather than as `Bash`. If route 1 declines, that user stays denied for the
    rest of the session while the condition has cleared, which is the cardinal
    over-block this mechanism exists to remove.

    NON-VACUITY: the removal of a file is a POSITIVE state change, so this
    assertion cannot be satisfied by a function that did nothing.
    """

    @pytest.mark.parametrize("tool_name", ["Edit", "Write"])
    def test_an_edit_to_the_managed_file_removes_the_marker_once_it_clears(
        self, edit_route_bed, tool_name
    ):
        """The two tools take one leg, so each one drives the same assertion."""
        present, recorder = edit_route_bed(
            tool_name, MANAGED_EDIT_PATH, None)

        assert recorder.calls == 1, (
            f"a {tool_name} of the managed file reached the staleness re-read "
            f"{recorder.calls} time(s), and it must reach it one time. A count "
            "of zero means route 1 returned before the re-read, so the marker "
            "state below says nothing about the route"
        )
        assert not present, (
            f"THE MARKER SURVIVED A {tool_name} OF THE MANAGED FILE THAT "
            "CLEARED THE CONDITION. Route 1 is one of the two members of the "
            "trigger union. A clear bound to the Bash leg alone leaves a user "
            "who repairs the pins by hand denied for the rest of the session"
        )


class TestTheEditRouteRefusesAnUnrelatedFile:
    """CASE (e). THE PATH GATE ON ROUTE 1, HELD BY A TWO-PATH DISCRIMINATION.

    THE MARKER ALONE CANNOT HOLD THIS CASE. Under an unrelated path the marker
    is untouched, which is what a broken function produces too. So the oracle
    is the RECORDER: an unrelated path must not reach the staleness re-read at
    all, and the managed path under the identical fixture must reach it. The
    pair separates "the path gate refused this edit" from "nothing ran".

    The signal answers None on the two halves on purpose. If the path gate is
    removed, the unrelated edit proceeds to the re-read, finds the condition
    clear, and unlinks a marker that no repair of the pinned region earned.
    """

    @pytest.mark.parametrize("tool_name", ["Edit", "Write"])
    def test_an_edit_of_another_file_does_not_reach_the_clear(
        self, edit_route_bed, tool_name
    ):
        """One fixture, two paths, opposite answers."""
        present_other, recorder_other = edit_route_bed(
            tool_name, UNRELATED_EDIT_PATH, None)
        present_managed, recorder_managed = edit_route_bed(
            tool_name, MANAGED_EDIT_PATH, None)

        assert recorder_managed.calls == 1, (
            "CONTROL HALF FAILED: the managed path did not reach the staleness "
            "re-read, so this fixture cannot tell a refused path from a "
            "function that does not run. Repair this half before you read the "
            "assertion below"
        )
        assert not present_managed, (
            "CONTROL HALF FAILED: the managed path left the marker in place "
            "under a cleared signal"
        )

        assert recorder_other.calls == 0, (
            f"a {tool_name} of {UNRELATED_EDIT_PATH!r} reached the staleness "
            f"re-read {recorder_other.calls} time(s). Route 1 must test the "
            "edited path against the managed file FIRST, so an edit of one "
            "other file costs one resolve and no signal read. The path gate "
            "is gone"
        )
        assert present_other, (
            f"A {tool_name} OF AN UNRELATED FILE CLEARED THE PIN STALENESS "
            f"MARKER. The path was {UNRELATED_EDIT_PATH!r}, which is not the "
            "managed CLAUDE.md. The gate that admits only the managed file "
            "has been removed, so an edit of one other file retires the marker"
        )
