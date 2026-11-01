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
    _display_doc,
    _hide_git,
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


def test_the_archive_base_refusal_fires_when_only_the_base_is_unknown(
    tmp_path, monkeypatch
):
    """The base-level refusal is defence-in-depth BEHIND the anchor-level one.

    Nothing in the population reached it: replacing its raise with a marker
    left the suite green. This arm faults ONLY the second classification --
    the anchor answers with a real root -- so the base refusal is the one
    that fires, and its own message (not the anchor's) is what is asserted.
    """
    import archive_pin
    import shared.project_scope as project_scope

    main = tmp_path / "base-unknown-main"
    main.mkdir()
    _git("init", "-q", str(main))
    _seed(main)
    main_md = main / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(_managed_doc(_stale_capable_pin()), encoding="utf-8")

    real = project_scope.project_memory_root_or_none
    classified = []

    def anchor_known_base_unknown(candidate):
        classified.append(candidate)
        if len(classified) == 1:
            return real(candidate)   # the ANCHOR classification succeeds
        return None                  # the BASE classification cannot answer

    # The archive imports the classifier INSIDE resolve_claude_md, so the
    # source-module attribute is what the import re-reads; patching
    # `archive_pin.project_memory_root_or_none` would be inert.
    monkeypatch.setattr(
        project_scope, "project_memory_root_or_none", anchor_known_base_unknown
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(main))
    monkeypatch.chdir(main)

    with pytest.raises(archive_pin._Unevaluable) as excinfo:
        archive_pin.resolve_claude_md()

    assert classified == [main, main], (
        f"expected the anchor then the base classification, saw {classified}"
    )
    reason = excinfo.value.reason
    assert "could not classify" in reason, reason
    assert "cannot be verified" in reason, reason
    assert "cannot be identified" not in reason, (
        f"the ANCHOR refusal fired, not the base one: {reason}"
    )


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


def test_a_moved_worktree_is_unknown_and_the_archive_refuses(
    tmp_path, monkeypatch
):
    """MOVED worktree (`mv` aside, not `git worktree move`).

    Every rev-parse still answers from the moved directory, but
    `git worktree list` names the OLD path, so the listing verification
    fails. The separate-git-dir fix (unverified non-bare linked worktree =
    UNKNOWN) closes this too -- MEASURED, not assumed: the same predicate
    branch decides both. RED before that fix, green with it.
    """
    import archive_pin
    from shared.project_scope import project_memory_root_or_none

    main = tmp_path / "moved-main"
    main.mkdir()
    _git("init", "-q", str(main))
    _seed(main)
    wt = tmp_path / "moved-wt"
    assert _git("worktree", "add", "-q", str(wt), "-b", "wt",
                cwd=main).returncode == 0
    moved = tmp_path / "moved-wt-new"
    os.rename(wt, moved)
    wt_md = moved / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(_managed_doc(_stale_capable_pin()), encoding="utf-8")

    assert project_memory_root_or_none(moved) is None, (
        "a moved worktree still verified as its own root"
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(moved))
    monkeypatch.chdir(moved)
    with pytest.raises(archive_pin._Unevaluable) as excinfo:
        archive_pin.resolve_claude_md()
    assert "project memory root unknown" in excinfo.value.reason


def test_the_no_argument_reader_answers_nothing_on_a_worktree_cwd(
    tmp_path, monkeypatch
):
    """F1: with the variable unset and cwd inside a linked worktree whose
    MAIN holds no document, the no-arg helper used the raw git/cwd
    fall-through and returned the worktree copy. Three production consumers
    take that route: check_pin_caps, match_project_claude_md, track_files."""
    import check_pin_caps
    import staleness
    from shared.claude_md_manager import match_project_claude_md

    main = tmp_path / "f1-main"
    main.mkdir()
    _git("init", "-q", str(main))
    _seed(main)
    wt = tmp_path / "f1-wt"
    assert _git("worktree", "add", "-q", str(wt), "-b", "f1wt",
                cwd=main).returncode == 0
    wt_md = wt / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(_managed_doc(_stale_capable_pin()), encoding="utf-8")

    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(wt)

    assert staleness.resolve_session_claude_md_path() is None
    assert match_project_claude_md(str(wt_md)) is None, (
        "the pin gate matched the worktree copy the no-arg reader returned"
    )
    pins, reason = check_pin_caps._resolve_pins()
    assert pins == [] and reason == "claude.md not found"


def test_the_display_resolver_rejects_a_document_outside_the_checkout(
    tmp_path, monkeypatch
):
    """F3: a --separate-git-dir checkout with a canonical document at the
    common-dir parent. Branch 3 probed that parent unconditionally and
    returned a document OUTSIDE the checkout, which the scope guard admitted
    via `same_repository` rule 2. The parent is now probed only when it is
    THIS repository's checkout root."""
    import scripts.working_memory as wm

    base = tmp_path / "f3"
    base.mkdir()
    outside = base / "CLAUDE.md"
    outside.write_text(_display_doc(), encoding="utf-8")
    sep = base / "sep.git"
    work = base / "work"
    work.mkdir()
    assert _git("init", "-q", f"--separate-git-dir={sep}", str(work)).returncode == 0
    _seed(work)

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(work))
    monkeypatch.chdir(work)
    monkeypatch.setattr(wm, "get_project_dir_from_session_record", lambda: None)
    monkeypatch.setattr(wm, "env_record_project_dir_disagreement", lambda: None)

    found, found_base = wm._resolve_display_claude_md_with_base()
    assert (found, found_base) == (None, None), (
        f"the resolver took a document outside the checkout: {found}"
    )
    result = wm.sync_to_claude_md(
        {"context": "OUTSIDE-TOKEN", "goal": "g"}, memory_id="0" * 32
    )
    assert result.reason == wm.SyncResult.UNRESOLVED, result.reason
    assert "OUTSIDE-TOKEN" not in outside.read_text(encoding="utf-8")


