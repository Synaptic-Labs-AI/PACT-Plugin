"""Remediation arms for the security/hardening findings on repository identity.

Each arm names the defect it would catch, not the implementation it reads:
a wrong classification, a redirect that binds another repository, a command
that cannot be pasted, a log line a directory name can forge.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.project_identity as pi
from scripts.database import CANONICAL_ID_MAX_LENGTH

NO_PORTABLE_ORIGIN = pi.OUTCOME_NO_PORTABLE_ORIGIN
DECLINE = pi.OUTCOME_DECLINE
CANONICAL = pi.OUTCOME_CANONICAL


# ---------------------------------------------------------------------------
# Classification: BOM, leading whitespace, colon-in-userinfo
# ---------------------------------------------------------------------------

class TestLeadingBytesClassification:
    def test_a_leading_bom_declines_in_both_spellings(self):
        """Catches a BOM absorbed into the scp userinfo: the same value must
        not be refused in URL form and accepted in scp form."""
        assert pi.classify_remote_origin("\ufeffhttps://h/o/r")[0] == DECLINE
        assert pi.classify_remote_origin("\ufeffgit@h:o/r")[0] == DECLINE
        assert pi.normalize_remote_url("\ufeffgit@h:o/r") is None

    def test_leading_whitespace_declines_instead_of_basename(self):
        """Catches the asymmetry where trailing space declined and leading
        space silently fell back to a folder name."""
        assert pi.classify_remote_origin(" https://h/o/r")[0] == DECLINE
        assert pi.classify_remote_origin("https://h/o/r ")[0] == DECLINE

    def test_a_leading_space_before_a_local_path_stays_local(self):
        """The classification strip must not turn local paths into declines."""
        assert pi.classify_remote_origin(" /srv/git/repo")[0] == NO_PORTABLE_ORIGIN


class TestSchemeLessColonUserinfo:
    def test_a_colon_in_userinfo_declines(self):
        """Catches the CI shape `deploy-token:<token>@host/path` being filed
        under the folder basename with no warning."""
        assert pi.classify_remote_origin(
            "deploy-token:SECRET@github.com/owner/repo"
        ) == (DECLINE, None)
        assert pi.classify_remote_origin(
            "gitlab-ci-token:abc@gitlab.com/g/p"
        ) == (DECLINE, None)

    def test_the_ruled_narrowing_is_unchanged(self):
        """Catches a wider rule that would decline a user-less `host:path` or
        a Windows drive path."""
        assert pi.classify_remote_origin("github.com:owner/repo") == (
            NO_PORTABLE_ORIGIN, None
        )
        assert pi.classify_remote_origin("C:/repo") == (NO_PORTABLE_ORIGIN, None)
        assert pi.classify_remote_origin("C:\\repo") == (NO_PORTABLE_ORIGIN, None)

    def test_a_local_path_containing_at_is_still_local(self):
        assert pi.classify_remote_origin("/srv/mirrors/team@repo.git") == (
            NO_PORTABLE_ORIGIN, None
        )
        assert pi.classify_remote_origin("team@repo.git") == (NO_PORTABLE_ORIGIN, None)


# ---------------------------------------------------------------------------
# The length cap is ONE constant
# ---------------------------------------------------------------------------

def test_the_identifier_cap_comes_from_database():
    """Catches a second 255 that can drift from the grammar's own cap."""
    identifier = "h/" + "a" * (CANONICAL_ID_MAX_LENGTH - 2)
    assert pi.normalize_remote_url(f"https://{identifier}") == identifier
    over = "h/" + "a" * (CANONICAL_ID_MAX_LENGTH - 1)
    assert pi.normalize_remote_url(f"https://{over}") is None


# ---------------------------------------------------------------------------
# Override reader: the non-regular hardening and its contract
# ---------------------------------------------------------------------------

class TestOverrideContract:
    def test_a_fifo_is_refused_without_blocking(self, tmp_path):
        """Catches a regression to a plain open(), which would hang every
        memory operation that resolves an identity."""
        fifo = tmp_path / "pact-project-id"
        os.mkfifo(fifo)
        read = pi.read_override(tmp_path)
        assert read.value is None
        assert read.reason == "not_regular"
        assert read.path == fifo

    def test_a_symlink_is_refused(self, tmp_path):
        target = tmp_path / "elsewhere"
        target.write_text("github.com/owner/repo\n", encoding="utf-8")
        (tmp_path / "pact-project-id").symlink_to(target)
        read = pi.read_override(tmp_path)
        assert read.value is None
        assert read.reason == "not_regular"

    def test_the_reason_token_is_documented(self):
        """Catches a reason token that no consumer can anticipate."""
        assert "not_regular" in (pi.OverrideRead.__doc__ or "")


