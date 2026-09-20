"""The embedding model must load the snapshot the cache itself calls ``main``.

model2vec resolves a bare model id through its own cache probe, which selects a
snapshot directory by ``max(mtime)`` and never checks that the directory is the
revision anyone asked for. With several snapshots cached -- which is the normal
state after any model update -- the one that loads is whichever was touched
last. So the vectors that enter the index are chosen by a filesystem timestamp,
and two machines with the same cache can index different embeddings.

``refs/main`` is a pointer huggingface_hub maintains, so "which revision is
main" is the CACHE'S answer rather than a policy chosen here. No hash is
pinned; nothing decides a revision. The change is only that the question gets
asked.

🔴 THE HAZARD IS UNOBSERVABLE ON A DEVELOPER'S REAL CACHE TODAY, AND THAT IS
WHY THIS FILE BUILDS ITS OWN. On the machine where this was written the cache
holds five snapshots of the pinned model, and ``refs/main`` names the SAME
directory that ``max(mtime)`` would pick -- the newest download is also the
current revision, which is the ordinary case. A mutant that swaps the ref
lookup back for the newest-directory rule therefore comes back GREEN against
the real cache, and a reader who meets that green will reasonably conclude the
fix was decoration.

A SECOND ARM WAS WRITTEN HERE AND REMOVED, AND THE REASON IS WORTH MORE THAN
THE ARM WAS. It asserted the result was NOT the newest directory, justified as
failing on different mutants than the arm that asserts it IS the ref target.
That justification is false: ``== ref`` entails ``!= anything-else``, so the
refutation arm was strictly weaker and could never fail alone. It was caught by
the mutation run rather than by review -- it SURVIVED the ``max(mtime)`` mutant,
because it set the mtime of only one of its two directories and left the other
at wall-clock time, which is far larger than the constant it compared against.
So its topology was inverted and it had been passing for the wrong reason. Two
defects, one arm: strictly weaker than its sibling, and not building the
situation it described.

It is not decoration, and the two selectors agree by coincidence rather than by
construction: nothing keeps ``refs/main`` pointing at the newest directory. A
re-download of an older revision, a partially-completed fetch, or a ``touch``
inverts it. ``test_the_ref_wins_when_the_newest_directory_is_a_different_one``
constructs exactly that inversion, so the property has an arm that can fail
even though the ambient cache cannot express it.

WHAT IS PINNED HERE AND WHAT IS NOT. The resolution is pinned -- which
directory is chosen, and that every failure degrades to the previous behaviour
rather than raising. No model is loaded and nothing is downloaded: the arms
build directory trees and empty files, so they neither need the network nor a
populated cache.
"""
from pathlib import Path

import pytest

from scripts.embeddings import MODEL_NAME, _cache_snapshot_for

huggingface_hub = pytest.importorskip("huggingface_hub")
from huggingface_hub.file_download import repo_folder_name  # noqa: E402


def _repo(cache_root: Path) -> Path:
    """The repo directory huggingface_hub would use, named by ITS OWN rule.

    Derived rather than spelled ``models--minishlab--potion-base-8M``: a
    hand-written name would keep matching after upstream changed the
    convention, and the arms would then all build a directory the library never
    looks at -- every one of them passing while measuring nothing.
    """
    return cache_root / repo_folder_name(repo_id=MODEL_NAME, repo_type="model")


def _snapshot(cache_root: Path, sha: str, *, complete: bool = True) -> Path:
    """Create a snapshot directory; ``complete`` controls the embeddings file."""
    snap = _repo(cache_root) / "snapshots" / sha
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")
    (snap / "tokenizer.json").write_text("{}", encoding="utf-8")
    if complete:
        (snap / "model.safetensors").write_bytes(b"")
    return snap


def _write_ref(cache_root: Path, value: str) -> None:
    refs = _repo(cache_root) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(value, encoding="utf-8")


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    """Point huggingface_hub's cache resolution at an empty tmp tree.

    ``try_to_load_from_cache`` reads ``constants.HF_HUB_CACHE`` at CALL time
    rather than binding it at import, so patching the attribute is enough and
    no environment variable needs setting.
    """
    root = tmp_path / "hub"
    root.mkdir()
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(root))
    return root


