"""
Location: pact-plugin/hooks/shared/project_scope.py
Summary: Predicates answering "are these two directories the same project?",
         and the reader for the session record that answers it for a declared
         worktree that no longer exists.
Used by: scripts/archive_pin.py (pin archival resolution) and
         skills/pact-memory/scripts/working_memory.py (working-memory
         projection), both of which resolve a CLAUDE.md and must tell a
         legitimate fall-through from a wrong-project one. Both call
         `stays_in_declared_project` with
         `get_worktree_identity_from_session_record()`; `same_repository` is
         one of its rules. hooks/session_init.py writes the record
         (`WORKTREE_IDENTITY_FILE`) through `_record_worktree_identity`;
         skills/pact-memory/scripts/pact_session.py
         re-exports the reader and shares `_session_record_on_disk`.

WHY THIS IS NOT IN git_helpers.py. That module is a narrow subprocess
wrapper whose docstring scopes it to "try/except + subprocess boilerplate
only", with callers owning the decision. `same_repository` IS a decision, so
it lives here and COMPOSES `run_git` rather than widening that contract.

WHY NOT IN THE MODULE THAT FIRST NEEDED IT. This began as a private helper in
archive_pin.py, where the defect was found. A function's home follows its
SUBJECT, not its discovery site — and the subject here is project identity,
which is neither pin archival nor memory projection. Importing archive_pin to
reach it is worse than it looks: that module loads two hook modules at import
time and registers them in sys.modules under bare top-level names, so a
consumer would acquire a global namespace mutation to borrow one predicate.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Set

from . import state_file
from .git_helpers import run_git
from .pact_context import _UNSAFE_SLUG_CHARS_RE, _build_session_path, project_slug
from .paths import get_claude_config_dir

# Git LOCATES the repository from these instead of discovering it from `-C` or
# the working directory. Inherited -- a git hook runs with GIT_DIR exported for
# its own repository -- they make every directory report that one repository,
# so an unrelated directory compares equal to it.
_GIT_LOCATION_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")

# Written by session_init into a session's own folder when the session starts
# inside a linked worktree; read back by the working-memory write guard.
WORKTREE_IDENTITY_FILE = "worktree-identity.json"


def git_env_without_location() -> dict:
    """Return this process's environment without the git location variables.

    Every git call that decides which project a directory belongs to runs with
    it -- this module's guard and the CLAUDE.md resolvers alike -- so the
    guard and the resolvers judge the same repository.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if key not in _GIT_LOCATION_VARIABLES
    }


def _git_output(directory: Path, *args: str) -> Optional[str]:
    """Return the stripped stdout of `git -C <directory> <args>`, or None.

    None on a git error, a timeout, a non-repo directory or an OSError, so
    every rule built on it fails toward refusal.
    """
    # `run_git` absorbs TimeoutExpired and FileNotFoundError only. The original
    # predicate caught OSError entire, and that breadth is load-bearing here:
    # a PermissionError reaching a caller as an exception instead of a refusal
    # would turn a fail-safe into a crash on a write path.
    try:
        result = run_git(
            ["-C", str(directory), *args], timeout=5, env=git_env_without_location()
        )
    except OSError:
        return None
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def _rev_parse_path(directory: Path, flag: str) -> Optional[Path]:
    """Return `git -C <directory> rev-parse <flag>` as a resolved path, or None."""
    output = _git_output(directory, "rev-parse", flag)
    if output is None:
        return None
    path = Path(output)
    if not path.is_absolute():
        path = Path(directory) / path
    try:
        return path.resolve()
    except OSError:
        return None