# ---------------------------------------------------------------------------
# Redirected checkouts must not bind another repository's identity
# ---------------------------------------------------------------------------

def _repo(path: Path, origin: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", origin], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=a@b", "-c", "user.name=a",
         "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
    )
    return path


def _common(root: Path) -> Path:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    path = Path(out)
    return (path if path.is_absolute() else root / path).resolve()


class TestRedirectedCheckouts:
    def test_a_direct_gitfile_redirect_declines(self, tmp_path):
        """Catches a `.git` FILE pointing at another repository's `.git`."""
        victim = _repo(tmp_path / "victim", "https://github.com/victim/private")
        forged = tmp_path / "forged"
        forged.mkdir()
        (forged / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")
        assert pi._gitfile_points_at_foreign_gitdir(forged) is True
        identity = pi.detect_id_for_root(forged, _common(victim))
        assert identity.project_id is None
        assert identity.source == "declined"

    def test_a_relative_gitfile_redirect_declines(self, tmp_path):
        victim = _repo(tmp_path / "victim", "https://github.com/victim/private")
        forged = tmp_path / "forged"
        forged.mkdir()
        (forged / ".git").write_text("gitdir: ../victim/.git\n", encoding="utf-8")
        assert pi._gitfile_points_at_foreign_gitdir(forged) is True

    def test_a_forged_direct_pointer_still_binds_the_victims_pin(self, tmp_path):
        """THE ACKNOWLEDGED NARROWING, pinned so it cannot widen silently: the
        origin is suppressed, but an override the VICTIM wrote still answers."""
        victim = _repo(tmp_path / "victim", "https://github.com/victim/private")
        (victim / ".git" / "pact-project-id").write_text(
            "github.com/victim/pinned\n", encoding="utf-8"
        )
        forged = tmp_path / "forged"
        forged.mkdir()
        (forged / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")
        identity = pi.detect_id_for_root(forged, _common(victim))
        assert identity.project_id == "github.com/victim/pinned"
        assert identity.source == "override file"

    def test_a_submodule_gitfile_is_not_a_redirect(self, tmp_path):
        """Catches a rule that refuses the legitimate `.../modules/<name>`
        shape."""
        src = _repo(tmp_path / "src", "https://github.com/owner/src")
        superrepo = _repo(tmp_path / "super", "https://github.com/owner/super")
        subprocess.run(
            ["git", "-C", str(superrepo), "-c", "protocol.file.allow=always",
             "submodule", "add", str(src), "submod"],
            check=True, capture_output=True,
        )
        assert pi._gitfile_points_at_foreign_gitdir(superrepo / "submod") is False

    def test_the_measured_separate_git_dir_exception_is_refused(self, tmp_path):
        """The one legitimate layout the predicate refuses, PINNED as a known
        exception so a future change makes it visible rather than silent."""
        store = tmp_path / "store" / ".git"
        store.parent.mkdir()
        checkout = tmp_path / "checkout"
        subprocess.run(
            ["git", "init", "-q", f"--separate-git-dir={store}", str(checkout)],
            check=True,
        )
        assert pi._gitfile_points_at_foreign_gitdir(checkout) is True

    def test_the_separate_git_dir_exception_is_recoverable_with_one_pin(self, tmp_path):
        """Catches a refusal placed BEFORE the override read, which would make
        this legitimate layout permanently unusable."""
        store = tmp_path / "store" / ".git"
        store.parent.mkdir()
        checkout = tmp_path / "checkout"
        subprocess.run(
            ["git", "init", "-q", f"--separate-git-dir={store}", str(checkout)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "remote", "add", "origin",
             "https://github.com/owner/sep"],
            check=True,
        )
        assert pi.detect_id_for_root(checkout, store.resolve()).project_id is None
        (store / "pact-project-id").write_text(
            "github.com/owner/pinned-sep\n", encoding="utf-8"
        )
        identity = pi.detect_id_for_root(checkout, store.resolve())
        assert identity.project_id == "github.com/owner/pinned-sep"
        assert identity.source == "override file"

    def test_a_linked_worktree_is_not_a_redirect(self, tmp_path):
        from scripts.memory_api import _worktree_redirect

        repo = _repo(tmp_path / "repo", "https://github.com/owner/repo")
        worktree = tmp_path / "wt"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-q", str(worktree), "-b", "wt"],
            check=True,
        )
        assert _worktree_redirect(worktree, _common(repo)) is False

    def test_a_forged_worktree_admin_pointer_is_a_redirect(self, tmp_path):
        from scripts.memory_api import _worktree_redirect

        repo = _repo(tmp_path / "repo", "https://github.com/owner/repo")
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-q",
             str(tmp_path / "wt"), "-b", "wt2"],
            check=True,
        )
        admin = next((repo / ".git" / "worktrees").iterdir())
        forged = tmp_path / "forged"
        forged.mkdir()
        (forged / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
        assert _worktree_redirect(forged, _common(repo)) is True

    def test_a_plain_checkout_and_a_submodule_are_not_redirects(self, tmp_path):
        from scripts.memory_api import _worktree_redirect

        repo = _repo(tmp_path / "repo", "https://github.com/owner/repo")
        assert _worktree_redirect(repo, _common(repo)) is False


# ---------------------------------------------------------------------------
# The rendered remedy must be runnable and store-accurate
# ---------------------------------------------------------------------------

class TestRunnableRekeyCommand:
    def test_the_command_carries_an_interpreter_and_a_script_path(self):
        """Catches the bare `pact-memory` verb, which no executable provides."""
        command = pi_module_render("host/owner/repo")
        words = command.split(" ", 2)
        assert words[0] == "python3"
        assert words[1].endswith("cli.py"), words[1]
        assert "project-id rekey --from" in command

    def test_the_command_carries_the_store_when_known(self, tmp_path):
        """Catches a remedy that reports against the DEFAULT store while the
        bucket was found in another one."""
        command = pi_module_render("host/owner/repo", db_path=tmp_path / "x.db")
        assert "--db-path" in command
        assert str(tmp_path / "x.db") in command

    def test_a_hostile_id_stays_one_argument(self):
        import shlex

        hostile = "Legacy; echo PWNED"
        words = shlex.split(pi_module_render(hostile))
        assert words[-1] == hostile


def pi_module_render(project_id, db_path=None):
    from scripts.memory_api import render_rekey_command

    return render_rekey_command(project_id, db_path=db_path)


# ---------------------------------------------------------------------------
# A `.git`-marked directory git cannot answer for must DECLINE, not basename
# ---------------------------------------------------------------------------

def _broken_repo(path: Path) -> Path:
    """A directory carrying a `.git` FILE whose gitdir target does not exist."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").write_text(
        f"gitdir: {path / 'gone' / '.git'}\n", encoding="utf-8"
    )
    return path


class TestBrokenRepositoryDeclines:
    def test_a_cwd_inside_a_broken_repository_declines(self, tmp_path, monkeypatch):
        broken = _broken_repo(tmp_path / "broken")
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(broken)
        from scripts.memory_api import PACTMemory

        memory = PACTMemory(db_path=tmp_path / "store" / "memory.db")
        assert memory.project_id is None
        assert memory._project_id_source == "unresolved"

    def test_a_declared_subdirectory_of_a_broken_repository_declines(
        self, tmp_path, monkeypatch
    ):
        """Catches a SUBDIRECTORY name becoming the project id."""
        broken = _broken_repo(tmp_path / "broken")
        sub = broken / "sub"
        sub.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(sub))
        # Run INSIDE the broken tree, so the git rung cannot rescue the
        # answer from the suite's own checkout, and neutralise the record
        # rung, which would otherwise legitimately answer.
        monkeypatch.chdir(broken)
        import scripts.memory_api as memory_api

        monkeypatch.setattr(
            memory_api, "get_project_dir_from_session_record", lambda: None
        )
        from scripts.memory_api import PACTMemory

        memory = PACTMemory(db_path=tmp_path / "store" / "memory.db")
        assert memory.project_id is None, memory.project_id
        assert memory._project_id_source == "unresolved"

    def test_a_plain_umbrella_keeps_its_designed_basename(self, tmp_path, monkeypatch):
        """The narrowing that must survive: a non-git folder basenames."""
        umbrella = tmp_path / "my-workspace"
        umbrella.mkdir()
        (umbrella / "CLAUDE.md").write_text("x", encoding="utf-8")
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(umbrella)
        from scripts.memory_api import PACTMemory

        memory = PACTMemory(db_path=tmp_path / "store" / "memory.db")
        assert memory.project_id == "my-workspace"

    def test_a_broken_repository_declines_before_the_user_setting(
        self, tmp_path, monkeypatch
    ):
        """Catches a stale/broken repository being absorbed by config.json."""
        memory_dir = tmp_path / "mem"
        memory_dir.mkdir()
        (memory_dir / "config.json").write_text(
            '{"project_id": "user.example.com/me/pinned"}', encoding="utf-8"
        )
        broken = _broken_repo(tmp_path / "broken")
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(memory_dir))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(broken)
        from scripts.memory_api import PACTMemory

        memory = PACTMemory(db_path=tmp_path / "store" / "memory.db")
        assert memory.project_id is None
        assert memory._project_id_source == "unresolved"


# ---------------------------------------------------------------------------
# The legacy gate must not RAISE on a read path (S2 integration)
# ---------------------------------------------------------------------------

class TestTheGateDoesNotRaiseOnAReadPath:
    def test_a_user_setting_identity_widens_instead_of_raising(
        self, tmp_path, monkeypatch
    ):
        """End-to-end S2 shape: canonical id from config.json, no
        CLAUDE_PROJECT_DIR, no session record, cwd outside any repository, and
        a populated legacy bucket.

        THIS IS THE SHAPE THAT USED TO RAISE. An earlier caller resolved its
        root set through `backlog.checkout_roots()`, which refuses with
        BacklogWriteError exactly here, so a READ raised a write-oriented
        error. The production path no longer touches that helper; the
        negative control below is kept to pin the FIXTURE, so a later
        simplification cannot quietly turn this into a shape that never
        exercised the defect.
        """
        memory_dir = tmp_path / "mem"
        memory_dir.mkdir()
        (memory_dir / "config.json").write_text(
            '{"project_id": "user.example.com/me/pinned"}', encoding="utf-8"
        )
        umbrella = tmp_path / "my-workspace"
        umbrella.mkdir()
        (umbrella / "CLAUDE.md").write_text("x", encoding="utf-8")
        store = tmp_path / "store" / "memory.db"
        store.parent.mkdir()

        from scripts.database import create_memory, db_connection

        with db_connection(str(store)) as conn:
            create_memory(
                conn, {"project_id": "my-workspace", "context": "legacy row"}
            )

        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(memory_dir))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(umbrella)

        import scripts.memory_api as memory_api

        monkeypatch.setattr(
            memory_api, "get_project_dir_from_session_record", lambda: None
        )

        # NEGATIVE CONTROL: the refusal must be live, or this arm is vacuous.
        from shared.backlog import BacklogWriteError, checkout_roots

        with pytest.raises(BacklogWriteError):
            checkout_roots()

        memory = memory_api.PACTMemory(db_path=store)
        rows = memory.list(limit=10)
        assert any(
            "legacy row" in (getattr(row, "context", "") or "") for row in rows
        ), rows
        state = memory._legacy_scope_state
        assert state is not None, "the legacy gate never ran"
        assert state["included_unverified"] is True, state


# ---------------------------------------------------------------------------
# GIT_DISCOVERY_ACROSS_FILESYSTEM must parse like git, not like Python
# ---------------------------------------------------------------------------

class TestGitEnvBoolMatchesGit:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1", True),
            ("true", True),
            ("YES", True),
            ("on", True),
            ("2", True),
            ("0", False),
            ("false", False),
            ("NO", False),
            ("off", False),
            ("", False),
            ("maybe", False),
            (" true", False),
        ],
    )
    def test_boolean_values(self, value, expected, monkeypatch):
        """Catches Python truthiness: bool("0") is True, git reads FALSE."""
        from scripts.memory_api import _git_env_bool

        monkeypatch.setenv("PACT_PROBE_BOOL", value)
        assert _git_env_bool("PACT_PROBE_BOOL") is expected

    def test_unset_is_false(self, monkeypatch):
        from scripts.memory_api import _git_env_bool

        monkeypatch.delenv("PACT_PROBE_BOOL", raising=False)
        assert _git_env_bool("PACT_PROBE_BOOL") is False

    def test_a_device_boundary_stops_the_walk_unless_git_would_cross(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: a false value must NOT cross a st_dev boundary.

        Before the fix `bool("0")` was True, so the walk crossed and found a
        marker git would never have found — a false decline.
        """
        from scripts import memory_api

        outer = tmp_path / "outer"
        outer.mkdir()
        (outer / ".git").mkdir()
        inner = outer / "inner"
        inner.mkdir()

        real_stat = os.stat

        def fake_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            device = 1 if str(path).endswith("inner") else 2
            return os.stat_result(
                (result.st_mode, result.st_ino, device, result.st_nlink,
                 result.st_uid, result.st_gid, result.st_size,
                 result.st_atime, result.st_mtime, result.st_ctime)
            )

        monkeypatch.setattr(memory_api.os, "stat", fake_stat)
        monkeypatch.delenv("GIT_DISCOVERY_ACROSS_FILESYSTEM", raising=False)
        assert memory_api._unanswerable_repository(inner) is None

        monkeypatch.setenv("GIT_DISCOVERY_ACROSS_FILESYSTEM", "1")
        assert memory_api._unanswerable_repository(inner) == outer / ".git"

        for false_value in ("0", "false", "no", "off", "maybe"):
            monkeypatch.setenv("GIT_DISCOVERY_ACROSS_FILESYSTEM", false_value)
            assert memory_api._unanswerable_repository(inner) is None, false_value


# ---------------------------------------------------------------------------
# Cold re-check: deleted cwd, submodule-admin redirect, cache-key identity root
# ---------------------------------------------------------------------------

class TestDeletedCwdDoesNotRaise:
    def test_the_user_setting_rung_survives_a_deleted_cwd(self, tmp_path, monkeypatch):
        """Catches `Path.cwd()` raising FileNotFoundError at construction.

        The user-setting rung is reachable with a deleted cwd: no declaration,
        no repository, and the process's directory has been removed.
        """
        memory_dir = tmp_path / "mem"
        memory_dir.mkdir()
        (memory_dir / "config.json").write_text(
            '{"project_id": "user.example.com/me/pinned"}', encoding="utf-8"
        )
        doomed = tmp_path / "doomed"
        doomed.mkdir()

        import scripts.memory_api as memory_api

        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(memory_dir))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.setattr(
            memory_api, "get_project_dir_from_session_record", lambda: None
        )

        previous = os.getcwd()
        os.chdir(doomed)
        try:
            os.rmdir(doomed)
            memory = memory_api.PACTMemory(db_path=tmp_path / "store" / "memory.db")
        finally:
            os.chdir(previous)

        assert memory.project_id == "user.example.com/me/pinned"
        assert memory._legacy_project_basename is None
        assert memory._project_identity_root is None


