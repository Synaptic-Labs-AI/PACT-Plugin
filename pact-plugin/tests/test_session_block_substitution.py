"""The session block reaches CLAUDE.md literally, not through an escape grammar.

WHY THESE ARMS SIT AT `update_session_info` AND NOT AT A PURE FUNCTION.
The defect these arms hold is not in the pattern, not in the sanitize, and not
in the block assembly. Each of those is correct on its own. It exists ONLY in
the COMPOSED CALL, where an assembled block built from caller-influenced values
reached the replacement-STRING position of `re.sub`, and `re.sub` expands its
replacement grammar in a string. An arm at a pure-function surface cannot see a
defect that is a property of the composition.

THE SANITIZE DOES NOT COVER THIS, WHICH IS WHY THE ARMS LOOK REDUNDANT AND ARE
NOT. `_PROMPT_CONTROL_CHARS_RE` strips control characters, and a BACKSLASH is
not a control character. So the guard removes a newline and a string
replacement PUTS ONE BACK. Two carriers reach this call with no attacker,
because a directory name can legally contain a backslash.

THE ONE ARM THAT CARRIES THE ROW IS THE INVALID ESCAPE, AND IT IS NOT ABOUT A
CRASH. A `\\d` in the value raises `re.PatternError` inside the substitution,
and the `except Exception` in the same function catches it and returns a
FAILURE STRING. Nothing crashes. That is what made it survivable and permanent:
each later pass fails identically, so the block FREEZES at first-pass content
while the directory name persists, and state recovery then reads a stale
pointer. AN ARM THAT ASSERTS ONLY THAT NO EXCEPTION ESCAPED IS GREEN ON THE
PRE-FIX CODE FOR THE OTHER TWO PRODUCTIONS AS WELL, so it is worth nothing.
The assertion that separates the two states is a PAIR: the return value is the
update message, AND the block on disk carries the SECOND pass values.

THE POSITIVE CONTROL IS LOAD-BEARING. Each of the three negative arms is
satisfied by a substitution that stopped running. The control asserts an
ordinary path still writes its line, so a mechanism made inert reddens here
rather than passing three absence checks.

EVERY FIXTURE WRITES BELOW `tmp_path`. This suite must not reach a real
CLAUDE.md: the file is gitignored, always loaded, and has no commit behind it.

THE AXIS THESE ARMS CANNOT SEE, NAMED RATHER THAN LEFT IMPLICIT. They drive
Case 1, the marker-replacement path. Case 0 (file absent) and Case 2 (no
SESSION markers) build their content by f-string and `str.replace`, which have
no replacement grammar, so no production of this family reaches them. A future
edit that routes Case 2 through `re.sub` would be outside this file.
"""

SESSION_START = "<!-- SESSION_START -->"
SESSION_END = "<!-- SESSION_END -->"
_UPDATED = "Session info updated in project CLAUDE.md"
_CREATED = "Session info created in new project CLAUDE.md"


def _seed_first_pass(root):
    """Write the first session block, and return the target path.

    Case 0 builds the file with an f-string and reaches no substitution, so
    the payload arms must run as a SECOND pass. This helper makes that
    ordering explicit rather than incidental to each arm. ``root`` is the
    caller-owned project world (a subworld below tmp_path — see the REBIND
    note at each call site).
    """
    from shared.session_resume import update_session_info

    target = root / ".claude" / "CLAUDE.md"
    result = update_session_info(
        "session-alpha", "team-alpha", session_dir="/tmp/ordinary/alpha"
    )
    assert result == _CREATED, (
        "the fixture did not reach the create path, so the arm below would "
        f"test a different branch. got: {result!r}"
    )
    assert target.exists()
    return target


