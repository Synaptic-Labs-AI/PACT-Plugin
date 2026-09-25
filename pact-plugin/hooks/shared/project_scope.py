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
         (`WORKTREE_IDENTITY_FILE`); skills/pact-memory/scripts/pact_session.py
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

from .git_helpers import run_git
from .pact_context import _UNSAFE_SLUG_CHARS_RE
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
    # os.path.realpath, not Path.resolve(): on 3.9 resolve() raises
    # RuntimeError on a symlink loop, while 3.13 and later return the path
    # with the looping component unresolved. realpath does that on every
    # interpreter, so every caller compares the same path.
    try:
        return Path(os.path.realpath(path))
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
    except (OSError, RuntimeError):
        # RuntimeError: Path.resolve() on a symlink loop under 3.9. That is
        # False here; on later versions the unresolved loop is not the main
        # repository's root either, so the answer is False on every one.
        return False


def _nearest_existing_directory(path: Path) -> Optional[Path]:
    """Return `path` if it is a directory, else its closest ancestor that is."""
    for candidate in (path, *path.parents):
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            return None
    return None


def _listed_worktrees(checkout: Path) -> Set[Path]:
    """Resolved paths of every worktree git records for the repository at `checkout`.

    Includes a PRUNABLE worktree, whose directory is gone but whose record
    remains until `git worktree prune`. `git worktree remove` deletes the
    record as well, so a worktree removed that way is not listed.
    """
    output = _git_output(checkout, "worktree", "list", "--porcelain")
    listed: Set[Path] = set()
    for line in (output or "").splitlines():
        if line.startswith("worktree "):
            try:
                listed.add(Path(line[len("worktree "):]).resolve())
            except OSError:
                continue
    return listed


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
    # os.path.realpath, not Path.resolve(), on both sides here and in the
    # worktree membership check below: on 3.9 resolve() raises RuntimeError on
    # a symlink loop, while 3.13 and later return the path with the looping
    # component unresolved. realpath does that on every interpreter, so a
    # looped declaration gets the same verdict everywhere.
    try:
        resolved = Path(os.path.realpath(resolved_root))
        if Path(os.path.realpath(declared)) == resolved:
            return True
    except OSError:
        return False
    anchor = _nearest_existing_directory(declared)
    if anchor is None:
        return False
    if anchor != declared:
        try:
            if Path(os.path.realpath(declared)) in _listed_worktrees(resolved):
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

    session_init writes it into the session's own folder, for every role, when
    the session starts inside a linked worktree. The working-memory write guard
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