class TestSubmoduleAdminRedirect:
    def _submodule(self, tmp_path):
        src = _repo(tmp_path / "src", "https://github.com/owner/subsrc")
        superrepo = _repo(tmp_path / "super", "https://github.com/owner/super")
        subprocess.run(
            ["git", "-C", str(superrepo), "-c", "protocol.file.allow=always",
             "submodule", "add", str(src), "submod"],
            check=True, capture_output=True,
        )
        return superrepo, superrepo / "submod", superrepo / ".git" / "modules" / "submod"

    def test_a_genuine_submodule_is_not_a_redirect(self, tmp_path):
        superrepo, checkout, _admin = self._submodule(tmp_path)
        assert pi._gitfile_points_at_foreign_gitdir(checkout) is False
        assert pi._admin_dir_names_another_checkout(
            superrepo / ".git" / "modules" / "submod", checkout
        ) is False

    def test_a_separate_git_dir_checkout_is_not_a_redirect(self, tmp_path):
        """POSITIVE CONTROL for the core.worktree discriminator: an external
        store has no `core.worktree` recorded, so there is nothing to
        contradict and the layout must resolve normally."""
        store = tmp_path / "store" / "ext.git"
        store.parent.mkdir()
        checkout = tmp_path / "sep"
        subprocess.run(
            ["git", "init", "-q", f"--separate-git-dir={store}", str(checkout)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "remote", "add", "origin",
             "https://github.com/owner/sep"],
            check=True,
        )
        assert pi._gitfile_points_at_foreign_gitdir(checkout) is False
        identity = pi.detect_id_for_root(checkout, store.resolve())
        assert identity.project_id == "github.com/owner/sep"
        assert identity.source == "origin remote"

    def test_a_subdirectory_of_a_genuine_submodule_is_not_a_redirect(self, tmp_path):
        """POSITIVE CONTROL for the walk-up: the marker is found one level up,
        and the comparison is against the MARKER's directory, so a genuine
        submodule's own subdirectory must not be refused."""
        _super, checkout, _admin = self._submodule(tmp_path)
        (checkout / "pkg").mkdir()
        assert pi._gitfile_points_at_foreign_gitdir(checkout / "pkg") is False

    def test_a_subdirectory_of_a_forged_pointer_is_a_redirect(self, tmp_path):
        """Catches testing only `<dir>/.git`: a declared path one level below
        the forged checkout kept the victim bound."""
        _super, _checkout, admin = self._submodule(tmp_path)
        forged = tmp_path / "forged-sub"
        (forged / "sub").mkdir(parents=True)
        (forged / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
        assert pi._gitfile_points_at_foreign_gitdir(forged / "sub") is True

    def test_a_gitfile_pointing_at_a_submodule_admin_dir_declines(self, tmp_path):
        """Catches a pointer at `<other>/.git/modules/<name>`: its git dir
        EQUALS its common dir, so the listing check cannot see it and the
        basename is `<name>`, not `.git`."""
        superrepo, checkout, admin = self._submodule(tmp_path)
        forged = tmp_path / "forged-sub"
        forged.mkdir()
        (forged / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")

        assert pi._gitfile_points_at_foreign_gitdir(forged) is True
        identity = pi.detect_id_for_root(forged, admin.resolve())
        assert identity.project_id is None
        assert identity.source == "declined"


class TestSaveIngressRejectsNulFilePaths:
    def test_a_nul_file_path_is_refused_before_any_row_is_written(self, tmp_path):
        """Catches a save that raises 'nothing was written' AFTER the memory
        row is committed — measured: the row persisted. The refusal must come
        before the connection is opened."""
        import scripts.memory_api as memory_api

        store = tmp_path / "store" / "memory.db"
        store.parent.mkdir()
        memory = memory_api.PACTMemory(project_id="some-bucket", db_path=store)

        with pytest.raises(ValueError) as caught:
            memory.save(
                {"context": "nul file path"},
                files=["bad\x00path.py"],
                sync_to_claude=False,
            )
        assert "must not contain a NUL" in str(caught.value)
        # NOTHING WRITTEN. `_ensure_ready()` legitimately creates the store
        # FILE, so the assertion is on ROWS: before the fix the memory row
        # persisted while the error text claimed nothing was written.
        import sqlite3

        rows = 0
        if store.exists():
            check = sqlite3.connect(str(store))
            try:
                found = check.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='memories'"
                ).fetchone()
                if found:
                    rows = check.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            finally:
                check.close()
        assert rows == 0, f"the refused save left {rows} memory row(s)"


class TestForgedSubmoduleAdminEndToEnd:
    def test_constructing_from_the_forged_dir_declines(self, tmp_path, monkeypatch):
        """THE PRODUCTION PATH, which my unit arm above did not exercise.

        `main_repo_root` follows the forged gitfile's `core.worktree` to the
        VICTIM's checkout, so `detect_id_for_root` receives a genuine
        submodule as `root` and reads the victim's origin. The predicate must
        therefore be evaluated on the ASKED directory.
        """
        import scripts.memory_api as memory_api

        victim = _repo(tmp_path / "victimlib", "https://github.com/victim/secret-lib")
        sup = _repo(tmp_path / "super", "https://github.com/owner/super")
        subprocess.run(
            ["git", "-C", str(sup), "-c", "protocol.file.allow=always",
             "submodule", "add", str(victim), "submod"],
            check=True, capture_output=True,
        )
        admin = sup / ".git" / "modules" / "submod"
        forged = tmp_path / "forged-sub"
        forged.mkdir()
        (forged / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")

        # The victim's origin must be a PORTABLE URL, or the redirect only
        # reaches a basename and the arm would pass for the wrong reason.
        subprocess.run(
            ["git", "-C", str(sup / "submod"), "remote", "set-url", "origin",
             "https://github.com/victim/secret-lib"],
            check=True,
        )

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(tmp_path / "mem"))
        (tmp_path / "mem").mkdir()
        monkeypatch.setattr(
            memory_api, "get_project_dir_from_session_record", lambda: None
        )
        monkeypatch.chdir(forged)

        memory = memory_api.PACTMemory(db_path=tmp_path / "store" / "memory.db")
        assert memory.project_id != "github.com/victim/secret-lib"
        assert memory.project_id is None
        assert memory._project_id_source == "unresolved"

        # THE DECLARED PATHS, including a SUBDIRECTORY of the forged dir:
        # `<subdir>/.git` does not exist, so the marker must be found by
        # walking up. In this suite the cwd is a real repository, so a
        # declined declaration legitimately falls through to it — what must
        # never appear is the VICTIM's id.
        monkeypatch.chdir(tmp_path)
        (forged / "sub").mkdir()
        for declared in (forged, forged / "sub"):
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(declared))
            declared_memory = memory_api.PACTMemory(
                db_path=tmp_path / "store" / "memory.db"
            )
            assert declared_memory.project_id != "github.com/victim/secret-lib"
            assert declared_memory._project_id_source != "origin remote" or (
                declared_memory.project_id
                != "github.com/victim/secret-lib"
            )


