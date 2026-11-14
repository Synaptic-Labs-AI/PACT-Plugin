"""Only a lead writes pact-session-context.json.

session_init treats that file's presence in a session's own directory as proof
that a lead ran there: a lead resumed (not forked) without `--agent` is
recovered from it. So a writer that runs for a teammate or a no-role session
would let that session be treated as the lead. Two pins keep every writer
lead-gated:

- CENSUS, over the shipped Python under hooks/, skills/, scripts/, bin/ and
  telegram/ (plus a text scan of their non-Python, non-markdown files). Each
  scan counts every occurrence per site and is compared with a known table of
  counts, so a new site, or a new occurrence at a known site, fails with
  instructions: references to the write APIs (a call, a functools.partial or
  an assignment, through an import alias too); references to
  build_context_cache, _get_context_file_path and pact_session's
  _context_file_path, each of which returns a path a writer could use without
  naming the file; code strings naming the file; uses of the context module's
  path global, as an attribute outside that module and as a bare name inside
  it; direct write calls (write_text, write_bytes, touch, a write-mode open,
  os.open) in any function the other scans place at the file, so a write
  through a variable that already holds the path is seen; and relocation or
  link calls (os.symlink, os.link, Path.symlink_to, Path.hardlink_to: the
  recovery check's is_file() follows a link), through a module alias or a
  from-import too, in functions that derive a session path. Each scan has a
  seeded positive control, and every directory of shipped code must be a scan
  root. Not covered: markdown instruction files (all of them only read the
  file today); a relocation whose path reaches the function under a name that
  does not mark it as a session path; a path handed as an argument to a helper
  that writes it, outside session_init.main (which the behaviour arms drive);
  and a filename split across string parts, which only deliberate construction
  produces.
- BEHAVIOUR: each known writer, driven for a non-lead frame, writes nothing,
  while a lead control on the same setup writes the file.
"""
import ast
import functools
import io
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

import bootstrap_marker_writer  # noqa: E402
import session_init  # noqa: E402
import shared.pact_context as pact_context  # noqa: E402
from fixtures.tracked_files import tracked_files as _shared_tracked_files
from fixtures.tracked_files import tracked_glob as _shared_tracked_glob
from shared.pact_context import _build_session_path, project_slug  # noqa: E402

_PLUGIN = Path(__file__).resolve().parent.parent
_ROOTS = ("hooks", "skills", "scripts", "bin", "telegram")
FILENAME = "pact-session-context.json"
_WRITE_APIS = frozenset({"persist_context", "write_context"})
_PATH_BUILDER = "build_context_cache"
# Functions that return the context file's path: pact_context's accessor, and
# pact_session's, which builds the same path for the memory scripts.
_PATH_ACCESSORS = frozenset({"_get_context_file_path", "_context_file_path"})
_TRACKED_APIS = _WRITE_APIS | {_PATH_BUILDER} | _PATH_ACCESSORS
_CONTEXT_MODULE = "hooks/shared/pact_context.py"


def _tracked_glob(repo: Path, pattern: str, known_member: str) -> list[Path]:
    """Keep deleted index entries visible to failure assertions."""
    return _shared_tracked_glob(
        repo, pattern, known_member, keep_deleted=True
    )


# Relocation and link calls: module functions, Path's link methods, Path.rename,
# and Path.replace, which takes one argument where str.replace takes two.
_MODULE_RELOCATIONS = frozenset({
    ("os", "rename"), ("os", "replace"), ("os", "renames"),
    ("os", "symlink"), ("os", "link"),
    ("shutil", "move"), ("shutil", "copytree"), ("shutil", "copy"),
    ("shutil", "copy2"), ("shutil", "copyfile"),
})
_PATH_LINKS = frozenset({"symlink_to", "hardlink_to"})
# What marks a function as deriving a session path.
_SESSION_PATH_CALLS = frozenset({
    "build_session_path", "_build_session_path", "project_slug",
    "get_session_dir", "reconstruct_session_dir",
})
_SESSION_PATH_NAMES = ("session_dir", "session_path", "session_folder")

# Write calls on a path. touch() counts: the recovery check reads is_file(), so
# an empty file is enough.
_WRITE_METHODS = frozenset({"write_text", "write_bytes", "touch"})
# A string argument that is an open() mode, rather than a path.
_OPEN_MODE = re.compile(r"[rwxabt+]{1,4}")

# (file, enclosing function, write API referenced): count. The three
# lead-gated writers, the realign command, plus write_context itself, which
# is persist_context behind a path builder.
KNOWN_WRITE_CALLS = Counter({
    ("hooks/session_init.py", "main", "persist_context"): 1,
    ("hooks/shared/pact_context.py", "heal_context_if_missing", "write_context"): 1,
    ("hooks/bootstrap_marker_writer.py", "_write_back_aligned_team_name", "write_context"): 1,
    # The realign command: refuses unless a lead context already exists for
    # $CLAUDE_CODE_SESSION_ID, so it only rewrites a lead's own file.
    ("hooks/shared/realign_team.py", "realign_team", "write_context"): 1,
    ("hooks/shared/pact_context.py", "write_context", "persist_context"): 1,
    # The harness-stamp writer (non-TTY posture): internally lead-gated on the
    # hook frame (is_lead on agent_type — same gate as persist_context's
    # frame_is_lead caller), refusing non-lead frames before touching disk.
    ("hooks/shared/pact_context.py", "set_harness_stamp", "persist_context"): 1,
})

# (file, enclosing function): count of references to build_context_cache, which
# returns the context file's path. Both call it and pass the path straight to
# persist_context.
KNOWN_PATH_BUILDER_CALLS = Counter({
    ("hooks/session_init.py", "main"): 1,
    ("hooks/shared/pact_context.py", "write_context"): 1,
})

