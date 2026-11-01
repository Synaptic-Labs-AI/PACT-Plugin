"""A `--separate-git-dir` worktree is a linked worktree, not its own root.

Ordinary git, no attacker: `git init --separate-git-dir=<sep> work` then
`git worktree add <wt>`. In the worktree, `--git-dir` differs from
`--git-common-dir`, so the resolver knows it is a linked worktree -- but the
candidate main root (common-dir's parent) is not a checkout, so verification
fails and the fail-safe returned the INPUT as a KNOWN root. Every writer then
targeted the worktree copy.

The fix is in the shared resolver, not at each site: an UNVERIFIED linked
worktree is UNKNOWN unless the repository is BARE (a bare repo's worktree is
a genuine checkout and legitimately owns its project memory). These arms drive
every writer the security lane measured, plus the archive, plus a forged
gitfile and the bare control.

Each arm is RED on the previous head and green after the resolver fix.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from tests.test_worktree_git_failure_skip import (
    _align_memory_scope,
    _managed_doc,
    _run,
    _session_with_session_start,
    _stale_capable_pin,
)


def _git(*args, cwd=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=None if cwd is None else str(cwd),
        env=env, capture_output=True, text=True, timeout=30,
    )


def _seed(repo):
    (repo / "README").write_text("seed\n")
    assert _git("add", "README", cwd=repo).returncode == 0
    assert _git("commit", "-qm", "seed", cwd=repo).returncode == 0


@pytest.fixture
def sep_worktree(tmp_path):
    """A non-bare repository whose git dir is elsewhere, plus a worktree.

    The worktree carries a managed CLAUDE.md; MAIN holds none -- the shape in
    which every writer used to target the worktree copy.
    """
    (tmp_path / "gitdirs").mkdir()
    # The git dir sits one level down so the display resolver's branch-3
    # common-dir parent is `gitdirs`, not tmp_path -- the autouse config-root
    # fixture seeds tmp_path/CLAUDE.md, and that seed is not this test's subject.
    sep = tmp_path / "gitdirs" / "sep.git"
    work = tmp_path / "work"
    work.mkdir()
    assert _git("init", "-q", f"--separate-git-dir={sep}", str(work)).returncode == 0
    _seed(work)
    wt = tmp_path / "wt"
    added = _git("worktree", "add", "-q", str(wt), "-b", "wt", cwd=work)
    assert added.returncode == 0, added.stderr
    wt_md = wt / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(_managed_doc(_stale_capable_pin()), encoding="utf-8")
    return work, wt, wt_md


def test_the_resolver_reports_unknown_while_the_reader_fails_open(sep_worktree):
    from shared.project_scope import project_memory_root, project_memory_root_or_none

    _work, wt, _md = sep_worktree
    assert project_memory_root_or_none(wt) is None, (
        "an unverified non-bare linked worktree must be UNKNOWN to writers"
    )
    assert project_memory_root(wt) == wt, (
        "the reader fail-open answer must stay the input"
    )


def test_the_session_start_writers_skip(sep_worktree, monkeypatch):
    _work, wt, wt_md = sep_worktree
    before = wt_md.read_bytes()

    _context, system_message = _run(monkeypatch, wt)

    assert "Project CLAUDE.md skipped" in system_message, system_message[:300]
    assert wt_md.read_bytes() == before, (
        "the session-start writers targeted the worktree copy"
    )


def test_the_pin_marker_writer_skips(sep_worktree, monkeypatch):
    from test_pin_marker_writer import build_claude_md

    import pin_marker_writer

    _work, wt, _md = sep_worktree
    original = build_claude_md()
    wt_md = wt / ".claude" / "CLAUDE.md"
    wt_md.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    monkeypatch.chdir(wt)

    outcome = pin_marker_writer._plan_and_write()

    assert outcome == "noop_memory_root_unknown", (
        f"the pin writer targeted the worktree copy: {outcome}"
    )
    assert wt_md.read_text(encoding="utf-8") == original


def test_the_realign_team_line_writes_nothing(sep_worktree, monkeypatch, capsys):
    from shared.realign_team import _write_team_line

    work, wt, wt_md = sep_worktree
    original = wt_md.read_text(encoding="utf-8")
    session_dir = _session_with_session_start(work)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    monkeypatch.chdir(wt)

    _write_team_line("sid", "session-new", str(session_dir), "/plugin")

    err = capsys.readouterr().err
    assert "project CLAUDE.md skipped" in err, err
    assert wt_md.read_text(encoding="utf-8") == original, (
        "the realign line was written to the worktree copy"
    )


def test_the_sync_writers_name_an_unknown_root(sep_worktree, monkeypatch):
    import scripts.working_memory as wm

    work, wt, wt_md = sep_worktree
    original = wt_md.read_text(encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    monkeypatch.chdir(wt)
    _align_memory_scope(monkeypatch, wm, wt)

    wrote = wm.sync_to_claude_md(
        {"context": "external entry", "goal": "g"}, memory_id="0" * 32
    )
    retrieved = wm.sync_retrieved_to_claude_md(
        [{"id": "0" * 32, "context": "external entry"}], query="q"
    )

    assert wrote.reason == wm.SyncResult.ROOT_UNKNOWN, wrote.reason
    assert retrieved.reason == wm.SyncResult.ROOT_UNKNOWN, retrieved.reason
    assert wt_md.read_text(encoding="utf-8") == original


def test_the_archive_refuses(sep_worktree, monkeypatch):
    import archive_pin

    _work, wt, wt_md = sep_worktree
    original = wt_md.read_text(encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    monkeypatch.chdir(wt)

    with pytest.raises(archive_pin._Unevaluable) as excinfo:
        archive_pin.resolve_claude_md()

    assert "project memory root unknown" in excinfo.value.reason
    assert wt_md.read_text(encoding="utf-8") == original


def test_a_bare_repository_worktree_stays_a_known_root(tmp_path, monkeypatch):
    """A bare repository's worktree IS a genuine checkout: it keeps today's
    behaviour (its own root, writers act there), not the unknown the non-bare
    shape gets."""
    from shared.project_scope import project_memory_root_or_none
    from test_pin_marker_writer import build_claude_md

    import pin_marker_writer

    plain = tmp_path / "plain"
    plain.mkdir()
    _git("init", "-q", str(plain))
    _seed(plain)
    bare = tmp_path / "bare.git"
    assert _git("clone", "-q", "--bare", str(plain), str(bare)).returncode == 0
    wt = tmp_path / "bare-wt"
    added = _git("worktree", "add", "-q", str(wt), cwd=bare)
    assert added.returncode == 0, added.stderr

    assert project_memory_root_or_none(wt) == wt

    # Behavioural half of the statement: KNOWN means writers still act here.
    wt_md = wt / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(build_claude_md(), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    monkeypatch.chdir(wt)
    assert pin_marker_writer._plan_and_write() == "written", (
        "a bare repository's worktree is a checkout and keeps its writers"
    )


def test_a_forged_gitfile_is_unknown_and_the_archive_refuses(
    sep_worktree, tmp_path, monkeypatch
):
    """MEDIUM: a forged `.git` naming another repository's worktree admin dir
    used to let the archive's fall-through resolve that project's document.
    The resolver now reports UNKNOWN, so the archive refuses."""
    import archive_pin
    from shared.project_scope import project_memory_root_or_none

    work, _wt, _md = sep_worktree
    victim = tmp_path / "victim"
    victim.mkdir()
    _git("init", "-q", str(victim))
    _seed(victim)
    victim_wt = tmp_path / "victim-wt"
    assert _git("worktree", "add", "-q", str(victim_wt), "-b", "vwt",
                cwd=victim).returncode == 0
    victim_md = victim / ".claude" / "CLAUDE.md"
    victim_md.parent.mkdir()
    victim_md.write_text(_managed_doc(_stale_capable_pin()), encoding="utf-8")
    victim_before = victim_md.read_bytes()

    forged = tmp_path / "forged"
    forged.mkdir()
    (forged / ".git").write_text(
        f"gitdir: {victim}/.git/worktrees/victim-wt\n", encoding="utf-8"
    )

    assert project_memory_root_or_none(forged) is None
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)
    with pytest.raises(archive_pin._Unevaluable) as excinfo:
        archive_pin.resolve_claude_md()

    assert "project memory root unknown" in excinfo.value.reason
    assert victim_md.read_bytes() == victim_before, (
        "the forged gitfile redirected the archive to another project"
    )
