"""Cross-site bidirectional certification of the #1247 CLAUDE.md write-path
containment guard.

The guard is inlined at the top of the `_atomic_write_text` twin
(hooks/shared/claude_md_manager.py + skills/pact-memory/scripts/working_memory.py):
it refuses a write unless the directory the write will BIND INTO is contained
within `project_root`, decided by KERNEL OBJECT ANCESTRY on a pinned directory
descriptor — no `Path.resolve()`, no `os.path.realpath`, no string comparison
takes part (#1247). Correctness hinges on ANCHOR IDENTITY — each of the 7 write
callers passes its OWN pre-resolve base, never a re-derived root.

The per-obligation certification of that predicate lives in the sibling file
test_containment_certification.py; this file is the cross-SITE sweep.

The seed tests (test_staleness.py) cover site 1 (staleness) via a LEAF symlink.
This file WIDENS to the actual F1 vector — a symlinked-PARENT `.claude` — driven
through each caller's REAL production entry point (NOT synthetic no-path calls;
that is the discipline that would have caught both premises falsified this arc),
and asserts BOTH directions at every site: F1 escape → REFUSED, benign → ALLOWED.
A guard tested only for refusal passes by refusing everything (the cardinal
over-block).

Real-production-entry topology (traced against merged code, not assumed):
  * session_init SessionStart  -> site 1 check_pinned_staleness,
                                   site 5 strip_orphan_kernel_block (GLOBAL anchor),
                                   site 6 ensure_project_memory_md,
                                   site 7 migrate_to_managed_structure
  * bootstrap_marker_writer     -> sites 2-4 update_session_info
  * memory_api save/search      -> sites 8-9 sync_to_claude_md / sync_retrieved
"""

import os
from pathlib import Path

import pytest

# hooks/shared on path (mirrors the other hook test files).


# ---------------------------------------------------------------------------
# Layout helpers — the actual F1 vector is a symlinked-PARENT `.claude`.
# ---------------------------------------------------------------------------

def _mount_symlinked_parent_escape(project: Path, outside: Path) -> Path:
    """Ship `project/.claude` as a symlink pointing OUTSIDE the project.

    This is F1: the leaf `is_symlink()` on `.claude/CLAUDE.md` is False (the leaf
    is a regular path component under the symlinked dir), yet `resolve()` escapes
    the project. Returns the (unresolved) escaping target path
    `project/.claude/CLAUDE.md`.
    """
    project.mkdir(parents=True, exist_ok=True)
    outside.mkdir(parents=True, exist_ok=True)
    os.symlink(str(outside), str(project / ".claude"), target_is_directory=True)
    return project / ".claude" / "CLAUDE.md"


def _mount_benign_dot_claude(project: Path) -> Path:
    """A normal in-project `.claude/` directory. Returns project/.claude/CLAUDE.md."""
    (project / ".claude").mkdir(parents=True, exist_ok=True)
    return project / ".claude" / "CLAUDE.md"


def _mode(p: Path) -> int:
    import stat
    return stat.S_IMODE(p.stat().st_mode)


# ---------------------------------------------------------------------------
# Site 6 — ensure_project_memory_md (anchor: CLAUDE_PROJECT_DIR; writes on CREATE)
# ---------------------------------------------------------------------------