def same_repository(env_dir: Path, base: Path) -> bool:
    """True when `base` is the main repo of the git checkout at `env_dir`.

    🔴 THIS IS NOT SYMMETRIC AND THE ARGUMENT ORDER CHANGES THE ANSWER. It
    asks "is `base` the MAIN REPO OF the checkout at `env_dir`" — NOT "are
    these two directories related". Swapping the arguments silently returns a
    different verdict, and nothing will fail to tell you:

        same_repository(worktree, main_repo)  -> True   (main IS the main repo)
        same_repository(main_repo, worktree)  -> False  (a worktree is not one)
        same_repository(subdir,    repo_root) -> True
        same_repository(repo_root, subdir)    -> False  (a subdir is not one)

    Its one production caller, `stays_in_declared_project`, passes the
    DECLARATION first. Put the declaration first or invert the meaning.

    `--git-common-dir` is shared by every worktree of a repo, so its parent is
    the main root for both the worktree and the main checkout. It is NOT a
    checkout root for a submodule or a `--separate-git-dir` repository, whose
    common dir lives elsewhere; `stays_in_declared_project` covers those.

    FAIL-SAFE IS FALSE, WHICH MEANS REFUSE. Any git error, timeout, or
    non-repo directory returns False. Declining to guess is the safe direction
    on a write path: refusing costs a recoverable skip, while guessing wrong
    writes into a project nobody named.
    """
    common_dir = _rev_parse_path(Path(env_dir), "--git-common-dir")
    if common_dir is None:
        return False
    try:
        return common_dir.parent == Path(base).resolve()
    except OSError:
        return False


def _raw_rev_parse(directory: Path, flag: str) -> tuple[Optional[Path], bool]:
    """`git -C <directory> rev-parse <flag>` as (resolved path, git RAN).

    THE OUTPUT IS NOT STRIPPED, unlike `_git_output`. A path can end in
    whitespace, and the worktree listing preserves it, so comparing a
    strip()ped value against the listing fails in BOTH directions: it rejects
    a legitimate worktree at `.../wt ` and it admits a forged directory at
    `.../wt ` whose gitfile names the real `.../wt`. Drop exactly one
    trailing newline -- git's record terminator -- and keep every other byte.

    The boolean separates "git answered, and the answer is no path"
    (None, True) from "git could not run" (None, False). The session-start
    writers skip on the latter rather than fall back to planting a CLAUDE.md
    in a worktree during a git failure.
    """
    try:
        result = run_git(
            ["-C", str(directory), "rev-parse", flag],
            timeout=5,
            env=git_env_without_location(),
        )
    except OSError:
        # `run_git` absorbs a timeout and a missing git but NOT a bare OSError
        # from launching it; this helper must be total on a write-adjacent
        # path, so an unlaunchable git is "could not answer".
        return None, False
    if result is None:
        return None, False
    if result.returncode != 0 or not result.stdout:
        return None, True
    raw = result.stdout
    if raw.endswith("\n"):
        raw = raw[:-1]
    path = Path(raw)
    if not path.is_absolute():
        path = Path(directory) / path
    try:
        return path.resolve(), True
    except OSError:
        return None, True


def _toplevel_path(directory: Path) -> Optional[Path]:
    """`--show-toplevel` via `_raw_rev_parse` (trailing whitespace preserved)."""
    return _raw_rev_parse(directory, "--show-toplevel")[0]


def _project_memory_root(
    project_dir: str | os.PathLike[str],
) -> tuple[Path, bool]:
    """The memory root AND whether git could answer.

    `project_memory_root` keeps the fail-safe answer (the input) for both
    cases; `project_memory_root_or_none` reports the difference so a writer
    can skip when the target is unknown instead of planting in a worktree.
    """
    given = Path(project_dir)
    git_dir, ran = _raw_rev_parse(given, "--git-dir")
    if not ran:
        return given, False
    common_dir, ran = _raw_rev_parse(given, "--git-common-dir")
    if not ran:
        return given, False
    if git_dir is None or common_dir is None or git_dir == common_dir:
        return given, True
    candidate = common_dir.parent
    toplevel_at_candidate, ran = _raw_rev_parse(candidate, "--show-toplevel")
    if not ran:
        return given, False
    if toplevel_at_candidate != candidate:
        return given, True
    common_dir_again, ran = _raw_rev_parse(candidate, "--git-common-dir")
    if not ran:
        return given, False
    if common_dir_again != common_dir:
        return given, True
    # A GITFILE CAN BE FORGED. A directory carrying only a `.git` FILE whose
    # `gitdir:` line names another repository's worktree admin dir makes every
    # check above agree: git reports that repository's git dir, common dir and
    # checkout root, while the asking directory is not a checkout of it at all
    # -- so a writer would target a project nobody named. The worktree records
    # git keeps must list this directory's own top level; a forged gitfile is
    # not listed. Fail-safe is the input.
    toplevel, ran = _raw_rev_parse(given, "--show-toplevel")
    if not ran:
        return given, False
    listed = _listed_worktrees_result(candidate, resolve_against=candidate)
    if listed is None:
        return given, False
    if toplevel is None or toplevel not in listed:
        return given, True
    return candidate, True