# --- the arm that can actually fail ----------------------------------------


def test_the_ref_wins_when_the_newest_directory_is_a_different_one(cache_root):
    """THE ONLY ARM HERE THAT A TRANSPOSITION MUTANT CANNOT SURVIVE.

    Two snapshots, and the ref names the OLDER one. Every other arm in this
    file would pass just as well against a ``max(mtime)`` implementation,
    because with one candidate the two rules agree. This is the topology the
    real cache does not currently exhibit, built so the choice is observable.

    The mtimes are set explicitly rather than relying on creation order: a
    filesystem with coarse timestamp granularity can report two
    just-created directories as equal, which would make the two selectors
    agree again and quietly return this arm to vacuity.
    """
    ref_target = "a" * 40
    newer = "b" * 40
    ref_snap = _snapshot(cache_root, ref_target)
    newest_snap = _snapshot(cache_root, newer)
    _write_ref(cache_root, ref_target)

    import os

    os.utime(ref_snap, (1_000_000, 1_000_000))
    os.utime(newest_snap, (2_000_000, 2_000_000))
    assert newest_snap.stat().st_mtime > ref_snap.stat().st_mtime, (
        "the topology this arm needs was not built: both directories report "
        "the same mtime, so choosing by ref and choosing by recency agree and "
        "this arm cannot discriminate"
    )

    resolved = _cache_snapshot_for(MODEL_NAME)

    assert resolved == str(ref_snap), (
        f"resolved to {resolved!r}, expected the directory refs/main names "
        f"({ref_snap}). Resolving to {newest_snap} means selection fell back "
        "to the newest directory -- the arbitrary rule this replaces."
    )


# --- the fallback states, which are the risk surface ------------------------
#
# The happy path above is one lookup. Every way this can get WORSE than the
# behaviour it replaces is here: a failure that raises, or that returns a
# directory the loader cannot use, would turn a working degraded path into a
# broken one. Every one of them must return None so the caller passes the bare
# model id and gets exactly today's behaviour.


def test_an_absent_repo_directory_falls_back(cache_root):
    """Nothing cached at all -- the first-run case."""
    assert _cache_snapshot_for(MODEL_NAME) is None


def test_an_absent_ref_falls_back(cache_root):
    """Snapshots present but no ``refs/main``: the cache cannot answer."""
    _snapshot(cache_root, "e" * 40)
    assert _cache_snapshot_for(MODEL_NAME) is None


def test_a_ref_naming_a_missing_snapshot_falls_back(cache_root):
    """The ref points somewhere that is not there -- a pruned cache."""
    _snapshot(cache_root, "f" * 40)
    _write_ref(cache_root, "0" * 40)
    assert _cache_snapshot_for(MODEL_NAME) is None


def test_an_incomplete_snapshot_falls_back(cache_root):
    """The ref names a directory whose embeddings file never arrived.

    This is the end state of an interrupted transfer. Returning the directory
    would hand the loader a path that raises on every run, and the repair
    retry cannot help because an explicit path bypasses re-download. Falling
    back lets the bare-id path find a usable copy instead.
    """
    sha = "1" * 40
    _snapshot(cache_root, sha, complete=False)
    _write_ref(cache_root, sha)
    assert _cache_snapshot_for(MODEL_NAME) is None


@pytest.mark.parametrize(
    "ref_value",
    ["", "   ", "2" * 40 + "\n", " " + "2" * 40],
    ids=["empty", "whitespace", "trailing-newline", "leading-space"],
)
def test_a_malformed_ref_falls_back(cache_root, ref_value):
    """A ref that is empty or carries stray whitespace degrades quietly.

    huggingface_hub reads the ref file WITHOUT stripping, so a padded value
    matches no snapshot directory. The trailing-newline case is the one worth
    naming: it is what a hand-edited or shell-written ref looks like, it is
    indistinguishable from a healthy ref by eye, and it silently returns this
    resolution to the previous arbitrary behaviour rather than failing. That is
    the correct direction and it is the reason this is best-effort
    determinism rather than a guarantee.
    """
    _snapshot(cache_root, "2" * 40)
    _write_ref(cache_root, ref_value)
    assert _cache_snapshot_for(MODEL_NAME) is None


