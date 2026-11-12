"""A repository-chosen name must never become a store key or a read-scope entry.

CLASS PROPERTY. Three routes let a checked-out repository choose the bucket a
read binds, and each one is exercised here:

1. a `.gitmodules` submodule NAME (forged: `PACT-prompt/x`), whose admin dir
   `<super>/.git/modules/PACT-prompt/x` makes the legacy candidate
   `PACT-prompt` — the caller's own populated bucket;
2. a DEFAULT-named submodule (`third_party`), whose admin dir
   `<super>/.git/modules/third_party` has the parent name `modules`, so a
   bucket literally named `modules` is unioned. A guard keyed on "the name
   contains a slash" fixes route 1 and leaves this one, so the condition under
   test is the ADMIN-DIR PATH SHAPE, not the derived name;
3. a unique CASE-FOLDED candidate, where the checkout folder differs from a
   stored bucket only by case.

A fourth route is the same key-steering at the PRIMARY id rather than the
legacy candidate: a submodule whose origin is a non-portable local path falls
through to the checkout root's basename, so a bucket named after the
repository's own directory becomes the binding id.

INTERFACE CONTRACT for the fix: the case arms force
`filesystem_case_insensitive`, which the fix must consult before folding.
The stub is created when absent (`raising=False`) so the UNFIXED tree reddens
for the fold it actually performs; a fix that renames the probe without
updating this module fails the case-INSENSITIVE control loudly instead of
silently depending on the host filesystem (this suite also runs on APFS).

POSITIVE CONTROLS. A blanket suppression would pass every negative arm, so
each one is paired: the fold must still happen on a case-insensitive root, and
a relative-only bucket whose name IS the caller's own must still be included.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    make_repo,
    run_git,
    seed_session_evidence,
)
from scripts.database import (
    count_project_memories,
    db_connection,
    ensure_initialized,
)
from scripts.memory_api import PACTMemory

from scripts import memory_api
from scripts import project_identity as pi

CANONICAL = "github.com/attacker/docs"
FOLDED_CANONICAL = "github.com/attacker/foreignproj"
CREATED_AT = "2026-01-01T00:00:00+00:00"

# The probe the fix must consult. See the module docstring's contract.
CASE_PROBE = "filesystem_case_insensitive"


def _force_case_sensitivity(monkeypatch, *, insensitive: bool) -> None:
    """Force the fix's case-sensitivity probe, creating it when absent."""
    stub = lambda *_args, **_kwargs: insensitive  # noqa: E731 - a probe stub
    for module in (memory_api, pi):
        monkeypatch.setattr(module, CASE_PROBE, stub, raising=False)


def _submodule(
    tmp_path: Path,
    *,
    name: str | None,
    path: str,
    network_remote: str | None,
) -> Path:
    """A real superproject with one submodule checkout at `path`.

    `name` is the `.gitmodules` section name — the repository-chosen value.
    `network_remote`, when given, replaces the local-path origin so the
    submodule's PRIMARY id resolves canonically and the arm measures the
    legacy-candidate route rather than the primary-id route.
    """
    source = make_repo(tmp_path / ("source-" + path.replace("/", "-")))
    superproject = make_repo(tmp_path / "super")
    argv = ["-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f"]
    if name is not None:
        argv += ["--name", name]
    argv += [str(source), path]
    run_git(*argv, cwd=superproject)
    checkout = superproject / path
    if network_remote is not None:
        run_git("remote", "set-url", "origin", network_remote, cwd=checkout)
    return checkout


def _seed_bucket(store: Path, bucket: str, memory_id: str = "victim") -> None:
    """One memory and one RELATIVE file row — the measured legacy shape.

    A relative path carries no location, so the plausibility gate answers
    `unverified` and INCLUDES the bucket. That inclusion is what these arms
    bound: it is legitimate for the caller's own name and must not be
    reachable when the repository chose the name.
    """
    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories "
            "(id, context, project_id, session_id, created_at, updated_at) "
            "VALUES (?, 'private victim memory', ?, 's', ?, ?)",
            (memory_id, bucket, CREATED_AT, CREATED_AT),
        )
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
            ("file-" + memory_id, "src/relative.py", bucket),
        )
        conn.commit()


def _enter(monkeypatch, tmp_path: Path, checkout: Path) -> None:
    assert_outside_repository(tmp_path)
    monkeypatch.chdir(checkout)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    memory_api._LEGACY_SCOPE_CACHE.clear()
    memory_api._EXCLUSION_WARNED.clear()
    memory_api._CASE_VARIANT_WARNED.clear()


def _memory(store: Path) -> PACTMemory:
    return PACTMemory(session_id="s", db_path=store)