def project_memory_root(project_dir: str | os.PathLike[str]) -> Path:
    """Return the directory that owns this session's project memory file.

    For a linked worktree -- its root or a subdirectory of one -- that is the
    main checkout's root; for everything else (a main checkout, a
    subdirectory of one, a non-git directory, or any git failure) it is
    `project_dir` EXACTLY as given.

    THE EXACT RETURN IS LOAD-BEARING. Callers compare their output against the
    pre-worktree behaviour, so a resolved or absolutised path would change a
    status message for a reason that has nothing to do with worktrees. Return
    the constructed `Path` untouched; never `resolve()`, `abspath` or normalise
    here.

    WHY `--git-common-dir`'s PARENT IS NOT ENOUGH. It is the main root for
    both a worktree and a main checkout, but it is NOT a checkout root for a
    submodule or a `--separate-git-dir` repository, whose common dir lives
    elsewhere. The discriminator below excludes those two (their git dir and
    common dir are equal), and the candidate verification excludes a worktree
    of a bare repository, where the parent is not a checkout root either.

    Composes `_raw_rev_parse` for every git field (unstripped, so a path's own
    trailing whitespace survives, and reporting whether git actually ran) and
    `_git_output` only for the listing's last-resort line parser, all running
    `git -C <dir>` with `git_env_without_location()` -- an inherited GIT_DIR
    would otherwise make every directory report one repository, and a bare
    `git rev-parse` would classify the hook's CWD instead of the project dir.
    """
    return _project_memory_root(project_dir)[0]


def project_memory_root_or_none(
    project_dir: str | os.PathLike[str],
) -> Optional[Path]:
    """The memory root, or None when git could not answer.

    A NON-GIT directory is a KNOWN answer and returns the directory itself;
    only a git invocation that could not run (timeout, no git, an unreadable
    worktree listing) returns None. Session-start writers pass this so an
    unknown target is skipped rather than resolved to the worktree by the
    fail-safe.
    """
    root, answered = _project_memory_root(project_dir)
    return root if answered else None


def _nearest_existing_directory(path: Path) -> Optional[Path]:
    """Return `path` if it is a directory, else its closest ancestor that is."""
    for candidate in (path, *path.parents):
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            return None
    return None


def _worktree_paths_from_admin_gitdirs(checkout: Path) -> Optional[list[str]]:
    """Raw worktree paths from `<common-dir>/worktrees/*/gitdir`.

    THE FALLBACK FOR A GIT WITHOUT `-z` (pre-2.36). git records each linked
    worktree's own `.git` path in its admin dir, and that file is not
    line-quoted, so dropping exactly one trailing newline recovers a path
    containing a newline. The recorded value is `<worktree>/.git`, so the
    worktree root is its parent -- the record terminator follows `/.git`, not
    the path, which is why an internal newline survives.

    Returns None when git cannot answer at all (the caller keeps the line
    parser as the last resort). An empty repository returns its own root.
    LIMIT: a relative recorded path (`worktree.useRelativePaths`) is resolved
    against the main root, unmeasured on the git available here (2.43).
    """
    common_dir, ran = _raw_rev_parse(checkout, "--git-common-dir")
    if not ran:
        return None
    if common_dir is None:
        return []
    paths = [str(common_dir.parent)]
    try:
        admins = sorted((common_dir / "worktrees").iterdir())
    except OSError:
        return paths
    for admin in admins:
        try:
            content = (admin / "gitdir").read_text(encoding="utf-8")
        except OSError:
            continue
        if content.endswith("\n"):
            content = content[:-1]
        # The admin `gitdir` file holds the worktree's `.git` path with NO
        # `gitdir: ` prefix (unlike a worktree's own gitfile, which does).
        # Accept either so a future format change cannot silently empty the
        # fallback.
        if content.startswith("gitdir: "):
            content = content[len("gitdir: "):]
        if not content:
            continue
        gitfile = Path(content)
        if not gitfile.is_absolute():
            gitfile = common_dir.parent / gitfile
        paths.append(str(gitfile.parent))
    return paths


