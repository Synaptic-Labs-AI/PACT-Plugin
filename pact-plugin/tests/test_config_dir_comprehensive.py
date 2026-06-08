"""
Comprehensive TEST-phase coverage for #926 (CLAUDE_CONFIG_DIR-aware path
resolution), BEYOND the per-commit verification tests devops shipped in CODE.

What is ALREADY covered (do not duplicate):
  - L1 resolver contract (all precedence branches, ~/x exact-slice, FOLLOWS
    accessors) ........................................ test_paths.py (21 tests)
  - L2 dispatch-flip happy path + relative_to escape (task_utils idiom) under a
    relocated config ................. test_config_dir_dispatch_integration.py
  - parents-based idiom POSITIVE under relocated config + sibling-prefix under
    the DEFAULT root .......... test_session_registry.py (C4b: L245 / L289 / L304)

What THIS file adds (the genuine comprehensive gaps):
  1. The INTERSECTION devops's tests miss: the parents-based sibling-prefix
     collision (`<config>/pact-sessions-evil`) under a RELOCATED config dir, for
     BOTH parents-based anchors (session_init:433 + session_registry:107), plus a
     traversal-escape negative. The default-root sibling test and the
     relocated-positive test each cover one axis; neither covers their product.
  2. session_init:350 required-dir membership config-awareness (deferred from
     C5 as a tip-check, per #13 metadata.test_phase_items).
  3. dispatch_gate both-modes confirmatory pair (in-process vs tmux) — devops's
     L2 file explicitly deferred this to the TEST phase.
  4. Classification STAY-FIXED (section C): install/cache (CLAUDE_PLUGIN_ROOT)
     do NOT follow CLAUDE_CONFIG_DIR — the backstop against over-application.

ANTI-VACUITY (the whole point — a mocked/DI-bypassed seam is a vacuous green):
every test drives the REAL resolver via monkeypatch.setenv("CLAUDE_CONFIG_DIR")
+ Path.home redirect, NEVER the tasks_base_dir DI seam. The redirected home is a
SEPARATE EMPTY dir, so a source-revert of any re-anchoring (back to
Path.home()/".claude") flips the assertion — these tests FAIL BEHAVIORALLY, and
reference only pre-existing production entrypoints (no post-fix-only symbol), so a
revert fails on the assertion, not on an ImportError artifact.

Counter-test cardinality (source-only revert of the re-anchoring, leaving these
tests in place) — documented per class below.

L3 (live @-ref + real-session dispatch) is RUNBOOK-deferred (lead-confirmed):
see tests/runbooks/926-config-dir-live-probe.md — platform @-ref resolution
happens at real session bootstrap, which pytest cannot drive.
"""
import json
import os
from pathlib import Path

import pytest

from shared.dispatch_helpers import has_task_assigned
import shared.session_registry as sr
from shared.plugin_manifest import _resolve_plugin_root


