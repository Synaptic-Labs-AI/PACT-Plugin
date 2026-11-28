"""A `--separate-git-dir` worktree is its own project root.

Ordinary git, no attacker: `git init --separate-git-dir=<sep> work` then
`git worktree add <wt>`. In the worktree, `--git-dir` differs from
`--git-common-dir`, so the resolver knows it is a linked worktree, but git
names no main checkout for it: it lists the main worktree at the git dir
itself, and no folder holds the git dir as its `.git`. The worktree is then
its own project root, and every writer acts on its own CLAUDE.md, at
`<wt>/.claude`.

A worktree whose main checkout exists but cannot be verified (a moved
worktree) and a forged gitfile stay UNKNOWN, and a bare repository's worktree
stays its own root. These arms drive every writer the security lane
measured, plus the archive, plus a forged gitfile and the bare control.
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


def test_the_resolver_reports_the_worktree_as_its_own_root(sep_worktree):
    from helpers import known_root, root_or_input

    _work, wt, _md = sep_worktree
    assert known_root(wt) == wt.resolve(), (
        "a listed worktree whose main checkout git cannot name is its own root"
    )
    assert root_or_input(wt) == wt.resolve()


def test_an_unanswered_git_dir_question_leaves_the_worktree_unknown(
    sep_worktree, monkeypatch
):
    """The worktree is its own root only when git says the common dir's parent
    is not inside a git dir; when that question gets no answer (git timed out
    or is missing), the root is unknown, not the worktree. Control: the same
    worktree with the question answered is its own root."""
    from helpers import known_root
    from shared import project_scope

    _work, wt, _md = sep_worktree
    assert known_root(wt) == wt.resolve()
    real = project_scope.run_git
    asked = []

    def unanswered(args, *rest, **kwargs):
        if "--is-inside-git-dir" in args:
            asked.append(args)
            return None
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(project_scope, "run_git", unanswered)
    assert known_root(wt) is None, "an unanswered git-dir question made the worktree its own root"
    assert asked, "the question the arm withholds was never asked"


def test_the_session_start_writers_write_the_worktree_copy(sep_worktree, monkeypatch):
    _work, wt, wt_md = sep_worktree
    before = wt_md.read_bytes()

    _context, system_message = _run(monkeypatch, wt)

    assert "Project CLAUDE.md skipped" not in system_message, system_message[:300]
    assert wt_md.read_bytes() != before, (
        "the session-start writers did not write the worktree's own copy"
    )


def test_the_pin_marker_writer_writes(sep_worktree, monkeypatch):
    from test_pin_marker_writer import build_claude_md

    import pin_marker_writer

    _work, wt, _md = sep_worktree
    original = build_claude_md()
    wt_md = wt / ".claude" / "CLAUDE.md"
    wt_md.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    monkeypatch.chdir(wt)

    outcome = pin_marker_writer._plan_and_write()

    assert outcome == "written", (
        f"the pin writer did not write the worktree's own copy: {outcome}"
    )


def test_the_realign_team_line_writes_the_worktree_copy(sep_worktree, monkeypatch, capsys):
    from shared.realign_team import _write_team_line

    work, wt, wt_md = sep_worktree
    original = wt_md.read_text(encoding="utf-8")
    session_dir = _session_with_session_start(work)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    monkeypatch.chdir(wt)

    _write_team_line("sid", "session-new", str(session_dir), "/plugin")

    err = capsys.readouterr().err
    assert "project CLAUDE.md skipped" not in err, err
    assert wt_md.read_text(encoding="utf-8") != original, (
        "the realign line was not written to the worktree's own copy"
    )


def test_the_sync_writers_write_the_worktree_copy(sep_worktree, monkeypatch):
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

    assert wrote.reason == wm.SyncResult.WROTE, wrote.reason
    assert retrieved.reason == wm.SyncResult.WROTE, retrieved.reason
    assert wt_md.read_text(encoding="utf-8") != original


def test_the_archive_resolves_the_worktree_copy(sep_worktree, monkeypatch):
    import archive_pin

    _work, wt, wt_md = sep_worktree
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    monkeypatch.chdir(wt)

    path, _base = archive_pin.resolve_claude_md()

    assert os.path.realpath(path) == os.path.realpath(wt_md), path


def test_a_bare_repository_worktree_stays_a_known_root(tmp_path, monkeypatch):
    """A bare repository's worktree IS a genuine checkout: it keeps today's
    behaviour (its own root, writers act there), not the unknown the non-bare
    shape gets."""
    from helpers import known_root
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

    assert known_root(wt) == wt
    # The separate-git-dir rule (the worktree's top level is its root) is for
    # a non-bare repository only: a start below a bare repository's worktree
    # keeps the root it is given, as a start below a main checkout does.
    sub = wt / "sub"
    sub.mkdir()
    assert known_root(sub) == sub

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
    from helpers import known_root

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

    assert known_root(forged) is None
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)
    with pytest.raises(archive_pin._Unevaluable) as excinfo:
        archive_pin.resolve_claude_md()

    reason = excinfo.value.reason
    assert "project memory root unknown" in reason
    # THE ANCHOR REFUSAL, NOT ITS BASE SIBLING. Both begin with the same
    # prefix, so a prefix-only assertion passes when the anchor refusal is
    # removed and the fall-through reaches the base refusal instead.
    assert (
        "exists but could not be verified as a checkout of a repository"
        in reason
    ), reason
    # NOT the git-failure text: on these three shapes git RAN and answered.
    assert "git could not answer" not in reason, reason
    assert "cannot be identified" in reason, reason
    assert "could not classify" not in reason, reason
    assert victim_md.read_bytes() == victim_before, (
        "the forged gitfile redirected the archive to another project"
    )


def test_a_moved_worktree_is_unknown_and_the_archive_refuses(
    tmp_path, monkeypatch
):
    """MOVED worktree (`mv` aside, not `git worktree move`).

    Every rev-parse still answers from the moved directory, but
    `git worktree list` names the OLD path, so the listing verification
    fails, and the worktree stays UNKNOWN rather than becoming its own root.
    """
    import archive_pin
    from helpers import known_root

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

    assert known_root(moved) is None, (
        "a moved worktree still verified as its own root"
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(moved))
    monkeypatch.chdir(moved)
    with pytest.raises(archive_pin._Unevaluable) as excinfo:
        archive_pin.resolve_claude_md()
    reason = excinfo.value.reason
    assert "project memory root unknown" in reason
    # THE ANCHOR REFUSAL, NOT ITS BASE SIBLING. Both begin with the same
    # prefix, so a prefix-only assertion passes when the anchor refusal is
    # removed and the fall-through reaches the base refusal instead.
    assert (
        "exists but could not be verified as a checkout of a repository"
        in reason
    ), reason
    # NOT the git-failure text: on these three shapes git RAN and answered.
    assert "git could not answer" not in reason, reason
    assert "cannot be identified" in reason, reason
    assert "could not classify" not in reason, reason


@pytest.mark.parametrize("core_worktree", ["set", "unset"])
def test_a_submodules_linked_worktree_is_unknown_and_the_archive_refuses(
    tmp_path, monkeypatch, core_worktree
):
    """A linked worktree of a submodule. Its common dir is the submodule's
    git dir under `.git/modules/`, so no folder holds it as its `.git` and
    git lists the main worktree at the git dir, as for a separate-git-dir
    repository. With `core.worktree` set, git, asked from that git dir,
    names the submodule's own checkout as its top level; with it unset
    (`git config --unset core.worktree`), git refuses there, as it does for
    a separate-git-dir store, but places the git dir inside the
    superproject's. Either way the worktree is not its own root: it stays
    UNKNOWN, and its CLAUDE.md is not the project memory's."""
    import archive_pin
    from helpers import known_root

    lib = tmp_path / "lib"
    lib.mkdir()
    _git("init", "-q", str(lib))
    _seed(lib)
    sup = tmp_path / "super"
    sup.mkdir()
    _git("init", "-q", str(sup))
    _seed(sup)
    added = _git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "sub", cwd=sup)
    if added.returncode != 0:
        # A git that refuses the local clone `submodule add` makes cannot
        # build the layout; that is the environment, not the behaviour.
        pytest.skip(f"git could not add the submodule: {added.stderr.strip()}")
    wt = tmp_path / "sub-wt"
    added = _git("worktree", "add", "-q", str(wt), "-b", "subwt", cwd=sup / "sub")
    assert added.returncode == 0, added.stderr
    if core_worktree == "unset":
        unset = _git("config", "--unset", "core.worktree", cwd=sup / ".git" / "modules" / "sub")
        assert unset.returncode == 0, unset.stderr
    # Control: the submodule's own checkout is still a known root.
    assert known_root(sup / "sub") == (sup / "sub").resolve()
    wt_md = wt / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(_managed_doc(_stale_capable_pin()), encoding="utf-8")

    assert known_root(wt) is None, "a submodule's linked worktree became its own root"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
    monkeypatch.chdir(wt)
    with pytest.raises(archive_pin._Unevaluable) as excinfo:
        archive_pin.resolve_claude_md()
    reason = excinfo.value.reason
    assert "project memory root unknown" in reason, reason
    assert (
        "exists but could not be verified as a checkout of a repository"
        in reason
    ), reason
    # git answered on this layout, so the refusal is the anchor's, not the
    # git-failure text
    assert "git could not answer" not in reason, reason
    # the worktree's own document is left as it was
    assert wt_md.read_text(encoding="utf-8") == _managed_doc(_stale_capable_pin())


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
    UNRESOLVED ("no file found") because `_display_root_unknown_reason`
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
        # The session anchor reads the record through project_scope, the one
        # precedence every site shares; the escape guard reads it through
        # working_memory's own import.
        import shared.project_scope as project_scope

        monkeypatch.setattr(
            project_scope, "recorded_project_dir", lambda: str(forged)
        )
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