def test_an_env_absent_unknown_root_reports_root_unknown(tmp_path, monkeypatch):
    """F4: with the variable UNSET, an unknown cwd root was reported as
    UNRESOLVED ("no file found") because `_declared_display_root_unknown`
    only looked at the declared env base. It now anchors on the cwd -- the
    resolver's last anchor -- so "cannot tell where the file is" is reported
    as ROOT_UNKNOWN."""
    import scripts.working_memory as wm

    plain = tmp_path / "f4-plain"
    plain.mkdir()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(plain)
    _hide_git(monkeypatch)

    result = wm.sync_retrieved_to_claude_md(
        [{"id": "0" * 32, "context": "external entry"}], query="q"
    )

    assert result.reason == wm.SyncResult.ROOT_UNKNOWN, result.reason


# --- Copilot findings at ca9660a3 -------------------------------------------

def _forged_fixture(tmp_path):
    """A victim repository with a document, and a forged `.git` naming its
    worktree admin dir."""
    victim = tmp_path / "victim"
    victim.mkdir()
    _git("init", "-q", str(victim))
    _seed(victim)
    victim_wt = tmp_path / "victim-wt"
    assert _git("worktree", "add", "-q", str(victim_wt), "-b", "vwt",
                cwd=victim).returncode == 0
    victim_md = victim / ".claude" / "CLAUDE.md"
    victim_md.parent.mkdir()
    victim_md.write_text(_display_doc(), encoding="utf-8")
    forged = tmp_path / "forged"
    forged.mkdir()
    (forged / ".git").write_text(
        f"gitdir: {victim}/.git/worktrees/victim-wt\n", encoding="utf-8"
    )
    return forged, victim_md


@pytest.mark.parametrize("route", ["declared", "cwd-only", "session-record"])
def test_both_sync_writers_stop_on_an_unknown_anchor(
    tmp_path, monkeypatch, route
):
    """Copilot HIGH: the forged anchor was UNKNOWN, yet the fallback chain
    reached the repository the gitfile NAMES and both sync writers WROTE its
    document. Resolution now stops when the SESSION ANCHOR is unknown, on
    all three anchor routes."""
    import scripts.working_memory as wm

    forged, victim_md = _forged_fixture(tmp_path)
    before = victim_md.read_bytes()

    if route == "declared":
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
        monkeypatch.chdir(forged)
        monkeypatch.setattr(wm, "get_project_dir_from_session_record", lambda: None)
    elif route == "cwd-only":
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(forged)
        monkeypatch.setattr(wm, "get_project_dir_from_session_record", lambda: None)
    else:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(elsewhere)
        monkeypatch.setattr(
            wm, "get_project_dir_from_session_record", lambda: str(forged)
        )
    monkeypatch.setattr(wm, "env_record_project_dir_disagreement", lambda: None)

    wrote = wm.sync_to_claude_md(
        {"context": "FORGED-TOKEN-1", "goal": "g"}, memory_id="0" * 32
    )
    retrieved = wm.sync_retrieved_to_claude_md(
        [{"id": "0" * 32, "context": "FORGED-TOKEN-2"}], query="q"
    )

    assert wrote.reason == wm.SyncResult.ROOT_UNKNOWN, wrote.reason
    assert retrieved.reason == wm.SyncResult.ROOT_UNKNOWN, retrieved.reason
    assert victim_md.read_bytes() == before, (
        f"the {route} anchor wrote another repository's document"
    )


def test_a_whitespace_named_checkout_still_syncs(tmp_path, monkeypatch):
    """Copilot OVER-BLOCK: the checkout-root verification stripped the path,
    so a checkout named `repo ` was rejected and a declared session in an
    empty subdirectory lost the ambient sync it previously had. Only git's
    terminating newline may be dropped."""
    import scripts.working_memory as wm

    space = tmp_path / "repo "
    space.mkdir()
    _git("init", "-q", str(space))
    _seed(space)
    sub = space / "sub"
    sub.mkdir()
    space_md = space / ".claude" / "CLAUDE.md"
    space_md.parent.mkdir()
    space_md.write_text(_display_doc(), encoding="utf-8")

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(sub))
    monkeypatch.chdir(sub)
    monkeypatch.setattr(wm, "get_project_dir_from_session_record", lambda: None)
    monkeypatch.setattr(wm, "env_record_project_dir_disagreement", lambda: None)

    result = wm.sync_to_claude_md(
        {"context": "SPACE-TOKEN", "goal": "g"}, memory_id="0" * 32
    )

    assert result.reason == wm.SyncResult.WROTE, result.reason
    assert "SPACE-TOKEN" in space_md.read_text(encoding="utf-8")
