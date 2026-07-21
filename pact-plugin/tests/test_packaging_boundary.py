"""
Packaging-boundary invariants: the capture-shim installers must not ship.

WHAT THIS GUARDS, AND WHY EVERY ASSERTION HERE IS A PROXY
---------------------------------------------------------
The property that matters is "no PACT consumer receives the capture-shim
installers." It is not observable from inside this repository: it describes a
distribution artifact that does not exist until a release is installed, on
machines nobody here can inspect. What IS observable is the shape of the
repository tree, so every assertion below is a repo-tree statement standing in
for a distribution statement.

The substitution is legitimate because the repo-to-distribution mapping over
``pact-plugin/`` is the IDENTITY function. ``.claude-plugin/marketplace.json``
declares one plugin with ``"source": "./pact-plugin"``; that subtree is
mirrored wholesale into ``<plugin cache>/PACT/<version>/``; and no mechanism
exists to select a subset of it. Under identity, "absent under pact-plugin/"
and "absent from a consumer's cache" are the same predicate — which is exactly
what lets an observable assertion falsify an unobservable property.

That single inference is the guard's whole foundation, so the premise is
ASSERTED here rather than assumed. If ``source`` ever became ``"."``, or a
file-selection key or a ``.pluginignore`` appeared, the absence assertion would
keep passing while the installers shipped again — a green test standing over a
broken property, which is the worst failure this guard can have. Asserting the
premise turns that silent failure into a loud one.

WHAT THIS DELIBERATELY DOES NOT CLAIM
-------------------------------------
Only FUTURE installs are covered. Plugin cache versions accumulate rather than
self-purge, so a machine that installed an earlier version still holds both
installers until version rotation removes those directories. Nothing in this
repository changes that, and no assertion here should be read as claiming it.

ABSENCE ALONE WOULD BE A DELETION GUARD, NOT A RELOCATION GUARD
---------------------------------------------------------------
"Nothing under pact-plugin/ matches the installer name shape" is satisfied by
every history in which the files are not there — including one where a
contributor deletes both outright. It pins a necessary condition of relocation
and no sufficient one. The relocation invariant is the CONJUNCTION of absence
under the packaged tree with presence under ``dev/``; neither leg means much
alone, so both are asserted.

The presence leg also checks each script is non-empty, carries an executable
bit, and starts with a shebang. Bare existence would pin only that a file with
that name exists — a contributor could truncate either script to an empty stub
and a name-only check would stay green. The relocation commit's rename-
similarity certification is content evidence that expires at the next commit;
these three cheap checks are what stays durable afterwards. They stop short of
pinning content, which would fire on legitimate edits.

WHY THE ENUMERATION CARRIES ITS OWN POSITIVE CONTROL
----------------------------------------------------
An absence assertion evaluated over an EMPTY scan passes for a reason that has
nothing to do with the property. That is the failure family where a
verification step reports success because its input was empty or narrowed, and
it is the specific way this guard would fail silently. ``_packaged_files``
therefore bakes the control in: it asserts the enumeration is non-empty and
that it reached this very file. A caller cannot write the absence check without
the control, because the control lives inside the helper that produces the
input.

WHY THIS MODULE SKIPS OUTSIDE A SOURCE CHECKOUT
-----------------------------------------------
This module ships. ``pact-plugin/tests/`` is inside the packaged tree, so a
copy lands in every consumer's plugin cache — where the repo root it needs does
not exist, ``dev/`` is absent by design, and ``pact-plugin/`` is absent because
the cache IS the mirrored subtree. Run blindly there, the presence leg would
fail and the absence leg would pass over an empty scan: both wrong, in opposite
directions.

The root is therefore located by walking up for
``.claude-plugin/marketplace.json``, which exists only in a source checkout and
is never mirrored into the cache. Outside a source checkout these tests SKIP
with a reason naming the cache case. A skip here carries NO INFORMATION about
the packaging boundary and must never be read as a pass.

That makes this module a self-aware member of the class "shipped artifact
referencing a non-shipped path". The membership is unavoidable rather than
careless: CI runs ``python -m pytest tests/`` with ``pact-plugin/`` as the
working directory, so a guard placed anywhere else would never execute.

A CLASS-LEVEL SWEEP WAS MEASURED AND REJECTED — REMEASURE BEFORE ADDING ONE
---------------------------------------------------------------------------
A general "no shipped file references a non-shipped path" assertion was
measured against this tree and is not shippable. Scanning every tracked file
under ``pact-plugin/`` for references to each repo-root sibling outside the
packaged tree: the ``dev/`` token produced 70 raw hits, 16 after filtering
``/dev/null``-family device paths, and every one of those 16 was either prose
using "dev" as a word ("dev/staging/prod", "dev/test") or a ``/Users/dev/...``
fixture path — zero actionable findings. ``testing/``, ``.github/`` and
``pyproject.toml`` yielded 8 hits between them, all generic documentation
examples about a consumer's own project rather than references to this repo.
``scripts/`` is unusable as a signal at all: 97 hits, nearly all naming
``pact-plugin/scripts/`` or ``skills/*/scripts/``, which do ship.

The narrower instance form — "no shipped file contains ``dev/install_``" — is
worse than noisy: it is RED against this tree right now. Its one hit is the
deliberate, reviewed reference in the fork-session-context runbook under
``tests/runbooks/``, which names the relocated script AND tells a cache reader
that ``dev/`` is absent by design. That reference is the correct handling of
the very defect the class was meant to catch, so an assertion forbidding it
would fire on the fix. Both forms were dropped for these reasons, not
overlooked.
"""