def test_the_fallback_arms_are_not_all_passing_for_the_same_reason(cache_root):
    """Non-vacuity control for the fallback arms above.

    Every one of them asserts ``is None``, which is also what a resolver that
    returned None unconditionally would produce -- including one broken by a
    bad cache-root patch, which would make the whole file green while measuring
    nothing. This builds the ONE topology that must succeed and proves the
    same code path can return a directory.
    """
    sha = "3" * 40
    snap = _snapshot(cache_root, sha)
    _write_ref(cache_root, sha)
    assert _cache_snapshot_for(MODEL_NAME) == str(snap), (
        "the resolver returned None on a well-formed cache, so the "
        "fallback arms above prove nothing about the fallback"
    )


# --- the interaction with the repair retry ----------------------------------


def test_the_repair_retry_does_not_inherit_the_resolved_snapshot(monkeypatch):
    """THE RETRY MUST BE HANDED THE MODEL ID, NEVER THE RESOLVED DIRECTORY.

    ``_ensure_initialized`` retries once with ``force_download=True`` when a
    cached copy fails to load, which is the only way back past a snapshot that
    is present but unusable. model2vec resolves its argument with
    ``_resolve_folder``, whose FIRST action is to return the argument unchanged
    if it exists on disk -- before ``force_download`` is consulted at all.

    MEASURED, not inferred: an existing path with ``force_download=True`` comes
    back unchanged, while a non-existent one falls through to the download.
    So handing the retry the resolved directory would make it re-select the
    same unusable snapshot, fail identically, and restore the permanent
    degradation the retry exists to break -- with every existing arm still
    green, because they exercise the exception path rather than the re-fetch.

    This arm exists because the natural tidy-up is to hoist one ``target``
    variable and use it for both calls. That refactor is silent, plausible, and
    wrong, and nothing else in the suite would redden.
    """
    from unittest.mock import MagicMock, patch

    from scripts.embeddings import EmbeddingService

    resolved = "/tmp/a-resolved-snapshot-directory"
    # Patched on the function's own `__globals__` rather than by module name.
    # Another file in this suite evicts `scripts.embeddings` from `sys.modules`
    # and re-imports it, leaving two live module objects for one file: a patch
    # applied by name reaches whichever currently holds the name, while the
    # method body reads the namespace it was compiled against. This arm happens
    # to import inside its own body and so would resolve consistently today,
    # but that is a property of statement order rather than of the seam.
    monkeypatch.setitem(
        EmbeddingService._ensure_initialized.__globals__,
        "_cache_snapshot_for",
        lambda _name: resolved,
    )

    calls = []

    def _from_pretrained(target, **kwargs):
        calls.append((target, kwargs))
        if len(calls) == 1:
            raise RuntimeError("cached copy unusable")
        return MagicMock()

    fake_cls = MagicMock()
    fake_cls.from_pretrained.side_effect = _from_pretrained
    service = EmbeddingService()
    with patch.dict("sys.modules", {"model2vec": MagicMock(StaticModel=fake_cls)}):
        assert service._ensure_initialized() is True

    assert len(calls) == 2, (
        f"expected a first load and one retry, saw {len(calls)} call(s) -- "
        "the retry did not run, so this arm measures nothing"
    )
    first_target, _ = calls[0]
    retry_target, retry_kwargs = calls[1]

    assert first_target == resolved, (
        "the FIRST load did not use the resolved snapshot, so this arm is not "
        "testing the configuration it describes"
    )
    assert retry_kwargs.get("force_download") is True, (
        "the retry did not request a re-download, so its argument does not "
        "matter and this arm proves nothing"
    )
    assert retry_target == MODEL_NAME, (
        f"the retry was handed {retry_target!r}. It must be the bare model id: "
        "an existing directory short-circuits model2vec's resolution before "
        "force_download is read, so the re-fetch never happens and the "
        "degradation becomes permanent."
    )