def _worktree_list_paths(checkout: Path) -> Optional[list[str]]:
    """The `worktree ` paths git records, or None when git cannot answer.

    `-z` (git 2.36+) NUL-separates the porcelain FIELDS, so a path containing
    a newline survives intact. The line-based form splits that record and
    truncates the path, which a membership check would then refuse -- falling
    back to the worktree and reopening the write this guard exists to stop.

    When `-z` is unavailable the admin `gitdir` files are the next choice:
    they hold each linked worktree's full path and are not line-quoted. The
    line parser remains only as the last resort, for a git that cannot answer
    either; on it a newline-containing path is still truncated. That residue
    is stated, not hidden: it needs a pre-2.36 git AND an unreadable admin
    enumeration.
    """
    result = run_git(
        ["-C", str(checkout), "worktree", "list", "--porcelain", "-z"],
        timeout=5,
        env=git_env_without_location(),
    )
    if result is not None and result.returncode == 0:
        return [
            field[len("worktree "):]
            for field in (result.stdout or "").split("\0")
            if field.startswith("worktree ")
        ]
    recorded = _worktree_paths_from_admin_gitdirs(checkout)
    if recorded is not None:
        return recorded
    output = _git_output(checkout, "worktree", "list", "--porcelain")
    if output is None:
        return None
    return [
        line[len("worktree "):]
        for line in output.splitlines()
        if line.startswith("worktree ")
    ]


def _listed_worktrees_result(
    checkout: Path, resolve_against: Optional[Path] = None
) -> Optional[Set[Path]]:
    """Resolved worktree paths, or None when git could not answer.

    Includes a PRUNABLE worktree, whose directory is gone but whose record
    remains until `git worktree prune`. `git worktree remove` deletes the
    record as well, so a worktree removed that way is not listed.

    `resolve_against` is the directory a RELATIVE recorded path resolves
    against. git records relative worktree paths against the main worktree
    root, so a caller holding that root passes it; the default, the process
    cwd, is what every pre-existing caller used.

    The None/empty distinction is load-bearing for `project_memory_root`:
    an EMPTY set is a real answer (no worktrees), while None means git could
    not enumerate, which the session-start writers treat as unknown.
    """
    paths = _worktree_list_paths(checkout)
    if paths is None:
        return None
    base = resolve_against or Path.cwd()
    listed: Set[Path] = set()
    for raw in paths:
        try:
            path = Path(raw)
            if not path.is_absolute():
                path = base / path
            listed.add(path.resolve())
        except OSError:
            continue
    return listed


def _listed_worktrees(
    checkout: Path, resolve_against: Optional[Path] = None
) -> Set[Path]:
    """`_listed_worktrees_result`, with an unanswerable git reported as empty.

    Every pre-existing caller keeps today's shape and signature; only
    `project_memory_root` needs the None/empty distinction.
    """
    return _listed_worktrees_result(checkout, resolve_against) or set()


def _recorded_common_dir(identity: Optional[dict], declared: Path) -> Optional[str]:
    """The common dir a session record names, when it was written for exactly `declared`."""
    if not isinstance(identity, dict) or identity.get("declared") != os.path.realpath(declared):
        return None
    common_dir = identity.get("common_dir")
    return common_dir if isinstance(common_dir, str) and os.path.isabs(common_dir) else None