class TestReadScopeCacheKeyIncludesTheIdentityRoot:
    def test_two_roots_with_the_same_scope_do_not_share_a_verdict(self, tmp_path):
        """Catches a cache key without the identity root: the plausibility
        verdict is computed AGAINST that root, so two same-named checkouts of
        one repository would serve each other's include/exclude decision."""
        import scripts.memory_api as memory_api
        from scripts.database import create_memory, db_connection
        from scripts.graph import track_file

        # Two checkouts with the SAME folder name and the SAME origin, so the
        # canonical scope and the legacy basename are identical for both.
        left = _repo(tmp_path / "left" / "PACT-prompt", "https://github.com/o/PACT-prompt")
        right = _repo(tmp_path / "right" / "PACT-prompt", "https://github.com/o/PACT-prompt")
        store = tmp_path / "store" / "memory.db"
        store.parent.mkdir()

        with db_connection(str(store)) as conn:
            create_memory(conn, {"project_id": "PACT-prompt", "context": "legacy"})
            track_file(conn, str(left / "src" / "x.py"), "PACT-prompt")

        # CLEARED ONCE, NEVER BETWEEN THE TWO CALLS: a per-call clear would
        # make this arm pass even with the identity root missing from the key,
        # because the second call would recompute instead of hitting.
        memory_api._LEGACY_SCOPE_CACHE.clear()
        memory_api._EXCLUSION_WARNED.clear()

        def verdict(checkout):
            memory = memory_api.PACTMemory(
                project_id="github.com/o/PACT-prompt", db_path=store
            )
            # `project_id` supplied means no detection; pin the root by hand,
            # which is exactly the input under test.
            memory._project_identity_root = checkout
            memory._legacy_project_basename = "PACT-prompt"
            return memory._read_project_scopes()

        # Warm the cache from the FIRST root, then ask with the second.
        assert verdict(left) == ("github.com/o/PACT-prompt", "PACT-prompt")
        # The identity root is a KEY COMPONENT, not merely used in the verdict:
        # asserted directly so the arm does not depend on which entry survives.
        assert any(
            str(left.resolve()) in key for key in memory_api._LEGACY_SCOPE_CACHE
        ), memory_api._LEGACY_SCOPE_CACHE
        assert verdict(right) == ("github.com/o/PACT-prompt",)
        # SAME-ROOT CONTROL: asking again with the first root must include,
        # so the exclusion above is the ROOT's verdict and not "the second
        # call always excludes".
        assert verdict(left) == ("github.com/o/PACT-prompt", "PACT-prompt")