# (file, enclosing function): count of references to a path accessor. Each
# reads the file.
KNOWN_PATH_ACCESSOR_REFS = Counter({
    ("hooks/shared/pact_context.py", "get_pact_context"): 1,
    ("skills/pact-memory/scripts/pact_session.py", "get_session_id_from_context_file"): 1,
})

# (file, enclosing function): count of bare-name uses of _context_path inside
# the context module, read or assigned. Each sets, clears, returns or reads it.
KNOWN_MODULE_PATH_USES = Counter({
    ("hooks/shared/pact_context.py", "<module>"): 1,
    ("hooks/shared/pact_context.py", "_get_context_file_path"): 1,
    ("hooks/shared/pact_context.py", "build_context_cache"): 3,
    ("hooks/shared/pact_context.py", "describe_context_failure"): 3,
    ("hooks/shared/pact_context.py", "heal_context_if_missing"): 3,
    ("hooks/shared/pact_context.py", "init"): 2,
    ("hooks/shared/pact_context.py", "is_initialized"): 1,
    ("hooks/shared/pact_context.py", "reset_for_tests"): 1,
    # set_harness_stamp: reads the recorded file, hands the path to
    # persist_context — three bare-name uses (None-check, read, pass-through).
    ("hooks/shared/pact_context.py", "set_harness_stamp"): 3,
})

# (file, enclosing function): count of code strings naming the file. Each one
# builds the path for a READ, builds the path persist_context writes
# (build_context_cache), or is message text.
KNOWN_NAME_SITES = Counter({
    ("hooks/dispatch_gate.py", "<module>"): 2,                   # message text
    ("hooks/dispatch_gate.py", "evaluate_dispatch"): 3,          # message text
    ("hooks/session_init.py", "_saved_context"): 1,              # read
    ("hooks/session_init.py", "_lead_context_persisted"): 1,     # existence read
    ("hooks/shared/pact_context.py", "build_context_cache"): 1,  # the writers' path
    ("hooks/shared/pact_context.py", "persist_context"): 1,  # the writer's own temp-file prefix
    ("hooks/shared/pact_context.py", "init"): 1,                 # read path
    ("hooks/shared/pact_context.py", "_clear_carryover_team"): 1,  # read (earlier sessions' contexts)
    ("hooks/shared/pact_context.py", "_project_session_records"): 1,  # read (this project's session contexts)
    ("hooks/shared/pact_harvest.py", "main"): 2,                 # CLI help text
    ("hooks/shared/session_registry.py", "_lead_context_exists_for"): 1,  # existence read
    ("hooks/shared/session_resume.py", "_saved_team_of"): 1,         # read
    ("skills/pact-memory/scripts/pact_session.py", "_context_file_path"): 1,       # read
    ("skills/pact-memory/scripts/pact_session.py", "_context_record_on_disk"): 1,  # read
    ("skills/pact-memory/scripts/memory_api.py", "_legacy_session_evidence"): 1,  # read (legacy-admission evidence)
    ("hooks/session_end.py", "_pruned_session_line"): 1,  # read (prune-ledger evidence before a reap)
    ("hooks/shared/project_scope.py", "<module>"): 1,  # read (the record rung's _CONTEXT_FILE)
})

# (file, function, call): count of relocations in a function that derives a
# session path. None can put a context file under a session id that lacks one.
KNOWN_SESSION_RELOCATIONS = Counter({
    # Moves a session's whole dir from the old slug to the resolved one. It
    # keeps the session id, so it carries only a file already written under it.
    ("hooks/session_init.py", "_adopt_old_slug_session_dir", "os.rename"): 1,
    # Moves the compact summary to an archive name inside the session's own dir.
    ("hooks/session_init.py", "_archive_own_dir_stale_summary", "Path.replace"): 1,
    # The bootstrap marker's atomic temp-file write.
    ("hooks/bootstrap_marker_writer.py", "_write_marker", "os.replace"): 1,
    # The teammate registry's atomic rewrite under pact-sessions/.
    ("hooks/session_end.py", "_prune_registry_dead_teams", "os.replace"): 1,
})

# (file, function, call): count of direct write calls in a function that any
# other scan places at the file. None today: every such function reads the
# file, names it, or writes it through the tracked write APIs.
KNOWN_DIRECT_WRITES = Counter()

_INSTRUCTION = (
    "session_init treats pact-session-context.json in a session's own dir as "
    "proof that a lead ran there. If the new site WRITES or MOVES the file, gate "
    "it on the lead (frame_is_lead / pact_context.is_lead) and add a behaviour "
    "arm below, then add the site to the known table. If it only reads, names or "
    "moves something else, add it, or raise its count, in the known table with "
    "a comment saying so."
)


def _differs(found, known):
    """The failure text for a census result that differs from its known table."""
    return (
        f"new: {sorted((found - known).items())}; "
        f"gone: {sorted((known - found).items())}. {_INSTRUCTION}"
    )


def _own_nodes(function):
    """The nodes of ``function`` itself, not of functions or classes nested in it."""
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(node))


def _derives_a_session_path(function):
    names = [a.arg for a in function.args.args + function.args.kwonlyargs]
    for node in _own_nodes(function):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if called in _SESSION_PATH_CALLS:
                return True
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "pact-sessions" in node.value:
            return True
    return any(marker in name for name in names for marker in _SESSION_PATH_NAMES)


# ANCHOR of the name census: the context file's name or a piece of it, in
# any string constant or f-string piece outside a docstring (a docstring
# counts too in a module that reads one, `_reads_a_docstring`). `pact-sessions`
# (the sessions root) is another word, and `context.json` counts only as the
# file's own tail (`context.json`, `-context.json`, `session-context.json`),
# not inside another name such as `planted-context.json`.
_NAME_ANCHOR = re.compile(
    r"pact-session(?![s\w])|session-context|(?:^-?|session-)context\.json"
)
# A complete literal file name in the sessions folder other than the context
# file: `pact-session-<word>.<ext>`, no placeholder.
_OTHER_SESSION_FILE = re.compile(r"pact-session-(?!context\b)[a-z0-9_]+\.[a-z]+\b")