import json
import os
import re
from pathlib import Path
from typing import List, Optional

import pytest

_THIS_FILE = Path(__file__).resolve()

# Basename shape of a capture-shim installer. Deliberately broader than the
# `install_*_shim.sh` of the two files this was written for, which would miss
# `installshim.sh` or a `.py` port of the same instrument. Anchoring on a
# leading `install` keeps test modules (`test_*`) out of the match, so the
# broadening costs nothing here. This pins a NAMING SHAPE and not the hazard
# itself: a capture-shim installer under an unrelated name evades it, and no
# cheap assertion closes that gap.
_INSTALLER_NAME = re.compile(r"^install.*shim.*\.(sh|bash|zsh|py)$", re.IGNORECASE)

# The two scripts the relocation moved. Absence under the packaged tree AND
# presence under dev/ is the relocation invariant; see the module docstring.
_RELOCATED_INSTALLERS = (
    "install_session_start_logging_shim.sh",
    "install_taskcompleted_logging_shim.sh",
)

# Manifest keys that would select a subset of the packaged tree instead of
# mirroring it wholesale. This is a denylist and therefore a lower bound: it
# names the keys that exist today, so re-check it whenever the plugin manifest
# schema changes. The premise it protects is stated in the module docstring.
_SELECTION_KEYS = ("files", "include", "exclude", "ignore")

# Directories that cannot contribute to the distributed artifact: version
# control internals, ignored build/cache output, and gitignored working
# directories. Pruned so the premise sweep does not walk into `.git`.
_PRUNED_DIRS = frozenset({
    ".git", ".worktrees", ".history", "docs", "node_modules",
    "__pycache__", ".pytest_cache", ".hypothesis", "skills-research",
})


def _find_source_repo_root() -> Optional[Path]:
    """Locate the source-repo root, or return None outside a source checkout.

    Anchors on ``.claude-plugin/marketplace.json``. That file sits at the repo
    root and is NOT part of the packaged subtree, so it exists in a source
    checkout and nowhere in an installed plugin cache — which is precisely the
    discrimination this guard needs. Counting parent directories would not do:
    in a cache the same relative depth lands on the version directory.
    """
    for candidate in list(_THIS_FILE.parents)[:6]:
        if (candidate / ".claude-plugin" / "marketplace.json").is_file():
            return candidate
    return None


