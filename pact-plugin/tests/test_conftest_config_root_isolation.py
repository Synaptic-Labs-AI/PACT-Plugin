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
import ast
import os
from pathlib import Path

import pytest

import merge_guard_post
import merge_guard_pre
import session_init
import shared.merge_guard_common as common
from shared.paths import get_claude_config_dir

# Read at IMPORT time — collection runs before any function-scoped autouse
# fixture, so this is the ambient value the scrub will later remove. Capturing
# it is what lets the session-id cell below tell "the scrub fired" apart from
# "there was nothing to scrub", which absence alone cannot distinguish.
_AMBIENT_SESSION_ID = os.environ.get("CLAUDE_CODE_SESSION_ID")

# Same import-time capture discipline for the CLAUDE_PROJECT_DIR scrub pin
# below: on a machine whose shell exports the var (a Claude Code hook process
# or a developer shell that sourced one) the ambient-removal cell
# discriminates; on CI it skips by the same logic as the session-id cell.
_AMBIENT_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR")

# The EXACT module population of the claude_md_ambient opt-out (architect
# ruling): module-level pytestmark is the multiplicative hazard — one line
# exempts every test in the file — so its membership is pinned as a set,
# both directions. Per-test decoration is the designed granularity and is
# deliberately NOT pinned.
_MODULE_WIDE_AMBIENT = frozenset({
    "test_claude_md_resolver_parity",
    "test_containment_guard",
    "test_project_dir_resolution",
    "test_prose_root_gate",
    "test_removed_worktree_record",
    "test_resolver_git_env",
    "test_working_memory_worktree_sync",
})


