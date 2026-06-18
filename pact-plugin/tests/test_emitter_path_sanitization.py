"""Path sanitization tests for agent_handoff_emitter.py."""
import re
from pathlib import Path

import pytest

from fixtures.emitter import VALID_HANDOFF, _run_main


class TestPathSanitization:
    """Direct coverage for `sanitize_path_component` helper + integration
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
        """Direct unit tests against sanitize_path_component. Independent
        of task #24 guard — these exercise the regex behavior alone."""

        @pytest.mark.parametrize(
            "legitimate",
            ["42", "12345", "feature-task-5", "task_5", "abc-def"],
        )
        def test_sanitize_preserves_legitimate_task_ids(self, legitimate):
            from shared.agent_handoff_marker import sanitize_path_component
            assert sanitize_path_component(legitimate) == legitimate, (
                f"legitimate task_id {legitimate!r} was altered by sanitizer; "
                f"the regex must only strip `/`, `\\\\`, and `..` substrings."
            )

        def test_sanitize_strips_forward_slash(self):
            from shared.agent_handoff_marker import sanitize_path_component
            assert sanitize_path_component("foo/bar") == "foobar"

        def test_sanitize_strips_backslash(self):
            from shared.agent_handoff_marker import sanitize_path_component
            assert sanitize_path_component("foo\\bar") == "foobar"

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
            from shared.agent_handoff_marker import sanitize_path_component
            assert sanitize_path_component(input_value) == expected, (
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
            from shared.agent_handoff_marker import sanitize_path_component
            out = sanitize_path_component(traversal_attempt)
            assert "/" not in out, f"forward slash survived in {out!r}"
            assert "\\" not in out, f"backslash survived in {out!r}"
            assert ".." not in out, f"parent-dir sequence survived in {out!r}"

        def test_sanitize_preserves_single_dot(self):
            """Documented quirk: single `.` is NOT stripped (regex only
            matches `..`). Caller guards against this degenerate shape
            separately (#24 guard). This test pins the current contract
            so a future regex tightening is a deliberate decision."""
            from shared.agent_handoff_marker import sanitize_path_component
            assert sanitize_path_component(".") == "."

        def test_sanitize_preserves_whitespace(self):
            """Whitespace is not a path-traversal primitive — regex doesn't
            strip it. Whitespace-only values create filesystem-valid
            (if unusual) filenames, so the guard does NOT need to treat
            them as degenerate."""
            from shared.agent_handoff_marker import sanitize_path_component
            assert sanitize_path_component(" ") == " "
            assert sanitize_path_component("  ") == "  "

        def test_sanitize_empty_string_unchanged(self):
            """Empty input returns empty — pinned for guard-paired tests
            that rely on the empty sentinel reaching _already_emitted."""
            from shared.agent_handoff_marker import sanitize_path_component
            assert sanitize_path_component("") == ""

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
            from shared.agent_handoff_marker import sanitize_path_component
            out = sanitize_path_component(control_input)
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
            from shared.agent_handoff_marker import sanitize_path_component
            sanitized = sanitize_path_component(raw_input)

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

        POST-AC-5-REBIND CHANNEL NOTE (the team_name axis):
        the b1 emitter no longer reads the stdin ``team_name`` field — the
        marker team_name is resolved from ``get_pact_context()`` (the SSOT
        source b2/b3 share). So a degenerate value reaching the team_name
        axis now travels through the AUTHORITATIVE context channel, NOT
        stdin. The ``_run_main`` helper bridges the per-test stdin
        ``team_name`` into the patched context (``_ctx["team_name"]``), so
        the degenerate value still reaches ``already_emitted`` as the marker
        team_name and the #24 guard is genuinely exercised — these tests
        defend the residual invariant "a degenerate team_name arriving via
        the context channel is contained by the #24 guard". The companion
        ``test_stdin_team_name_is_inert_post_rebind`` proves the OTHER half:
        a hostile STDIN team_name is inert (cannot reach the marker key)
        once the context team_name is path-safe.

        TWO UPSTREAM GUARDS (a degenerate CONTEXT team_name cannot occur in
        prod): (1) the context team_name is always a ``session-<id8>`` value
        minted by generate_team_name (see
        ``test_marker_team_name_source_is_path_safe``); AND (2) #979 Phase-2
        hardening #2 (KEPT) re-validates the persisted value at the
        ``get_pact_context()`` READ BOUNDARY via ``is_safe_path_component``,
        rejecting any degenerate value to ``""`` before it reaches this sink
        (see ``test_handoff_writability_parity.py::TestMarkerKeyConvergence``).
        ``_run_main`` patches ``get_pact_context`` directly, bypassing guard (2)
        so the #24 SINK guard is exercised in isolation — these remain
        regression guards on the sink guard machinery (defense-in-depth behind
        the read boundary), not a live production state.
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
            """team_name axis symmetry — degenerate value via the
            AUTHORITATIVE context channel (post-AC-5-rebind).

            The marker team_name is now resolved from ``get_pact_context()``,
            not stdin. ``_run_main`` bridges this test's stdin ``team_name``
            into the patched context, so a degenerate value still reaches
            ``already_emitted`` as the marker team_name — the #24 guard is
            genuinely exercised on the context channel (see the non-vacuity
            assertion below, which proves the degenerate value reached the
            guard). This is the live containment path; the dropped stdin read
            is covered separately by
            ``test_stdin_team_name_is_inert_post_rebind``.

            DEFENSE-IN-DEPTH, NOT A LIVE VECTOR (two upstream guards now make a
            degenerate CONTEXT team_name unreachable at this sink in prod):
              (1) PRODUCER: the context team_name is ALWAYS a
                  ``generate_team_name`` value (``session-[a-f0-9-]``, path-safe
                  by construction — pinned by
                  ``test_marker_team_name_source_is_path_safe``).
              (2) READ BOUNDARY (#979 Phase-2 hardening #2, KEPT): even a
                  corrupted / hand-edited persisted context value that WERE
                  degenerate is rejected to ``""`` by ``get_pact_context()``'s
                  ``is_safe_path_component`` re-validation BEFORE it reaches this
                  sink (pinned by
                  ``test_handoff_writability_parity.py::TestMarkerKeyConvergence``).
            This test deliberately uses ``_run_main``, which patches
            ``get_pact_context`` DIRECTLY (bypassing guard 2) to feed the
            degenerate value to the sink in ISOLATION — so the #24 sink guard is
            exercised on its own as defense-in-depth BEHIND the read boundary.
            Do NOT re-read it as a live attack path: in production a degenerate
            value cannot reach here through either guard.

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
            # Non-vacuity capture: spy the team_name that actually reaches
            # already_emitted so we PROVE the degenerate value traveled
            # through the marker-key derivation (a vacuous pass would never
            # reach the guard). Patch the symbol bound in the hook module.
            import agent_handoff_emitter as _emitter
            from shared.agent_handoff_marker import (
                already_emitted as _real_already_emitted,
            )
            seen_team_names: list[str] = []

            def _spy_already_emitted(team_name, task_id, occupant):
                seen_team_names.append(team_name)
                return _real_already_emitted(team_name, task_id, occupant)

            monkeypatch.setattr(
                _emitter, "already_emitted", _spy_already_emitted
            )
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
            # NON-VACUITY: the degenerate value reached already_emitted as
            # the marker team_name (via the context channel) — so the #24
            # guard was the mechanism that returned False, not a no-op path.
            assert seen_team_names == [raw_team_name], (
                f"degenerate team_name {raw_team_name!r} did NOT reach "
                f"already_emitted as the marker team_name (saw "
                f"{seen_team_names!r}); the test would be vacuous — the #24 "
                f"guard is not actually being exercised."
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

            POST-AC-5-REBIND: the degenerate team_name arrives via the
            AUTHORITATIVE context channel (``_run_main`` bridges stdin
            ``team_name`` → patched context); the degenerate task_id still
            arrives via stdin. So the compound case exercises the #24 guard
            on BOTH the context-sourced team_name axis and the stdin-sourced
            task_id axis. The non-vacuity assertion below proves the
            degenerate team_name reached already_emitted's marker key.

            Pre-#24: either axis alone could trigger the collapse bug;
            both together produce either home-root pollution (if
            team_name='..') or permanent suppression (if task_id
            collapses). Post-#24 guard returns False on EITHER axis
            being degenerate, so the compound case short-circuits via
            the first matched branch.

            DEFENSE-IN-DEPTH, NOT A LIVE VECTOR: the degenerate CONTEXT
            team_name axis is impossible-in-prod behind TWO upstream guards —
            (1) the context team_name is always generate_team_name's
            ``session-[a-f0-9-]`` form (see
            ``test_marker_team_name_source_is_path_safe``), AND (2) #979 Phase-2
            hardening #2 (KEPT) rejects any degenerate persisted value to ``""``
            at the ``get_pact_context()`` read boundary before it reaches this
            sink (see
            ``test_handoff_writability_parity.py::TestMarkerKeyConvergence``).
            ``_run_main`` patches ``get_pact_context`` directly (bypassing guard
            2) so this compound test exercises the #24 SINK guard in isolation —
            a defensive regression guard behind the read boundary, not a live
            attack path.
            """
            monkeypatch.setenv("HOME", str(tmp_path))
            # Non-vacuity capture: prove the degenerate team_name reached the
            # marker-key derivation via the context channel (not a no-op).
            import agent_handoff_emitter as _emitter
            from shared.agent_handoff_marker import (
                already_emitted as _real_already_emitted,
            )
            seen_team_names: list[str] = []

            def _spy_already_emitted(team_name, task_id, occupant):
                seen_team_names.append(team_name)
                return _real_already_emitted(team_name, task_id, occupant)

            monkeypatch.setattr(
                _emitter, "already_emitted", _spy_already_emitted
            )
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
            # NON-VACUITY: the degenerate team_name reached already_emitted as
            # the marker team_name (via the context channel) — so the #24
            # guard, not a vacuous path, is what contained the compound case.
            assert seen_team_names == [raw_team_name], (
                f"compound degenerate team_name {raw_team_name!r} did NOT "
                f"reach already_emitted as the marker team_name (saw "
                f"{seen_team_names!r}); the test would be vacuous."
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
            from shared.agent_handoff_marker import occupant_hash

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
            # Marker file (if created) lives at the sanitized basename —
            # now occupant-keyed ({sanitized}-{occupant_hash}, #887) —
            # INSIDE the team's .agent_handoff_emitted dir, never at an
            # escape path. occupant = hash(owner + subject), fixed across
            # the parametrized cases.
            occ = occupant_hash("probe-agent", "path-traversal probe")
            expected_marker = (
                tmp_path / ".claude" / "teams" / "pact-test"
                / ".agent_handoff_emitted" / f"{expected_sanitized}-{occ}"
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


class TestPostRebindMarkerKeyInvariants:
    """AC-5 (#979 Phase-2) marker-key invariants on the POST-REBIND emitter.

    The b1 emitter (agent_handoff_emitter.main) resolves the marker
    ``team_name`` from ``pact_context.get_pact_context()`` — the SSOT source
    b2/b3 share — and NO LONGER reads the stdin ``team_name`` field
    (commit "fix(handoff-emitter): resolve marker team name from session
    context, not stdin"). These tests pin the three properties that change
    surfaces with that rebind:

    1. ``test_stdin_team_name_is_inert_post_rebind`` — a hostile STDIN
       team_name cannot reach the marker key when the authoritative context
       team_name is path-safe (the containment-by-construction property).
    2. ``test_empty_context_defers_before_marker_claim`` — an unresolvable
       (empty) context on a teammate frame short-circuits at the #917
       writability gate BEFORE the O_EXCL marker claim, so it defers to b2/b3
       and never claims a poisoned/empty-team marker.
    3. ``test_marker_team_name_source_is_path_safe`` — the SSOT producer
       ``generate_team_name`` constrains the context team_name to a path-safe
       ``session-[a-f0-9-]+`` form, so dropping b1's producer-side
       ``sanitize_path_component`` wrapper on team_name does not open a
       traversal vector (task_id sanitize remains the stdin-axis guard).

    This is the AC-5 sanitize-drop safety story the security reviewer leans
    on at peer-review.
    """

    def test_stdin_team_name_is_inert_post_rebind(self, tmp_path, monkeypatch):
        """Containment-by-construction: a HOSTILE stdin ``team_name`` is
        INERT post-rebind. With a path-safe context team_name, a stdin
        team_name carrying traversal primitives never reaches the marker key
        — the marker is scoped to the context team_name, and ``already_emitted``
        receives the context value verbatim, not the stdin value.

        This is the assertion that proves the actual fix (dropping the stdin
        read). A counter-test reverting :157 back to the stdin-first read
        (``input_data.get("team_name") or get_team_name()``) would route the
        hostile stdin value into the marker key and fail the assertions below.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        import agent_handoff_emitter as _emitter
        from shared.agent_handoff_marker import (
            already_emitted as _real_already_emitted,
            occupant_hash,
        )

        safe_context_team = "session-deadbeef"
        hostile_stdin_team = "../../../etc/passwd"

        seen_team_names: list[str] = []

        def _spy_already_emitted(team_name, task_id, occupant):
            seen_team_names.append(team_name)
            return _real_already_emitted(team_name, task_id, occupant)

        monkeypatch.setattr(_emitter, "already_emitted", _spy_already_emitted)

        calls: list[dict] = []
        _run_main(
            stdin_payload={
                "task_id": "42",
                "task_subject": "stdin-inert probe",
                "teammate_name": "probe-agent",
                "team_name": hostile_stdin_team,  # hostile — must be ignored
            },
            task_data={
                "status": "completed",
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
            context_team_name=safe_context_team,  # authoritative, path-safe
        )

        # The marker key uses the CONTEXT team_name, never the stdin value.
        assert seen_team_names == [safe_context_team], (
            f"the hostile stdin team_name {hostile_stdin_team!r} reached the "
            f"marker key (saw {seen_team_names!r}); post-rebind the marker "
            f"team_name MUST come from get_pact_context(), making the stdin "
            f"field inert. A regression to the stdin-first read would fail here."
        )
        assert len(calls) == 1, "writable resolvable-context fire must emit once."

        # The marker landed inside the SAFE context team's scope, not at an
        # escape path derived from the hostile stdin value.
        occ = occupant_hash("probe-agent", "stdin-inert probe")
        safe_marker = (
            tmp_path / ".claude" / "teams" / safe_context_team
            / ".agent_handoff_emitted" / f"42-{occ}"
        )
        assert safe_marker.exists(), (
            f"marker must live under the safe context team dir at "
            f"{safe_marker}; it was not found there."
        )
        # NEGATIVE inertness proof (the other half of "stdin never reaches the
        # marker key"): no filesystem artifact derives from the hostile stdin
        # token. If the pre-rebind stdin-first read regressed, the stdin value
        # would sanitize_path_component to "etcpasswd" and scope a marker dir
        # there — assert that dir does NOT exist, and that the ONLY team dir is
        # the safe context one.
        from shared.agent_handoff_marker import sanitize_path_component

        sanitized_stdin_token = sanitize_path_component(
            str(hostile_stdin_team).lower()
        )  # what the OLD code would have keyed the marker on
        teams_dir = tmp_path / ".claude" / "teams"
        assert not (teams_dir / sanitized_stdin_token).exists(), (
            f"a team dir keyed on the (sanitized) stdin token "
            f"{sanitized_stdin_token!r} was created — the hostile stdin "
            f"team_name reached the marker key. Post-rebind it MUST be inert."
        )
        assert not (tmp_path / ".claude" / "etc").exists(), (
            "hostile stdin team_name created an etc/ path under .claude — "
            "the stdin value leaked into a filesystem join."
        )
        teams_children = (
            {p.name for p in teams_dir.iterdir()} if teams_dir.exists() else set()
        )
        assert teams_children == {safe_context_team}, (
            f"unexpected team dirs {teams_children - {safe_context_team}} — "
            f"the hostile stdin team_name must not create any team scope; only "
            f"the authoritative context team {safe_context_team!r} may appear."
        )

    def test_empty_context_defers_before_marker_claim(
        self, tmp_path, monkeypatch
    ):
        """#917 × AC-5 (architect §3.6): an UNRESOLVABLE teammate frame —
        ``get_pact_context()`` returns the empty context — must SHORT-CIRCUIT
        at the canonical-journal writability gate (``if not get_journal_path()``)
        BEFORE the O_EXCL marker claim (``already_emitted``). It defers to the
        lead's b2 and does NOT claim a poisoned/empty-team marker.

        DISTINCT TRIGGER from test_handoff_writability_parity.py's
        ``test_unwritable_fire_defers_no_marker_no_event``: that test forces
        ``get_journal_path()==''`` DIRECTLY (independent of context). This test
        drives unwritability through the EMPTY CONTEXT and lets the REAL
        ``get_journal_path()`` resolve — proving the post-rebind STRENGTHENING:
        because the marker team_name and the journal path now derive from the
        SAME ``get_pact_context()``/``get_session_dir()`` source, an empty
        context zeroes BOTH (team_name='' AND journal path='') so the defer
        gate fires at least as often as it must. The marker claim is never
        reached, so no empty-team marker can be poisoned.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        import io
        import json
        from unittest.mock import patch

        import agent_handoff_emitter as _emitter
        import shared.pact_context as _pc
        from shared.agent_handoff_marker import (
            already_emitted as _real_already_emitted,
        )

        # Real _EMPTY_CONTEXT (all-empty-string keys) — what get_pact_context
        # returns when the session context file is missing/unreadable, i.e. a
        # teammate frame whose context is unpersisted (#877 is_lead-gated).
        empty_ctx = dict(_pc._EMPTY_CONTEXT)

        reached_marker_claim = {"value": False}

        def _spy_already_emitted(team_name, task_id, occupant):
            reached_marker_claim["value"] = True
            return _real_already_emitted(team_name, task_id, occupant)

        monkeypatch.setattr(_emitter, "already_emitted", _spy_already_emitted)

        calls: list[dict] = []

        def _append_spy(event):
            calls.append(event)
            return True

        stdin_payload = {
            "task_id": "42",
            "task_subject": "empty-context teammate frame",
            "teammate_name": "probe-agent",
            # A stdin team_name is present but POST-REBIND it is never read —
            # the empty context is authoritative.
            "team_name": "session-whatever",
        }
        task_data = {
            "status": "completed",
            "owner": "probe-agent",
            "metadata": {"handoff": VALID_HANDOFF},
        }

        # CRITICAL: do NOT patch get_journal_path — let it resolve for REAL
        # from the (empty) context so the shared-source coupling is genuinely
        # exercised. Only get_pact_context is patched to the empty context.
        with patch.object(
            _emitter.pact_context, "get_pact_context", return_value=empty_ctx
        ), patch.object(
            _emitter, "read_task_json", return_value=task_data
        ), patch.object(
            _emitter, "append_event", side_effect=_append_spy
        ), patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
            # Sanity: the REAL get_journal_path resolves to '' under empty ctx.
            assert _emitter.get_journal_path() == "", (
                "precondition broken: empty context must zero get_journal_path() "
                "via the shared get_session_dir() source — if it does not, the "
                "post-rebind shared-source strengthening claim is false."
            )
            with pytest.raises(SystemExit) as exc_info:
                _emitter.main()

        assert exc_info.value.code == 0, "defer path must exit 0 (suppress)."
        assert reached_marker_claim["value"] is False, (
            "the emitter reached already_emitted (the O_EXCL marker claim) on "
            "an empty-context teammate frame — the #917 writability gate at "
            ":253 must short-circuit BEFORE the marker claim at :269. An empty "
            "context could otherwise poison a marker keyed on an empty team_name."
        )
        assert calls == [], "an unresolvable teammate frame must write no event."
        # No marker dir of any kind was created (the claim was never reached).
        teams_dir = tmp_path / ".claude" / "teams"
        created_markers = (
            [str(p) for p in teams_dir.rglob(".agent_handoff_emitted")]
            if teams_dir.exists()
            else []
        )
        assert created_markers == [], (
            f"a deferred (empty-context) fire claimed marker dir(s): "
            f"{created_markers}. The defer gate must run before any marker join."
        )

    @pytest.mark.parametrize(
        "session_id_input",
        [
            "0001639f",          # canonical lowercase hex
            "deadbeef",          # all hex letters
            "ABCDEF12",          # uppercase non-[a-f0-9] chars stripped
            "../../etc",         # traversal primitives stripped
            "a/b\\c.d",          # slashes + dot stripped
            "sess ion!",         # space + punctuation stripped
            "",                  # empty session_id → random hex fallback
        ],
    )
    def test_marker_team_name_source_is_path_safe(self, session_id_input):
        """AC-5 sanitize-drop safety: the marker team_name SOURCE is path-safe
        BY CONSTRUCTION, so b1 dropping its producer-side
        ``sanitize_path_component`` wrapper on team_name does NOT open a
        traversal vector.

        ``generate_team_name`` is the SSOT producer for every PACT-minted team
        directory name (pact_context.py INVARIANT). It strips every character
        outside ``[a-f0-9-]`` from session_id[:8] (falling back to a random hex
        suffix) and prefixes ``session-``. So its output is ALWAYS
        ``session-[a-f0-9-]+`` — no ``/``, ``\\``, ``.`` or ``..`` — i.e. it
        can never produce a degenerate or traversal-bearing team_name. This is
        the property the security reviewer leans on: with the producer
        guaranteed path-safe, the consumer-side sanitize on team_name is
        defense-in-depth that is safe to drop; the stdin-axis traversal guard
        (task_id ``sanitize_path_component`` at :156) remains.

        If ``generate_team_name``'s charset/regex ever drifts to admit an
        unsafe character, this test fails — making the sanitize-drop a
        deliberate, test-gated re-decision rather than a silent regression.
        """
        from shared.pact_context import generate_team_name

        team_name = generate_team_name({"session_id": session_id_input})

        # Structural invariant: session- prefix + only [a-f0-9-] thereafter.
        assert team_name.startswith("session-"), (
            f"generate_team_name({session_id_input!r}) = {team_name!r} lost the "
            f"'session-' prefix — the platform-team adoption invariant broke."
        )
        suffix = team_name[len("session-"):]
        assert suffix, (
            f"generate_team_name({session_id_input!r}) produced an empty suffix "
            f"{team_name!r}; the random-hex fallback must guarantee non-empty."
        )
        assert re.fullmatch(r"[a-f0-9-]+", suffix), (
            f"generate_team_name({session_id_input!r}) = {team_name!r}; suffix "
            f"{suffix!r} contains a non-[a-f0-9-] character — a path-unsafe "
            f"team_name could reach the marker join now that b1 dropped its "
            f"sanitize wrapper. The AC-5 sanitize-drop safety claim is broken."
        )
        # Explicit traversal-primitive absence (the marker-join threat model).
        assert "/" not in team_name, f"forward slash in team_name {team_name!r}"
        assert "\\" not in team_name, f"backslash in team_name {team_name!r}"
        assert ".." not in team_name, f"parent-dir sequence in {team_name!r}"
        assert "." not in team_name, (
            f"single dot in team_name {team_name!r} — even a lone '.' is a "
            f"degenerate marker-key value the #24 guard would have to catch."
        )

    def test_marker_key_and_task_read_share_one_team_name_source(
        self, tmp_path, monkeypatch
    ):
        """AC-5 single-source invariant: the marker key (already_emitted) and
        the task-status read (read_task_json) consume the SAME team_name value.

        Post-rebind both sinks read the one ``team_name`` local resolved once
        from ``get_pact_context()`` at emitter:157 — read_task_json at :159 and
        already_emitted at :269. A future edit that re-introduced a second
        team_name source for either sink (e.g. re-reading stdin for the status
        read, or sanitizing only one of the two) would split the sinks and the
        marker dir could diverge from the team dir the status read targeted.
        This pins the two sinks to one source by spying BOTH and asserting they
        saw the identical value — regression-proof against a sink split.

        NON-VACUITY: the assertion compares the two captured values directly, so
        it can only pass when both sinks actually fired with the same string. A
        split that fed one sink a different team_name would make the captured
        pair unequal and flip this RED. The companion
        ``test_stdin_team_name_is_inert_post_rebind`` proves the source is the
        CONTEXT channel; this test proves the two downstream sinks do not
        diverge from each other.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        import io
        import json
        from unittest.mock import patch

        import agent_handoff_emitter as _emitter
        from shared.agent_handoff_marker import (
            already_emitted as _real_already_emitted,
        )

        context_team = "session-cafebabe"
        ctx = {
            "team_name": context_team,
            "session_id": "",
            "project_dir": "",
            "plugin_root": "",
            "started_at": "",
        }
        task_data = {
            "status": "completed",
            "owner": "probe-agent",
            "metadata": {"handoff": VALID_HANDOFF},
        }

        marker_team_names: list[str] = []
        read_team_names: list[str] = []
        calls: list[dict] = []

        def _spy_already_emitted(team_name, task_id, occupant):
            marker_team_names.append(team_name)
            return _real_already_emitted(team_name, task_id, occupant)

        def _spy_read_task_json(task_id, team_name, *args, **kwargs):
            read_team_names.append(team_name)
            return task_data

        def _append_spy(event):
            calls.append(event)
            return True

        stdin_payload = {
            "task_id": "42",
            "task_subject": "single-source probe",
            "teammate_name": "probe-agent",
            # A stdin team_name is present but inert post-rebind; the context
            # channel is authoritative for BOTH sinks.
            "team_name": "session-stdin-ignored",
        }

        # Drive main() directly (NOT via _run_main, whose own read_task_json
        # patch would shadow the spy) so the read_task_json spy actually fires
        # and captures the team_name the status read consumed.
        with patch.object(
            _emitter.pact_context, "get_pact_context", return_value=ctx
        ), patch.object(
            _emitter, "read_task_json", side_effect=_spy_read_task_json
        ), patch.object(
            _emitter, "already_emitted", side_effect=_spy_already_emitted
        ), patch.object(
            _emitter, "append_event", side_effect=_append_spy
        ), patch.object(
            _emitter, "get_journal_path",
            return_value="/pact-test/session-journal.jsonl",
        ), patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
            with pytest.raises(SystemExit) as exc_info:
                _emitter.main()

        assert exc_info.value.code == 0, "emit path must exit 0."

        # Both sinks fired exactly once (the emit path ran end-to-end).
        assert read_team_names == [context_team], (
            f"read_task_json saw team_name(s) {read_team_names!r}; the status "
            f"read must consume the context team_name {context_team!r}."
        )
        assert marker_team_names == [context_team], (
            f"already_emitted saw team_name(s) {marker_team_names!r}; the marker "
            f"key must consume the context team_name {context_team!r}."
        )
        # THE INVARIANT: one source feeds both sinks — the captured values are
        # identical. A sink split (a second team_name source for either) breaks
        # this equality.
        assert read_team_names == marker_team_names, (
            f"team_name SOURCE SPLIT: read_task_json saw {read_team_names!r} but "
            f"already_emitted saw {marker_team_names!r}. Post-AC-5 both sinks "
            f"MUST read the single team_name local resolved once from "
            f"get_pact_context() at emitter:157 — a divergence means a future "
            f"edit re-introduced a second team_name source for one sink."
        )
        assert len(calls) == 1, "the single-source emit path must emit once."