def _ids(memories) -> list:
    return [memory.id for memory in memories]


class TestNoRepositoryChosenNameBindsAStoreKey:
    """The class property, route by route."""

    def test_a_forged_submodule_name_does_not_join_the_read_scope(
        self, tmp_path, monkeypatch
    ):
        """Route 1. `[submodule "PACT-prompt/x"] path = docs`.

        The admin dir is `<super>/.git/modules/PACT-prompt/x`, so the derived
        legacy candidate is `PACT-prompt` — a populated bucket the repository
        does not own. RED when the candidate is derived from the admin-dir
        parent at all: the read scope becomes
        (canonical, 'PACT-prompt') and the victim row is returned.
        """
        checkout = _submodule(
            tmp_path,
            name="PACT-prompt/x",
            path="docs",
            network_remote="https://github.com/attacker/docs.git",
        )
        store = tmp_path / "store.db"
        _seed_bucket(store, "PACT-prompt")
        _enter(monkeypatch, tmp_path, checkout)
        memory = _memory(store)

        # The store really holds the row: the negative assertion below is not
        # measuring an empty store.
        with db_connection(store) as conn:
            assert count_project_memories(conn, "PACT-prompt") == 1

        scopes = memory._read_project_scopes() or ()
        assert "PACT-prompt" not in scopes, (
            "a repository-chosen .gitmodules name steered the read scope into "
            f"a populated foreign bucket: {scopes!r}"
        )
        assert memory.list(limit=10) == [], (
            "a read returned rows from a bucket named by the repository's own "
            ".gitmodules"
        )
        assert memory._legacy_project_basename is None, (
            "the legacy candidate is still derived from the submodule admin "
            "dir; the fix suppresses it at the name derivation, so `project-id "
            "show` cannot advertise a candidate the read path never uses"
        )

    def test_a_default_named_submodule_does_not_join_the_read_scope(
        self, tmp_path, monkeypatch
    ):
        """Route 2, the shape a slash-keyed guard misses.

        `git submodule add <url> third_party` gives the admin dir
        `<super>/.git/modules/third_party`, whose PARENT name is `modules` —
        no slash in the derived name at all — so a read from it unions a
        bucket literally named `modules`. RED when the suppression is keyed on
        the name rather than the admin-dir PATH SHAPE.
        """
        checkout = _submodule(
            tmp_path,
            name=None,
            path="third_party",
            network_remote="https://github.com/attacker/third_party.git",
        )
        store = tmp_path / "store.db"
        _seed_bucket(store, "modules")
        _enter(monkeypatch, tmp_path, checkout)
        memory = _memory(store)

        with db_connection(store) as conn:
            assert count_project_memories(conn, "modules") == 1

        scopes = memory._read_project_scopes() or ()
        assert "modules" not in scopes, (
            "the default-named submodule's admin dir (parent name 'modules') "
            f"joined the read scope: {scopes!r}"
        )
        assert memory.list(limit=10) == [], (
            "a read returned rows from the bucket named by a default-named "
            "submodule's admin dir"
        )
        assert memory._legacy_project_basename is None, (
            "a submodule shape still derives a legacy candidate; the guard "
            "must follow the admin-dir PATH SHAPE, not the derived name"
        )

    def test_a_unique_folded_candidate_is_excluded_on_a_case_sensitive_root(
        self, tmp_path, monkeypatch, caplog
    ):
        """Route 3, negative half. Checkout `foreignproj` vs bucket
        `ForeignProj`.

        RED on the unfixed tree: the fold is unconditional, so the scope is
        (canonical, 'ForeignProj') and the foreign row is returned. The probe
        is forced to "sensitive" so the assertion is deterministic on APFS
        too, where the host filesystem would fold.
        """
        _force_case_sensitivity(monkeypatch, insensitive=False)
        repo = make_repo(
            tmp_path / "foreignproj",
            remote="https://github.com/attacker/foreignproj.git",
        )
        store = tmp_path / "store.db"
        _seed_bucket(store, "ForeignProj", memory_id="folded")
        _enter(monkeypatch, tmp_path, repo)
        memory = _memory(store)

        with caplog.at_level(logging.WARNING):
            scopes = memory._read_project_scopes() or ()
        assert scopes == (FOLDED_CANONICAL,), (
            "a case-folded candidate joined the scope on a case-SENSITIVE "
            f"root: {scopes!r}"
        )
        assert memory.list(limit=10) == [], (
            "the folded candidate returned another project's row"
        )
        # THE SUPPRESSION IS DISCLOSED, NOT SILENT: the bucket is present but
        # NOT included, and the operator gets the literal rekey command. It is
        # deliberately NOT `excluded`: the plausibility gate never judged it.
        state = memory._legacy_scope_state
        assert state["case_variant"] == ["ForeignProj"], (
            "a suppressed case variant was not disclosed; the bucket is "
            f"invisible to every read with no explanation: {state!r}"
        )
        assert state["excluded"] is False, (
            "a case-suppressed bucket was reported as gate-excluded, but the "
            "gate never judged it"
        )
        assert "project-id rekey --from" in state["case_variant_rekey"][0], state
        assert "ForeignProj" in caplog.text and "project-id rekey" in caplog.text, (
            f"the suppression warning did not name the bucket and the command: "
            f"{caplog.text!r}"
        )

    def test_a_unique_folded_candidate_still_joins_on_a_case_insensitive_root(
        self, tmp_path, monkeypatch
    ):
        """Route 3, POSITIVE CONTROL — the fix must not stop folding.

        A blanket "never fold" repair would pass the negative arm above and
        break the legitimate rescue it exists for (a case-only difference on a
        filesystem that cannot distinguish the spellings). Forcing the probe
        to "insensitive" asserts the fold survives, and that it returns the
        RAW stored spelling.
        """
        _force_case_sensitivity(monkeypatch, insensitive=True)
        repo = make_repo(
            tmp_path / "foreignproj",
            remote="https://github.com/attacker/foreignproj.git",
        )
        store = tmp_path / "store.db"
        _seed_bucket(store, "ForeignProj", memory_id="folded")
        # A recorded session of this checkout wrote the bucket: the evidence
        # an unverified bucket needs to be read.
        seed_session_evidence(tmp_path / "home" / ".claude", repo, "s")
        _enter(monkeypatch, tmp_path, repo)
        memory = _memory(store)

        scopes = memory._read_project_scopes() or ()
        assert scopes == (FOLDED_CANONICAL, "ForeignProj"), (
            "the fold stopped happening on a case-INSENSITIVE root, so the "
            f"legitimate rescue is gone: {scopes!r}"
        )
        assert _ids(memory.list(limit=10)) == ["folded"], (
            "the folded candidate did not reach the stored bucket"
        )

    def test_a_relative_only_bucket_named_by_the_caller_is_still_included(
        self, tmp_path, monkeypatch
    ):
        """POSITIVE CONTROL for the suppression: the caller's OWN name.

        The measured real legacy bucket is relative-only, so an
        "exclude everything unverified" repair would pass every arm above and
        lose 2,535 rows. A plain checkout whose folder name IS the bucket name
        must still include it.
        """
        repo = make_repo(tmp_path / "own-bucket", remote="https://github.com/o/r.git")
        store = tmp_path / "store.db"
        _seed_bucket(store, "own-bucket", memory_id="own")
        # A recorded session of this checkout wrote the bucket: the evidence
        # an unverified bucket needs to be read.
        seed_session_evidence(tmp_path / "home" / ".claude", repo, "s")
        _enter(monkeypatch, tmp_path, repo)
        memory = _memory(store)

        assert memory._legacy_project_basename == "own-bucket"
        assert _ids(memory.list(limit=10)) == ["own"], (
            "a relative-only bucket whose name is the caller's own folder was "
            "dropped by the fix"
        )

    def test_a_local_path_origin_submodule_does_not_bind_its_basename(
        self, tmp_path, monkeypatch
    ):
        """Route 4: the PRIMARY id, not the legacy candidate.

        A submodule at `checks/ForeignProj` whose origin is a local path
        resolves `no_portable_origin`, and the git rung then answers the
        checkout root's basename — so the binding id becomes `ForeignProj`,
        a bucket the repository's own tree chose. The refusal SHAPE is the
        implementer's (decline or override-required); this arm asserts the
        OUTCOME: no read and no write under the attacker-chosen name.
        """
        checkout = _submodule(
            tmp_path,
            name=None,
            path="checks/ForeignProj",
            network_remote=None,  # `submodule add <local path>` keeps a local origin
        )
        store = tmp_path / "store.db"
        _seed_bucket(store, "ForeignProj", memory_id="attacker")
        _enter(monkeypatch, tmp_path, checkout)
        memory = _memory(store)

        assert memory.project_id != "ForeignProj", (
            "a submodule with a non-portable origin bound the repository-"
            f"chosen checkout basename as its primary id: {memory.project_id!r}"
        )
        assert _ids(memory.list(limit=10)) == [], (
            "a read bound the attacker-chosen basename and returned its rows"
        )

        with db_connection(store) as conn:
            before = count_project_memories(conn, "ForeignProj")
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            patcher.setattr(
                PACTMemory, "_store_embedding", lambda self, *a, **k: None
            )
            try:
                memory.save(
                    {"context": "attacker-scope write"}, sync_to_claude=False
                )
            except Exception:
                # The refusal shape is the implementer's choice; the outcome
                # assertion below is what this arm pins.
                pass
        with db_connection(store) as conn:
            assert count_project_memories(conn, "ForeignProj") == before, (
                "a write landed in the bucket named by the repository's own "
                "checkout path"
            )