class TestProjectIdShowRoots:
    def test_repo_root_is_the_identity_root_and_the_process_root_is_named(
        self, tmp_path
    ):
        """Catches a primary field named `repo_root` that names the PROCESS's
        repository: in a declared-repo session that is a different tree from
        the one the legacy gate used, and it was the misdirection the finding
        was about."""
        cli = Path(__file__).resolve().parents[1] / "skills" / "pact-memory" / "scripts" / "cli.py"
        process_repo = _repo(tmp_path / "process-repo", "https://github.com/owner/process")
        declared_repo = _repo(tmp_path / "declared-repo", "https://github.com/owner/declared")
        store = tmp_path / "store" / "memory.db"
        store.parent.mkdir()
        env = dict(
            os.environ,
            PACT_TEST_MEMORY_DIR=str(store.parent),
            CLAUDE_PROJECT_DIR=str(declared_repo),
        )

        from scripts.database import db_connection

        with db_connection(str(store)):
            pass

        show = subprocess.run(
            [sys.executable, str(cli), "project-id", "show", "--db-path", str(store)],
            capture_output=True, text=True, env=env, cwd=str(process_repo),
        )
        assert show.returncode == 0, show.stderr
        payload = json.loads(show.stdout)["result"]

        assert payload["repo_root"] == str(declared_repo.resolve())
        assert payload["process_repo_root"] == str(process_repo.resolve())
        assert "identity_root" not in payload