def _reads_a_docstring(tree):
    """Whether `tree` can read a docstring as data: a `__doc__` name or
    attribute, a `"__doc__"` string (`getattr`, `vars`), or `getdoc`. A
    docstring is exempt only where nothing reads one, since a read turns it
    into a value the code can use."""
    return any(
        (isinstance(node, ast.Name) and node.id in ("__doc__", "getdoc"))
        or (isinstance(node, ast.Attribute) and node.attr in ("__doc__", "getdoc"))
        or (isinstance(node, ast.Constant) and node.value == "__doc__")
        for node in ast.walk(tree)
    )


def _unreadable_name(text):
    """Whether `text` holds an anchor occurrence the census cannot judge.

    Called for a constant `_names_the_file` did not count. An occurrence that
    is part of a complete other session-file name is read as benign; every
    other occurrence (a `%`, `{}` or `$` template, a piece such as
    `"context.json"` or `"pact-session-ctx"` that a join, a replace or a `+`
    completes) is unreadable, so a context-file name the census cannot see
    fails the census instead of escaping it."""
    for match in _NAME_ANCHOR.finditer(text):
        if match.group().startswith("pact-session") and _OTHER_SESSION_FILE.match(text, match.start()):
            continue
        return True
    return False


def _names_the_file(text):
    """A string piece that names the context file, or the piece of a name
    assembled from several (`"pact-session-" + "context.json"`, an f-string
    field after `pact-session-`, a temp name built on `pact-session-context`),
    so an assembled name counts as a name site rather than escaping the
    census."""
    return (
        FILENAME in text
        or "session-context" in text
        or text.endswith(("pact-session-", "pact-session"))
    )