def _require_source_checkout() -> Path:
    """Return the repo root, or SKIP LOUDLY when there is not one.

    The skip is the honest outcome in an installed plugin cache, where neither
    the packaged tree nor ``dev/`` exists at the resolved location. It is not a
    pass, and the reason string says so, because a silent skip reads as success
    to anyone scanning a summary line.
    """
    root = _find_source_repo_root()
    if root is None:
        pytest.skip(
            "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json above "
            f"{_THIS_FILE}, so this is not a PACT source checkout. The expected "
            "case is an INSTALLED PLUGIN CACHE: pact-plugin/ is mirrored into "
            "<cache>/PACT/<version>/, so this file's ancestors are the version "
            "directory rather than a repo root, and both the packaged tree and "
            "dev/ are absent there by design. These invariants describe "
            "source-repo layout and cannot be evaluated here. This skip is NO "
            "INFORMATION about the packaging boundary — never read it as a pass."
        )
    return root


def _packaged_tree(root: Path) -> Path:
    """Return the subtree that ships, asserting the root resolved sanely.

    A root that exists but has no ``pact-plugin/`` under it means the upward
    walk stopped somewhere unintended. Failing here is correct: silently
    scanning the wrong directory is how an absence assertion goes green for a
    reason unrelated to the property.
    """
    tree = root / "pact-plugin"
    assert tree.is_dir(), (
        f"resolved repo root {root} has no pact-plugin/ subtree — the upward "
        "walk for .claude-plugin/marketplace.json stopped at the wrong "
        "directory, so any scan rooted here would be meaningless"
    )
    return tree


def _packaged_files(tree: Path) -> List[Path]:
    """Every file under the packaged tree, with the positive control baked in.

    The control is deliberately inseparable from the enumeration. An absence
    assertion over an empty list passes for the wrong reason, so the helper
    that produces the input is the helper that proves the input is real: the
    scan must be non-empty AND must have reached this very module. Callers get
    the control whether or not they remember to ask for it.

    The walk is filesystem-based rather than git-based, which makes it a
    SUPERSET of what ships: an untracked installer sitting in a worktree is
    caught too. Erring that way cannot produce a false clean.
    """
    files = [p for p in tree.rglob("*") if p.is_file()]
    assert files, (
        f"packaged-tree enumeration under {tree} is EMPTY — the scan input is "
        "not real, so no absence conclusion drawn from it means anything"
    )
    assert _THIS_FILE in {p.resolve() for p in files}, (
        f"packaged-tree enumeration under {tree} did not reach {_THIS_FILE}, "
        "which lives inside it — the scan is not covering the tree it claims to"
    )
    return files