# ---------------------------------------------------------------------------
# The exclusion warning must not assert an unread row's location
# ---------------------------------------------------------------------------

class TestExclusionWarningHedgesTheLocation:
    def test_the_warning_says_could_be_located_not_lies_under(
        self, tmp_path, monkeypatch, caplog
    ):
        """Catches a warning that asserts an unverified fact: for a row the
        gate could not READ, whether it sits under the root is UNKNOWN, and
        'none ... lies under' sends an operator hunting paths when the cause
        is a corrupt row."""
        from scripts.database import create_memory, db_connection
        from scripts.graph import track_file

        checkout = _repo(
            tmp_path / "repo" / "PACT-prompt", "https://github.com/o/PACT-prompt"
        )
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        store = tmp_path / "store" / "memory.db"
        store.parent.mkdir()
        with db_connection(str(store)) as conn:
            create_memory(conn, {"project_id": "PACT-prompt", "context": "legacy"})
            track_file(conn, str(elsewhere / "x.py"), "PACT-prompt")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(checkout))
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(store.parent))
        import scripts.memory_api as memory_api

        memory_api._LEGACY_SCOPE_CACHE.clear()
        memory_api._EXCLUSION_WARNED.clear()

        with caplog.at_level(logging.WARNING):
            memory = memory_api.PACTMemory(db_path=store)
            memory._read_project_scopes()

        text = "\n".join(record.getMessage() for record in caplog.records)
        assert "could be located under" in text, text
        assert "lies under" not in text, text
        assert "unknown location" in text, text