def _pytestmark_carries_ambient(tree):
    """True when a module-level ``pytestmark`` assignment carries
    ``pytest.mark.claude_md_ambient`` — as a bare Attribute or as a list
    element (the list form is pytest's documented multi-mark shape, so the
    pin must see it even though no member uses it today)."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in node.targets
        ):
            continue
        elements = (
            node.value.elts
            if isinstance(node.value, (ast.List, ast.Tuple))
            else [node.value]
        )
        for element in elements:
            if (
                isinstance(element, ast.Attribute)
                and isinstance(element.value, ast.Attribute)
                and isinstance(element.value.value, ast.Name)
                and element.value.value.id == "pytest"
                and element.value.attr == "mark"
                and element.attr == "claude_md_ambient"
            ):
                return True
    return False


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

    def test_scrub_removes_an_ambient_session_id(self):
        """Pin the SECOND autouse scrub, ``_scrub_session_id_from_test_env``.

        That fixture removes ``CLAUDE_CODE_SESSION_ID`` so a test process cannot
        resolve the developer's live session and compute paths belonging to it.
        It is deliberately NOT redundant with the ``PYTEST_CURRENT_TEST`` refusal
        in ``pact_session._discover_session_id``: the two fail on different
        signals, and the scrub is the one that still holds if a spawn-environment
        allowlist keeps the session id and drops the pytest marker. Nothing else
        in the suite observes it — removing the fixture body leaves every other
        test green.

        STRONGER THAN THE CELL ABOVE, deliberately. Absence alone cannot tell
        "the scrub fired" from "there was nothing to scrub", so this cell SKIPS
        rather than passes when the ambient value was never present. A skip is
        visible in the pytest header; a trivial pass is not.
        """
        if _AMBIENT_SESSION_ID is None:
            pytest.skip(
                "no ambient CLAUDE_CODE_SESSION_ID was present at collection, so "
                "this cell cannot discriminate a working scrub from an absent "
                "input — it is meaningful only when the suite runs under a "
                "session that exports one"
            )

        assert "CLAUDE_CODE_SESSION_ID" not in os.environ, (
            "CLAUDE_CODE_SESSION_ID survived into the test body although the "
            "ambient environment carried one at collection — the autouse scrub "
            "did not fire, and a test resolving the developer's live session id "
            "can compute and write paths that belong to it."
        )


class TestAutouseProjectDirScrubPinned:
    """Pin the autouse ``_scrub_claude_project_dir_env`` fixture's scrub.

    ``CLAUDE_PROJECT_DIR`` is a live production input, not just test plumbing:
    ``pact_context.init()`` derives the session context path from it and
    ``backlog.project_root()`` anchors project resolution on it (NOT the
    process cwd). An ambient value leaking into the suite flips
    environment-sensitive tests from deterministic to machine-dependent — the
    measured shape was three dev-machine-red / CI-green failures
    (test_backlog's "refusal: no root" exit-code arm; both
    test_bootstrap_prompt_gate no-session tests, where init() re-derives a
    path the test's ``_context_path = None`` patch cannot prevent). This
    class is the delete-the-fix counter-test for the scrub: restore the old
    snapshot/restore-only posture (or remove the setup POP) under an
    exporting shell and the cells below go red.
    """

    @pytest.mark.claude_md_ambient
    def test_scrub_removes_an_ambient_project_dir(self):
        """Same skip-unless-ambient discipline as the session-id cell: absence
        alone cannot tell "the scrub fired" from "there was nothing to
        scrub", so skip (visible in the header) when the ambient was clean.

        AMENDED for the suite-wide CLAUDE.md redirect: this cell is marked
        ``claude_md_ambient``, so the redirect SKIPS it and the scrub is the
        only autouse actor over ``CLAUDE_PROJECT_DIR`` — the same world every
        other marked test lives in, and the one where the scrub's pop does
        the protecting. Unmarked tests see the redirect's per-test tmp
        declaration instead (scrub pops, redirect sets — the ordering the
        redirect's dependency parameter forces); that half of the contract is
        pinned structurally by the parameter, and a redirect that stopped
        honoring the marker reddens HERE (the var would arrive set).
        """
        if _AMBIENT_PROJECT_DIR is None:
            pytest.skip(
                "no ambient CLAUDE_PROJECT_DIR was present at collection, so "
                "this cell cannot discriminate a working scrub from an absent "
                "input — it is meaningful only when the suite runs under a "
                "shell that exports one"
            )

        assert "CLAUDE_PROJECT_DIR" not in os.environ, (
            "CLAUDE_PROJECT_DIR survived into the test body although the "
            "ambient environment carried one at collection — the autouse scrub "
            "did not fire, and env-keyed resolvers (pact_context.init, "
            "backlog.project_root) are resolving against the developer's live "
            "project instead of the test's inputs."
        )

    @pytest.mark.claude_md_ambient
    def test_init_cannot_derive_a_context_path_from_ambient_env(self):
        """Mechanism pin for the measured failure mode: with the var scrubbed,
        ``pact_context.init()`` must leave ``_context_path`` None for an input
        carrying only a session_id — the exact precondition the
        bootstrap no-session tests rely on (no session dir -> no-op gate).

        AMENDED for the suite-wide CLAUDE.md redirect: marked
        ``claude_md_ambient`` for the same reason as the sibling cell above —
        the marked world is scrub-only, which is the precondition this
        mechanism pin has always asserted. An unmarked test would carry the
        redirect's tmp declaration, from which init() MAY legitimately derive
        an in-tmp context path.
        """
        import shared.pact_context as ctx

        ctx.reset_for_tests()
        try:
            ctx.init({"session_id": "probe-no-project-dir"})
            assert ctx._context_path is None, (
                f"init() derived a context path ({ctx._context_path!r}) from "
                "an input with no project-dir axis — an ambient "
                "CLAUDE_PROJECT_DIR leaked through the autouse scrub and "
                "re-anchored session resolution onto the live machine state."
            )
        finally:
            ctx.reset_for_tests()

    def test_redirect_declaration_survives_scrub_at_test_body(self, tmp_path):
        """UNMARKED ORDERING PIN (§11.3.1 class (e) amendment, architect
        ruling): at an unmarked test body, the redirect's declaration must be
        the value in force — the scrub POPPED at setup and the redirect SET
        after it, the ordering the redirect's dependency parameter forces.

        The direct witness for the unmarked half of the contract the marked
        cells above cannot see: they measure the scrub-only world, so a
        redirect that stopped honoring the marker reddens THERE, while a
        removed dependency parameter or an inverted setup order reddens HERE
        first (the pop would land after the set and the declaration would be
        gone).
        """
        assert os.environ.get("CLAUDE_PROJECT_DIR") == str(tmp_path), (
            "CLAUDE_PROJECT_DIR at the test body is not the redirect's "
            "per-test tmp declaration — the scrub's pop ran after the "
            "redirect's set (ordering broken), or the redirect did not run"
        )

    def test_hf_cache_pins_survive_at_test_body(self):
        """The redirect's THIRD half, witnessed in-process at an UNMARKED
        test body: the HuggingFace pins must be in force. PRESENCE and
        offline-ness only — NO path-equality assert, because
        ``hf_cache_env`` deliberately MIRRORS huggingface_hub's own
        resolution (env vars first, expanduser fallback), so pinning a
        specific path here would pin the helper's internals rather than
        the property spawned children depend on: no network reach, and a
        cache that is not the operator's live one."""
        assert os.environ.get("HF_HUB_OFFLINE") == "1", (
            "HF_HUB_OFFLINE was not in force at the test body — a spawned "
            "child with a cold cache will attempt an unbounded network read"
        )
        assert os.environ.get("HF_HOME"), (
            "HF_HOME was not in force at the test body — a spawned child "
            "will resolve an ambient cache instead of the redirect's pin"
        )

    def test_claude_md_ambient_marker_is_registered(self, pytestconfig):
        """The opt-out marker must be REGISTERED in pytest_configure, not
        merely attached at sites: the fixture checks the marker by NAME
        (``get_closest_marker`` works unregistered), so a dropped
        registration breaks no test at the marker sites — it only erases
        the marker's documentation and its --strict-markers/census
        contract. This pin reddens if the registration line leaves
        ``pytest_configure``, keeping the class vocabulary defined in the
        registration text reachable from every census-tagged site."""
        markers = pytestconfig.getini("markers")
        assert any(
            line.startswith("claude_md_ambient:") for line in markers
        ), (
            "claude_md_ambient is not among the registered markers — the "
            "opt-out sites and their census tags resolve against the "
            "registration text in pytest_configure"
        )

    def test_module_wide_claude_md_ambient_census_is_exact(self):
        """EXACT-SET pin on the MODULE population of the claude_md_ambient
        opt-out (architect ruling): a module-level ``pytestmark`` exempts
        EVERY test in its file, so over-opt-out is the multiplicative
        hazard and membership is pinned BOTH directions — any module
        BEYOND ``_MODULE_WIDE_AMBIENT`` opting out module-wide, or any
        module IN the set dropping the mark, reddens here. Per-test
        decoration is the designed granularity and is deliberately
        UNPINNED (its growth is a census decision, not a regression).

        Scan shape: a tree AST census (test_conftest_import_guard style),
        NOT collection-time marker introspection — the AST walk follows
        the tree wherever a file moves and reads no pytest runtime state,
        while a collection-derived set would need session-wide item
        plumbing to see the same population."""
        tests_dir = Path(__file__).resolve().parent
        found = {
            path.stem
            for path in sorted(tests_dir.rglob("test_*.py"))
            if _pytestmark_carries_ambient(
                ast.parse(path.read_text(encoding="utf-8"))
            )
        }
        assert found == _MODULE_WIDE_AMBIENT, (
            f"module-wide claude_md_ambient population drifted — "
            f"added: {sorted(found - _MODULE_WIDE_AMBIENT)}, "
            f"removed: {sorted(_MODULE_WIDE_AMBIENT - found)}. Module-level "
            "opt-out is the multiplicative hazard; per-test decoration is "
            "the designed granularity"
        )

    def test_unmarked_resolution_lands_on_the_legacy_seed(self, tmp_path):
        """The redirect's BOTH-HALVES contract, asserted in-process at an
        UNMARKED test body: resolution must terminate branch 1 on the
        fixture's LEGACY seed (``tmp_path/CLAUDE.md``, base=``tmp_path``),
        with the preferred slot never created.

        The declaration half alone is pinned by the ordering pin above, and
        the memory_cli proof class pins the seed half through a spawned
        child — but both are one hop removed from the composed in-process
        contract, and this is the assertion that reddens DIRECTLY on either
        half regressing: a conftest edit that drops the seed (setenv-only —
        the silent no-op trap the isolation issue names) sends resolution
        through the git walk to the ambient anchors, and one that seeds the
        preferred slot (``tmp_path/.claude``) instead of legacy breaks the
        §11.2 shape (M2's collision surface). No bytes move: this test only
        resolves and asserts.
        """
        from scripts.working_memory import _resolve_display_claude_md_with_base

        assert (tmp_path / "CLAUDE.md").exists(), (
            "the conftest redirect did not seed the legacy slot for this "
            "unmarked test — resolution will fall through to the git walk"
        )
        assert not (tmp_path / ".claude").exists(), (
            "the redirect created the preferred slot, violating the §11.2 "
            "legacy-only shape"
        )
        path, base = _resolve_display_claude_md_with_base()
        assert path == tmp_path / "CLAUDE.md", (
            f"an unmarked test's resolution landed on {path} (base {base}) "
            "instead of the fixture's legacy seed — the redirect is not "
            "terminating branch 1 in-tmp"
        )
        assert base == tmp_path, (
            f"resolution reported base {base} — branch 1 did not answer "
            "from the declared project dir"
        )


class TestAutouseMergeTokenDirIsolationPinned:
    """Pin the autouse ``_isolate_merge_token_dir_to_tmp`` fixture.

    ``TOKEN_DIR`` is bound at import, before any autouse fixture runs, so the
    ``Path.home`` redirect never reaches it. The hook modules are imported
    here at collection time, which is when they bind the real config root.
    """

    def test_every_hook_copy_of_the_token_dir_lands_under_tmp(self, tmp_path):
        """An in-process ``session_init.main()`` cleans orphan merge tokens in
        its module's ``TOKEN_DIR``. Each hook's copy must be the test's tmp
        config root. Disabling the fixture leaves them all at the real
        ``~/.claude``, and this fails."""
        for module in (common, session_init, merge_guard_pre, merge_guard_post):
            assert module.TOKEN_DIR == tmp_path / ".claude", (
                f"{module.__name__}.TOKEN_DIR is {module.TOKEN_DIR!r}, not the "
                "test's tmp config root: a hook run in process would glob and "
                "unlink merge tokens in the operator's real config root.")