@pytest.fixture
def relocated(tmp_path, monkeypatch):
    """Non-default CLAUDE_CONFIG_DIR + a SEPARATE empty redirected home.

    Returns (config_dir, fake_home). The empty fake_home is the anti-vacuity
    lever: any anchor that lags on Path.home() resolves into this empty tree and
    the corresponding assertion flips.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    return config_dir, fake_home


class TestParentsBasedContainmentUnderRelocatedConfig:
    """Section-B teeth: the parents-based containment idiom
    (`candidate == root or root in candidate.parents`) must re-anchor to the
    relocated root AND keep rejecting the sibling-prefix-collision class.

    Counter-test cardinality (source-only revert of the :433 / :107 root
    re-anchoring back to Path.home()): the legit-accept tests flip to None
    (reject) and the silent-bail surfaces -> {2 accept-tests fail}. The reject
    tests stay green under that revert (a home-anchored root still rejects a
    $CONFIG sibling) but flip under a str-prefix regression of the idiom.
    """

    def test_session_init_validate_accepts_legit_path_under_relocated_config(self, relocated):
        config_dir, _ = relocated
        from session_init import _validate_under_pact_sessions

        legit = str(config_dir / "pact-sessions" / "myproject" / "abc12345")
        # Non-vacuous: if sessions_root lagged on Path.home()/.claude, this
        # $CONFIG-rooted candidate would NOT be under it -> None.
        assert _validate_under_pact_sessions(legit) == legit

    def test_session_init_validate_rejects_sibling_prefix_under_relocated_config(self, relocated):
        config_dir, _ = relocated
        from session_init import _validate_under_pact_sessions

        # THE GAP: sibling-prefix collision under the RELOCATED root. A naive
        # str-prefix check ("<config>/pact-sessions-evil".startswith(
        # "<config>/pact-sessions")) would WRONGLY accept; the parents-based
        # idiom rejects.
        sibling = str(config_dir / "pact-sessions-evil" / "stolen" / "x")
        assert _validate_under_pact_sessions(sibling) is None

    def test_session_init_validate_rejects_traversal_escape_under_relocated_config(self, relocated):
        config_dir, _ = relocated
        from session_init import _validate_under_pact_sessions

        escape = str(config_dir / "pact-sessions" / ".." / ".." / "etc" / "passwd")
        assert _validate_under_pact_sessions(escape) is None

    def test_session_registry_rejects_sibling_prefix_under_relocated_config(self, relocated):
        config_dir, _ = relocated
        # Complements C4b: L245 covers the sibling under the DEFAULT root and
        # L304 covers a legit path under a relocated root; THIS covers their
        # product — sibling-prefix under the RELOCATED root.
        sibling = config_dir / "pact-sessions-evil" / "x.jsonl"
        assert sr._is_under_pact_sessions(sibling) is False

    def test_session_registry_accepts_legit_under_relocated_config(self, relocated):
        config_dir, _ = relocated
        legit = config_dir / "pact-sessions" / "team" / "reg.jsonl"
        # Non-vacuous: a home-lagged anchor rejects this $CONFIG path.
        assert sr._is_under_pact_sessions(legit) is True


class TestSessionInit350RequiredDirConfigAwareness:
    """session_init:350 required-dir membership check must anchor the required
    {teams, pact-sessions} set on get_claude_config_dir(), so the setup tip
    reflects the relocated config dir.

    Counter-test cardinality (source-only revert of the :350/:351-354 anchor
    back to Path.home()/.claude): the non-vacuity test below flips
    (old-home paths would then satisfy `required`, so no tip) -> {1 fail}.
    """

    def _write_settings(self, config_dir, additional_dirs):
        (config_dir / "settings.json").write_text(
            json.dumps({"permissions": {"additionalDirectories": additional_dirs}}),
            encoding="utf-8",
        )

    def test_no_tip_when_config_dir_paths_are_configured(self, relocated):
        config_dir, _ = relocated
        from session_init import check_additional_directories

        self._write_settings(
            config_dir,
            [str(config_dir / "teams"), str(config_dir / "pact-sessions")],
        )
        assert check_additional_directories() is None

    def test_tip_fires_when_only_old_home_paths_configured(self, relocated):
        config_dir, fake_home = relocated
        from session_init import check_additional_directories

        # NON-VACUITY: configure the PRE-#926 home-based paths. Because the
        # required set now anchors on $CONFIG, these stale paths no longer
        # satisfy it -> a tip MUST fire. If :350 had stayed on Path.home(),
        # required would equal these paths -> no tip -> this assertion fails.
        self._write_settings(
            config_dir,
            [
                str(fake_home / ".claude" / "teams"),
                str(fake_home / ".claude" / "pact-sessions"),
            ],
        )
        tip = check_additional_directories()
        assert tip is not None
        assert "additionalDirectories" in tip


class TestDispatchGateBothModesUnderRelocatedConfig:
    """Both-modes confirmatory pair (deferred from devops's L2 file).

    The #926 resolver keys on $CLAUDE_CONFIG_DIR (process env) and the dispatch
    gate keys on team_name (arg) — NEITHER consults session topology — so
    has_task_assigned resolves IDENTICALLY under the in-process
    (session_id == leadSessionId) and tmux (session_id != leadSessionId)
    teammateModes. This pair asserts that invariance, satisfying the standing
    dual-mode merge-gate convention WITHOUT implying a mode branch exists (there
    is none; a divergence here would itself be the finding).
    """

    def _write_task(self, config_dir, team, owner):
        team_dir = config_dir / "tasks" / team
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "5.json").write_text(
            json.dumps({"id": "5", "owner": owner, "status": "in_progress"}),
            encoding="utf-8",
        )

    @pytest.mark.parametrize(
        "mode,session_id,lead_session_id",
        [
            ("in_process", "sess-AAAA", "sess-AAAA"),   # session_id == leadSessionId
            ("tmux", "sess-BBBB", "sess-AAAA"),         # session_id != leadSessionId
        ],
    )
    def test_dispatch_resolves_identically_across_modes(
        self, relocated, monkeypatch, mode, session_id, lead_session_id
    ):
        config_dir, _ = relocated
        team = "pact-relocated-team"
        owner = "test-engineer"
        self._write_task(config_dir, team, owner)
        # Set the mode-discriminating signals as ambient env; the resolution
        # path does not read them, which is exactly the invariance under test.
        monkeypatch.setenv("CLAUDE_SESSION_ID", session_id)
        monkeypatch.setenv("PACT_LEAD_SESSION_ID", lead_session_id)
        assert has_task_assigned(team, owner) is True, (
            f"dispatch resolution diverged under teammateMode={mode}"
        )


class TestClassificationStayFixed:
    """Section C: install/cache paths key on CLAUDE_PLUGIN_ROOT and MUST NOT
    follow CLAUDE_CONFIG_DIR (architect spec: 0 Path.home() sites for
    install/cache). Guards against R3 over-application — a future change routing
    plugin-root through the config resolver.
    """

    def test_plugin_root_does_not_follow_config_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/opt/plugin/root")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfgA"))
        assert _resolve_plugin_root() == "/opt/plugin/root"
        # Relocate the CONFIG dir; plugin root MUST be unchanged.
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfgB-different"))
        assert _resolve_plugin_root() == "/opt/plugin/root"

    def test_plugin_root_reader_does_not_route_through_config_resolver(self):
        # Negative source backstop: the plugin-root reader must not derive from
        # the config-dir resolver (over-application would couple install paths to
        # CLAUDE_CONFIG_DIR). Asserts the anti-pattern is absent at the source.
        import inspect
        import shared.plugin_manifest as pm

        src = inspect.getsource(pm._resolve_plugin_root)
        assert "get_claude_config_dir" not in src
        assert "CLAUDE_CONFIG_DIR" not in src
