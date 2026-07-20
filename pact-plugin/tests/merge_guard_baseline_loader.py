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


# ─────────────────────────────────────────────────────────────────────────────
# Third baseline: v4.6.10 (5017d1f2), the PARENT of the #1203 commits (C1/C3/C3b/
# C1b). The base for test_merge_guard_1203_cert.py. A SEPARATE entry point rather
# than a parameter, so the two existing consumers are byte-untouched. Vendored via
# `git show 5017d1f2:…` (the parent is committed, so the SHA resolves; commit-code-
# first satisfied). At this base every #1203 over-block is PRESENT: the implicit
# force-push and bare `gh pr merge` are gated-but-UNMINTABLE (no sentinel key
# exists yet), and the inert `gh pr merge --help` is gated (no inert recognizer).
# ─────────────────────────────────────────────────────────────────────────────
_FIXTURE_PATH_5017D1F2 = (
    Path(__file__).parent
    / "fixtures"
    / "merge_guard_baseline"
    / "merge_guard_common_5017d1f2.py"
)
# sha256 of merge_guard_common.py at 5017d1f2 (the #1203 parent, `git show`-vendored).
_FIXTURE_SHA256_5017D1F2 = (
    "e6bcca04e22bff115bb6d6be62cd5d397364a64934f5b74a74d4b545e71fab1a"
)

# The identity keys a mintable context may bind (the gated-but-unmintable check:
# a #1203 implicit form is gated at base with NONE of these present).
_MINT_KEYS_1203 = (
    "pr_number", "branch", "branch_set", "target_ref", "push_set",
    "force_push_set", "force_push_implicit", "merge_implicit",
    "mass_target", "protected_branch",
)

# Pre-fix discriminator inputs (danger literals assembled at runtime so this file
# carries no raw destructive literal and stays inert to the live guard).
_DISCRIMINATOR_1203_FP = "git " + "push " + "--force"           # SET A implicit force-push
_DISCRIMINATOR_1203_MERGE = "gh " + "pr " + "merge"             # bare gh pr merge
_DISCRIMINATOR_1203_INERT = "gh " + "pr " + "merge --help"      # inert help over-block

_cached_baseline_5017d1f2 = None


def load_baseline_5017d1f2():
    """Load the vendored v4.6.10 (5017d1f2) classifier module — the PARENT of the
    #1203 commits — cached, loud-failing on any integrity problem. Same contract and
    calling convention as load_baseline(); callers use it as
    `load_baseline_5017d1f2().is_dangerous_command(cmd)`.

    Pre-fix discriminators (the baseline MUST still exhibit the #1203 over-blocks, or
    post-fix bytes were vendored and the cert would certify against itself):
      1. `git push --force` is DANGEROUS (is_dangerous True) yet binds NONE of the mint
         keys — the gated-but-UNMINTABLE implicit force-push over-block C3 closes.
      2. bare `gh pr merge` is DANGEROUS yet binds no mint key — the gated-but-unmintable
         bare-merge over-block C3b closes.
      3. `gh pr merge --help` is DANGEROUS (no inert-help recognizer exists yet) — the
         inert over-block C1 closes. Post-fix all three are fixed (sentinels mint / inert
         un-gates), so a post-fix module fails these discriminators loudly.
    """
    global _cached_baseline_5017d1f2
    if _cached_baseline_5017d1f2 is not None:
        return _cached_baseline_5017d1f2

    if not _FIXTURE_PATH_5017D1F2.is_file():
        pytest.fail(
            "5017d1f2 baseline fixture missing (%s) — the #1203 cert cannot run"
            % _FIXTURE_PATH_5017D1F2
        )
    data = _FIXTURE_PATH_5017D1F2.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != _FIXTURE_SHA256_5017D1F2:
        pytest.fail(
            "5017d1f2 baseline fixture sha256 mismatch: got %s, pinned %s — fixture "
            "bytes drifted; re-vendor from `git show 5017d1f2:…`"
            % (digest, _FIXTURE_SHA256_5017D1F2)
        )

    spec = importlib.util.spec_from_file_location(
        "shared._merge_guard_baseline_5017d1f2", _FIXTURE_PATH_5017D1F2
    )
    if spec is None or spec.loader is None:
        pytest.fail(
            "5017d1f2 baseline fixture spec unloadable: %s — the cert cannot run"
            % _FIXTURE_PATH_5017D1F2
        )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "shared"
    spec.loader.exec_module(module)

    def _binds_no_mint_key(cmd):
        ctx = module.extract_command_context(cmd)
        return not any(ctx.get(k) for k in _MINT_KEYS_1203)

    if not (
        module.is_dangerous_command(_DISCRIMINATOR_1203_FP) is True
        and _binds_no_mint_key(_DISCRIMINATOR_1203_FP)
    ):
        pytest.fail(
            "vendored 5017d1f2 baseline does not exhibit the implicit force-push "
            "gated-but-unmintable over-block — post-fix bytes were vendored"
        )
    if not (
        module.is_dangerous_command(_DISCRIMINATOR_1203_MERGE) is True
        and _binds_no_mint_key(_DISCRIMINATOR_1203_MERGE)
    ):
        pytest.fail(
            "vendored 5017d1f2 baseline does not exhibit the bare-merge "
            "gated-but-unmintable over-block — post-fix bytes were vendored"
        )
    if module.is_dangerous_command(_DISCRIMINATOR_1203_INERT) is not True:
        pytest.fail(
            "vendored 5017d1f2 baseline does not gate the inert `gh pr merge --help` "
            "form — the inert-help recognizer is already present; post-fix bytes were "
            "vendored"
        )

    _cached_baseline_5017d1f2 = module
    return module
