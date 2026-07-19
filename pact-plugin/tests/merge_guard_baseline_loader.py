"""
Location: pact-plugin/tests/merge_guard_baseline_loader.py
Summary: Loud-fail loaders for the COMMITTED vendored merge-guard baseline fixtures.
         TWO independent pre-fix baselines, each vendored as committed bytes (never a
         `git show` of a SHA a shallow clone cannot resolve — 9 of 14 existing baseline
         SHAs are CI-invisible non-ancestors, the lesson these loaders exist to close):

           load_baseline()          -> fixtures/merge_guard_baseline/merge_guard_common_b4041ccf.py
                                       (pre OBS-A→I; the over-block-cluster certs' base)
           load_baseline_172a77dd() -> fixtures/merge_guard_baseline/merge_guard_common_172a77dd.py
                                       (post OBS-A→I / pre #1134; the three-dimension cert's base)

         RETAIN both. b4041ccf has FIVE cert consumers and is NOT superseded by the
         172a77dd baseline — they pin DIFFERENT pre-fix states (different bug sets).
Used by: b4041ccf  -> test_merge_guard_1181_cert.py, test_merge_guard_1155_cert.py,
                      test_merge_guard_1148_cert.py, test_merge_guard_obs_cert.py,
                      test_merge_guard_overblock_cluster_monotonicity.py
         172a77dd -> test_merge_guard_1134_cert.py

CONTRACT (both loaders — all failure modes are HARD pytest.fail, NEVER skip, no
@requires_history):
  1. Fixture file missing            -> pytest.fail (the cert cannot certify silently).
  2. sha256 mismatch with the pin    -> pytest.fail (fixture bytes drifted).
  3. Pre-fix discriminator rows fail -> pytest.fail (post-fix bytes were vendored:
     the baseline MUST still exhibit the bug(s) its consuming cert fixes).
"""
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

_FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "merge_guard_baseline"
    / "merge_guard_common_b4041ccf.py"
)
# sha256 of merge_guard_common.py at the pre-fix baseline (worktree HEAD b4041ccf).
_FIXTURE_SHA256 = "2c1cf8bc1a95ac310932199d556c11c80f1631a47be363247bff60dfdc52ba23"

# Pre-fix discriminator inputs (danger literals assembled at runtime so this file
# carries no raw destructive literal and stays inert to the live guard).
_DISCRIMINATOR_OVER_BLOCK = "git log --grep '" + "gh " + "pr " + "merge 5 --admin" + "'"
_DISCRIMINATOR_BITE = (
    "gh pr close 5 --comment 'weighed "
    + "gh "
    + "pr "
    + "merge 5 --admin but closing instead'"
)
_DISCRIMINATOR_1148 = (
    'git commit -m "note: ' + "gh " + "pr " + 'merge 5 --admin later" '
    "# reviewed, do not pipe | sh"
)

_cached_baseline = None


def load_baseline():
    """Load the vendored pre-fix classifier module (cached), loud-failing on any
    integrity problem. Returns the module; callers use it exactly like the live
    `shared.merge_guard_common` (e.g. `load_baseline().is_dangerous_command(cmd)`)."""
    global _cached_baseline
    if _cached_baseline is not None:
        return _cached_baseline

    if not _FIXTURE_PATH.is_file():
        pytest.fail(
            "baseline fixture missing (%s) — the bidirectional cert cannot run"
            % _FIXTURE_PATH
        )
    data = _FIXTURE_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != _FIXTURE_SHA256:
        pytest.fail(
            "baseline fixture sha256 mismatch: got %s, pinned %s — fixture bytes "
            "drifted; re-vendor from the pre-fix commit" % (digest, _FIXTURE_SHA256)
        )

    # Import under the `shared` package so the module's relative import
    # (`from .paths import get_claude_config_dir`) resolves against the REAL
    # shared package already importable in the test env.
    spec = importlib.util.spec_from_file_location(
        "shared._merge_guard_baseline", _FIXTURE_PATH
    )
    if spec is None or spec.loader is None:
        pytest.fail(
            "baseline fixture spec unloadable: %s — the bidirectional cert cannot run"
            % _FIXTURE_PATH
        )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "shared"
    spec.loader.exec_module(module)

    # In-test pre-fix discriminators: the loaded baseline MUST still exhibit the
    # three bugs this arc fixes; any failure means post-fix bytes were vendored.
    if module.is_dangerous_command(_DISCRIMINATOR_OVER_BLOCK) is not True:
        pytest.fail(
            "vendored baseline does not exhibit the #1181 read-verb over-block — "
            "post-fix bytes were vendored"
        )
    if module.detect_command_operation_type(_DISCRIMINATOR_BITE) != "merge":
        pytest.fail(
            "vendored baseline does not exhibit the #1155 cross-auth recognition "
            "bug — post-fix bytes were vendored"
        )
    if (
        module._has_pipe_to_shell(module._executed_surface_view(_DISCRIMINATOR_1148))
        is not True
    ):
        pytest.fail(
            "vendored baseline does not exhibit the #1148 comment-survives-view "
            "bug — post-fix bytes were vendored"
        )

    _cached_baseline = module
    return module


