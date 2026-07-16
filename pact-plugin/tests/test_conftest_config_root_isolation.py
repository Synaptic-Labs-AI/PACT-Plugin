"""
Location: pact-plugin/tests/test_conftest_config_root_isolation.py
Summary: Positive regression pin for the autouse conftest fixture
         ``_isolate_config_root_to_tmp`` (PR #1189 / #1186). The fixture scrubs
         ``CLAUDE_CONFIG_DIR`` and redirects ``Path.home()`` -> ``tmp_path`` so
         every PACT state writer resolves under the per-test tmp, NEVER the
         operator's real ``~/.claude``. The real-writer suites
         (test_agent_handoff_marker, test_snapshot_marker_root_fallback, ...)
         prove the closure MECHANISM — but each SELF-PATCHES ``Path.home``, so
         they stay green even if the fixture were disabled: a future edit
         removing or breaking the fixture would reopen #1186 SILENTLY (suite
         green, destructive leak resumed). This file is the delete-the-fix
         counter-test that pins the FIXTURE's own closure.
Used by: pytest.
"""
import os

from shared.paths import get_claude_config_dir


class TestAutouseConfigRootIsolationPinned:
    """Pin the autouse ``_isolate_config_root_to_tmp`` fixture's closure.

    DELIBERATELY does NOT self-patch ``Path.home`` and does NOT set
    ``CLAUDE_CONFIG_DIR`` in-body: it relies SOLELY on the autouse fixture. That
    is what makes it a delete-the-fix counter-test for the fixture itself,
    rather than a redundant mechanism assertion (the closure mechanism is
    already covered by the real-writer suites, which self-patch).
    """

    def test_resolver_lands_under_tmp_not_real_home(self, tmp_path):
        """The SSOT resolver ``get_claude_config_dir()`` — every PACT state
        writer resolves through it — must land under the autouse fixture's
        ``tmp_path``, NOT the operator's real ``~/.claude``.

        Under the fixture: ``CLAUDE_CONFIG_DIR`` is scrubbed (unset) at setup
        and ``Path.home()`` -> ``tmp_path``, so the SSOT resolves to
        ``tmp_path / ".claude"`` (the HOME fallthrough — precedence-1 is empty).

        COUNTER-TEST (the pinning property this file exists for): if the
        fixture's ``monkeypatch.setattr(Path, "home", ...)`` is removed,
        ``Path.home()`` is the operator's real home -> ``get_claude_config_dir``
        resolves to real ``~/.claude`` -> the ``startswith(tmp_path)``
        assertion FAILS. If the scrub is removed AND an ambient
        ``CLAUDE_CONFIG_DIR`` is exported, precedence-1 resolves to that
        ambient value -> the assertion also FAILS. Verified by local
        fixture-disable (setattr line removed), reverted before staging.
        """
        resolved = get_claude_config_dir()

        # (1) Lands under the autouse fixture's tmp, NOT the real home.
        assert str(resolved).startswith(str(tmp_path)), (
            f"get_claude_config_dir() resolved to {resolved!r}, NOT under the "
            f"autouse fixture's tmp_path {str(tmp_path)!r}. The autouse "
            "_isolate_config_root_to_tmp closure has REGRESSED (Path.home "
            "setattr removed, or CLAUDE_CONFIG_DIR scrub removed with an "
            "ambient value leaking through) — #1186's destructive leak would "
            "resume silently under a green suite."
        )
        # (2) Specifically the .claude leaf (the HOME-fallthrough shape), not
        # some other tmp-rooted path — pins the exact resolution contract.
        assert resolved == tmp_path / ".claude", (
            f"expected tmp_path / '.claude', got {resolved!r}"
        )

    def test_scrub_leaves_claude_config_dir_absent_during_body(self, tmp_path):
        """Companion observability for the scrub half of the closure: after the
        autouse fixture's setup scrub fires, ``CLAUDE_CONFIG_DIR`` is absent
        from ``os.environ`` during the test body, so the HOME fallthrough is
        the LIVE resolution path (precedence-1 is empty).

        Honest scope of this cell: it is NOT a strong standalone counter-test —
        a clean ambient env with no ``CLAUDE_CONFIG_DIR`` makes it pass
        trivially. Paired with the cell above it documents and partially pins
        the scrub posture: a regression that leaves an inherited
        ``CLAUDE_CONFIG_DIR`` set would surface here whenever the suite runs
        under an env that exports it.
        """
        assert "CLAUDE_CONFIG_DIR" not in os.environ, (
            "CLAUDE_CONFIG_DIR is present during the test body — the autouse "
            "fixture's setup scrub did not fire; a contributor env exporting it "
            "would leak through and shadow the HOME fallthrough (precedence-1)."
        )
        assert get_claude_config_dir() == tmp_path / ".claude"