class TestSessionBlockSubstitutionIsLiteral:
    """The four arms of the composed call, three negative and one control."""

    def test_a_newline_escape_lands_no_heading_in_the_managed_region(
        self, tmp_path, monkeypatch
    ):
        """A backslash-n in the session dir must not open a line.

        PRE-FIX: the string replacement re-materialises a newline. Inline code
        in markdown does not span a line break, so the value leaves its
        backtick span and lands a HEADING of its own in the managed region.
        """
        from shared.session_resume import update_session_info

        # REBIND (§11.3.1 class (c)): this test owns its project world, so
        # CPD is re-declared at a subworld below tmp_path — the suite-wide
        # conftest redirect's legacy seed (tmp_path/CLAUDE.md) is not this
        # world's fallback file, and the create path stays reachable.
        world = tmp_path / "session-block-world"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(world))
        target = _seed_first_pass(world)

        payload = "/tmp/dir\\n## INJECTED HEADING"
        # NON-VACUITY ON THE INPUT, asserted before the call and independent
        # of the code under test: a payload that carried neither token would
        # make the absence check below pass for the wrong cause.
        assert "\\n" in payload
        assert "## INJECTED" in payload

        result = update_session_info("session-beta", "team-beta", session_dir=payload)

        assert result == _UPDATED, (
            f"the second pass did not rewrite the block. got: {result!r}"
        )
        content = target.read_text(encoding="utf-8")
        offenders = [
            line for line in content.splitlines() if line.startswith("## INJECTED")
        ]
        assert not offenders, (
            "the session dir opened a line of its own in the managed region: "
            f"{offenders}"
        )
        # The value must still be PRESENT, backslash and all. An arm that only
        # checked for the absent heading would also pass if the whole line
        # were dropped.
        assert "\\n## INJECTED HEADING" in content

    def test_an_invalid_escape_rewrites_the_block_rather_than_freezing_it(
        self, tmp_path, monkeypatch
    ):
        """A backslash-d must not freeze the block at first-pass content.

        THIS IS THE ARM THAT SEPARATES "NO CRASH" FROM "NO DEFECT". Pre-fix the
        substitution raises `re.PatternError`, the handler one frame away
        returns a failure string, and NOTHING IS WRITTEN. The session survives,
        every later pass fails the same way, and the block keeps first-pass
        content while the directory moves on. State recovery then reads a
        pointer to a session that has ended.

        So this arm asserts TWO things that a no-exception check does not: the
        RETURN VALUE is the update message, and the CONTENT is the second pass.
        """
        from shared.session_resume import update_session_info

        # REBIND (§11.3.1 class (c)): this test owns its project world, so
        # CPD is re-declared at a subworld below tmp_path — the suite-wide
        # conftest redirect's legacy seed (tmp_path/CLAUDE.md) is not this
        # world's fallback file, and the create path stays reachable.
        world = tmp_path / "session-block-world"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(world))
        target = _seed_first_pass(world)

        result = update_session_info(
            "session-beta", "team-beta", session_dir="/tmp/dir\\dtail"
        )

        assert result == _UPDATED, (
            "the invalid escape did not crash, and that is the point: it "
            "returned a FAILURE STRING and wrote nothing, so the block froze. "
            f"got: {result!r}"
        )
        content = target.read_text(encoding="utf-8")
        assert "session-beta" in content, "the second pass values are absent"
        assert "session-alpha" not in content, (
            "the block still carries first-pass content, so the write was "
            "skipped and the pointer is stale"
        )

    def test_a_group_reference_does_not_splice_the_block_into_itself(
        self, tmp_path, monkeypatch
    ):
        """A backslash-g-zero must not expand to the matched block.

        PRE-FIX: `\\g<0>` is a group reference to the whole match, so the
        ENTIRE old block, markers and all, lands inside the new one. The file
        then carries two opening markers, and every later reader that slices
        between the markers takes a region that contains a second region.
        """
        from shared.session_resume import update_session_info

        # REBIND (§11.3.1 class (c)): this test owns its project world, so
        # CPD is re-declared at a subworld below tmp_path — the suite-wide
        # conftest redirect's legacy seed (tmp_path/CLAUDE.md) is not this
        # world's fallback file, and the create path stays reachable.
        world = tmp_path / "session-block-world"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(world))
        target = _seed_first_pass(world)

        result = update_session_info(
            "session-beta", "team-beta", session_dir="/tmp/dir\\g<0>tail"
        )

        assert result == _UPDATED, f"got: {result!r}"
        content = target.read_text(encoding="utf-8")
        assert content.count(SESSION_START) == 1, (
            "the managed block was spliced into itself: "
            f"{content.count(SESSION_START)} opening markers"
        )
        assert content.count(SESSION_END) == 1, (
            f"{content.count(SESSION_END)} closing markers"
        )

    def test_control_an_ordinary_path_still_writes_its_line(
        self, tmp_path, monkeypatch
    ):
        """THE POSITIVE CONTROL, without which the three arms above are empty.

        Each arm above asserts an ABSENCE. A substitution that stopped running
        satisfies all three. This arm asserts the mechanism is live: an
        ordinary path is written, and it is written once.
        """
        from shared.session_resume import update_session_info

        # REBIND (§11.3.1 class (c)): this test owns its project world, so
        # CPD is re-declared at a subworld below tmp_path — the suite-wide
        # conftest redirect's legacy seed (tmp_path/CLAUDE.md) is not this
        # world's fallback file, and the create path stays reachable.
        world = tmp_path / "session-block-world"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(world))
        target = _seed_first_pass(world)

        result = update_session_info(
            "session-beta", "team-beta", session_dir="/tmp/ordinary/beta"
        )

        assert result == _UPDATED, f"got: {result!r}"
        content = target.read_text(encoding="utf-8")
        assert "- Session dir: `/tmp/ordinary/beta`" in content
        assert "session-beta" in content
        assert content.count(SESSION_START) == 1