def stays_in_declared_project(
    declared: Path,
    resolved_root: Path,
    claude_md: Path,
    worktree_identity: Optional[dict] = None,
) -> bool:
    """True when resolution that started at `declared` ended in the same project.

    `resolved_root` is the directory the resolver found `claude_md` under.
    `worktree_identity` is this session's worktree record, or None.

    THE DISCRIMINATOR BETWEEN A LEGITIMATE FALL-THROUGH AND A WRONG-PROJECT
    ONE. A resolver that probes a declared directory and finds no CLAUDE.md
    continues to its git anchors. That is CORRECT when it lands back inside
    the same project — PACT's own spawned paths set CLAUDE_PROJECT_DIR to a
    worktree, where CLAUDE.md is gitignored and therefore absent, and the git
    anchors then find the MAIN repo's file, which is the intended answer. A
    blanket "declared dir has no CLAUDE.md -> refuse" rule breaks that on every
    such invocation — a cardinal over-block.

    ADMITTED, and nothing else:
      1. The declaration itself.
      2. The main repo of the declaration's checkout (`same_repository`).
      3. The root of ANY checkout of the declaration's repository: the
         declaration's own checkout, the main checkout, or another worktree.
         The resolvers anchor on `--show-toplevel` as well as
         `--git-common-dir`, and for a submodule, a `--separate-git-dir`
         repository, or a main checkout whose worktree holds the CLAUDE.md,
         the common dir's parent is not the root they landed on. Judging by
         one anchor alone refused those same-project writes.

    A DECLARATION THAT NO LONGER EXISTS is judged three ways, in order. If the
    resolved repository still lists it as a worktree, that record proves
    identity and it is admitted. Otherwise, a session record written while this
    exact declaration existed decides before the ancestor walk: the resolution
    is admitted only at a checkout root of the repository the record names, and
    refused otherwise, including when git cannot answer. A record for any other
    declaration is ignored. Otherwise it is judged from its nearest existing
    ancestor, so a removed worktree or subdirectory still maps to the
    repository it was in; rules 2 and 3 still require the resolution to land in
    that repository. That includes a removed INDEPENDENT repository that was
    nested inside another: its nearest ancestor lies in the enclosing
    repository, so a resolution into the enclosing repository is admitted when
    no session record names it, because nothing else this check reads
    separates it from a removed subdirectory, which must stay admitted. (A
    removed submodule leaves a record under the enclosing repository's
    .git/modules, which this check does not read.) The ancestor stands in for a
    directory git can no longer see, so it NEVER admits a resolution at the
    home directory or into the config root's own CLAUDE.md: every project under
    the user loads those files, and a deleted project under a git-versioned
    home would otherwise project into them. A live declaration that resolves
    there is not affected.

    REFUSED: a SUBDIRECTORY that is not a checkout root (a path below a root is
    containment, not identity — a nested directory can be its own project), a
    different repository nested inside or around a LIVE declaration, a worktree
    removed with `git worktree remove` from outside its repository's tree when
    no session record names it, a removed declaration whose session record
    names a different repository, and any non-git layout other than the
    declaration itself.

    FAIL-SAFE IS FALSE, WHICH MEANS REFUSE.
    """
    declared = Path(declared)
    try:
        resolved = Path(resolved_root).resolve()
        if declared.resolve() == resolved:
            return True
    except OSError:
        return False
    anchor = _nearest_existing_directory(declared)
    if anchor is None:
        return False
    if anchor != declared:
        try:
            if declared.resolve() in _listed_worktrees(resolved):
                return True
            recorded_common_dir = _recorded_common_dir(worktree_identity, declared)
            if recorded_common_dir is not None:
                return _rev_parse_path(resolved, "--show-toplevel") == resolved and (
                    _rev_parse_path(resolved, "--git-common-dir")
                    == Path(recorded_common_dir)
                )
            if resolved == Path.home().resolve():
                return False
            config_claude_md = (get_claude_config_dir() / "CLAUDE.md").resolve()
            if Path(claude_md).resolve() == config_claude_md:
                return False
        except (OSError, RuntimeError):
            # RuntimeError: Path.home() when no home directory can be found.
            return False
    if same_repository(anchor, resolved):
        return True
    if _rev_parse_path(resolved, "--show-toplevel") != resolved:
        return False
    anchor_common_dir = _rev_parse_path(anchor, "--git-common-dir")
    return anchor_common_dir is not None and anchor_common_dir == _rev_parse_path(
        resolved, "--git-common-dir"
    )


