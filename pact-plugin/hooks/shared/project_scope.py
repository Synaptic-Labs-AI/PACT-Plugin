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
import re
from pathlib import Path
from typing import List, NamedTuple, Optional, Set, Tuple

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


def _existing_anchor(path: Path) -> Tuple[Path, Tuple[str, ...]]:
    """The deepest ancestor of `path` (itself included) that exists, and the
    names below it, in order."""
    rest: List[str] = []
    while not path.exists() and path.parent != path:
        rest.append(path.name)
        path = path.parent
    return path, tuple(reversed(rest))


def _case_insensitive(directory: Path) -> bool:
    """Whether names are matched without case where `directory` lives: its
    case-swapped spelling names the same directory. A name with no cased
    letters cannot be probed and reads as case-sensitive."""
    swapped = directory.name.swapcase()
    if swapped == directory.name:
        return False
    try:
        return os.path.samefile(directory, directory.with_name(swapped))
    except (OSError, ValueError):
        return False


def _same_file(a: Path, b: Path) -> Optional[bool]:
    """Whether `a` and `b` name the same file, created or not: True or False
    when decided, None when an error prevents deciding.

    When both exist, `os.path.samefile` (a hardlink or a case variant counts
    as the same file). Otherwise the deepest existing ancestor of each must be
    the same directory, and the names below it must be equal, or equal
    case-folded where that directory's volume matches names without case.
    """
    try:
        if a.exists() and b.exists():
            return os.path.samefile(a, b)
        anchor_a, rest_a = _existing_anchor(a)
        anchor_b, rest_b = _existing_anchor(b)
        if not os.path.samefile(anchor_a, anchor_b):
            return False
        if rest_a == rest_b:
            return True
        folded = [name.casefold() for name in rest_a] == [name.casefold() for name in rest_b]
        return folded and _case_insensitive(anchor_a)
    except (OSError, ValueError):
        return None


def same_path(a: Path, b: Path) -> bool:
    """Whether `a` and `b` name the same file, created or not.

    `_same_file` decided True. Never raises: an error reads as different
    paths, the direction a gate wants (allow on doubt).
    """
    return _same_file(a, b) is True


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


def _fsdecode(data: bytes | str) -> str:
    """Decode git output as filesystem text, preserving undecodable bytes.

    `subprocess.run(text=True)` decodes with the LOCALE encoding and raises
    UnicodeDecodeError on a path that is not valid in it -- and a Linux path
    may contain any byte except NUL and `/`. os.fsdecode uses the filesystem
    encoding with surrogateescape, so the bytes round-trip through Path and
    os.fsencode and classify as the path git actually named.
    """
    return data if isinstance(data, str) else os.fsdecode(data)


def _git_output(directory: Path, *args: str) -> Optional[str]:
    """Return the stripped stdout of `git -C <directory> <args>`, or None.

    None on a git error, a timeout, a non-repo directory or an OSError, so
    every rule built on it fails toward refusal.

    Output is decoded with `_fsdecode`, never by `subprocess`, so a path git
    prints is not locale-decoded (#1653).
    """
    # `run_git` absorbs TimeoutExpired and FileNotFoundError only. The original
    # predicate caught OSError entire, and that breadth is load-bearing here:
    # a PermissionError reaching a caller as an exception instead of a refusal
    # would turn a fail-safe into a crash on a write path.
    try:
        result = run_git(
            ["-C", str(directory), *args],
            timeout=5,
            text=False,
            env=git_env_without_location(),
        )
    except (OSError, ValueError):
        # ValueError covers a subprocess-level refusal (an embedded NUL and
        # the like): "could not answer", not a crash on the write path.
        return None
    if result is None or result.returncode != 0:
        return None
    stdout = _fsdecode(result.stdout).strip()
    return stdout or None


def _rev_parse_path(directory: Path, flag: str) -> Optional[Path]:
    """Return `git -C <directory> rev-parse <flag>` as a resolved path, or None.

    The path half of `_raw_rev_parse`, so every rev-parse field is read the
    same way: UNSTRIPPED (a path's own trailing whitespace survives, matching
    the worktree listing) and total.
    """
    return _raw_rev_parse(Path(directory), flag)[0]


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

    TWO production callers, and each chooses its order.
    `stays_in_declared_project` passes the DECLARATION first. `gate_target`
    passes the TARGET's base first DELIBERATELY: it asks "is the declared
    base the main repo of the checkout this target sits in", which is the
    direction that refuses a worktree copy as the pre-state (a test pins that
    order). Swapping either caller's arguments inverts its meaning.

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
        # RuntimeError: a symlink loop, on Pythons before 3.13.
        return False