# ---------------------------------------------------------------------------
# The forged-`.git` guard vs a DEINIT-ED submodule admin dir
# ---------------------------------------------------------------------------

VICTIM = "github.com/victim/private"


def _deinit_ed_admin(tmp_path: Path) -> Path:
    """A submodule admin dir after `submodule deinit -f`, origin still set.

    MEASURED: `core.worktree` is unset for this shape while
    `remote.origin.url` survives in the admin config. The forged-gitfile guard
    uses `core.worktree` to tell a genuine submodule from a pointer at
    somebody else's, so an unset value makes the guard answer False — "nothing
    to contradict the pointer" — and the victim's origin is read.
    """
    source = make_repo(tmp_path / "deinit-source")
    superproject = make_repo(tmp_path / "deinit-super")
    run_git(
        "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
        str(source), "submod",
        cwd=superproject,
    )
    admin = superproject / ".git" / "modules" / "submod"
    run_git(
        "--git-dir", str(admin), "config", "remote.origin.url",
        f"https://{VICTIM}.git",
        cwd=superproject,
    )
    run_git("submodule", "deinit", "-f", "submod", cwd=superproject)
    return admin


def _forged_checkout(tmp_path: Path, admin: Path) -> Path:
    """A directory whose `.git` FILE points at `admin` — the forged pointer."""
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
    return attacker