# ---------------------------------------------------------------------------
# B1, THE DELIVERABLE: the printed remedy must execute
# ---------------------------------------------------------------------------

class TestTheRenderedCommandExecutes:
    def test_the_printed_remedy_runs_against_the_store_it_names(self, tmp_path):
        """Takes the string a real surface printed and RUNS it.

        Catches both halves of B1: the bare `pact-memory` verb (argparse exit
        2) and the missing `--db-path` (a success envelope reporting 0 moved
        from the DEFAULT store while the real bucket sat untouched).
        """
        cli = Path(__file__).resolve().parents[1] / "skills" / "pact-memory" / "scripts" / "cli.py"
        store = tmp_path / "store" / "memory.db"
        store.parent.mkdir()
        # The operator runs the remedy from the repository, so the canonical
        # target resolves by detection and the command needs no --to.
        checkout = _repo(tmp_path / "checkout", "https://github.com/owner/target")
        env = dict(
            os.environ,
            PACT_TEST_MEMORY_DIR=str(store.parent),
            CLAUDE_PROJECT_DIR=str(checkout),
        )

        from scripts.database import create_memory, db_connection

        with db_connection(str(store)) as conn:
            create_memory(conn, {"project_id": "Legacy-Bucket", "context": "x"})

        show = subprocess.run(
            [sys.executable, str(cli), "project-id", "show", "--db-path", str(store)],
            capture_output=True, text=True, env=env, cwd=str(tmp_path),
        )
        assert show.returncode == 0, show.stderr
        rendered = json.loads(show.stdout)["result"]["candidates"][0]["rekey"]

        run = subprocess.run(
            rendered, shell=True, capture_output=True, text=True,
            env=env, cwd=str(tmp_path),
        )
        assert run.returncode == 0, (rendered, run.stderr)
        payload = json.loads(run.stdout)["result"]
        assert payload["dry_run"] is True
        assert payload["from"] == "Legacy-Bucket"
        assert payload["to"] == "github.com/owner/target"
        # The store it names is the store that answered.
        assert payload["memories"] == 1