class TestSite6EnsureProjectMemoryMd:
    """ensure_project_memory_md CREATES project/.claude/CLAUDE.md when absent,
    anchored on CLAUDE_PROJECT_DIR. Driven through the real function (no path
    param — it resolves internally from the env)."""

    def test_f1_symlinked_parent_escape_refused(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import ensure_project_memory_md

        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        _mount_symlinked_parent_escape(project, outside)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = ensure_project_memory_md()

        # Refused with the opaque skip; NO file created through the escaping link.
        assert result == "Project CLAUDE.md skipped: path precondition not met."
        assert not (outside / "CLAUDE.md").exists(), "write escaped to outside the project"

    def test_benign_dot_claude_allows_creation(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import ensure_project_memory_md

        project = tmp_path / "proj"
        target = _mount_benign_dot_claude(project)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = ensure_project_memory_md()

        # Allowed: the in-project file is created (over-block cert for this site).
        assert result == "Created project CLAUDE.md with memory sections"
        assert target.exists()
        assert _mode(target) == 0o600


# ---------------------------------------------------------------------------
# Site 7 — migrate_to_managed_structure (anchor: CLAUDE_PROJECT_DIR; writes when
# an unmanaged file EXISTS)
# ---------------------------------------------------------------------------

class TestSite7MigrateToManagedStructure:
    """migrate_to_managed_structure rewrites an existing unmanaged project
    CLAUDE.md in place, anchored on CLAUDE_PROJECT_DIR."""

    _LEGACY = "# Project Memory\n\n## Pinned Context\n\n## Working Memory\n"

    def test_f1_symlinked_parent_escape_refused(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import migrate_to_managed_structure

        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        _mount_symlinked_parent_escape(project, outside)
        # An unmanaged file exists THROUGH the escaping link (so the migrate
        # write-path is reached, not the new_default no-op).
        (outside / "CLAUDE.md").write_text(self._LEGACY, encoding="utf-8")
        outside_before = (outside / "CLAUDE.md").read_text(encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = migrate_to_managed_structure()

        assert result == "Migration skipped: project CLAUDE.md path precondition not met."
        # The out-of-project victim is byte-unchanged — no write-through.
        assert (outside / "CLAUDE.md").read_text(encoding="utf-8") == outside_before
        assert "PACT_MANAGED_START" not in (outside / "CLAUDE.md").read_text(encoding="utf-8")

    def test_benign_dot_claude_allows_migration(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import migrate_to_managed_structure

        project = tmp_path / "proj"
        target = _mount_benign_dot_claude(project)
        target.write_text(self._LEGACY, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = migrate_to_managed_structure()

        assert result == "Migrated project CLAUDE.md to managed structure (#404)"
        assert "PACT_MANAGED_START" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Site 1 — check_pinned_staleness: F1 PARENT vector + multi-hop chain.
# (The seed in test_staleness.py covers the LEAF vector; this covers the actual
# F1 symlinked-PARENT `.claude` and a multi-hop escape, via the real wrapper.)
# ---------------------------------------------------------------------------

_STALE = (
    "# Project Memory\n\n## Pinned Context\n\n"
    "### Old Feature (PR #100, merged 2020-01-01)\n- detail\n\n"
)


class TestSite1StalenessParentAndMultiHop:
    def test_f1_symlinked_parent_escape_refused(self, tmp_path):
        from session_init import check_pinned_staleness

        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        project.mkdir()
        outside.mkdir()
        os.symlink(str(outside), str(project / ".claude"), target_is_directory=True)
        managed = project / ".claude" / "CLAUDE.md"  # resolves into `outside`
        managed.write_text(_STALE, encoding="utf-8")
        before = (outside / "CLAUDE.md").read_text(encoding="utf-8")

        from unittest.mock import patch
        with patch("session_init._get_project_claude_md_path", return_value=managed), \
             patch("staleness._get_project_claude_md_path", return_value=managed):
            result = check_pinned_staleness()

        # Lexical base = proj (parent.name=='.claude' -> parent.parent); target
        # resolves into `outside` -> escapes -> opaque refuse, victim untouched.
        assert result == "Pinned staleness skipped: path precondition not met."
        assert (outside / "CLAUDE.md").read_text(encoding="utf-8") == before

    def test_multi_hop_symlink_chain_escape_refused(self, tmp_path):
        from session_init import check_pinned_staleness

        project = tmp_path / "proj"
        project.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        # .claude -> hop1 -> hop2 -> outside  (resolve() follows the whole chain)
        hop2 = tmp_path / "hop2"
        os.symlink(str(outside), str(hop2), target_is_directory=True)
        hop1 = tmp_path / "hop1"
        os.symlink(str(hop2), str(hop1), target_is_directory=True)
        os.symlink(str(hop1), str(project / ".claude"), target_is_directory=True)
        managed = project / ".claude" / "CLAUDE.md"
        managed.write_text(_STALE, encoding="utf-8")
        before = (outside / "CLAUDE.md").read_text(encoding="utf-8")

        from unittest.mock import patch
        with patch("session_init._get_project_claude_md_path", return_value=managed), \
             patch("staleness._get_project_claude_md_path", return_value=managed):
            result = check_pinned_staleness()

        assert result == "Pinned staleness skipped: path precondition not met."
        assert (outside / "CLAUDE.md").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Sites 8-9 — display sync OVER-BLOCK CERT (the load-bearing anchor-identity
# test). External worktree ALLOWS on its own --show-toplevel base; the SAME
# write REFUSES when the anchor is swapped to the main-repo root (--git-common-
# dir). Proves the anchor is the resolver's own base, not a re-derivation.
# ---------------------------------------------------------------------------

def _git(*args, cwd):
    import subprocess
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


class TestSites89DisplayOverBlockAnchorIdentity:
    def _external_worktree(self, tmp_path):
        """A main repo plus an EXTERNAL worktree (NOT nested under main), each a
        real git worktree. Returns (main, worktree)."""
        main = tmp_path / "mainrepo"
        main.mkdir()
        _git("init", cwd=main)
        _git("config", "user.email", "t@e.com", cwd=main)
        _git("config", "user.name", "T", cwd=main)
        (main / "README.md").write_text("x\n", encoding="utf-8")
        _git("add", "README.md", cwd=main)
        _git("commit", "-m", "init", cwd=main)
        worktree = tmp_path / "external-wt"  # sibling of main, NOT under it
        _git("worktree", "add", str(worktree), "-b", "feat", cwd=main)
        (worktree / ".claude").mkdir()
        (worktree / ".claude" / "CLAUDE.md").write_text(
            "# WT\n\n## Working Memory\n<!-- Auto-managed by pact-memory skill. -->\n",
            encoding="utf-8",
        )
        return main, worktree

    def test_external_worktree_allows_on_own_base(self, tmp_path, monkeypatch):
        import scripts.working_memory as wm

        main, worktree = self._external_worktree(tmp_path)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        prev = Path.cwd()
        os.chdir(str(worktree))
        try:
            # Real resolver: --show-toplevel base = the worktree root -> the
            # worktree target is contained -> ALLOWED (the over-block cert).
            result = wm.sync_to_claude_md(
                {"context": "external worktree entry"}, None, "id1"
            )
        finally:
            os.chdir(str(prev))

        # The ALLOW decision is what this cert is about, so name the outcome
        # rather than only asking whether it is truthy.
        assert result.reason == wm.SyncResult.WROTE
        wt_text = (worktree / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert "external worktree entry" in wt_text

    def test_anchor_swap_to_main_root_would_refuse(self, tmp_path, monkeypatch):
        """MUTATION cert: swap the resolver's base from the worktree root to the
        MAIN-repo root. The SAME contained write now escapes the wrong anchor and
        is REFUSED. This is what proves the anchor is load-bearing rather than
        vacuous — anchoring on --git-common-dir (main root) over-blocks an
        external worktree's own legitimate write."""
        import scripts.working_memory as wm

        main, worktree = self._external_worktree(tmp_path)
        target = worktree / ".claude" / "CLAUDE.md"
        before = target.read_text(encoding="utf-8")
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        # Swap the base to the MAIN repo root (the wrong anchor). The worktree
        # target's parent chain never walks up to main's inode -> the ancestry
        # walk reaches the filesystem root without matching -> ContainmentError.
        def _wrong_base(**_):
            return (target, main)
        monkeypatch.setattr(wm, "_resolve_display_claude_md_with_base", _wrong_base)

        result = wm.sync_to_claude_md({"context": "should be refused"}, None, "id2")

        # sync swallows the ContainmentError inside its degradation handler, so
        # the write is skipped and the worktree file is byte-unchanged. Pin the
        # REASON, not just the falsiness: `failed` says the write was attempted
        # and refused, which is the whole point of this mutation arm. A bare
        # falsy result would also be satisfied by `unresolved` or `missing` --
        # a target that was never reached, which would make the arm vacuous.
        assert result.reason == wm.SyncResult.FAILED
        assert target.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Sites 2-4 — update_session_info (anchor: CLAUDE_PROJECT_DIR).
# ---------------------------------------------------------------------------

class TestSites234UpdateSessionInfo:
    def test_f1_symlinked_parent_escape_refused(self, tmp_path, monkeypatch):
        from shared.session_resume import update_session_info

        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        _mount_symlinked_parent_escape(project, outside)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = update_session_info("sid-123", "PACT-team")

        # Refused: nothing written through the escaping link.
        assert not (outside / "CLAUDE.md").exists(), "session write escaped the project"
        assert result is None or "skipped" in result.lower()

    def test_benign_allows_session_write(self, tmp_path, monkeypatch):
        from shared.session_resume import update_session_info

        project = tmp_path / "proj"
        _mount_benign_dot_claude(project)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = update_session_info("sid-123", "PACT-team")

        target = project / ".claude" / "CLAUDE.md"
        assert target.exists()
        assert "SESSION_START" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Site 5 — strip_orphan_kernel_block: GLOBAL config-dir anchor + R4 negative.
# ---------------------------------------------------------------------------

_ORPHAN = "# Global\n<!-- PACT_START: kernel -->\nstale kernel block\n<!-- PACT_END -->\nafter\n"


class TestSite5StripOrphanGlobalAnchor:
    def test_leaf_out_of_config_allowed_and_victim_untouched(self, tmp_path, monkeypatch):
        """LEAF-out at the global-config anchor: ALLOWED, victim untouched.

        WHY THIS SITE HAS NO PARENT-OUT TOPOLOGY, which is why the test looks
        different from every other site's: the anchor here IS the target's
        parent (the config dir holds CLAUDE.md directly), so no intermediate
        component sits between anchor and leaf and there is nothing to point
        outward. A leaf symlink is the only escape-shaped construction
        available. This test was previously named for F1 and asserted a
        refusal; the name described a topology it does not build, and the
        refusal was the over-block #1247 exists to remove.

        Containment is decided on the parent chain and never consults the leaf,
        so this is contained and the write proceeds. It is SAFE because
        os.replace is renameat(2): it binds the final component as a directory
        ENTRY without following it, so the payload lands on the in-config entry
        and the outside victim keeps its bytes.
        """
        from shared.claude_md_manager import strip_orphan_kernel_block

        config = tmp_path / "cfg"
        config.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        victim = outside / "victim.md"
        victim.write_text(_ORPHAN, encoding="utf-8")  # orphan block => write-path reached
        entry = config / "CLAUDE.md"
        os.symlink(str(victim), str(entry))  # global CLAUDE.md leaf points outside cfg
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
        before = victim.read_text(encoding="utf-8")

        strip_orphan_kernel_block()

        # Boundary 1 -- no write-through: the out-of-config victim is byte-intact.
        # This holds via renameat's leaf-swap and would ALSO hold under a refusal,
        # so on its own it cannot distinguish ALLOW from REFUSE.
        assert victim.read_text(encoding="utf-8") == before
        # Boundary 2 -- the write HAPPENED, at the in-config entry: the symlink is
        # replaced by a real, stripped file. This is the assertion that pins the
        # ALLOW, and it is the one that flips if anyone makes the write follow the
        # leaf or re-adds a leaf-symlink ban.
        assert not entry.is_symlink(), (
            "expected the in-config entry to be replaced by a real file"
        )
        assert "PACT_START" not in entry.read_text(encoding="utf-8")

    def test_benign_global_file_allows_strip(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import strip_orphan_kernel_block

        config = tmp_path / "cfg"
        config.mkdir()
        (config / "CLAUDE.md").write_text(_ORPHAN, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

        strip_orphan_kernel_block()

        # Allowed: the orphan block was stripped from the in-config global file.
        after = (config / "CLAUDE.md").read_text(encoding="utf-8")
        assert "PACT_START" not in after and "stale kernel block" not in after

    def test_r4_a_project_root_anchor_would_overblock_the_global_file(self, tmp_path, monkeypatch):
        """R4 NEGATIVE TEST: the strip site anchors on the GLOBAL config dir, NOT
        a project root, and must stay that way. The global ~/.claude/CLAUDE.md is
        NEVER under a project root, so a well-meaning 'unify onto CLAUDE_PROJECT_DIR'
        refactor would over-block EVERY strip invocation. Demonstrated directly at
        the _atomic_write_text level: the same global target is REFUSED on a
        project-root anchor and ALLOWED on the correct global anchor."""
        from shared.claude_md_manager import _atomic_write_text, ContainmentError
        from shared.paths import get_claude_config_dir

        config = tmp_path / "cfg"
        config.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
        target = get_claude_config_dir() / "CLAUDE.md"
        target.write_text("orig\n", encoding="utf-8")

        unrelated_project = tmp_path / "some_project"
        unrelated_project.mkdir()

        # WRONG anchor (a project root) -> the global file escapes -> REFUSED.
        with pytest.raises(ContainmentError):
            _atomic_write_text(target, "new\n", unrelated_project)
        assert target.read_text(encoding="utf-8") == "orig\n"  # untouched

        # CORRECT anchor (the global config dir) -> contained -> ALLOWED.
        _atomic_write_text(target, "new\n", get_claude_config_dir())
        assert target.read_text(encoding="utf-8") == "new\n"


# ---------------------------------------------------------------------------
# R6 — documented residual (security-engineer signed-off, OUT of #1247 scope).
# ---------------------------------------------------------------------------

class TestR6InProjectRedirectResidualDocumented:
    """DOCUMENTED negative test for the R6 residual -- do NOT 'fix' this back.

    Containment ALLOWS a leaf-symlink redirect. The leaf is never consulted at
    all: containment is decided entirely on the PARENT chain, because the parent
    chain is the only part the kernel traverses on the way to the write. The old
    leaf `is_symlink` ban refused ALL symlinks, an over-block on benign ones.

    It is SAFE because `os.replace` is renameat(2), which REPLACES the leaf entry
    rather than writing THROUGH it: it unlinks whatever entry sits at the final
    name and binds the temp file's inode there, never opening or following the
    leaf. So a crafted redirect cannot overwrite its pointed-to file -- it
    clobbers the attacker's own symlink.

    THIS TEST IS A COUPLING TRIPWIRE, and that is its real job. The ALLOW above
    is sound only while the write replaces the leaf ENTRY. Two rewrites would
    silently turn it into a real escape with the predicate untouched: resolving
    the target once and using it downstream (which makes check and act agree by
    making the WRITE follow the leaf), or replacing temp-plus-rename with an
    open/truncate on the target. MEASURED: with either rewrite spliced in, the
    victim-untouched assertion below turns RED. Do NOT 'fix' a failure here by
    making the guard refuse the topology -- that would re-introduce the cardinal
    over-block #1247 removed. A failure here means the WRITE SHAPE changed.

    SUPERSEDED PRESCRIPTION, recorded because it appeared in three places and its
    available reading was harmful: earlier notes sketched the follow-up as
    "open+verify the parent-dir fd, then O_NOFOLLOW + renameat". The pinned form
    has since landed, and it does NOT use O_NOFOLLOW anywhere. O_NOFOLLOW on the
    parent open would refuse any symlinked final component of the parent path,
    including a benign in-project `.claude` -> `<project>/config/claude` that
    both the old and the current code allow -- a NEW over-block on an axis that
    is not containment. It would also add nothing: the ancestry test runs ON the
    opened descriptor, so there is no check-then-open gap for it to close.

    The fd-relative TOCTOU that was signed off as BELOW the good-faith threat
    floor and deferred is now closed as a side effect, since the check and the
    write share one pinned parent descriptor. That closure was a bonus of the
    pinned form, never its justification.

    THE LOCK KEYING ALSO DEPENDS ON THIS TEST, AND THAT IS NOT OBVIOUS FROM
    THE LOCK CODE.
    `file_lock` keys its sidecar on `target_file.parent.resolve()` plus the
    LITERAL leaf name -- it deliberately does NOT follow the leaf. That is
    only correct while the write REPLACES the leaf entry rather than writing
    THROUGH it, which is the exact property this test pins.

    Two names for one inode therefore no longer share a lock, and that was
    retired ON PURPOSE: a rename-based write destroys the alias relationship
    on the first commit, so the property protected a relationship that no
    longer exists.

    If the write ever became an open/truncate on the target, the two names
    would share a destination again, the lost-update silhouette would become
    real, and the lock keying would become WRONG -- SILENTLY, because the lock
    would still look correct from the lock code. There is no path from
    `file_lock` back to this test, so anyone weakening or deleting this
    tripwire is also invalidating the lock keying and has no way to learn it
    where they are working. That is why the dependency is stated here rather
    than only in the design document.
    """

    def test_in_project_redirect_allowed_and_os_replace_does_not_write_through(self, tmp_path):
        from shared.claude_md_manager import _atomic_write_text

        project = tmp_path / "proj"
        project.mkdir()
        sibling = project / "sib.md"
        sibling.write_text("SIBLING CONTENT\n", encoding="utf-8")
        target = project / "CLAUDE.md"
        os.symlink(str(sibling), str(target))  # in-project leaf redirect

        # Contained (both in project) -> ALLOWED; no exception.
        _atomic_write_text(target, "NEW MANAGED CONTENT\n", project)

        # os.replace replaced the LINK, did not write THROUGH it:
        assert sibling.read_text(encoding="utf-8") == "SIBLING CONTENT\n"  # victim untouched
        assert not target.is_symlink()                                     # link -> real file
        assert target.read_text(encoding="utf-8") == "NEW MANAGED CONTENT\n"


# ---------------------------------------------------------------------------
# Predicate discriminator — sibling-prefix mounted layout. `/abc` is NOT within
# `/ab`, and the refusal is now STRUCTURAL rather than a property of a chosen
# string primitive.
# ---------------------------------------------------------------------------

class TestSiblingPrefixMountedLayout:
    def test_sibling_prefix_refused_structurally(self, tmp_path):
        """RATIONALE UPDATED FOR THE CORRECTED PREDICATE (#1247).

        This case used to be explained by the choice of `os.path.commonpath`
        over `str.startswith`. That primitive has left the write path
        entirely, so the old rationale named a mechanism that no longer
        exists. The refusal now has nothing to do with text: `/abc` simply
        never walks up to `/ab`'s inode. The string-prefix trap the old
        wording guarded against cannot even be expressed in this predicate.
        """
        from shared.claude_md_manager import _atomic_write_text, ContainmentError

        anchor = tmp_path / "ab"
        anchor.mkdir()
        sibling = tmp_path / "abc"       # shares the STRING prefix "ab", not a path child
        sibling.mkdir()
        target = sibling / "CLAUDE.md"
        target.write_text("orig\n", encoding="utf-8")

        # The ancestry walk from /abc reaches the filesystem root without ever
        # matching /ab's (st_dev, st_ino) -> REFUSE.
        with pytest.raises(ContainmentError):
            _atomic_write_text(target, "new\n", anchor)
        assert target.read_text(encoding="utf-8") == "orig\n"

    def test_benign_prefix_symlink_on_base_allowed(self, tmp_path):
        """A benign symlinked alias of the anchor (the /tmp->/private/tmp
        class) -> contained -> ALLOWED.

        `os.stat` on the alias FOLLOWS it, so the anchor key is the real
        directory's (st_dev, st_ino) and the walk matches it. The alias and the
        real directory are one kernel object, so there is no spelling for the
        predicate to disagree about."""
        from shared.claude_md_manager import _atomic_write_text

        real_root = tmp_path / "real"
        (real_root / ".claude").mkdir(parents=True)
        target = real_root / ".claude" / "CLAUDE.md"
        target.write_text("orig\n", encoding="utf-8")
        # Anchor passed via a symlinked alias of the SAME dir.
        alias_root = tmp_path / "alias"
        os.symlink(str(real_root), str(alias_root), target_is_directory=True)

        # target under real_root; anchor is the alias (resolves to real_root) ->
        # both resolve equal -> contained -> ALLOWED.
        _atomic_write_text(target, "new\n", alias_root)
        assert target.read_text(encoding="utf-8") == "new\n"