# THE THREE FACTS BEHIND AN UNKNOWN PROJECT-MEMORY ROOT. Callers that render a
# message must name the one that occurred: a directory that is gone was never
# offered to git, a directory whose answer failed verification was, and a
# git that never ran or could not enumerate is a third case again. Collapsing
# them to one "unknown" produced a message that blamed git on the first and
# third-vs-second ambiguity. `memory_root_of` and `session_memory_root` return
# these as the cause (None for a KNOWN root; ROOT_CAUSE_OK is internal);
# compare against the constants, never literals.
ROOT_CAUSE_OK = "ok"
ROOT_CAUSE_MISSING = "missing"
ROOT_CAUSE_UNVERIFIED = "unverified"
ROOT_CAUSE_UNANSWERABLE = "unanswerable"
# A known checkout whose canonical document IS the user-global CLAUDE.md (the
# config dir itself, or a home whose `.claude` is the config dir): every
# session loads that file, so it is not one project's memory.
ROOT_CAUSE_USER_GLOBAL = "user_global"


def _unanswered_cause(given: Path) -> str:
    """Why a `not ran` step leaves the root unknown: missing or unanswerable.

    The DECLARATION decides, not the step's own directory: a `not ran` on the
    verification CANDIDATE still leaves a declaration that exists, and that is
    a git failure, not a missing directory.
    """
    return (
        ROOT_CAUSE_MISSING
        if _directory_is_missing(given)
        else ROOT_CAUSE_UNANSWERABLE
    )


_GIT_TRUE_WORDS = frozenset({"true", "yes", "on"})
_GIT_FALSE_WORDS = frozenset({"", "false", "no", "off"})
# git's integer form: optional sign, decimal or 0x-hex, optional k/m/g unit.
_GIT_INT_RE = re.compile(r"\s*[-+]?(?:0[xX](?P<hex>[0-9a-fA-F]+)|(?P<dec>[0-9]+))[kKmMgG]?")


def _git_env_bool(value: Optional[str]) -> bool:
    """A git boolean environment value, read as git reads it.

    Unset is false. The words true/yes/on and false/no/off (any case) and the
    empty string are git's text forms; otherwise git parses an integer, and
    any nonzero integer is true. A value git cannot parse makes git itself
    refuse to run, so it reads as true here: the walk then counts more
    metadata, which is the fail-closed direction.
    """
    if value is None:
        return False
    lowered = value.lower()
    if lowered in _GIT_TRUE_WORDS:
        return True
    if lowered in _GIT_FALSE_WORDS:
        return False
    match = _GIT_INT_RE.fullmatch(value)
    if match is None:
        return True
    digits = match.group("hex") or match.group("dec")
    return digits.strip("0") != ""


def _git_ceiling_directories() -> Set[str]:
    """The GIT_CEILING_DIRECTORIES entries git honours, in both spellings.

    Only absolute entries count, as in git: a relative entry is ignored. Each
    is kept as written (normalised) and, until the first EMPTY entry, as its
    real path too: git resolves symlinks in the entries before an empty one
    and compares the entries after it as written.
    """
    ceilings: Set[str] = set()
    resolve_symlinks = True
    for entry in os.environ.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep):
        if not entry:
            resolve_symlinks = False
            continue
        if not os.path.isabs(entry):
            continue
        ceilings.add(os.path.normpath(entry))
        if not resolve_symlinks:
            continue
        try:
            ceilings.add(os.path.realpath(entry))
        except (OSError, ValueError):
            continue
    return ceilings


def _git_metadata_at_or_above(directory: Path) -> bool:
    """Whether git's own repository discovery from `directory` meets a `.git`.

    A `.git` file or directory on that path means git's refusal to answer is a
    FAILURE on a checkout (a broken gitfile, a deleted admin dir, a moved main
    checkout, dubious ownership), not the answer "no repository".

    THE WALK IS GIT'S DISCOVERY PATH AND STOPS WHERE GIT STOPS: from the real
    path of `directory` (always checked, even when it is itself a ceiling
    entry), upwards, never stepping INTO a GIT_CEILING_DIRECTORIES entry, and
    never onto a filesystem other than the START directory's unless
    GIT_DISCOVERY_ACROSS_FILESYSTEM is a true git boolean. A `.git` beyond
    those limits is
    one git never reached, so a refusal there is a genuine "no repository".
    Both variables reach git unchanged (`git_env_without_location` strips only
    the location variables), so the walk and git read the same values.

    Anything that cannot be stat'ed for a reason other than absence counts as
    metadata present: unable to tell, the caller fails closed.
    """
    try:
        current = Path(os.path.realpath(directory))
        start_device = os.stat(current).st_dev
    except (OSError, ValueError):
        return True
    ceilings = _git_ceiling_directories()
    across_filesystems = _git_env_bool(
        os.environ.get("GIT_DISCOVERY_ACROSS_FILESYSTEM")
    )
    while True:
        try:
            os.lstat(current / ".git")
            return True
        except (FileNotFoundError, NotADirectoryError):
            pass
        except (OSError, ValueError):
            return True
        parent = current.parent
        if parent == current or str(parent) in ceilings:
            return False
        if not across_filesystems:
            try:
                if os.stat(parent).st_dev != start_device:
                    return False
            except (OSError, ValueError):
                return True
        current = parent


