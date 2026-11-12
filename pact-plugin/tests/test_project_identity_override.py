"""Override precedence and the resolver rungs.

The override location is the contract: `<git-common-dir>/pact-project-id`,
resolved through `git rev-parse --git-common-dir` — never a literal `.git`
join. An in-tree `.pact/project-id` is IGNORED because it is plantable by a
clone, a tarball or a vendor copy.

Each arm names the defect it would catch. Every fixture is a REAL repository:
the seam under test is what git answers, not what a mock says.
"""

from __future__ import annotations

import json
import logging

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    common_dir,
    make_repo,
    make_separate_git_dir_repo,
    make_worktree,
    write_common_dir_override,
)
from scripts.memory_api import PACTMemory, main_repo_common_dir

from scripts import memory_api

OVERRIDE = "override.example.com/team/pinned"
REMOTE = "https://github.com/syn-labs/pact-plugin.git"
CANONICAL = "github.com/syn-labs/pact-plugin"


@pytest.fixture
def identity_env(monkeypatch, tmp_path):
    """A tmp root outside any repository, with no ambient declaration."""
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    # The session-record reader refuses test processes, so rung 1.5 is inert
    # here by design; the env rung is tested explicitly and removed by default.
    return tmp_path


def _memory(tmp_path: str, **kwargs) -> PACTMemory:
    return PACTMemory(session_id="s", db_path=tmp_path / "identity.db", **kwargs)


# ---------------------------------------------------------------------------
# Where the override lives
# ---------------------------------------------------------------------------