def _write_call(call):
    """The kind of write ``call`` makes on a path, or None."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in _WRITE_METHODS:
        return name
    if name != "open":
        return None
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "os":
        return "os.open"
    for arg in [*call.args, *(kw.value for kw in call.keywords if kw.arg == "mode")]:
        if (
            isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            and _OPEN_MODE.fullmatch(arg.value) and set(arg.value) & set("wax+")
        ):
            return "open"
    return None


class _Census(ast.NodeVisitor):
    """Count tracked-API references, code strings naming the file, uses of the
    context module's path global, session-path relocations, and direct write
    calls, keyed by (file, enclosing function)."""

    def __init__(self, rel, tree):
        self.rel = rel
        self.stack = []
        self.docstrings = set()
        self.reads_docstrings = _reads_a_docstring(tree)
        self.api_refs = Counter()
        self.name_sites = Counter()
        self.unreadable_names = []
        self.path_refs = Counter()
        self.module_path_uses = Counter()
        self.relocations = Counter()
        self.writes = Counter()
        # `from ... import persist_context as _persist` binds a second name,
        # and `import shutil as sh` or `from os import rename` binds a
        # relocation. Resolved per file, which errs toward flagging.
        self.aliases = {api: api for api in _TRACKED_APIS}
        self.modules = {}
        self.functions = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.modules[alias.asname] = alias.name
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name in _TRACKED_APIS:
                        self.aliases[bound] = alias.name
                    if (node.module, alias.name) in _MODULE_RELOCATIONS:
                        self.functions[bound] = f"{node.module}.{alias.name}"

    def _where(self):
        return ".".join(self.stack) or "<module>"

    def _note_docstring(self, node):
        body = getattr(node, "body", [])
        if self.reads_docstrings:
            return
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            self.docstrings.add(id(body[0].value))

    def _note_writes(self, scope):
        for inner in _own_nodes(scope):
            kind = isinstance(inner, ast.Call) and _write_call(inner)
            if kind:
                self.writes[(self.rel, self._where(), kind)] += 1

    def visit_Module(self, node):
        self._note_docstring(node)
        self._note_writes(node)
        self.generic_visit(node)

    def _relocation(self, call):
        func = call.func
        if isinstance(func, ast.Name):
            return self.functions.get(func.id)
        if not isinstance(func, ast.Attribute):
            return None
        if isinstance(func.value, ast.Name):
            module = self.modules.get(func.value.id, func.value.id)
            if (module, func.attr) in _MODULE_RELOCATIONS:
                return f"{module}.{func.attr}"
        if func.attr in _PATH_LINKS:
            return f"Path.{func.attr}"
        if func.attr == "rename" and len(call.args) == 1:
            return "Path.rename"
        if func.attr == "replace" and len(call.args) == 1 and not call.keywords:
            return "Path.replace"
        return None

    def _visit_scope(self, node):
        self._note_docstring(node)
        self.stack.append(node.name)
        if not isinstance(node, ast.ClassDef):
            self._note_writes(node)
            if _derives_a_session_path(node):
                for inner in _own_nodes(node):
                    moved = isinstance(inner, ast.Call) and self._relocation(inner)
                    if moved:
                        self.relocations[(self.rel, self._where(), moved)] += 1
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _visit_scope

    # A reference, not only a call: a call's func is a Load reference too, and
    # functools.partial(write_context) or `p = pact_context.persist_context`
    # hands the API on without calling it here.
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in self.aliases:
            self.api_refs[(self.rel, self._where(), self.aliases[node.id])] += 1
        if node.id == "_context_path" and self.rel == _CONTEXT_MODULE:
            self.module_path_uses[(self.rel, self._where())] += 1

    def visit_Constant(self, node):
        if not isinstance(node.value, str) or id(node) in self.docstrings:
            return
        if _names_the_file(node.value):
            self.name_sites[(self.rel, self._where())] += 1
        elif _unreadable_name(node.value):
            self.unreadable_names.append(f"{self.rel}:{node.lineno}: {node.value[:60]!r}")

    def visit_Attribute(self, node):
        if isinstance(node.ctx, ast.Load) and node.attr in self.aliases:
            self.api_refs[(self.rel, self._where(), self.aliases[node.attr])] += 1
        if node.attr == "_context_path" and self.rel != _CONTEXT_MODULE:
            self.path_refs[(self.rel, self._where())] += 1
        self.generic_visit(node)


def _shipped(root, pattern):
    """Shipped files under ``root``. The tests/ and __pycache__ exclusions read
    the path BELOW ``root``, so a checkout under a directory named tests is
    still scanned. Repository-backed inputs exclude untracked files; synthetic
    and gitless inputs keep their filesystem population and may be empty.
    """
    for path in _shared_tracked_files(root.parent, root.name, allow_empty=True):
        relative = path.relative_to(root)
        if (
            relative.match(pattern)
            and "__pycache__" not in relative.parts
            and "tests" not in relative.parts
            and not path.name.startswith("test_")
        ):
            yield path


def test_shipped_census_drops_a_locally_deleted_file(tmp_path):
    """The text census reads existing files, so a deleted tracked file is dropped.

    This population is a content consumer: it already enumerates with the
    default ``keep_deleted`` false, so a local deletion cannot reach the
    ``read_text`` below. Pinning it keeps a future ``keep_deleted=True`` from
    turning one deletion into a FileNotFoundError from the census reader.
    """
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    hooks = plugin / "hooks"
    hooks.mkdir(parents=True)
    known = hooks / "known.py"
    known.write_text("KNOWN = 1\n", encoding="utf-8")
    doomed = hooks / "doomed.py"
    doomed.write_text("DOOMED = 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--", "pact-plugin"], cwd=repo, check=True)
    doomed.unlink()  # the index entry stays; the disk entry is gone

    files = list(_shipped(hooks, "*.py"))

    assert known in files
    assert doomed not in files, (
        f"the deleted tracked {doomed.name} stayed in the text census "
        "population; the census reader would raise FileNotFoundError instead "
        "of a named missing-file failure")
    for path in files:  # the population is readable; that is the contract
        path.read_text(encoding="utf-8")


def _python_census(plugin):
    """Return a dict of the scan's results over ``plugin``'s scan roots."""
    found = {
        "scanned": {root: 0 for root in _ROOTS}, "write_calls": Counter(),
        "path_builder_calls": Counter(), "path_accessor_refs": Counter(),
        "name_sites": Counter(), "unreadable_names": [], "path_refs": Counter(),
        "module_path_uses": Counter(), "relocations": Counter(),
        "direct_writes": Counter(),
    }
    for root in _ROOTS:
        for path in _shipped(plugin / root, "*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            census = _Census(path.relative_to(plugin).as_posix(), tree)
            census.visit(tree)
            for (rel, where, api), count in census.api_refs.items():
                if api in _WRITE_APIS:
                    found["write_calls"][(rel, where, api)] += count
                elif api == _PATH_BUILDER:
                    found["path_builder_calls"][(rel, where)] += count
                else:
                    found["path_accessor_refs"][(rel, where)] += count
            found["name_sites"] += census.name_sites
            found["unreadable_names"] += census.unreadable_names
            found["path_refs"] += census.path_refs
            found["module_path_uses"] += census.module_path_uses
            found["relocations"] += census.relocations
            # Direct writes count only in a function another scan places at
            # the file.
            at_the_file = {key[:2] for key in census.api_refs} | set(census.name_sites) \
                | set(census.path_refs) | set(census.module_path_uses)
            for (rel, where, kind), count in census.writes.items():
                if (rel, where) in at_the_file:
                    found["direct_writes"][(rel, where, kind)] += count
            found["scanned"][root] += 1
    return found


def _text_census(plugin):
    """Return ({root: files scanned}, a Counter of non-Python, non-markdown
    files naming the file, by occurrence)."""
    hits, scanned = Counter(), {root: 0 for root in _ROOTS}
    for root in _ROOTS:
        for path in _shipped(plugin / root, "*"):
            if path.suffix in (".py", ".md", ".pyc"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned[root] += 1
            if FILENAME in text:
                hits[path.relative_to(plugin).as_posix()] += text.count(FILENAME)
    return scanned, hits


@functools.lru_cache(maxsize=1)
def _shipped_python_census():
    """The Python census over this plugin, scanned once per test process."""
    return _python_census(_PLUGIN)


@functools.lru_cache(maxsize=1)
def _shipped_text_census():
    """The text census over this plugin, scanned once per test process."""
    return _text_census(_PLUGIN)


def _code_bearing_dirs(plugin):
    """Top-level plugin dirs, other than tests/, holding tracked Python or an executable."""
    dirs = set()
    tracked = _tracked_glob(
        plugin.parent, f"{plugin.name}/**/*", f"{plugin.name}/hooks/session_init.py",
    )
    for path in tracked:
        parts = path.relative_to(plugin).parts
        if len(parts) < 2:
            continue
        child = parts[0]
        if child in ("tests", "__pycache__") or child.startswith(".") or "__pycache__" in parts:
            continue
        if path.suffix == ".py" or os.access(path, os.X_OK):
            dirs.add(child)
    return dirs


class TestTrackedPopulation:
    def test_untracked_is_excluded_and_deleted_tracked_stays_visible(self, tmp_path):
        repo = tmp_path / "repo"
        tracked = repo / "pact-plugin" / "hooks" / "tracked.py"
        tracked.parent.mkdir(parents=True)
        tracked.write_text("tracked")
        (tracked.parent / "untracked.py").write_text("untracked")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "--", "pact-plugin/hooks/tracked.py"], cwd=repo, check=True)
        pattern = "pact-plugin/hooks/*.py"
        assert _tracked_glob(repo, pattern, "pact-plugin/hooks/tracked.py") == [tracked]
        tracked.unlink()
        assert _tracked_glob(repo, pattern, "pact-plugin/hooks/tracked.py") == [tracked]

    def test_empty_and_wrong_roots_refuse(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(AssertionError, match="empty tracked population"):
            _tracked_glob(repo, "pact-plugin/hooks/*.py", "pact-plugin/hooks/tracked.py")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        with pytest.raises(AssertionError, match="empty tracked population"):
            _tracked_glob(repo, "pact-plugin/hooks/*.py", "pact-plugin/hooks/tracked.py")


class TestCensus:
    def test_the_python_census_finds_every_seeded_writer_shape(self, tmp_path):
        """Positive control: one seeded hook module per writer shape, each found
        and counted."""
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "seeded.py").write_text(
            "import functools\n"
            "import os\n"
            "import shutil as sh\n"
            "from os import rename\n"
            "from pathlib import Path\n"
            "from shutil import copytree\n"
            "import shared.pact_context as pact_context\n"
            "from shared.pact_context import persist_context as _persist\n"
            "from pact_session import _context_file_path\n\n"
            "def via_api():\n"
            "    pact_context.write_context('t', 's', 'p', 'r')\n\n"
            "def via_alias(target, context):\n"
            "    _persist(target, context)\n\n"
            "def twice(target, context):\n"
            "    _persist(target, context)\n"
            "    _persist(target, context)\n\n"
            "def via_partial(target):\n"
            "    return functools.partial(_persist, target)\n\n"
            "def via_assignment(target, context):\n"
            "    write = pact_context.persist_context\n"
            "    write(target, context)\n\n"
            "def via_builder():\n"
            "    target, context = pact_context.build_context_cache('t', 's', 'p', 'r')\n"
            "    target.write_text(str(context))\n\n"
            "def via_accessor():\n"
            "    pact_context._get_context_file_path().write_text('{}')\n\n"
            "def via_session_accessor(session_id, project_dir):\n"
            "    _context_file_path(session_id, project_dir).touch()\n\n"
            "def direct(session_dir):\n"
            "    (Path(session_dir) / f'pact-session-context.json').write_text('{}')\n\n"
            "def named_twice(session_dir):\n"
            "    old = Path(session_dir) / 'pact-session-context.json'\n"
            "    return old, Path(session_dir) / 'pact-session-context.json'\n\n"
            "def through_a_variable(session_dir):\n"
            "    path = Path(session_dir) / 'pact-session-context.json'\n"
            "    path.read_text()\n"
            "    with open(path, 'w') as handle:\n"
            "        handle.write('{}')\n\n"
            "def via_global():\n"
            "    pact_context._context_path.write_text('{}')\n\n"
            "def relocated(old_session_dir, new_session_dir):\n"
            "    os.rename(old_session_dir, new_session_dir)\n\n"
            "def from_import_rename(old_session_dir, new_session_dir):\n"
            "    rename(old_session_dir, new_session_dir)\n\n"
            "def from_import_copy(old_session_dir, new_session_dir):\n"
            "    copytree(old_session_dir, new_session_dir)\n\n"
            "def module_alias_move(old_session_dir, new_session_dir):\n"
            "    sh.move(old_session_dir, new_session_dir)\n\n"
            "def symlinked(old_session_dir, new_session_dir):\n"
            "    os.symlink(old_session_dir, new_session_dir)\n\n"
            "def hard_linked(old_session_dir, new_session_dir):\n"
            "    os.link(old_session_dir, new_session_dir)\n\n"
            "def path_symlinked(old_session_dir, new_session_dir):\n"
            "    Path(new_session_dir).symlink_to(old_session_dir)\n\n"
            "def path_hard_linked(old_session_dir, new_session_dir):\n"
            "    Path(new_session_dir).hardlink_to(old_session_dir)\n"
        )
        found = _python_census(tmp_path)
        assert found["scanned"]["hooks"] == 1
        assert found["write_calls"] == Counter({
            ("hooks/seeded.py", "via_api", "write_context"): 1,
            ("hooks/seeded.py", "via_alias", "persist_context"): 1,
            ("hooks/seeded.py", "twice", "persist_context"): 2,
            ("hooks/seeded.py", "via_partial", "persist_context"): 1,
            ("hooks/seeded.py", "via_assignment", "persist_context"): 1,
        })
        assert found["path_builder_calls"] == Counter({("hooks/seeded.py", "via_builder"): 1})
        assert found["path_accessor_refs"] == Counter({
            ("hooks/seeded.py", "via_accessor"): 1,
            ("hooks/seeded.py", "via_session_accessor"): 1,
        })
        assert found["name_sites"] == Counter({
            ("hooks/seeded.py", "direct"): 1,
            ("hooks/seeded.py", "named_twice"): 2,
            ("hooks/seeded.py", "through_a_variable"): 1,
        })
        assert found["path_refs"] == Counter({("hooks/seeded.py", "via_global"): 1})
        assert found["direct_writes"] == Counter({
            ("hooks/seeded.py", "via_builder", "write_text"): 1,
            ("hooks/seeded.py", "via_accessor", "write_text"): 1,
            ("hooks/seeded.py", "via_session_accessor", "touch"): 1,
            ("hooks/seeded.py", "direct", "write_text"): 1,
            ("hooks/seeded.py", "through_a_variable", "open"): 1,
            ("hooks/seeded.py", "via_global", "write_text"): 1,
        })
        assert found["relocations"] == Counter({
            ("hooks/seeded.py", "relocated", "os.rename"): 1,
            ("hooks/seeded.py", "from_import_rename", "os.rename"): 1,
            ("hooks/seeded.py", "from_import_copy", "shutil.copytree"): 1,
            ("hooks/seeded.py", "module_alias_move", "shutil.move"): 1,
            ("hooks/seeded.py", "symlinked", "os.symlink"): 1,
            ("hooks/seeded.py", "hard_linked", "os.link"): 1,
            ("hooks/seeded.py", "path_symlinked", "Path.symlink_to"): 1,
            ("hooks/seeded.py", "path_hard_linked", "Path.hardlink_to"): 1,
        })

    def test_the_python_census_finds_bare_path_global_uses_in_the_context_module(
        self, tmp_path
    ):
        """Positive control for the bare-name scan, with a seeded module standing
        in as the context module, and the same name elsewhere left out."""
        shared = tmp_path / "hooks" / "shared"
        shared.mkdir(parents=True)
        (shared / "pact_context.py").write_text(
            "_context_path = None\n\n"
            "def init(path):\n"
            "    global _context_path\n"
            "    _context_path = path\n\n"
            "def via_bare_name():\n"
            "    _context_path.write_text('{}')\n\n"
            "def via_alias():\n"
            "    path = _context_path\n"
            "    path.write_text('{}')\n"
        )
        (tmp_path / "hooks" / "elsewhere.py").write_text(
            "def own_global():\n"
            "    _context_path.write_text('{}')\n"
        )
        found = _python_census(tmp_path)
        assert found["scanned"]["hooks"] == 2
        assert found["module_path_uses"] == Counter({
            (_CONTEXT_MODULE, "<module>"): 1,
            (_CONTEXT_MODULE, "init"): 1,
            (_CONTEXT_MODULE, "via_bare_name"): 1,
            (_CONTEXT_MODULE, "via_alias"): 1,
        })

    def test_an_assembled_file_name_is_a_name_site(self, tmp_path):
        """Positive control for `_names_the_file`: a name assembled from
        pieces is counted where it is built; another session file's name is
        not."""
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "seeded.py").write_text(
            "def split(base):\n"
            "    return base / ('pact-session-' + 'context.json')\n\n"
            "def formatted(base, kind):\n"
            "    return base / f'pact-session-{kind}.json'\n\n"
            "def temp_name():\n"
            "    return '.pact-session-context-'\n\n"
            "def unrelated():\n"
            "    return ('pact-sessions', 'session-journal.jsonl')\n"
        )
        assert _python_census(tmp_path)["name_sites"] == Counter({
            ("hooks/seeded.py", "split"): 1,
            ("hooks/seeded.py", "formatted"): 1,
            ("hooks/seeded.py", "temp_name"): 1,
        })

    def test_a_name_the_census_cannot_read_is_reported_unreadable(self, tmp_path):
        """Positive control for the census's fail-closed branch: a name built
        from a `%` template lands in the unreadable list, while a complete
        name beside it is a name site and another session file's name is
        neither. A docstring holding the template is exempt, unless its
        module reads a docstring as data."""
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "seeded.py").write_text(
            "def templated(base, kind):\n"
            "    return base / ('pact-session-%s.json' % kind)\n\n"
            "def named(base):\n"
            "    return base / 'pact-session-context.json'\n\n"
            "def other(base):\n"
            "    return base / 'pact-session-journal.json'\n"
        )
        (hooks / "documented.py").write_text(
            "def f():\n"
            "    \"\"\"pact-session-%s.json\"\"\"\n"
        )
        (hooks / "read_doc.py").write_text(
            "def f(base):\n"
            "    \"\"\"pact-session-%s.json\"\"\"\n"
            "    return base / (f.__doc__ % 'context')\n"
        )
        found = _python_census(tmp_path)
        assert sorted(found["unreadable_names"]) == [
            "hooks/read_doc.py:2: 'pact-session-%s.json'",
            "hooks/seeded.py:2: 'pact-session-%s.json'",
        ], found["unreadable_names"]
        assert found["name_sites"] == Counter({("hooks/seeded.py", "named"): 1})
        for read in ("f.__doc__", "getattr(f, '__doc__')", "inspect.getdoc(f)", "vars(f)['__doc__']", "__doc__"):
            assert _reads_a_docstring(ast.parse(read)), read
        assert not _reads_a_docstring(ast.parse("f.__name__"))

    def test_a_str_replace_is_not_a_relocation(self, tmp_path):
        """Path.replace takes one argument; str.replace takes two."""
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "seeded.py").write_text(
            "def f(session_dir):\n"
            "    return str(session_dir).replace('a', 'b')\n"
        )
        assert _python_census(tmp_path)["relocations"] == Counter()

    def test_a_read_open_is_not_a_write(self, tmp_path):
        """open() with a read mode, or with the filename as its only string, is
        not a direct write: the mode test reads mode-shaped strings only."""
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "seeded.py").write_text(
            "def f(session_dir):\n"
            "    open(session_dir + '/pact-session-context.json').read()\n"
            "    open(session_dir + '/pact-session-context.json', 'rb').read()\n"
        )
        found = _python_census(tmp_path)
        assert found["name_sites"] == Counter({("hooks/seeded.py", "f"): 2})
        assert found["direct_writes"] == Counter()

    def test_a_checkout_under_a_directory_named_tests_is_scanned(self, tmp_path):
        """The tests/ exclusion reads the path below the scan root."""
        hooks = tmp_path / "tests" / "checkout" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "seeded.py").write_text(
            "def direct(session_dir):\n"
            "    return session_dir + '/pact-session-context.json'\n"
        )
        found = _python_census(tmp_path / "tests" / "checkout")
        assert found["scanned"]["hooks"] == 1
        assert found["name_sites"] == Counter({("hooks/seeded.py", "direct"): 1})

    def test_the_known_writers_are_found_in_the_shipped_tree(self):
        found = _shipped_python_census()
        assert sum(found["scanned"].values()) > 50, found["scanned"]
        assert ("hooks/session_init.py", "main", "persist_context") in found["write_calls"], (
            "the census cannot see session_init's writer, so it cannot see a new one"
        )

    def test_untracked_file_under_existing_root_cannot_change_shipped_census(self, tmp_path):
        plugin = tmp_path / "plugin"
        hooks = plugin / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "tracked.py").write_text(
            "def tracked(session_dir):\n"
            "    return session_dir + '/pact-session-context.json'\n",
            encoding="utf-8",
        )
        (hooks / "tracked.sh").write_text(
            "echo 'pact-session-context.json'\n", encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q"], cwd=plugin, check=True)
        subprocess.run(["git", "add", "hooks/tracked.py", "hooks/tracked.sh"], cwd=plugin, check=True)
        before_python = _python_census(plugin)
        before_text = _text_census(plugin)
        # THE ARM'S OWN PRECONDITION. With an empty population both equality
        # assertions below hold while proving nothing, so the sibling control
        # was the only thing keeping this arm honest. Assert the seeded files
        # were enumerated before the untracked ones exist.
        assert before_python["scanned"]["hooks"] == 1, before_python["scanned"]
        assert before_python["name_sites"] == Counter(
            {("hooks/tracked.py", "tracked"): 1}), before_python["name_sites"]
        assert before_text[0]["hooks"] == 1, before_text[0]
        (hooks / "untracked.py").write_text(
            "def untracked(session_dir):\n"
            "    return session_dir + '/pact-session-context.json'\n",
            encoding="utf-8",
        )
        (hooks / "untracked.sh").write_text(
            "echo 'pact-session-context.json'\n", encoding="utf-8"
        )
        assert _python_census(plugin) == before_python
        assert _text_census(plugin) == before_text

    def test_every_directory_of_shipped_code_is_scanned(self):
        """A scan root that is dropped or misspelled would blind the census."""
        python_scanned = _shipped_python_census()["scanned"]
        text_scanned, _ = _shipped_text_census()
        for root in _ROOTS:
            assert python_scanned[root] + text_scanned[root] > 0, f"{root}/ scanned no files"
        missing = _code_bearing_dirs(_PLUGIN) - set(_ROOTS)
        assert not missing, f"shipped code outside the scan roots: {sorted(missing)}"

    def test_every_write_api_call_is_a_known_lead_gated_writer(self):
        write_calls = _shipped_python_census()["write_calls"]
        assert write_calls == KNOWN_WRITE_CALLS, _differs(write_calls, KNOWN_WRITE_CALLS)

    def test_every_caller_of_the_path_builder_is_known(self):
        calls = _shipped_python_census()["path_builder_calls"]
        assert calls == KNOWN_PATH_BUILDER_CALLS, _differs(calls, KNOWN_PATH_BUILDER_CALLS)

    def test_every_reference_to_a_path_accessor_is_known(self):
        refs = _shipped_python_census()["path_accessor_refs"]
        assert refs == KNOWN_PATH_ACCESSOR_REFS, _differs(refs, KNOWN_PATH_ACCESSOR_REFS)

    def test_every_code_string_naming_the_file_is_known(self):
        name_sites = _shipped_python_census()["name_sites"]
        assert name_sites == KNOWN_NAME_SITES, _differs(name_sites, KNOWN_NAME_SITES)

    def test_each_name_form_is_counted_read_or_reported(self):
        """Live control for the name census: each form below is a counted
        name site, read as another file, or unreadable, as written."""
        cases = {
            "pact-session-context.json": "site",
            "pact-session-": "site",
            ".pact-session-context-": "site",
            "pact-session-%s.json": "unreadable",
            "pact-session-{}.json": "unreadable",
            "pact-session-$kind.json": "unreadable",
            "context.json": "unreadable",
            "pact-session-ctx": "unreadable",
            "pact-session-journal.jsonl": "other",
            "pact-sessions": "other",
            "-context.json": "unreadable",
            "planted-context.json": "other",
            "the pact-sessions root": "other",
        }
        verdicts = {
            text: "site" if _names_the_file(text) else "unreadable" if _unreadable_name(text) else "other"
            for text in cases
        }
        assert verdicts == cases

    def test_no_string_names_the_file_in_a_form_the_census_cannot_read(self):
        """FAIL CLOSED: every anchor occurrence (`_NAME_ANCHOR`) is a counted
        name site, part of another session file's complete name, or
        reported here."""
        unreadable = _shipped_python_census()["unreadable_names"]
        assert unreadable == [], f"the context file named in a form the census cannot read: {unreadable}"

    def test_nothing_outside_the_context_module_reaches_its_path_global(self):
        path_refs = _shipped_python_census()["path_refs"]
        assert path_refs == Counter(), _differs(path_refs, Counter())

    def test_every_bare_path_global_use_in_the_context_module_is_known(self):
        uses = _shipped_python_census()["module_path_uses"]
        assert uses == KNOWN_MODULE_PATH_USES, _differs(uses, KNOWN_MODULE_PATH_USES)

    def test_every_session_path_relocation_is_known(self):
        relocations = _shipped_python_census()["relocations"]
        assert relocations == KNOWN_SESSION_RELOCATIONS, (
            _differs(relocations, KNOWN_SESSION_RELOCATIONS)
        )

    def test_no_function_at_the_file_writes_it_directly(self):
        writes = _shipped_python_census()["direct_writes"]
        assert writes == KNOWN_DIRECT_WRITES, _differs(writes, KNOWN_DIRECT_WRITES)

    def test_the_text_census_finds_a_seeded_shell_writer(self, tmp_path):
        """Positive control for the non-Python scan."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "seeded.sh").write_text(
            'echo "{}" > "$SESSION_DIR/pact-session-context.json"\n'
        )
        scanned, hits = _text_census(tmp_path)
        assert scanned["bin"] == 1 and hits == Counter({"bin/seeded.sh": 1})

    def test_no_shipped_non_python_file_names_the_file(self):
        scanned, hits = _shipped_text_census()
        assert sum(scanned.values()) > 0, "the text census scanned no files"
        assert hits == Counter(), _differs(hits, Counter())