def _git_dir_answers(directory: Path) -> bool:
    """Whether `git -C <directory> rev-parse --git-dir` succeeds.

    Asked only after `--show-toplevel` failed on a directory holding git
    metadata: it separates a bare repository, which has no work tree, from a
    repository git cannot open at all. Any error is False, which makes the caller fail closed.
    """
    try:
        result = run_git(
            ["-C", str(directory), "rev-parse", "--git-dir"],
            timeout=5,
            text=False,
            env=git_env_without_location(),
        )
    except (OSError, ValueError):
        return False
    return result is not None and result.returncode == 0 and bool(result.stdout)


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

    A MISSING DIRECTORY IS "GIT COULD NOT RUN", NOT A GIT ANSWER. `git -C`
    refuses to change into a directory that is not there, so it exits 128
    BEFORE it looks for a repository -- the same status, and no better
    information, than the 128 it gives for an existing non-git directory.
    Reading that status as "git answered, no path" classified a deleted
    project dir as a KNOWN root, and the session-start writers then created
    the directory and planted a CLAUDE.md inside it. The two shapes differ
    only in stderr text, which is not a contract, so the check is the
    filesystem fact instead. `is_dir()` swallows OSError, so a directory that
    cannot be stat'ed also reads as could-not-run, which is the safe
    direction: the writers skip.
    """
    if _directory_is_missing(directory):
        return None, False
    try:
        result = run_git(
            ["-C", str(directory), "rev-parse", flag],
            timeout=5,
            text=False,
            env=git_env_without_location(),
        )
    except (OSError, ValueError):
        # `run_git` absorbs a timeout and a missing git but NOT a bare OSError
        # from launching it, nor a subprocess-level ValueError; this helper
        # must be total on a write-adjacent path, so either is "could not
        # answer".
        return None, False
    if result is None:
        return None, False
    if result.returncode != 0 or not result.stdout:
        # "No path" is an answer only where no git metadata exists, or where
        # git still locates a repository here. With a `.git` at or above the
        # directory and no repository git can open, the refusal is git failing
        # on a checkout, so it reads as could-not-run and the root is unknown.
        # ONLY a `--show-toplevel` refusal is re-probed: a bare repository
        # kept at `<dir>/.git` refuses it (no work tree) while `--git-dir`
        # succeeds, and its worktrees are genuine checkouts. Every other field
        # stays could-not-run on a failure, because the callers read a None
        # git dir or common dir as "no repository" (a KNOWN root).
        if not _git_metadata_at_or_above(Path(directory)):
            return None, True
        return None, flag == "--show-toplevel" and _git_dir_answers(directory)
    raw = _fsdecode(result.stdout)
    if raw.endswith("\n"):
        raw = raw[:-1]
    path = Path(raw)
    if not path.is_absolute():
        path = Path(directory) / path
    try:
        return path.resolve(), True
    except (OSError, RuntimeError):
        # resolve() does not raise OSError for an unreadable parent, but a
        # symlink loop raises RuntimeError; both are "answer unusable".
        return None, True


def _repository_is_bare(common_dir: Path) -> bool:
    """Whether the repository whose common dir this is has no checkout.

    A linked worktree of a BARE repository is a real checkout and legitimately
    owns its project memory. A worktree of a NON-bare repository whose git dir
    lives elsewhere (`--separate-git-dir`) is not the main checkout, and a
    forged gitfile names a repository the asking directory does not belong to
    -- neither may be treated as the project's root. Read only after candidate
    verification has already failed.
    """
    try:
        result = run_git(
            ["--git-dir", str(common_dir), "config", "--get", "core.bare"],
            timeout=5,
            text=False,
            env=git_env_without_location(),
        )
    except (OSError, ValueError):
        return False
    if result is None or result.returncode != 0:
        return False
    return _fsdecode(result.stdout).strip().lower() == "true"


def _failed_verification_root(
    given: Path, common_dir: Path
) -> tuple[Path, str]:
    """The answer when the candidate main root could not be verified.

    A bare repository's worktree is a genuine checkout, so it stays a KNOWN
    root (the input) and the cause is ROOT_CAUSE_OK -- NOT "unverified", which
    would make the message false for a legitimate checkout. Every other
    unverified linked worktree is ROOT_CAUSE_UNVERIFIED: a
    `--separate-git-dir` worktree has no resolvable main checkout, and a
    forged gitfile names a repository this directory is not part of. Writers
    must skip on it rather than target the worktree copy, which is the whole
    point of the distinction.
    """
    return (
        (given, ROOT_CAUSE_OK)
        if _repository_is_bare(common_dir)
        else (given, ROOT_CAUSE_UNVERIFIED)
    )


def _project_memory_root_cause(
    project_dir: str | os.PathLike[str],
) -> tuple[Path, str]:
    """The memory root AND the CAUSE when it is unknown.

    THE ONE PLACE THE THREE CAUSES ARE DECIDED; `memory_root_of` is its
    public face. Deciding here rather than in each consumer is what keeps the
    three branches unable to disagree.

    `_failed_verification_root` answers in the same vocabulary, so the bare
    repository's worktree -- a genuine checkout -- stays ROOT_CAUSE_OK.
    """
    given = Path(project_dir)
    git_dir, ran = _raw_rev_parse(given, "--git-dir")
    if not ran:
        return given, _unanswered_cause(given)
    common_dir, ran = _raw_rev_parse(given, "--git-common-dir")
    if not ran:
        return given, _unanswered_cause(given)
    if git_dir is None or common_dir is None or git_dir == common_dir:
        return given, ROOT_CAUSE_OK
    candidate = common_dir.parent
    toplevel_at_candidate, ran = _raw_rev_parse(candidate, "--show-toplevel")
    if not ran:
        return given, _unanswered_cause(given)
    if toplevel_at_candidate != candidate:
        return _failed_verification_root(given, common_dir)
    # DEFENCE IN DEPTH: the candidate must report OUR common dir. It refuses a
    # candidate checkout whose own `.git` is another repository (a common dir
    # relocated beside an unrelated checkout). The worktree-listing check below
    # refuses that layout too, so dropping this one changes no outcome; it
    # stays so that the listing is not the only thing standing there.
    common_dir_again, ran = _raw_rev_parse(candidate, "--git-common-dir")
    if not ran:
        return given, _unanswered_cause(given)
    if common_dir_again != common_dir:
        return _failed_verification_root(given, common_dir)
    # A GITFILE CAN BE FORGED. A directory carrying only a `.git` FILE whose
    # `gitdir:` line names another repository's worktree admin dir makes every
    # check above agree: git reports that repository's git dir, common dir and
    # checkout root, while the asking directory is not a checkout of it at all
    # -- so a writer would target a project nobody named. The worktree records
    # git keeps must list this directory's own top level; a forged gitfile is
    # not listed. Fail-safe is the input.
    toplevel, ran = _raw_rev_parse(given, "--show-toplevel")
    if not ran:
        return given, _unanswered_cause(given)
    listed = _listed_worktrees_result(candidate, resolve_against=candidate)
    if listed is None:
        # git could not ENUMERATE: a failed answer, not a failed verification.
        return given, ROOT_CAUSE_UNANSWERABLE
    if toplevel is None or toplevel not in listed:
        # The anti-forgery failure: a `.git` FILE naming another repository's
        # worktree admin dir. Not a bare repo, so this is UNKNOWN rather than
        # a known root -- otherwise the archive's fall-through can walk to the
        # named repository's document and accept a project nobody declared.
        return _failed_verification_root(given, common_dir)
    return candidate, ROOT_CAUSE_OK


def _directory_is_missing(directory: str | os.PathLike[str]) -> bool:
    """True when `directory` is not an existing directory.

    The filesystem fact behind ROOT_CAUSE_MISSING: `git -C` refuses a
    directory that is not there with the same status as a non-git one, so the
    classifier checks this first. `is_dir()` swallows OSError, so a directory
    that cannot be stat'ed reads as missing too, which is the safe direction.
    Anything unexpected answers False, so the caller keeps "git could not
    answer".
    """
    try:
        return not Path(directory).is_dir()
    except (OSError, TypeError, ValueError):
        return False


def unknown_root_reason(
    cause: str, project_dir: Optional[str | os.PathLike[str]]
) -> str:
    """The user-facing CLAUSE for an unknown-root cause (`SessionRoot.cause`).

    ONE SOURCE FOR EVERY CHANNEL THAT REPORTS THIS. Six surfaces compose their
    own sentence around this clause -- "Project CLAUDE.md skipped: ..." in
    session_init, the bootstrap prompt gate, the bootstrap marker writer and
    the memory sync, "realign_team: ..." on stderr, and archive_pin's
    "project memory root unknown: ..." refusal. Rendering here rather than at
    each site is what stops the cause text from reaching one channel and not
    its siblings, which is how the wrong wording survived four rounds.

    THE CAUSE IS A PARAMETER, NOT A SECOND LOOKUP. Every channel guards on a
    cause obtained from one `session_memory_root()` and renders from that
    SAME value, so the branch that decided to report and the text it reports
    describe one observation. A caller that guards on the root being None and
    then calls this separately has reintroduced the seam the helper exists to
    remove. There is deliberately no None return and no default text: a KNOWN
    root has no cause to report, and a caller in that state emits nothing
    rather than guessing a sentence.

    The anchor path is part of the clause: several worktrees can be in play and
    the user needs to know which one.

    A None anchor is the session whose cwd is gone with nothing naming it; it
    has only the MISSING clause, which names no path.

    Raises:
        ValueError: `cause` is not one of the four unknown causes ("ok", None
            or an unexpected value is a caller bug, never a sentence), or the
            anchor is None with a cause other than MISSING.
    """
    if project_dir is None:
        if cause == ROOT_CAUSE_MISSING:
            return "the working directory no longer exists"
        raise ValueError(f"no anchor for cause {cause!r}")
    anchor = f"{project_dir}"
    if cause == ROOT_CAUSE_MISSING:
        return f"the project directory {anchor} no longer exists"
    if cause == ROOT_CAUSE_UNVERIFIED:
        return (
            f"the project directory {anchor} exists but could not be verified "
            "as a checkout of a repository"
        )
    if cause == ROOT_CAUSE_UNANSWERABLE:
        return "git could not answer"
    if cause == ROOT_CAUSE_USER_GLOBAL:
        return (
            f"the project directory {anchor} holds the user-global CLAUDE.md, "
            "which every session loads, so it is not project memory"
        )
    raise ValueError(f"not an unknown-root cause: {cause!r}")


def unknown_root_status(
    subject: str,
    cause: str,
    anchor: Optional[str | os.PathLike[str]],
    consequence: str,
) -> str:
    """THE ONE TEMPLATE for a writer's skip line on an unknown root.

    `subject` names what was skipped ("Project CLAUDE.md"), `consequence`
    what that means for this invocation. The cause clause comes from
    `unknown_root_reason`, so the same ValueError guards a known cause.
    """
    return (
        f"{subject} skipped: {unknown_root_reason(cause, anchor)}, so the "
        f"project memory target is unknown; {consequence}"
    )


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
    A relative recorded path (`worktree.useRelativePaths`) is resolved against
    the admin dir that holds it, which is where git writes it relative to.
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
            # BYTES, not read_text: text mode performs UNIVERSAL-NEWLINE
            # translation, so a worktree named `wt\rname` would read as
            # `wt\nname`, the real worktree would be rejected, and a forged
            # directory at the newline spelling could pass the membership
            # check. os.fsdecode preserves every byte, and exactly one
            # trailing newline (git's record terminator) is dropped below.
            raw = (admin / "gitdir").read_bytes()
        except (OSError, ValueError):
            continue
        content = os.fsdecode(raw)
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
            # git writes a relative record (`worktree.useRelativePaths`)
            # relative to the admin dir that holds it, so it resolves there.
            gitfile = admin / gitfile
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
    enumeration. A timeout or a missing git on the first call answers None at
    once: the fallbacks would only wait on the same git again.
    """
    try:
        result = run_git(
            ["-C", str(checkout), "worktree", "list", "--porcelain", "-z"],
            timeout=5,
            text=False,
            env=git_env_without_location(),
        )
        if result is None:
            # `run_git` returns None only on a timeout or a missing git. Both
            # would repeat on the two sources below (two more rev-parse /
            # listing calls, 5 s each on a hang), so stop here.
            return None
        if result.returncode == 0:
            return [
                field[len("worktree "):]
                for field in _fsdecode(result.stdout or b"").split("\0")
                if field.startswith("worktree ")
            ]
    except (OSError, ValueError):
        # Fall through to the admin-dir records: a subprocess refusal is
        # "could not answer" here, and the next source may still answer.
        pass
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
    """Resolved worktree paths, or None when git could not ENUMERATE them.

    Includes a PRUNABLE worktree, whose directory is gone but whose record
    remains until `git worktree prune`. `git worktree remove` deletes the
    record as well, so a worktree removed that way is not listed.

    `resolve_against` is the directory a RELATIVE listed path resolves
    against; the default is the process cwd. DEFENCE IN DEPTH: every source
    `_worktree_list_paths` reads yields absolute paths (git prints them
    absolute, and the admin-record fallback resolves a relative record against
    its admin dir), so no path from git reaches this join today. The callers
    holding the main root pass it, so a relative path from some future source
    lands under that repository rather than under whatever the cwd is.

    The None/empty distinction is load-bearing for the classifier:
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
        except (OSError, RuntimeError):
            # A symlink loop raises RuntimeError; an unusable record is
            # skipped rather than aborting the listing.
            continue
    return listed


def _listed_worktrees(
    checkout: Path, resolve_against: Optional[Path] = None
) -> Set[Path]:
    """`_listed_worktrees_result`, with an unanswerable git reported as empty.

    For the callers that do not need the None/empty distinction (the scope
    guard); the classifier uses `_listed_worktrees_result`.
    """
    return _listed_worktrees_result(checkout, resolve_against) or set()


def _record_names_declaration(identity: Optional[dict], declared: Path) -> bool:
    """Whether the record was written for exactly this declaration."""
    return isinstance(identity, dict) and identity.get(
        "declared"
    ) == os.path.realpath(declared)


def _recorded_common_dir(identity: Optional[dict], declared: Path) -> Optional[str]:
    """The common dir a session record names, when it was written for exactly `declared`."""
    if not _record_names_declaration(identity, declared):
        return None
    common_dir = identity.get("common_dir")
    return common_dir if isinstance(common_dir, str) and os.path.isabs(common_dir) else None


RECORD_ABSENT = "absent"
RECORD_CORRUPT = "corrupt"
RECORD_CONTAINED = "contained"
RECORD_UNRELATED = "unrelated"


def record_resolution(
    declared: Path, identity: Optional[dict]
) -> tuple[str, Optional[Path]]:
    """Classify what a session record proves for `declared`, and the root.

    THE ONE DECISION EVERY RECORD CONSUMER USES. Four outcomes:

    absent     -- no record for this declaration. The caller's normal path.
    corrupt    -- a record for this declaration whose paths are unusable, or
                  whose candidate is not a self-reporting checkout: refuse,
                  never follow.
    contained  -- the record verifies AND the candidate checkout CONTAINS the
                  declaration. The only outcome that trusts the record.
    unrelated  -- a record for this declaration, naming a real checkout that
                  does NOT contain the declaration. NOT TRUSTED: a genuine
                  out-of-tree worktree and a forged redirect are identical
                  after `git worktree prune`, so the caller resolves WITHOUT
                  this record; a caller that owns a scope guard judges the
                  result itself, and this function only reports the status.

    CONTAINMENT IS THE TRUST TEST. At read time it cannot be proved that an
    out-of-tree worktree belonged to the recorded repository -- the listing
    entry and its admin dir are gone -- so trusting it would trust any
    repository the record names. `_record_worktree_identity` proves the
    relationship when it is still provable and refuses to write a record
    otherwise; this is the defence for records written before that check and
    for writers that bypass it.

    The `worktree` field is corroborated too: it must be the declaration
    itself (or an ancestor, for a subdirectory declaration) on real paths.
    """
    if not _record_names_declaration(identity, declared):
        return RECORD_ABSENT, None
    common_dir_str = _recorded_common_dir(identity, declared)
    if common_dir_str is None:
        return RECORD_CORRUPT, None
    recorded_worktree = identity.get("worktree")
    if not isinstance(recorded_worktree, str) or not os.path.isabs(recorded_worktree):
        return RECORD_CORRUPT, None
    try:
        declared_real = Path(os.path.realpath(declared))
        worktree_real = Path(os.path.realpath(recorded_worktree))
    except (OSError, ValueError):
        return RECORD_CORRUPT, None
    if declared_real != worktree_real and worktree_real not in declared_real.parents:
        return RECORD_CORRUPT, None
    try:
        common_dir = Path(common_dir_str)
        candidate = common_dir.parent.resolve()
        if not candidate.is_dir():
            return RECORD_CORRUPT, None
    except (OSError, RuntimeError):
        return RECORD_CORRUPT, None
    toplevel, ran = _raw_rev_parse(candidate, "--show-toplevel")
    if not ran or toplevel != candidate:
        return RECORD_CORRUPT, None
    common_again, ran = _raw_rev_parse(candidate, "--git-common-dir")
    if not ran or common_again is None:
        return RECORD_CORRUPT, None
    try:
        if common_again != common_dir.resolve():
            return RECORD_CORRUPT, None
    except (OSError, RuntimeError):
        return RECORD_CORRUPT, None
    if candidate == declared_real or candidate in declared_real.parents:
        return RECORD_CONTAINED, candidate
    return RECORD_UNRELATED, None


def recorded_main_root(
    declared: Path, identity: Optional[dict]
) -> Optional[Path]:
    """The MAIN root a record proves for `declared`, or None unless CONTAINED.

    Thin wrapper over `record_resolution` for callers that only need the root.
    A caller that must distinguish a corrupt record (refuse) from an unrelated
    one (fall through) uses the status directly.
    """
    status, root = record_resolution(declared, identity)
    return root if status == RECORD_CONTAINED else None


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
    home directory or into a user-global CLAUDE.md (the config root's, or the
    default profile's, compared by file identity): every project under the
    user loads those files, and a deleted project under a git-versioned
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
    except (OSError, RuntimeError):
        # RuntimeError: a symlink loop, on Pythons before 3.13.
        return False
    anchor = _nearest_existing_directory(declared)
    if anchor is None:
        return False
    if anchor != declared:
        try:
            if declared.resolve() in _listed_worktrees(resolved):
                return True
            # THE RECORD IS TRUSTED ONLY WHEN THE RECORDED CHECKOUT CONTAINS
            # THE DECLARATION (`record_resolution`'s containment). Without
            # that, a record naming ANY real repository admits a resolution
            # into it: with the cwd inside repository B and a forged record
            # naming B, the guard would admit B and would refuse without the
            # record -- the record doing the admitting, which is the redirect.
            status, _recorded_root = record_resolution(declared, worktree_identity)
            if status == RECORD_CONTAINED:
                recorded_common_dir = _recorded_common_dir(worktree_identity, declared)
                return _rev_parse_path(resolved, "--show-toplevel") == resolved and (
                    _rev_parse_path(resolved, "--git-common-dir")
                    == Path(recorded_common_dir)
                )
            if resolved == Path.home().resolve():
                return False
            if is_user_global_document(Path(claude_md)):
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
        # WRITE-TIME VERIFICATION, AND IT IS THE REASON THE CLASS GOES AWAY.
        # The relationship this record will later be trusted for is provable
        # NOW and gone after `git worktree prune`. Require the declaration's
        # own toplevel to be a worktree git LISTS for the candidate repository,
        # and require the declaration to BE that worktree or inside it: a
        # forged gitfile names a worktree the asking directory does not belong
        # to, and it is not listed for that repository either. Refusing the
        # write leaves the session on the no-record path, which is the safe
        # fall-through rather than a redirect.
        candidate = common_dir.parent
        listed = _listed_worktrees_result(candidate, resolve_against=candidate)
        if listed is None or worktree not in listed:
            return
        declared_real = Path(os.path.realpath(project_dir))
        if declared_real != worktree and worktree not in declared_real.parents:
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


# ---------------------------------------------------------------------------
# The session's anchor and project-memory root: ONE rule for every site.
# ---------------------------------------------------------------------------

_CONTEXT_FILE = "pact-session-context.json"
_DISCOVERY_UNSET = object()
# Cached RESULT OF THE FILESYSTEM LOOKUP only, keyed on the session id it was
# resolved for. The guards in `_discover_context_record` run on every call, so
# a cached answer never outlives the conditions that permitted it.
_discovered_record: object = _DISCOVERY_UNSET
_discovered_record_for_env: Optional[str] = None


def _discover_context_record() -> dict:
    """Return this session's parsed context record, discovered from the env id.

    A test process is refused before any read, and no env id means no answer;
    both guards run on every call. Only the filesystem lookup is cached.
    Returns {} whenever the answer is not unambiguous.
    """
    # Same guard pair as pact_session._discover_session_id -- keep in sync.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {}
    env_session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not env_session:
        return {}
    global _discovered_record, _discovered_record_for_env
    if (
        _discovered_record is _DISCOVERY_UNSET
        or _discovered_record_for_env != env_session
    ):
        _discovered_record = _session_record_on_disk(env_session, _CONTEXT_FILE)
        _discovered_record_for_env = env_session
    record = _discovered_record
    return record if isinstance(record, dict) else {}


def recorded_project_dir() -> str:
    """This session's recorded project_dir, or "" when unavailable.

    The session-record rung of the anchor: below CLAUDE_PROJECT_DIR (a present
    declaration wins), above the process cwd (in a multi-repo workspace the
    cwd's git root can be the wrong scope; the record is the session's own
    identity, written by session_init at SessionStart).

    Existence is NOT checked: the value is an anchor, and a recorded directory
    deleted since is the classifier's MISSING case. A non-absolute value is
    rejected (an old "." record would alias the reader's cwd). The record's
    session_id must equal the env id that located it; an ABSENT field is
    accepted, because legacy records predate it and the locating glob already
    matched the id's directory.

    Returns "" on every failure. Never raises.
    """
    record = _discover_context_record()
    found = record.get("project_dir", "")
    if not isinstance(found, str) or not os.path.isabs(found):
        return ""
    record_session = record.get("session_id")
    if record_session is not None and (
        not isinstance(record_session, str)
        or record_session != os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    ):
        return ""
    return found


class SessionRoot(NamedTuple):
    """One observation of where this session's project memory lives.

    `anchor` is the directory the session resolved from (never resolved here;
    None when the cwd is gone and nothing names it), `declared` says it came
    from CLAUDE_PROJECT_DIR, and `root` is the
    project-memory root, None exactly when `cause` names why it is unknown
    (ROOT_CAUSE_MISSING, ROOT_CAUSE_UNVERIFIED, ROOT_CAUSE_UNANSWERABLE or
    ROOT_CAUSE_USER_GLOBAL).
    """

    anchor: Optional[Path]
    declared: bool
    root: Optional[Path]
    cause: Optional[str]


def session_anchor() -> tuple[Optional[Path], bool]:
    """The session's anchor directory and whether it was declared.

    CLAUDE_PROJECT_DIR when non-empty, else the session record's project_dir,
    else the process cwd. A deleted cwd cannot be read: the shell's PWD names
    it when PWD is absolute AND gone, and the classifier answers MISSING.
    Otherwise (no PWD, or a PWD naming a directory that still exists, which is
    then not this process's cwd) the anchor is None, which
    `session_memory_root` reports as MISSING without classifying anything.
    """
    declared = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if declared:
        return Path(declared), True
    recorded = recorded_project_dir()
    if recorded:
        return Path(recorded), False
    try:
        return Path.cwd(), False
    except OSError:
        pwd = os.environ.get("PWD", "")
        if os.path.isabs(pwd) and not os.path.isdir(pwd):
            return Path(pwd), False
        return None, False


def memory_root_of(
    directory: str | os.PathLike[str],
) -> tuple[Optional[Path], Optional[str]]:
    """The pure classifier: `(root, None)` for a known root, `(None, cause)`.

    It never consults the session record, which belongs to a session and not
    to a directory; `session_memory_root` adds the record's recovery.

    A known root whose canonical document is the user-global CLAUDE.md is
    reported as ROOT_CAUSE_USER_GLOBAL: reading or writing it as project memory
    would change the file every session loads. Unable to tell counts as
    user-global (fail closed).
    """
    root, cause = _project_memory_root_cause(directory)
    if cause != ROOT_CAUSE_OK:
        return None, cause
    if names_user_global_document(Path(root)):
        return None, ROOT_CAUSE_USER_GLOBAL
    return root, None


def session_memory_root() -> SessionRoot:
    """This session's anchor, project-memory root and unknown cause.

    The anchor follows `session_anchor`. When the root is unknown because the
    anchor is MISSING or git is UNANSWERABLE, the session's worktree record
    may recover it, but ONLY when it verifies and its checkout CONTAINS the
    anchor. An UNVERIFIED root is never recovered: there the present answer
    contradicts the record, and a CORRUPT or UNRELATED record keeps the cause.
    """
    anchor, declared = session_anchor()
    if anchor is None:
        # The cwd is gone and nothing names it: no directory to classify, and
        # no record can be for it.
        return SessionRoot(None, declared, None, ROOT_CAUSE_MISSING)
    root, cause = memory_root_of(anchor)
    if cause in (ROOT_CAUSE_MISSING, ROOT_CAUSE_UNANSWERABLE):
        status, recovered = record_resolution(
            anchor, get_worktree_identity_from_session_record()
        )
        if status == RECORD_CONTAINED and recovered is not None:
            return SessionRoot(anchor, declared, recovered, None)
    return SessionRoot(anchor, declared, root, cause)


def user_global_documents() -> Tuple[Path, ...]:
    """The CLAUDE.md files Claude Code loads into EVERY session: the active
    config root's, and the default profile's `~/.claude/CLAUDE.md`, which a
    profile switch makes global again. Deduplicated by same_path. A source
    that raises is skipped; an empty tuple means neither could be computed.
    """
    found: List[Path] = []
    for source in (
        lambda: get_claude_config_dir() / "CLAUDE.md",
        lambda: Path.home() / ".claude" / "CLAUDE.md",
    ):
        try:
            path = source()
        except (OSError, RuntimeError, KeyError, ValueError):
            # RuntimeError / KeyError: Path.home() with no resolvable home.
            continue
        if not any(same_path(path, kept) for kept in found):
            found.append(path)
    return tuple(found)


def is_user_global_document(path: Path) -> bool:
    """True when `path` is, or would be once created, a user-global CLAUDE.md.

    Compared by file identity (hardlinks, case variants on a case-insensitive
    volume). Unable to tell reads as True: unlike a gate, which allows on
    doubt, this check refuses on doubt.
    """
    globals_ = user_global_documents()
    if not globals_:
        return True
    return any(_same_file(Path(path), g) is not False for g in globals_)


def names_user_global_document(root: Path) -> bool:
    """True when either canonical document under `root` is user-global."""
    root = Path(root)
    return any(
        is_user_global_document(candidate)
        for candidate in (root / ".claude" / "CLAUDE.md", root / "CLAUDE.md")
    )


def enclosing_main_checkout(root: Path) -> Optional[Path]:
    """The main checkout that strictly contains a KNOWN root, or None.

    Applies only when `root` is a subdirectory of a main checkout (not a
    linked worktree): its toplevel T differs from it, its git dir equals its
    common dir, T reports the SAME common dir (a `core.worktree` redirect
    fails this), and T classifies as its own known root. Never returns a
    directory whose canonical document is the user-global CLAUDE.md (a
    dotfiles-git home). Any git failure gives None.
    """
    toplevel, ran = _raw_rev_parse(Path(root), "--show-toplevel")
    if not ran or toplevel is None:
        return None
    try:
        root_real = Path(root).resolve()
    except (OSError, RuntimeError):
        return None
    if toplevel == root_real or toplevel not in root_real.parents:
        return None
    git_dir, ran = _raw_rev_parse(Path(root), "--git-dir")
    if not ran or git_dir is None:
        return None
    common_dir, ran = _raw_rev_parse(Path(root), "--git-common-dir")
    if not ran or common_dir is None or git_dir != common_dir:
        return None
    toplevel_common, ran = _raw_rev_parse(toplevel, "--git-common-dir")
    if not ran or toplevel_common != common_dir:
        return None
    if memory_root_of(toplevel) != (toplevel, None):
        return None
    if names_user_global_document(toplevel):
        return None
    return toplevel