class TestOverrideLocation:
    def test_a_repo_scoped_override_wins_from_the_main_checkout(
        self, identity_env, monkeypatch
    ):
        """Catches a resolver that only looks at the remote rung."""
        repo = make_repo(identity_env / "checkout", remote=REMOTE)
        write_common_dir_override(repo, OVERRIDE)
        monkeypatch.chdir(repo)
        assert PACTMemory._detect_project_id_with_source() == (
            OVERRIDE,
            "override file",
        )

    def test_the_override_is_shared_by_a_linked_worktree(self, identity_env, monkeypatch):
        """Catches a resolver that joins the WORKTREE's `.git` (a file).

        All worktrees share one common dir; the per-worktree admin dir must
        not be consulted for the override.
        """
        main = make_repo(identity_env / "main", remote=REMOTE)
        write_common_dir_override(main, OVERRIDE)
        worktree = make_worktree(main, identity_env / "wt")
        monkeypatch.chdir(worktree)
        assert PACTMemory._detect_project_id_with_source() == (
            OVERRIDE,
            "override file",
        )

    def test_the_override_is_found_from_a_subdirectory(self, identity_env, monkeypatch):
        """Catches a resolver that only checks the cwd itself."""
        repo = make_repo(identity_env / "checkout", remote=REMOTE)
        write_common_dir_override(repo, OVERRIDE)
        subdirectory = repo / "src" / "deep"
        subdirectory.mkdir(parents=True)
        monkeypatch.chdir(subdirectory)
        assert PACTMemory._detect_project_id_with_source() == (
            OVERRIDE,
            "override file",
        )

    def test_the_override_path_is_the_common_dir_not_a_literal_dot_git(
        self, identity_env, monkeypatch
    ):
        """A --separate-git-dir checkout proves the resolver asks git.

        The work tree's `.git` is a FILE pointing at the external common dir;
        a resolver that joins `<root>/.git` cannot read the override at all.
        """
        repo = make_separate_git_dir_repo(
            identity_env / "checkout", identity_env / "external.git"
        )
        assert (repo / ".git").is_file(), "fixture is not a separate-git-dir repo"
        write_common_dir_override(repo, OVERRIDE)
        monkeypatch.chdir(repo)
        assert PACTMemory._detect_project_id_with_source() == (
            OVERRIDE,
            "override file",
        )

    def test_an_in_tree_dot_pact_override_is_ignored(self, identity_env, monkeypatch):
        """THE SECURITY ARM. Catches a resolver that reads the plantable path.

        A clone can carry `.pact/project-id`; it must never redirect the
        memory scope. The same test carries the positive control: the
        common-dir file DOES win, so the negative assertion is not measuring
        a dead resolver.
        """
        repo = make_repo(identity_env / "checkout", remote=REMOTE)
        (repo / ".pact").mkdir()
        (repo / ".pact" / "project-id").write_text(
            "evil.example.com/attacker/redirect\n", encoding="utf-8"
        )
        subdirectory = repo / "src"
        subdirectory.mkdir()
        (subdirectory / ".pact").mkdir()
        (subdirectory / ".pact" / "project-id").write_text(
            "evil.example.com/attacker/subdir\n", encoding="utf-8"
        )

        monkeypatch.chdir(repo)
        assert PACTMemory._detect_project_id_with_source() == (
            CANONICAL,
            "origin remote",
        ), "a plantable in-tree override was honoured"

        # Positive control: the legitimate surface still works.
        write_common_dir_override(repo, OVERRIDE)
        assert PACTMemory._detect_project_id_with_source() == (
            OVERRIDE,
            "override file",
        )

    def _submodule(self, identity_env):
        sub = make_repo(
            identity_env / "sub-origin", remote="https://github.com/o/sub.git"
        )
        superproject = make_repo(identity_env / "super")
        write_common_dir_override(superproject, "super.example.com/pinned/super")
        # A local-path submodule needs the file protocol explicitly allowed.
        import subprocess

        result = subprocess.run(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                "-q",
                str(sub),
                "vendor/sub",
            ],
            cwd=str(superproject),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        submodule = superproject / "vendor" / "sub"
        # `submodule add <local path>` records a LOCAL-PATH origin, which is
        # `no_portable_origin` by design. Give it a network remote so the arm
        # tests the ID rung, not the local-path fallback.
        subprocess.run(
            [
                "git",
                "-C",
                str(submodule),
                "remote",
                "set-url",
                "origin",
                "https://github.com/o/sub.git",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return superproject, submodule

    def test_a_submodule_uses_its_own_common_dir_for_the_override(
        self, identity_env, monkeypatch
    ):
        """Catches an override resolver that walks to the superproject's
        `.git`. The submodule's common dir is its own; the superproject's
        override must not leak in."""
        superproject, submodule = self._submodule(identity_env)
        module_common = common_dir(submodule)
        assert "modules" in str(module_common), module_common
        assert common_dir(submodule) != common_dir(superproject)
        monkeypatch.chdir(submodule)
        # The superproject's override is NOT visible here; the submodule's own
        # origin decides.
        assert PACTMemory._detect_project_id_with_source() == (
            "github.com/o/sub",
            "origin remote",
        )
        # Its OWN common-dir override does win.
        write_common_dir_override(submodule, "sub.example.com/pinned/sub")
        assert PACTMemory._detect_project_id_with_source() == (
            "sub.example.com/pinned/sub",
            "override file",
        )

    def test_a_submodule_keeps_its_own_canonical_id(self, identity_env, monkeypatch):
        """DEFECT ARM. `main_repo_root()` assumes `common_dir.parent` is the
        repository root; in a submodule that is `<super>/.git/modules/vendor`,
        so the origin is read at the wrong path and the id collapses to
        'vendor'. Expected: the submodule's own canonical id.

        ALSO PINS THE LEGACY-CANDIDATE SUPPRESSION. The
        PRE-change resolver derived the legacy bucket from
        `common_dir.resolve().parent.name` — 'vendor' for this fixture — but a
        submodule admin dir's parent name comes from the repository's own
        tracked `.gitmodules`, so a repository could choose which populated
        bucket a read unions. No legacy candidate is derived for this shape at
        all. This supersedes the plan's pin-census line that required 'vendor'
        here; the derivation is now pinned as ABSENT rather than as a value.
        """
        _, submodule = self._submodule(identity_env)
        monkeypatch.chdir(submodule)
        assert PACTMemory._detect_project_id_with_source() == (
            "github.com/o/sub",
            "origin remote",
        ), "a submodule did not keep its own canonical id (common_dir.parent)"

        memory = _memory(identity_env)
        assert memory._legacy_project_basename is None, (
            "a submodule admin dir still derives a legacy candidate; its name "
            "is chosen by the tracked .gitmodules, so the read union must not "
            "use it (supersedes the plan's 'vendor' census line)"
        )

    def test_a_separate_git_dir_repo_keeps_its_own_canonical_id(
        self, identity_env, monkeypatch
    ):
        """DEFECT ARM, same root cause. `--separate-git-dir` puts the common
        dir outside the work tree, so `common_dir.parent` names the external
        directory's parent, not the checkout root."""
        repo = make_separate_git_dir_repo(
            identity_env / "checkout", identity_env / "external.git"
        )
        import subprocess

        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/o/sep.git"],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        monkeypatch.chdir(repo)
        assert PACTMemory._detect_project_id_with_source() == (
            "github.com/o/sep",
            "origin remote",
        ), "a --separate-git-dir repo lost its canonical id (common_dir.parent)"

        # The legacy candidate is the EXTERNAL git dir's parent name, not the
        # checkout folder: the pre-change resolver used common_dir.parent.
        memory = _memory(identity_env)
        assert memory._legacy_project_basename == identity_env.name, (
            "the legacy candidate followed the checkout folder instead of "
            "the external git dir's parent"
        )
        assert memory._legacy_project_basename != "checkout"

    def test_the_user_setting_is_the_no_repository_fallback(
        self, identity_env, monkeypatch
    ):
        """Catches a missing fallback for sessions with no repository at all."""
        umbrellas = identity_env / "umbrella"
        umbrellas.mkdir()
        (umbrellas / "CLAUDE.md").write_text("workspace\n", encoding="utf-8")
        config_root = identity_env / "config"
        config_root.mkdir()
        (config_root / "config.json").write_text(
            json.dumps({"project_id": "user.example.com/me/pinned"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(memory_api, "get_memory_dir", lambda: config_root)
        monkeypatch.chdir(umbrellas)
        assert PACTMemory._detect_project_id_with_source() == (
            "user.example.com/me/pinned",
            "user setting",
        )

    def test_a_repository_override_outranks_the_user_setting(
        self, identity_env, monkeypatch
    ):
        """Catches a user setting consulted BEFORE the repository rung.

        NOTE: the ruled disagreement WA RNING is unreachable by construction —
        the user setting is read only after no repository answered, so the two
        surfaces cannot disagree in one resolution. This arm pins precedence,
        not a warning. (Reported to the lead as a contract gap, not a test.)
        """
        repo = make_repo(identity_env / "checkout", remote=REMOTE)
        write_common_dir_override(repo, OVERRIDE)
        config_root = identity_env / "config"
        config_root.mkdir()
        (config_root / "config.json").write_text(
            json.dumps({"project_id": "user.example.com/me/pinned"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(memory_api, "get_memory_dir", lambda: config_root)
        monkeypatch.chdir(repo)
        assert PACTMemory._detect_project_id_with_source() == (
            OVERRIDE,
            "override file",
        )


# ---------------------------------------------------------------------------
# Bad override and the structured disclosure
# ---------------------------------------------------------------------------

class TestBadOverride:
    def test_an_unusable_override_is_disclosed_and_falls_through(
        self, identity_env, monkeypatch
    ):
        """Catches a bad override that either crashes or wins silently.

        The reason token is structured on the instance because a caller that
        parses the envelope never sees stderr.
        """
        repo = make_repo(identity_env / "checkout", remote=REMOTE)
        target = common_dir(repo) / "pact-project-id"
        target.write_bytes(b"\xffnot-utf8")
        monkeypatch.chdir(repo)

        memory = _memory(identity_env)
        assert memory._project_id == CANONICAL
        assert memory._project_id_source == "origin remote"
        assert memory._override_ignored is not None
        assert memory._override_ignored["reason"] == "not_utf8"
        assert memory._override_ignored["path"].endswith("pact-project-id")

    def test_an_empty_override_falls_through_with_a_reason(
        self, identity_env, monkeypatch
    ):
        """Catches an empty file being treated as a valid empty id."""
        repo = make_repo(identity_env / "checkout", remote=REMOTE)
        write_common_dir_override(repo, "   ")
        monkeypatch.chdir(repo)
        memory = _memory(identity_env)
        assert memory._project_id == CANONICAL
        assert memory._override_ignored["reason"] == "empty"


class TestUnrepresentableRemoteRemedy:
    def test_the_decline_message_names_the_override_and_the_inspection_verb(
        self, identity_env, monkeypatch, caplog
    ):
        """An unrepresentable remote path (an interior space) is DECLINED,
        and the remedy must be
        actionable. Catches a decline that leaves the user with a folder-name
        fallback or no way to recover.

        THE REMEDY IS NAMED AS A VERB, not as a shell command: the rendered
        rekey form is interpreter-prefixed and carries `--db-path`, so prose
        warnings name `project-id show` and the CLI help carries the runnable
        string. The override PATH is still the second half of the remedy and
        is asserted here.
        """
        repo = make_repo(
            identity_env / "checkout",
            remote="https://github.com/o/re po.git",
        )
        monkeypatch.chdir(repo)
        with caplog.at_level(logging.WARNING):
            memory = _memory(identity_env)
        assert memory._project_id is None, "an unrepresentable remote did not decline"
        text = caplog.text
        assert "pact-project-id" in text, (
            "the decline did not name the override path"
        )
        assert "`project-id show` verb" in text, (
            "the decline did not name the project-id inspection verb"
        )


class TestDeclaredDirectoryDecline:
    def test_an_unrepresentable_remote_on_a_declared_repo_declines(
        self, identity_env, monkeypatch, caplog
    ):
        """The DECLARED rung's split, not only the git rung's.

        The ruled plan names three declared-dir outcomes: canonical, basename
        with no portable origin, and DECLINE for a present network remote that
        cannot be represented. Only the canonical one had an arm; the other
        two were reachable only through `detect_id_for_root`'s unit tests,
        which do not prove the DECLARED rung routes them. A regression that
        returns the folder basename here files a session into a bucket nobody
        can re-derive — the silent-misscope class this change removes. RED
        WHEN the declared branch falls back to a basename on a decline.
        """
        repo = make_repo(identity_env / "checkout", remote="ftp://host/o/r.git")
        workspace = identity_env / "workspace"
        workspace.mkdir()
        monkeypatch.chdir(workspace)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))

        with caplog.at_level(logging.WARNING):
            name, source = PACTMemory._detect_project_id_with_source()

        assert name is None, (
            "an unrepresentable declared remote fell back to a basename"
        )
        text = caplog.text
        assert "pact-project-id" in text, (
            "the declared-rung decline did not name the override path"
        )
        assert "`project-id show` verb" in text, (
            "the declared-rung decline did not name the inspection verb"
        )

    def test_a_declared_repo_without_a_portable_origin_still_basenames(
        self, identity_env, monkeypatch
    ):
        """The SAME rung's other arm: no origin is a designed basename.

        Paired with the decline above so a regression cannot satisfy one by
        collapsing the two outcomes into each other.
        """
        repo = make_repo(identity_env / "checkout", remote=None)
        workspace = identity_env / "workspace"
        workspace.mkdir()
        monkeypatch.chdir(workspace)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))

        assert PACTMemory._detect_project_id_with_source() == (
            "checkout",
            "CLAUDE_PROJECT_DIR",
        )


class TestLegacyBasenameOverridePin:
    def test_a_basename_override_is_ignored_as_not_canonical(
        self, identity_env, monkeypatch
    ):
        """A folder-name pin is reported and skipped, never bound.

        Honoured, it would let a delivered checkout name any legacy bucket in
        the store. A copied store is moved with
        `project-id rekey --from=<old> --to=<canonical>` instead, so the id
        falls through to the origin remote.
        """
        repo = make_repo(identity_env / "checkout", remote=REMOTE)
        override = write_common_dir_override(repo, "PACT-prompt")
        monkeypatch.chdir(repo)

        memory = _memory(identity_env)

        assert memory._project_id == CANONICAL, (
            "a basename-shaped override was bound instead of the origin remote"
        )
        assert memory._project_id_source == "origin remote"
        assert memory._project_id_shape == "canonical"
        assert memory._override_ignored == {
            "path": str(override),
            "reason": "not_canonical",
        }


# ---------------------------------------------------------------------------
# Precedence: supplied > repository override > declared directory
# ---------------------------------------------------------------------------

class TestPrecedence:
    def test_a_supplied_id_short_circuits_every_rung(self, identity_env, monkeypatch):
        """Catches a supplied caller id being re-detected or overridden."""
        repo = make_repo(identity_env / "checkout", remote=REMOTE)
        write_common_dir_override(repo, OVERRIDE)
        monkeypatch.chdir(repo)
        memory = _memory(identity_env, project_id="supplied.example.com/me/x")
        assert memory._project_id == "supplied.example.com/me/x"
        assert memory._project_id_source == "supplied"
        assert memory._project_id_shape == "caller"

    def test_claude_project_dir_canonicalises_through_the_same_chain(
        self, identity_env, monkeypatch
    ):
        """Catches canonicalising only the git rung.

        PACT-spawned sessions set CLAUDE_PROJECT_DIR, so if this rung keeps a
        basename the cross-machine fix never fires in real sessions.
        """
        repo = make_repo(identity_env / "checkout", remote=REMOTE)
        workspace = identity_env / "workspace"
        workspace.mkdir()
        monkeypatch.chdir(workspace)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        assert PACTMemory._detect_project_id_with_source() == (
            CANONICAL,
            "CLAUDE_PROJECT_DIR",
        )

    def test_the_legacy_basename_is_the_checkout_folder_not_the_remote_repo(
        self, identity_env, monkeypatch
    ):
        """Catches re-deriving the legacy id from the canonical id.

        The union can only find the old bucket if this value is the FOLDER
        name the pre-change resolver used.
        """
        repo = make_repo(identity_env / "PACT-prompt-checkout", remote=REMOTE)
        monkeypatch.chdir(repo)
        memory = _memory(identity_env)
        assert memory._project_id == CANONICAL
        # A LITERAL, NOT A RE-COMPUTATION. The pre-change resolver returned the
        # checkout folder's name, and that name is fixed by the fixture above.
        # Deriving the expectation with production's own formula would make
        # this arm follow any change to that formula instead of catching it.
        assert memory._legacy_project_basename == "PACT-prompt-checkout"

    def test_a_worktree_carries_the_main_repo_basename_as_its_legacy(
        self, identity_env, monkeypatch
    ):
        """Catches a worktree legacy candidate equal to the worktree folder.

        The pre-change resolver rewrote a worktree to its main repo, so the
        legacy bucket is the MAIN checkout's name — computed here by the same
        old derivation, never hand-written.
        """
        main = make_repo(identity_env / "PACT-prompt-checkout", remote=REMOTE)
        worktree = make_worktree(main, identity_env / "worktree-folder")
        monkeypatch.chdir(worktree)
        memory = _memory(identity_env)
        assert memory._project_id == CANONICAL
        expected = main_repo_common_dir(str(worktree)).parent.name
        assert expected == "PACT-prompt-checkout", "fixture shape changed"
        assert memory._legacy_project_basename == expected

    def test_a_non_git_umbrella_legacy_candidate_is_the_folder_basename(
        self, identity_env, monkeypatch
    ):
        """The other ruled shape: no `.git`, so the old resolver's output is
        the folder basename. Catches a None or a canonicalised legacy there."""
        umbrella = identity_env / "umbrella-workspace"
        umbrella.mkdir()
        (umbrella / "CLAUDE.md").write_text("workspace\n", encoding="utf-8")
        monkeypatch.chdir(umbrella)
        memory = _memory(identity_env)
        assert memory._project_id == "umbrella-workspace"
        assert memory._legacy_project_basename == "umbrella-workspace"


# ---------------------------------------------------------------------------
# Credentials never reach a log line
# ---------------------------------------------------------------------------

class TestUserinfoNeverLogged:
    def test_a_token_bearing_remote_is_not_logged(self, identity_env, monkeypatch, caplog):
        """Catches a debug line printing the raw remote (CI tokens are
        routine there)."""
        repo = make_repo(
            identity_env / "checkout",
            remote="https://x-access-token:SECRET-TOKEN@github.com/syn-labs/pact-plugin.git",
        )
        monkeypatch.chdir(repo)
        with caplog.at_level(logging.DEBUG):
            memory = _memory(identity_env)
        assert memory._project_id == CANONICAL
        assert "SECRET-TOKEN" not in caplog.text
        assert "SECRET-TOKEN" not in (memory._project_id or "")