# --- Behaviour ---------------------------------------------------------------

LEAD = "PACT:pact-orchestrator"
TEAMMATE = "pact-backend-coder"
_SID = "cccc3333-0000-0000-0000-000000000003"


def _context_file(project, session_id=_SID):
    return _build_session_path(project_slug(str(project)), session_id) / FILENAME


def _frame(agent_type, **extra):
    frame = {"session_id": _SID, **extra}
    if agent_type is not None:
        frame["agent_type"] = agent_type
    return frame


def _run_session_init(monkeypatch, project, frame):
    pact_context.reset_for_tests()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.chdir(project)
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0


def _project(tmp_path, name):
    project = tmp_path / name
    project.mkdir()
    return project


class TestSessionInitWriter:
    @pytest.mark.parametrize("agent_type", [TEAMMATE, None], ids=["teammate", "no-role"])
    def test_a_non_lead_start_writes_no_context_file(
        self, agent_type, monkeypatch, tmp_path
    ):
        control = _project(tmp_path, "control")
        _run_session_init(monkeypatch, control, _frame(LEAD, source="startup"))
        assert _context_file(control).is_file(), (
            "control: a lead start wrote no context file"
        )

        project = _project(tmp_path, "subject")
        _run_session_init(monkeypatch, project, _frame(agent_type, source="startup"))
        assert not _context_file(project).exists()