class TestDeinitEdSubmoduleAdminDir:
    """The class property, at the ORIGIN rung: a repository-shaped pointer must
    not let a checkout bind somebody else's canonical id."""

    def test_a_forged_gitfile_at_a_deinit_ed_admin_dir_declines(
        self, tmp_path, monkeypatch
    ):
        """RED on the unfixed tree: the guard's `core.worktree` probe answers
        "unset" for a deinit-ed admin dir, so the origin is read from the
        victim and the id binds. Asserts the read AND write outcomes.
        """
        admin = _deinit_ed_admin(tmp_path)
        attacker = _forged_checkout(tmp_path, admin)
        store = tmp_path / "store.db"
        _seed_bucket(store, VICTIM, memory_id="victim-row")
        _enter(monkeypatch, tmp_path, attacker)
        memory = _memory(store)

        assert memory.project_id != VICTIM, (
            "a forged .git file at a deinit-ed submodule admin dir bound the "
            f"victim id: {memory.project_id!r}"
        )
        assert VICTIM not in (memory._read_project_scopes() or ()), (
            "the victim bucket joined the read scope through a deinit-ed "
            "submodule admin dir"
        )
        assert _ids(memory.list(limit=10)) == [], (
            "a read at a deinit-ed admin dir returned the victim's rows"
        )

        with db_connection(store) as conn:
            before = count_project_memories(conn, VICTIM)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(memory_api, "_ensure_ready", lambda: None)
            patcher.setattr(
                PACTMemory, "_store_embedding", lambda self, *a, **k: None
            )
            try:
                memory.save(
                    {"context": "forged-pointer write"}, sync_to_claude=False
                )
            except Exception:
                # The refusal shape is the implementer's choice.
                pass
        with db_connection(store) as conn:
            assert count_project_memories(conn, VICTIM) == before, (
                "a write landed in the victim's bucket through a deinit-ed "
                "submodule admin dir"
            )

    def test_a_live_submodule_still_resolves_its_own_id(self, tmp_path, monkeypatch):
        """POSITIVE CONTROL: the guard must keep resolving genuine submodules.

        A deinit-specific refusal that refuses every submodule would pass the
        arm above and break ordinary submodule use, so the live shape is
        asserted beside it.
        """
        source = make_repo(tmp_path / "live-source")
        superproject = make_repo(tmp_path / "live-super")
        run_git(
            "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
            str(source), "submod",
            cwd=superproject,
        )
        checkout = superproject / "submod"
        run_git(
            "remote", "set-url", "origin", "https://github.com/o/sub.git",
            cwd=checkout,
        )
        _enter(monkeypatch, tmp_path, checkout)

        memory = _memory(tmp_path / "store.db")
        assert memory.project_id == "github.com/o/sub", (
            "a genuine live submodule stopped resolving its own id: "
            f"{memory.project_id!r}"
        )