# ---------------------------------------------------------------------------
# S1: the user-setting rung must keep the pre-change basename
# ---------------------------------------------------------------------------

class TestUserSettingRungCarriesTheLegacyCandidate:
    def test_an_umbrella_with_a_config_knob_keeps_its_legacy_bucket(
        self, tmp_path, monkeypatch
    ):
        """Catches the rung returning a bare id: every row filed under the
        folder name disappears from reads and the backlog writer refuses."""
        memory_dir = tmp_path / "mem"
        memory_dir.mkdir()
        (memory_dir / "config.json").write_text(
            '{"project_id": "user.example.com/me/pinned"}', encoding="utf-8"
        )
        umbrella = tmp_path / "my-workspace"
        umbrella.mkdir()
        (umbrella / "CLAUDE.md").write_text("x", encoding="utf-8")
        monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(memory_dir))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(umbrella)

        from scripts.memory_api import PACTMemory

        memory = PACTMemory(db_path=tmp_path / "store" / "memory.db")
        assert memory._project_id == "user.example.com/me/pinned"
        assert memory._project_id_source == "user setting"
        assert memory._legacy_project_basename == "my-workspace"
        assert memory._project_identity_root is not None


# ---------------------------------------------------------------------------
# No user-facing text may name a `pact-memory <verb>` executable
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "pact-memory" / "scripts"

_FAKE_EXECUTABLE_RE = __import__("re").compile(
    r"\bpact-memory\s+(?:project-id|rekey|save|search|list|get|update|delete|sync|status|setup|project-file)\b"
)


def test_no_user_facing_text_names_a_pact_memory_executable():
    """Catches a remedy that tells the operator to run an executable that
    does not exist. Comments are exempt (`cli.py`'s explanation of the bug)."""
    import ast

    offences = []
    for name in ("memory_api.py", "cli.py"):
        tree = ast.parse((_SCRIPTS_DIR / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _FAKE_EXECUTABLE_RE.search(node.value):
                    offences.append((name, node.value[:80]))
    assert not offences, offences

def test_a_newline_in_a_repo_path_does_not_forge_a_warning_line(
    tmp_path, monkeypatch, caplog
):
    """Catches `%s` on a path argument: a directory name carrying a newline
    lands on its own stderr line, which an agent then reads as a message."""
    from scripts.memory_api import PACTMemory

    hostile = tmp_path / "hostile\ndir"
    _repo(hostile, "https://github.invalid")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(hostile))
    with caplog.at_level(logging.WARNING):
        PACTMemory(db_path=tmp_path / "store" / "memory.db")
    records = [r.getMessage() for r in caplog.records]
    assert any("could not be read or represented" in r for r in records), records
    assert all("\n" not in r for r in records), records