class TestHealWriter:
    """heal_context_if_missing re-creates a missing file. An in-process teammate
    carries the lead's session id, so it is the population that could heal the
    lead's file."""

    @pytest.mark.parametrize("agent_type", [TEAMMATE, None], ids=["teammate", "no-role"])
    def test_a_non_lead_frame_does_not_heal(self, agent_type, monkeypatch, tmp_path):
        control = _project(tmp_path, "control")
        pact_context.reset_for_tests()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(control))
        pact_context.init(_frame(LEAD))
        assert pact_context.heal_context_if_missing(_frame(LEAD)) is True
        assert _context_file(control).is_file(), "control: a lead frame did not heal"

        project = _project(tmp_path, "subject")
        pact_context.reset_for_tests()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        pact_context.init(_frame(agent_type))
        assert pact_context.heal_context_if_missing(_frame(agent_type)) is False
        assert not _context_file(project).exists()


class TestTeamNameWriteBack:
    """bootstrap_marker_writer rewrites the persisted team name when it differs
    from the identity-matched one. The divergence is made by stubbing the
    identity match's return value; everything else runs for real."""

    _PERSISTED = "session-cccc3333"
    _ALIGNED = "pact-real-team"

    def _seed_and_run(self, monkeypatch, project, agent_type, sink=False):
        """Seed a divergent context file, then run the marker writer's entry
        point, or with ``sink`` call the write-back helper directly, past the
        entry point's own lead check."""
        pact_context.reset_for_tests()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        pact_context.write_context(self._PERSISTED, _SID, str(project), "")
        before = _context_file(project).read_text()
        assert json.loads(before)["team_name"] == self._PERSISTED
        pact_context.reset_for_tests()
        monkeypatch.setattr(
            pact_context, "_resolve_aligned_team_name",
            lambda *args, **kwargs: self._ALIGNED,
        )
        if sink:
            pact_context.init(_frame(agent_type))
            bootstrap_marker_writer._write_back_aligned_team_name(_frame(agent_type))
        else:
            bootstrap_marker_writer._try_write_marker(_frame(agent_type))
        return before, _context_file(project).read_text()

    @pytest.mark.parametrize("agent_type", [TEAMMATE, None], ids=["teammate", "no-role"])
    def test_the_write_back_itself_refuses_a_non_lead_frame(
        self, agent_type, monkeypatch, tmp_path
    ):
        """The write site gates itself, as heal_context_if_missing does, so a
        caller that skips the entry point's lead check still cannot write."""
        _, after = self._seed_and_run(
            monkeypatch, _project(tmp_path, "control"), LEAD, sink=True
        )
        assert json.loads(after)["team_name"] == self._ALIGNED, (
            "control: a lead frame did not rewrite the divergent team name"
        )

        before, after = self._seed_and_run(
            monkeypatch, _project(tmp_path, "subject"), agent_type, sink=True
        )
        assert after == before

    @pytest.mark.parametrize("agent_type", [TEAMMATE, None], ids=["teammate", "no-role"])
    def test_a_non_lead_frame_does_not_rewrite_the_team_name(
        self, agent_type, monkeypatch, tmp_path
    ):
        _, after = self._seed_and_run(monkeypatch, _project(tmp_path, "control"), LEAD)
        assert json.loads(after)["team_name"] == self._ALIGNED, (
            "control: a lead frame did not rewrite the divergent team name"
        )

        before, after = self._seed_and_run(
            monkeypatch, _project(tmp_path, "subject"), agent_type
        )
        assert after == before


