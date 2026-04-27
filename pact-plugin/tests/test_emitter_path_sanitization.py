"""Path sanitization tests for agent_handoff_emitter.py."""
from pathlib import Path

import pytest

from fixtures.emitter import VALID_HANDOFF, _run_main


class TestPathSanitization:
    """Direct coverage for `_sanitize_path_component` helper + integration
    coverage for degenerate post-sanitize values.

    The helper uses `re.sub(r"[/\\\\]|\\.\\.", "", v)` which strips `/`,
    `\\`, and `..` substrings — but leaves single-dot segments untouched.
    This creates degenerate post-sanitize values (`''`, `'.'`, `'..'`)
    which, pre-guard, collapsed the marker path onto an existing
    directory (`marker_dir / '.'` → marker_dir itself), permanently
    suppressing future emits for the degenerate key.

    The guard:
        if team_name in ("", ".", "..") or task_id in ("", ".", ".."):
            return False  # emit without marker
    """

    class TestSanitizeHelper:
        """Direct unit tests against _sanitize_path_component. Independent
        of task #24 guard — these exercise the regex behavior alone."""

        @pytest.mark.parametrize(
            "legitimate",
            ["42", "12345", "feature-task-5", "task_5", "abc-def"],
        )
        def test_sanitize_preserves_legitimate_task_ids(self, legitimate):
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component(legitimate) == legitimate, (
                f"legitimate task_id {legitimate!r} was altered by sanitizer; "
                f"the regex must only strip `/`, `\\\\`, and `..` substrings."
            )

        def test_sanitize_strips_forward_slash(self):
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component("foo/bar") == "foobar"

        def test_sanitize_strips_backslash(self):
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component("foo\\bar") == "foobar"

        @pytest.mark.parametrize(
            "input_value,expected",
            [
                ("../foo", "foo"),
                ("..", ""),
                ("a..b", "ab"),
                ("..\\..", ""),
                ("...", "."),  # first two dots stripped; third survives
                ("....", ""),  # two consecutive `..` pairs strip to empty
            ],
        )
        def test_sanitize_strips_dotdot_sequences(self, input_value, expected):
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component(input_value) == expected, (
                f"sanitize({input_value!r}) expected {expected!r}; "
                f"regex may have drifted."
            )

        @pytest.mark.parametrize(
            "traversal_attempt",
            [
                "/etc/passwd",
                "./etc/passwd",
                "../../../../etc/shadow",
                "\\..\\..\\foo",
                "/../../../../root/.ssh/id_rsa",
            ],
        )
        def test_sanitize_strips_path_traversal_combinations(self, traversal_attempt):
            """Compound attack inputs — the output must contain no `/`,
            no `\\`, and no `..` substring. Exact value is less
            important than the absence of traversal primitives."""
            from agent_handoff_emitter import _sanitize_path_component
            out = _sanitize_path_component(traversal_attempt)
            assert "/" not in out, f"forward slash survived in {out!r}"
            assert "\\" not in out, f"backslash survived in {out!r}"
            assert ".." not in out, f"parent-dir sequence survived in {out!r}"

        def test_sanitize_preserves_single_dot(self):
            """Documented quirk: single `.` is NOT stripped (regex only
            matches `..`). Caller guards against this degenerate shape
            separately (#24 guard). This test pins the current contract
            so a future regex tightening is a deliberate decision."""
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component(".") == "."

        def test_sanitize_preserves_whitespace(self):
            """Whitespace is not a path-traversal primitive — regex doesn't
            strip it. Whitespace-only values create filesystem-valid
            (if unusual) filenames, so the guard does NOT need to treat
            them as degenerate."""
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component(" ") == " "
            assert _sanitize_path_component("  ") == "  "

        def test_sanitize_empty_string_unchanged(self):
            """Empty input returns empty — pinned for guard-paired tests
            that rely on the empty sentinel reaching _already_emitted."""
            from agent_handoff_emitter import _sanitize_path_component
            assert _sanitize_path_component("") == ""

        @pytest.mark.parametrize(
            "control_input",
            [
                "\x00",
                "\n",
                "\r",
                "\t",
                "\x01\x02\x03",
                "task\x00id",
                "task\nid",
                "task\rid",
                "\x00..\x00",
                "..\x00..",
            ],
        )
        def test_sanitize_strips_c0_control_characters(self, control_input):
            """C0 control characters (NUL, CR/LF, 0x00-0x1f) are stripped
            at the producer boundary. Without stripping, control chars
            would survive into filesystem path joins, enabling
            log-injection (CR/LF) and path-truncation (NUL on some
            filesystems) attacks."""
            from agent_handoff_emitter import _sanitize_path_component
            out = _sanitize_path_component(control_input)
            for ch in out:
                assert ord(ch) >= 0x20 or ch == "\x7f", (
                    f"control char {ord(ch):#x} survived in {out!r}"
                )

        @pytest.mark.parametrize(
            "raw_input",
            [
                "\x00",
                ".",
                "..",
                "...",
                "\x00..\x00",
                "task\x00../etc/passwd",
                "\n\r\t",
                "../\x00/..",
            ],
        )
        def test_post_sanitize_marker_key_is_safe_or_degenerate(
            self, raw_input, tmp_path, monkeypatch
        ):
            """Property-style invariant: for any input value, the
            post-sanitization-then-marker-key is either rejected by the
            dot/empty guard (degenerate path → emit without marker) OR
            is filesystem-safe (no `/`, no `\\`, no control chars, no
            `..`). Either branch preserves the fail-open data-integrity
            contract; neither branch can leak path-traversal primitives
            to the marker write."""
            from agent_handoff_emitter import _sanitize_path_component
            sanitized = _sanitize_path_component(raw_input)

            if sanitized in ("", ".", ".."):
                # Caught by the degenerate guard in _already_emitted —
                # emit without marker creation. No filesystem write,
                # no traversal risk.
                return

            # Otherwise the value flows into the marker join; assert it
            # carries no traversal or injection primitives.
            assert "/" not in sanitized
            assert "\\" not in sanitized
            assert ".." not in sanitized
            for ch in sanitized:
                assert ord(ch) >= 0x20 or ch == "\x7f", (
                    f"control char {ord(ch):#x} in marker key {sanitized!r}"
                )

    class TestDegenerateInputsDoNotCreateMarker:
        """Integration coverage — depends on security-reviewer's task #24
        guard. Degenerate post-sanitize values (`''`, `'.'`, `'..'`) in
        EITHER axis (task_id OR team_name) must NOT create a marker file,
        but MUST still emit the journal event (fail-open data-integrity
        per architect §2.4).

        The bug class is SYMMETRIC across both axes:
        - task_id degenerate: `marker_dir / '.'` → marker_dir itself;
          EEXIST collapses to "marker already exists" → permanent
          suppression of the degenerate key.
        - team_name='..' (WORSE): `home/.claude/teams/../.agent_handoff_emitted`
          normalizes to `home/.claude/.agent_handoff_emitted` — marker
          created OUTSIDE any team's scope, polluting user home root.
        - team_name='.': `home/.claude/teams/./.agent_handoff_emitted`
          normalizes to `home/.claude/teams/.agent_handoff_emitted` —
          cross-team pollution (marker directly under teams/, visible to
          every team's enumeration).

        Pre-#24 guard: `if not team_name or not task_id` caught empty
        string only. Post-#24: extended to `task_id/team_name in
        ("", ".", "..")` — catches the full degenerate set per axis.
        """

        @pytest.mark.parametrize(
            "raw_task_id",
            ["..", "..\\..", "...."],
            # All sanitize to `''`. Note `''` itself can't be sent directly
            # — the main() fallback substitutes "unknown" before sanitize,
            # so we test the PRE-sanitize inputs that produce empty output.
            # Empty post-sanitize was ALREADY guarded pre-#24 via the
            # original `if not team_name or not task_id` branch; these
            # tests pin that behavior and serve as regression guards.
        )
        def test_empty_post_sanitize_task_id_emits_without_marker(
            self, raw_task_id, tmp_path, monkeypatch
        ):
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": raw_task_id,
                    "task_subject": "degenerate-empty probe",
                    "teammate_name": "probe-agent",
                    "team_name": "pact-test",
                },
                task_data={
                    "status": "completed",
                    "owner": "probe-agent",
                    "metadata": {"handoff": VALID_HANDOFF},
                },
                append_calls=calls,
            )
            assert len(calls) == 1, (
                f"degenerate task_id {raw_task_id!r} (sanitizes to empty) "
                f"must still emit the journal event per fail-open data-"
                f"integrity invariant. Pre-#24 guard, EEXIST on "
                f"`marker_dir / ''` permanently suppressed the emit."
            )
            # Marker directory may exist (created by _already_emitted before
            # the guard returned False), but it must contain NO file named
            # with the degenerate sanitized value.
            marker_dir = (
                tmp_path / ".claude" / "teams" / "pact-test"
                / ".agent_handoff_emitted"
            )
            if marker_dir.exists():
                # The guard must prevent any degenerate marker file from
                # being created inside. Empty-string filename isn't a valid
                # path component; check no stray file landed here.
                files_in_dir = list(marker_dir.iterdir())
                assert files_in_dir == [], (
                    f"guard failed — degenerate task_id {raw_task_id!r} "
                    f"produced stray files in marker dir: {files_in_dir}"
                )

        @pytest.mark.parametrize(
            "raw_task_id",
            [".", "...", "/./"],  # all sanitize to '.'
            # These are the NEWLY-guarded cases in #24. Pre-#24 the guard
            # was `if not task_id` which missed post-sanitize `.` — it's
            # truthy. These tests are the paired-regression proof that
            # #24's extended check (`task_id in ("", ".", "..")`) closes
            # the collapse-onto-marker_dir bug.
        )
        def test_dot_only_post_sanitize_emits_without_marker(
            self, raw_task_id, tmp_path, monkeypatch
        ):
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": raw_task_id,
                    "task_subject": "degenerate-dot probe",
                    "teammate_name": "probe-agent",
                    "team_name": "pact-test",
                },
                task_data={
                    "status": "completed",
                    "owner": "probe-agent",
                    "metadata": {"handoff": VALID_HANDOFF},
                },
                append_calls=calls,
            )
            assert len(calls) == 1, (
                f"degenerate task_id {raw_task_id!r} (sanitizes to '.') "
                f"must still emit — the #24 guard protects against the "
                f"`marker_dir / '.'` collapse that otherwise permanently "
                f"suppresses future emits via spurious EEXIST."
            )
            marker_dir = (
                tmp_path / ".claude" / "teams" / "pact-test"
                / ".agent_handoff_emitted"
            )
            # Crucial invariant: marker_dir itself must not have been
            # interpreted as THE marker. If it was, a subsequent fire
            # with the same degenerate key would see EEXIST and suppress.
            # Verify by firing a SECOND time with the same degenerate key
            # and asserting a second event is written.
            _run_main(
                stdin_payload={
                    "task_id": raw_task_id,
                    "task_subject": "degenerate-dot probe second fire",
                    "teammate_name": "probe-agent",
                    "team_name": "pact-test",
                },
                task_data={
                    "status": "completed",
                    "owner": "probe-agent",
                    "metadata": {"handoff": VALID_HANDOFF},
                },
                append_calls=calls,
            )
            # Post-#24, degenerate keys are UN-DEDUPABLE (no marker
            # created → every fire emits). This is the intentional
            # accepted trade-off: rare duplication for degenerate keys
            # beats silent permanent event loss for ALL future fires.
            assert len(calls) == 2, (
                f"degenerate key {raw_task_id!r} second fire was suppressed "
                f"— the #24 guard is missing or the pre-guard EEXIST-on-dir "
                f"bug has resurfaced."
            )

        @pytest.mark.parametrize(
            "raw_task_id",
            ["/./.", ".//."],  # both sanitize to '..'
            # Per security-reviewer-538's empirical 17-input probe: these
            # forms produce `..` after regex stripping (two single-dot
            # segments separated by `/` collapse to `..` once the `/` is
            # stripped). Exercises the `task_id == ".."` branch of the
            # #24 guard — distinct from the `"."` branch covered above.
            # Pre-#24 without the branch, `marker_dir / ".."` resolves to
            # `marker_dir.parent`, causing EEXIST → permanent suppression
            # with a marker landing OUTSIDE the intended path.
        )
        def test_dotdot_post_sanitize_emits_via_guard(
            self, raw_task_id, tmp_path, monkeypatch
        ):
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": raw_task_id,
                    "task_subject": "dotdot-collapse probe",
                    "teammate_name": "probe-agent",
                    "team_name": "pact-test",
                },
                task_data={
                    "status": "completed",
                    "owner": "probe-agent",
                    "metadata": {"handoff": VALID_HANDOFF},
                },
                append_calls=calls,
            )
            assert len(calls) == 1, (
                f"task_id {raw_task_id!r} sanitizes to '..' which pre-#24 "
                f"resolved to `marker_dir.parent` → permanent suppression. "
                f"#24 guard `task_id in ('', '.', '..')` must catch this."
            )
            # Pin that no stray marker files landed above team scope at
            # `~/.claude/teams/` level (the pre-#24 escape shape when
            # the `..` collapses marker_dir onto its parent).
            teams_dir = tmp_path / ".claude" / "teams"
            teams_children = (
                {p.name for p in teams_dir.iterdir()}
                if teams_dir.exists() else set()
            )
            assert teams_children <= {"pact-test"}, (
                f"unexpected children in {teams_dir}: "
                f"{teams_children - {'pact-test'}}. The `..` collapse may "
                f"have created marker files above the team scope."
            )

        @pytest.mark.parametrize(
            "raw_team_name,expected_sanitized",
            [
                ("..", ""),       # pre-#24 guarded (empty branch)
                ("..\\..", ""),   # pre-#24 guarded
                (".", "."),       # NEWLY guarded by #24 — cross-team pollution without guard
                ("...", "."),     # NEWLY guarded by #24
                (".....", "."),   # NEWLY guarded by #24 — odd-count dots collapse to '.'
                ("/./", "."),     # NEWLY guarded by #24 — same root cause as task_id case
                ("/./.", ".."),   # NEWLY guarded by #24 — dotdot-collapse branch
                (".//.", ".."),   # NEWLY guarded by #24 — same class as /./.
            ],
        )
        def test_degenerate_team_name_values_guarded(
            self, raw_team_name, expected_sanitized, tmp_path, monkeypatch
        ):
            """team_name axis symmetry.

            Pre-#24 with team_name='..': marker_dir resolves to
            `home/.claude/teams/../.agent_handoff_emitted`, which Path-
            normalizes to `home/.claude/.agent_handoff_emitted` — a
            marker file created directly under the user's home .claude
            dir (OUTSIDE any team's scope). This is the home-root
            pollution case.

            Pre-#24 with team_name='.': marker_dir resolves to
            `home/.claude/teams/./.agent_handoff_emitted`, normalizing
            to `home/.claude/teams/.agent_handoff_emitted` — a marker
            file directly under teams/, visible to every team.

            Post-#24 guard catches all degenerate team_name values in
            `("", ".", "..")` and returns False before marker creation.
            """
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": "42",
                    "task_subject": "degenerate team probe",
                    "teammate_name": "probe-agent",
                    "team_name": raw_team_name,
                },
                task_data={
                    "status": "completed",
                    "owner": "probe-agent",
                    "metadata": {"handoff": VALID_HANDOFF},
                },
                append_calls=calls,
            )
            assert len(calls) == 1, (
                f"degenerate team_name {raw_team_name!r} (sanitizes to "
                f"{expected_sanitized!r}) must emit the journal event via "
                f"the #24 guard. team_name and task_id are symmetrically "
                f"protected."
            )
            # Critical home-root-pollution assertions — the bug's
            # WORST-case form is marker creation OUTSIDE any team's
            # scope. Guard must prevent all three escape paths:
            home_root_marker = (
                tmp_path / ".claude" / ".agent_handoff_emitted"
            )
            assert not home_root_marker.exists(), (
                f"home-root pollution detected: degenerate team_name "
                f"{raw_team_name!r} created marker at {home_root_marker} "
                f"(OUTSIDE any team's scope). The #24 guard failed."
            )
            teams_root_marker = (
                tmp_path / ".claude" / "teams" / ".agent_handoff_emitted"
            )
            assert not teams_root_marker.exists(), (
                f"cross-team pollution detected: degenerate team_name "
                f"{raw_team_name!r} created marker at {teams_root_marker} "
                f"(directly under teams/, visible to every team)."
            )
            # And no marker file bearing the task_id basename was
            # created at either escape path.
            assert not (home_root_marker / "42").exists()
            assert not (teams_root_marker / "42").exists()

        @pytest.mark.parametrize(
            "raw_task_id,raw_team_name",
            [
                ("", "."),
                (".", ""),
                ("..", "."),
                (".", ".."),
                ("...", "..."),
                ("/./", "/./"),
            ],
        )
        def test_combined_degenerate_both_axes_guarded(
            self, raw_task_id, raw_team_name, tmp_path, monkeypatch
        ):
            """Combined-axis matrix: both task_id AND team_name degenerate
            simultaneously. Emit invariant must still hold (fail-open
            wins over the compound-pollution failure mode).

            Pre-#24: either axis alone could trigger the collapse bug;
            both together produce either home-root pollution (if
            team_name='..') or permanent suppression (if task_id
            collapses). Post-#24 guard returns False on EITHER axis
            being degenerate, so the compound case short-circuits via
            the first matched branch.
            """
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": raw_task_id,
                    "task_subject": "compound degenerate probe",
                    "teammate_name": "probe-agent",
                    "team_name": raw_team_name,
                },
                task_data={
                    "status": "completed",
                    "owner": "probe-agent",
                    "metadata": {"handoff": VALID_HANDOFF},
                },
                append_calls=calls,
            )
            assert len(calls) == 1, (
                f"compound degenerate (task_id={raw_task_id!r}, "
                f"team_name={raw_team_name!r}) must emit via #24 guard."
            )
            # Neither home-root nor teams-root pollution.
            home_root_marker = tmp_path / ".claude" / ".agent_handoff_emitted"
            teams_root_marker = (
                tmp_path / ".claude" / "teams" / ".agent_handoff_emitted"
            )
            assert not home_root_marker.exists()
            assert not teams_root_marker.exists()

    class TestIntegrationPathTraversalAttempts:
        """Path-traversal inputs must NOT escape the team's marker dir.
        Independent of #24 guard — these test the sanitizer's stripping
        behavior integrated through main(). Input with traversal primitives
        sanitizes to a legitimate basename that lives inside the team dir.
        """

        @pytest.mark.parametrize(
            "attack_task_id,expected_sanitized",
            [
                ("../../../etc/shadow", "etcshadow"),
                ("/etc/passwd", "etcpasswd"),
                ("\\..\\..\\foo", "foo"),
                ("../../secrets", "secrets"),
            ],
        )
        def test_path_traversal_task_ids_contained_in_team_dir(
            self, attack_task_id, expected_sanitized, tmp_path, monkeypatch
        ):
            monkeypatch.setenv("HOME", str(tmp_path))
            calls: list[dict] = []
            _run_main(
                stdin_payload={
                    "task_id": attack_task_id,
                    "task_subject": "path-traversal probe",
                    "teammate_name": "probe-agent",
                    "team_name": "pact-test",
                },
                task_data={
                    "status": "completed",
                    "owner": "probe-agent",
                    "metadata": {"handoff": VALID_HANDOFF},
                },
                append_calls=calls,
            )
            assert len(calls) == 1
            # Marker file (if created) lives at the sanitized basename
            # INSIDE the team's .agent_handoff_emitted dir — never at
            # an escape path.
            expected_marker = (
                tmp_path / ".claude" / "teams" / "pact-test"
                / ".agent_handoff_emitted" / expected_sanitized
            )
            assert expected_marker.exists(), (
                f"path-traversal attempt {attack_task_id!r} sanitized to "
                f"{expected_sanitized!r}; marker must exist at expected "
                f"location but was not found at {expected_marker}."
            )
            # Escape-path check: nothing was created outside the team dir.
            # Any path containing "etc/shadow", "etc/passwd", or absolute
            # leakage is a failure.
            escape_targets = [
                tmp_path.parent / "etc" / "shadow",
                tmp_path / "etc" / "passwd",
                Path("/etc/shadow"),
                Path("/etc/passwd"),
            ]
            for escape in escape_targets:
                # Skip absolute system paths if they happen to pre-exist
                # (e.g., /etc/passwd on macOS is real — we can't assert
                # it doesn't exist; we assert we didn't CREATE it by
                # asserting our sanitized marker exists inside tmp_path
                # above, which is sufficient).
                if escape.is_absolute() and escape.exists():
                    continue
                assert not escape.exists(), (
                    f"path-traversal attempt {attack_task_id!r} created "
                    f"a file at escape path {escape}."
                )