def _walk_repo(root: Path):
    """Yield (dirpath, filenames) across the repo, skipping non-shipping dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNED_DIRS]
        yield Path(dirpath), filenames


# ---------- guard liveness ----------

def test_guard_is_active_in_a_source_checkout():
    """Fail if the guard cannot actually run where it is supposed to run.

    Without this, a mis-resolved root or an empty scan would turn every
    assertion below into a skip or a vacuous pass, and the suite summary would
    look identical either way.
    """
    root = _require_source_checkout()
    files = _packaged_files(_packaged_tree(root))
    assert len(files) > 100, (
        f"only {len(files)} files found under {root / 'pact-plugin'}; the "
        "packaged tree is far larger than that, so the scan is truncated"
    )


# ---------- the relocation invariant: absence AND presence ----------

def test_no_capture_shim_installer_ships_in_the_packaged_tree():
    """Absence leg. Alone this is only a deletion guard — see the presence leg."""
    root = _require_source_checkout()
    tree = _packaged_tree(root)
    files = _packaged_files(tree)

    # Exact names first, THEN the broader shape. Order is load-bearing: every
    # exact name also matches the shape pattern, so checking shape first would
    # make this assertion unreachable — it could never be the one to fire, and
    # a mutation could never prove it works. In this order each leg has inputs
    # only it catches: the two relocated names here, any other installer-shaped
    # name below.
    by_exact_name = sorted(
        str(p.relative_to(root)) for p in files if p.name in _RELOCATED_INSTALLERS
    )
    assert not by_exact_name, (
        "the relocated capture-shim installer(s) are back inside the packaged "
        f"tree: {by_exact_name}. That tree is mirrored wholesale into every "
        "consumer's plugin cache, and these instruments patch a consumer's live "
        "hook configuration. They belong at repo-root dev/."
    )

    by_shape = sorted(
        str(p.relative_to(root)) for p in files if _INSTALLER_NAME.match(p.name)
    )
    assert not by_shape, (
        "file(s) shaped like a capture-shim installer are present inside the "
        f"packaged tree and would ship to every consumer: {by_shape}"
    )


def test_relocated_installers_are_present_and_runnable_under_dev():
    """Presence leg. The conjunction with absence is what pins RELOCATION.

    Existence alone would leave an empty stub passing, so this also requires
    each script to be non-empty, executable, and shebang-led.
    """
    root = _require_source_checkout()
    dev = root / "dev"
    assert dev.is_dir(), (
        f"{dev} is missing — the capture-shim installers have nowhere to live "
        "outside the packaged tree, so absence under pact-plugin/ would mean "
        "they were deleted rather than relocated"
    )

    for name in _RELOCATED_INSTALLERS:
        script = dev / name
        assert script.is_file(), (
            f"{script} is missing. Absence under the packaged tree plus this "
            "absence means the installer was DELETED, not relocated"
        )
        stat = script.stat()
        assert stat.st_size > 0, f"{script} is empty — a hollow stub, not a script"
        assert stat.st_mode & 0o111, (
            f"{script} has lost its executable bit (mode {stat.st_mode & 0o777:o})"
        )
        assert script.read_bytes().startswith(b"#!"), (
            f"{script} does not begin with a shebang — it is no longer a "
            "directly runnable script"
        )


# ---------- the premise the proxy rests on ----------

def test_premise_marketplace_maps_only_the_pact_plugin_subtree():
    """The identity mapping is DECLARED here; everything above depends on it."""
    root = _require_source_checkout()
    manifest = json.loads(
        (root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    plugins = manifest.get("plugins") or []
    assert plugins, "marketplace.json declares no plugins"

    sources = sorted({plugin.get("source") for plugin in plugins})
    assert sources == ["./pact-plugin"], (
        f"marketplace.json now distributes {sources} rather than only "
        "['./pact-plugin']. The absence assertion in this module scans "
        "pact-plugin/ ONLY, so it would keep passing while repo-root dev/ "
        "shipped to consumers. Re-derive the packaging boundary before "
        "relaxing this."
    )


def test_premise_no_mechanism_selects_a_subset_of_the_packaged_tree():
    """A filter would break "position under pact-plugin/" as the only lever."""
    root = _require_source_checkout()
    plugin_manifest = json.loads(
        (root / "pact-plugin" / ".claude-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )
    present = sorted(k for k in _SELECTION_KEYS if k in plugin_manifest)
    assert not present, (
        f"plugin.json now carries file-selection key(s) {present}. The packaged "
        "tree is no longer 'all of pact-plugin/', so absence under that path no "
        "longer implies absence from a consumer's cache."
    )

    plugin_ignores = []
    gitattributes_filters = []
    for dirpath, filenames in _walk_repo(root):
        if ".pluginignore" in filenames:
            plugin_ignores.append(str((dirpath / ".pluginignore").relative_to(root)))
        if ".gitattributes" in filenames:
            path = dirpath / ".gitattributes"
            if "export-ignore" in path.read_text(encoding="utf-8", errors="replace"):
                gitattributes_filters.append(str(path.relative_to(root)))

    assert not plugin_ignores, (
        f"a .pluginignore now exists ({plugin_ignores}); it can exclude paths "
        "from the distributed artifact, which breaks the identity mapping this "
        "guard's absence assertion depends on"
    )
    assert not gitattributes_filters, (
        f"export-ignore now appears in {gitattributes_filters}. Archive-based "
        "distribution honours it, so it can silently change which parts of "
        "pact-plugin/ reach a consumer."
    )