def _session_record_on_disk(env_session: str, filename: str) -> dict:
    """Find the one `filename` in this session id's folder, and parse it.

    Session folders sit under `pact-sessions/<project slug>/<session id>/`, and
    a reader that knows only the session id cannot know the slug, so it globs
    every project for the id.

    Returns the parsed mapping, or {} on any failure: the glob raised, the
    match count was not exactly one (uniqueness was measured on one machine,
    not guaranteed, so picking the first would be a coin toss over which
    project's session this is), the file did not parse, or the payload was
    not a mapping. Fail-open by posture: callers land on their own fallback.
    """
    try:
        sessions_root = get_claude_config_dir() / "pact-sessions"
        # The writers collapsed unsafe characters in the id; match that name.
        safe_session = _UNSAFE_SLUG_CHARS_RE.sub("_", env_session)
        matches = list(sessions_root.glob(f"*/{safe_session}/{filename}"))
    except OSError:
        return {}

    if len(matches) != 1:
        return {}

    try:
        data = json.loads(matches[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}

    return data if isinstance(data, dict) else {}


_WORKTREE_IDENTITY_PATHS = ("declared", "worktree", "common_dir")


def get_worktree_identity_from_session_record() -> dict:
    """Return the worktree identity session_init recorded for this session, or {}.

    session_init writes it into the session's own folder, for lead and teammate
    frames, when the session starts inside a linked worktree. The working-memory write guard
    and archive_pin pass it to `stays_in_declared_project`, which reads it only
    when the declared directory no longer exists. Both hold this one object:
    pact_session re-exports it, and archive_pin imports it from here.

    A test process reads no record, and neither does a process without
    CLAUDE_CODE_SESSION_ID. Nothing is cached.

    Returns {} unless the record's `session_id` equals CLAUDE_CODE_SESSION_ID
    and `declared`, `worktree` and `common_dir` are all absolute path strings.
    Never raises.
    """
    # Same guard pair as pact_session._discover_session_id -- keep in sync.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {}
    env_session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not env_session:
        return {}
    record = _session_record_on_disk(env_session, WORKTREE_IDENTITY_FILE)
    if record.get("session_id") != env_session:
        return {}
    for key in _WORKTREE_IDENTITY_PATHS:
        value = record.get(key)
        if not isinstance(value, str) or not os.path.isabs(value):
            return {}
    return record


def _record_worktree_identity(session_id: str, project_dir: str) -> None:
    """Record which repository this session's linked worktree belongs to.

    Writes `<session_dir>/worktree-identity.json` when `project_dir` lies inside
    a linked worktree (its git dir and common dir differ), including a
    subdirectory of one. The working-memory write guard reads it back once that
    worktree is removed and git can no longer say which repository the declared
    directory was in. Runs for lead and teammate frames, so a separate-process
    teammate records its own session; an unknown frame returns before it.

    Fail-open: any error leaves no record.
    """
    try:
        directory = Path(project_dir)
        if not directory.is_dir():
            return
        git_dir = _rev_parse_path(directory, "--git-dir")
        common_dir = _rev_parse_path(directory, "--git-common-dir")
        if git_dir is None or common_dir is None or git_dir == common_dir:
            return
        worktree = _rev_parse_path(directory, "--show-toplevel")
        if worktree is None:
            return
        record = {
            "session_id": session_id,
            "declared": os.path.realpath(project_dir),
            "worktree": str(worktree),
            "common_dir": str(common_dir),
        }
        state_file.write_text(
            _build_session_path(project_slug(project_dir), session_id)
            / WORKTREE_IDENTITY_FILE,
            json.dumps(record),
            root=get_claude_config_dir() / "pact-sessions",
        )
    except Exception:
        return
