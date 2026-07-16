"""
Location: pact-plugin/tests/merge_guard_baseline_loader.py
Summary: Loud-fail loader for the COMMITTED vendored merge-guard baseline fixture
         (fixtures/merge_guard_baseline/merge_guard_common_b4041ccf.py). Every
         base-vs-HEAD differential row in the over-block-cluster cert files loads the
         pre-fix classifier through this ONE function so the bidirectional certs are
         CI-EXECUTABLE: the fixture is committed bytes, not a `git show` of a SHA that a
         shallow clone cannot resolve (9 of 14 existing baseline SHAs are CI-invisible
         non-ancestors — the lesson this loader exists to close).
Used by: test_merge_guard_1181_cert.py, test_merge_guard_1155_cert.py,
         test_merge_guard_1148_cert.py (and future certs migrating off `git show`).

CONTRACT (all failure modes are HARD pytest.fail, NEVER skip, no @requires_history):
  1. Fixture file missing            -> pytest.fail (the cert cannot certify silently).
  2. sha256 mismatch with the pin    -> pytest.fail (fixture bytes drifted).
  3. Pre-fix discriminator rows fail -> pytest.fail (post-fix bytes were vendored:
     the baseline MUST still exhibit all three over-block-cluster bugs).
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