class TestHarnessStampWriter:
    """pact_context.set_harness_stamp rewrites one key of the session-context
    file. The write site gates itself on the hook frame's lead role (same
    self-gating discipline as heal_context_if_missing and the team write-back),
    so a caller that skips the marker writer's entry-point lead check still
    cannot stamp."""

    def _seed(self, monkeypatch, project):
        pact_context.reset_for_tests()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        pact_context.write_context("session-cccc3333", _SID, str(project), "")
        before = _context_file(project).read_text()
        assert json.loads(before)["harness"] == ""
        pact_context.reset_for_tests()
        pact_context.init(_frame(LEAD))
        return before

    def test_a_lead_frame_stamps(self, monkeypatch, tmp_path):
        project = _project(tmp_path, "control")
        before = self._seed(monkeypatch, project)
        wrote = pact_context.set_harness_stamp("non_tty", _frame(LEAD))
        after = _context_file(project).read_text()
        assert wrote is True
        assert json.loads(after)["harness"] == "non_tty"
        assert json.loads(after)["team_name"] == json.loads(before)["team_name"]

    @pytest.mark.parametrize("agent_type", [TEAMMATE, None], ids=["teammate", "no-role"])
    def test_a_non_lead_frame_does_not_stamp(self, agent_type, monkeypatch, tmp_path):
        project = _project(tmp_path, "subject")
        before = self._seed(monkeypatch, project)
        wrote = pact_context.set_harness_stamp("non_tty", _frame(agent_type))
        assert wrote is False
        assert _context_file(project).read_text() == before