# ─────────────────────────────────────────────────────────────────────────────
# Second baseline: post OBS-A→I / pre #1134 (172a77dd), the base for the
# three-dimension cert. A SEPARATE entry point rather than a parameter on
# load_baseline(), so the five existing b4041ccf consumers are byte-untouched.
# ─────────────────────────────────────────────────────────────────────────────
_FIXTURE_PATH_172A77DD = (
    Path(__file__).parent
    / "fixtures"
    / "merge_guard_baseline"
    / "merge_guard_common_172a77dd.py"
)
# sha256 of merge_guard_common.py at 172a77dd (this arc's base, `git show`-vendored).
_FIXTURE_SHA256_172A77DD = (
    "1a3dc4a60bfe534133aea75a39915e78fb9d0617d69ff8278cfc6a6f26c2741b"
)

# Pre-fix discriminator inputs (danger literals assembled at runtime so this file
# carries no raw destructive literal and stays inert to the live guard).
_DISCRIMINATOR_1134_UNDERBLOCK = "cd /repo && " + "git " + "push " + "origin --delete feature"
_DISCRIMINATOR_1134_CLOSE_ASYM = "cd /repo && " + "gh " + "pr " + "close 5 -d"

_cached_baseline_172a77dd = None


def load_baseline_172a77dd():
    """Load the vendored post-OBS-A→I / pre-#1134 classifier module (cached),
    loud-failing on any integrity problem. Same contract and calling convention as
    load_baseline(); callers use it as `load_baseline_172a77dd().is_dangerous_command(cmd)`.

    Pre-fix discriminators (the baseline MUST still exhibit the #1134 under-block, or
    post-fix bytes were vendored and the cert would certify against itself):
      1. `cd … && git push origin --delete feature` runs UNGATED (is_dangerous False) —
         the good-faith-reachable non-first-leg under-block this arc closes.
      2. `cd … && gh pr close 5 -d` is CLASSIFIED (detect == "close") yet UNGATED
         (is_dangerous False) — the pre-fix mint==read asymmetry on the close family
         (the raw-fallback detect arm sees it; the per-leg gate does not). Post-fix both
         are True. This second discriminator also proves the fixture is not merely an
         is_dangerous-False stub: it pins the exact detect/gate SPLIT the fix repairs.
    """
    global _cached_baseline_172a77dd
    if _cached_baseline_172a77dd is not None:
        return _cached_baseline_172a77dd

    if not _FIXTURE_PATH_172A77DD.is_file():
        pytest.fail(
            "172a77dd baseline fixture missing (%s) — the three-dimension cert cannot run"
            % _FIXTURE_PATH_172A77DD
        )
    data = _FIXTURE_PATH_172A77DD.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != _FIXTURE_SHA256_172A77DD:
        pytest.fail(
            "172a77dd baseline fixture sha256 mismatch: got %s, pinned %s — fixture "
            "bytes drifted; re-vendor from `git show 172a77dd:…`"
            % (digest, _FIXTURE_SHA256_172A77DD)
        )

    spec = importlib.util.spec_from_file_location(
        "shared._merge_guard_baseline_172a77dd", _FIXTURE_PATH_172A77DD
    )
    if spec is None or spec.loader is None:
        pytest.fail(
            "172a77dd baseline fixture spec unloadable: %s — the cert cannot run"
            % _FIXTURE_PATH_172A77DD
        )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "shared"
    spec.loader.exec_module(module)

    if module.is_dangerous_command(_DISCRIMINATOR_1134_UNDERBLOCK) is not False:
        pytest.fail(
            "vendored 172a77dd baseline does not exhibit the #1134 non-first-leg "
            "under-block (delete-in-leg-2 already gates) — post-fix bytes were vendored"
        )
    if module.detect_command_operation_type(_DISCRIMINATOR_1134_CLOSE_ASYM) != "close":
        pytest.fail(
            "vendored 172a77dd baseline does not classify the leg-2 close form — the "
            "detect/gate asymmetry the cert pins is absent; wrong bytes were vendored"
        )
    if module.is_dangerous_command(_DISCRIMINATOR_1134_CLOSE_ASYM) is not False:
        pytest.fail(
            "vendored 172a77dd baseline already GATES the leg-2 close form — the pre-fix "
            "mint==read asymmetry is absent; post-fix bytes were vendored"
        )

    _cached_baseline_172a77dd = module
    return module
