"""
Location: pact-plugin/tests/merge_guard_baseline_loader.py
Summary: Loud-fail loaders for the COMMITTED vendored merge-guard baseline fixtures.
         TWO independent pre-fix baselines, each vendored as committed bytes (never a
         `git show` of a SHA no ref reaches. THE SPLIT IS BY ROLE, not across baseline
         SHAs as a class, and the mechanism rather than the tally is what stays true:
         a BASE SHA is USUALLY a commit on main, and those any clone of origin has.
         The exception is a base taken from a branch's own history rather than from
         main -- the parent of a pre-merge fix commit, say -- which the squash leaves
         off main like the commit it preceded; when the branch goes, so does it. A
         base that does not resolve is in that state, and the differential self-skips
         naming the object, so the choice is to re-derive the base from the merged
         history or to accept the skip. A PRE-FIX or
         HEAD-SIDE SHA is a point on a feature branch that was squash-merged, so the
         commit itself never lands on main and survives only where some other ref
         still reaches it -- a developer's fork remote, say. CI clones origin alone
         and sees none of them. Check any one with `git merge-base --is-ancestor <sha>
         origin/main`, and which SHAs hold which role with the variable names in the
         cert modules; the live per-module outcome is in the CI log's skip reasons.
         That is the lesson these loaders exist to close):

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


# ─────────────────────────────────────────────────────────────────────────────
# Vendored certification bases. One table-driven loader for the baked-SHA
# certification files, which read these blobs with `git show` and skipped
# wherever the commit was unreachable. Each fixture holds the exact bytes of
# hooks/shared/merge_guard_common.py at that commit, pinned by git blob id: the
# value `git show` printed and `git hash-object` recomputes. This loader adds no
# discriminator rows; each consuming cert keeps its own rows asserting that its
# base still exhibits the bug it certifies.
# ─────────────────────────────────────────────────────────────────────────────
_VENDORED_DIR = Path(__file__).parent / "fixtures" / "merge_guard_baseline"

# commit sha8 -> (fixture file name, git blob id of the fixture's bytes)
_VENDORED = {
    "c5e9b324": ("merge_guard_common_c5e9b324.py", "57eee4410e0a6ef2dae3e9021bdbb32bb7ce7d69"),
    "38f76965": ("merge_guard_common_38f76965.py", "745cbfe8adb4c303e83402fa5e2093fd51d718ed"),
    "023ee2c3": ("merge_guard_common_023ee2c3.py", "9f7f155bdac0012858701bd24e8f638d06bdadbb"),
    "6f404f2e": ("merge_guard_common_6f404f2e.py", "4c39097504d008ebe4d4ec61bc2e0d843afbaff4"),
    "51e6c5a5": ("merge_guard_common_51e6c5a5.py", "9a351309358c3f196995a106f1216b437bd151f1"),
    "72bacaf8": ("merge_guard_common_72bacaf8.py", "86f3d93d6f20757ed781bba519d781869a2246fb"),
    "b313ecaa": ("merge_guard_common_b313ecaa.py", "93e6f56d905b6b0326940ccabe5eaac775691d98"),
    "f6e3639a": ("merge_guard_common_f6e3639a.py", "cb24d0b883f092126e2714865c1b1d8ce89e9572"),
    "2d7fcd07": ("merge_guard_common_2d7fcd07.py", "b07f06c0c57ad9c966d637e4ac50e8beed7609ca"),
    "b6418727": ("merge_guard_common_b6418727.py", "f080051af6f9c6e9427d4dbaad886203b1276a5c"),
    "3972bb5f": ("merge_guard_common_3972bb5f.py", "2c29f088ff5fc03626a7ad5e31c976cc15d8012a"),
    "bf7c8786": ("merge_guard_common_bf7c8786.py", "66e1a48e33604ff98903d1624914af3cace24f7a"),
    "a62703f1": ("merge_guard_common_a62703f1.py", "8f67b82deb254e2ceff9532ab639f5012fadcf9f"),
    "a542e21b": ("merge_guard_common_a542e21b.py", "0d4be274af969a69f7053d343b8a81813115920a"),
}

_cached_vendored = {}


def _git_blob_id(data):
    """The id git gives these bytes as a blob: sha1 over a `blob <len>\\0` header."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_vendored(commit_sha8):
    """Load the vendored merge_guard_common.py at `commit_sha8` (cached per SHA).

    Fails loudly and never skips. An unknown SHA, a missing fixture, or bytes
    whose git blob id differs from the pin is a pytest.fail, so a certification
    file cannot pass without running its differential. Imported under the
    `shared` package so the module's relative `from .paths import ...` resolves
    against the live package, exactly as load_baseline() does.
    """
    if commit_sha8 in _cached_vendored:
        return _cached_vendored[commit_sha8]
    entry = _VENDORED.get(commit_sha8)
    if entry is None:
        pytest.fail(
            "no vendored merge_guard_common.py for commit %s: add its fixture and "
            "its _VENDORED row" % commit_sha8
        )
    name, blob_id = entry
    path = _VENDORED_DIR / name
    if not path.is_file():
        pytest.fail("vendored fixture missing (%s): the cert cannot run" % path)
    got = _git_blob_id(path.read_bytes())
    if got != blob_id:
        pytest.fail(
            "vendored fixture %s git blob id mismatch: got %s, pinned %s. The "
            "bytes drifted; re-vendor with `git show %s:pact-plugin/hooks/shared/"
            "merge_guard_common.py`" % (name, got, blob_id, commit_sha8)
        )
    spec = importlib.util.spec_from_file_location(
        "shared._merge_guard_baseline_%s" % commit_sha8, path
    )
    if spec is None or spec.loader is None:
        pytest.fail("vendored fixture spec unloadable: %s" % path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "shared"
    spec.loader.exec_module(module)
    _cached_vendored[commit_sha8] = module
    return module
