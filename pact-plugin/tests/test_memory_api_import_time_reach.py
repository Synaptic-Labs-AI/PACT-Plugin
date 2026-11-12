"""
Location: pact-plugin/tests/test_memory_api_import_time_reach.py

Summary: Tripwire asserting that nothing in the plugin calls into the memory API
at import time. While that holds, the collection-window gap in the test-isolation
guards is LATENT rather than live, and the conftest scrub may stay a per-test
fixture. The day this fails, the window is live and the scrub must become an
unconditional module-level scrub at conftest import.

Used by/with:
- tests/conftest.py: holds the per-test scrub whose sufficiency this guards.
- skills/pact-memory/scripts/pact_session.py: holds the PYTEST_CURRENT_TEST
  refusal, which pytest does not set during collection.

WHY THIS IS A TEST AND NOT A COMMENT. Both isolation guards are blind in the
same window: pytest sets PYTEST_CURRENT_TEST per test, around setup/call/
teardown, so it is absent while modules are imported; and a per-test fixture
cannot undo an import that already happened during collection.

TWO SEPARATE QUESTIONS, AND CONFLATING THEM IS WHAT MADE EVERY EARLIER VERSION
OF THIS PARAGRAPH WRONG. Ask how long the RESOLVER is exposed, and separately
how long the DAMAGE lasts. They have different answers.

- **THE RESOLVER EXPOSURE IS ONE-SHOT.** The guards are re-evaluated on every
  call and the cache sits below them, so an id resolved during collection is not
  served to any later caller: import time returns the live id, and every call
  after that returns empty.
- **THE FILESYSTEM DAMAGE FROM THAT ONE SHOT IS DURABLE.** A marker created or
  deleted in that single moment stays that way for the session, and **a deleted
  session marker is never recreated.** One resolution is enough.

The second half carries the severity, and it is why "one-shot" is not a
reassurance. `clear_embedding_marker()` resolves the SESSION-SCOPED marker path
and unlinks it. It has no production caller and no CLI surface, but any process
that imports the module can call it — and "no caller" is a different claim from
"no primitive". **A wrongly created marker suppresses a sweep; a wrongly deleted
one destroys a live session's state.** So a process that resolves a real session
id past a bypassed guard can DESTROY that session's marker, not merely learn its
name.

THIS PARAGRAPH HAS BEEN WRONG IN DEGREE THREE TIMES, IN ALTERNATING DIRECTIONS,
AND THE REASON IS WORTH MORE THAN THE CORRECTION. It said one-shot (understated),
then whole-run (true when written), then whole-run again after the cache fix had
made it one-shot once more. **Each version described the behaviour at the moment
it was written, and the behaviour kept changing underneath the words.** The split
above is meant to stop that: it states the PROPERTY — guards re-run, filesystem
writes persist — rather than the current mechanism. Prefer that shape if you
revise this again.

Nothing enters
that window today. A note asking a future reader to remember this has no failure
mode; this test has exactly one, and it is loud.

BOUND, AND IT IS THE REASON THE ANSWER IS "LATENT" AND NOT "IMPOSSIBLE": the
scan resolves calls BY NAME. An alias, a getattr, or a dynamic import evades it.
Treat a pass as evidence about the code as written, not as a proof of absence.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from fixtures.tracked_files import tracked_glob as _tracked_glob

# Names that mean "the memory API". Reaching any of these at import time is what
# would make the collection window live.
_MEMORY_API_NAMES = frozenset({
    "PACTMemory",
    "save_memory",
    "search_memories",
    "ensure_memory_ready",
    "maybe_embed_pending",
    "_ensure_ready",
    "_get_embedding_attempted_path",
    "get_session_id_from_context_file",
})

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _import_time_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, name) for every call that executes at import time.

    Module scope and class bodies execute on import. So do decorator
    expressions and default-argument expressions, even though both sit
    syntactically inside a `def` — that is the case a naive "is it inside a
    function?" walk gets wrong, and it is the one most likely to hide a reach.
    """
    found: list[tuple[int, str]] = []

    def call_names(node: ast.AST) -> list[tuple[int, str]]:
        hits = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name in _MEMORY_API_NAMES:
                    hits.append((sub.lineno, name))
        return hits

    def visit(node: ast.AST, at_import_time: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # The body is deferred, but decorators and defaults are not.
                for dec in child.decorator_list:
                    found.extend(call_names(dec))
                for default in child.args.defaults + [
                    d for d in child.args.kw_defaults if d is not None
                ]:
                    found.extend(call_names(default))
                visit(child, at_import_time=False)
            elif isinstance(child, ast.ClassDef):
                for dec in child.decorator_list:
                    found.extend(call_names(dec))
                # A class body executes on import even inside a function only
                # when that function runs, so inherit the enclosing context.
                visit(child, at_import_time)
                if at_import_time:
                    for stmt in child.body:
                        if not isinstance(
                            stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                        ):
                            found.extend(call_names(stmt))
            else:
                if at_import_time:
                    found.extend(call_names(child))
                visit(child, at_import_time)

    visit(tree, at_import_time=True)
    return found


def _python_files(root=_PLUGIN_ROOT) -> list[Path]:
    # Do NOT filter on ".worktrees": the plugin is routinely checked out inside
    # a worktree, so that filter excludes the entire population and the scan
    # passes at zero files. The non-empty control below exists because this
    # exact mistake makes the tripwire permanently and silently green.
    return [p for p in _tracked_glob(root, "**/*.py", allow_empty=True)
            if "__pycache__" not in p.parts]


class TestNoImportTimeReachIntoMemoryAPI:
    def test_population_is_not_empty(self):
        """Control: a zero-file scan would pass vacuously."""
        files = _python_files()
        assert len(files) > 100, f"scan population implausibly small: {len(files)}"

    def test_walker_detects_decorator_and_default_arg_calls(self):
        """Control: the walker must catch the two cases a naive walk misses.

        Without this, a walker that treated everything inside a `def` as
        deferred would report zero for the wrong reason and the tripwire would
        be permanently green.
        """
        src = (
            "def deco(x):\n"
            "    return x\n"
            "\n"
            "@deco(PACTMemory())\n"
            "def decorated():\n"
            "    pass\n"
            "\n"
            "def defaulted(arg=save_memory()):\n"
            "    pass\n"
            "\n"
            "def deferred():\n"
            "    return PACTMemory()\n"
        )
        hits = {name for _, name in _import_time_calls(ast.parse(src))}
        assert "PACTMemory" in hits, "decorator expression not detected"
        assert "save_memory" in hits, "default-argument expression not detected"

        deferred_only = "def f():\n    return PACTMemory()\n"
        assert _import_time_calls(ast.parse(deferred_only)) == [], (
            "a function body must NOT count as import time"
        )

    def test_a_path_that_vanishes_between_the_walk_and_the_read_is_skipped(
        self, monkeypatch
    ):
        """MEASURED, not hypothetical: a concurrent pytest planted a probe file
        under `tests/fixtures/`, this walk enumerated it, and the other run
        deleted it before the read — reddening this class with a
        FileNotFoundError naming a test that has nothing to do with the fault.

        Concurrency merely EXPOSED it. An editor save or a `git checkout`
        during a run removes a listed path just as well, so the guard is not
        about running two suites.

        THE GHOST IS APPENDED TO THE REAL POPULATION rather than replacing it,
        so the walk still does its real work and this arm cannot pass by
        scanning nothing — the vacuity `test_population_is_not_empty` exists
        to catch.

        RED WHEN FileNotFoundError leaves the except tuple.
        """
        ghost = _PLUGIN_ROOT / "tests" / "_vanished_between_walk_and_read.py"
        assert not ghost.exists(), "the ghost path must not exist on disk"

        real = _python_files()
        assert len(real) > 100, "real population implausibly small"
        monkeypatch.setattr(
            sys.modules[__name__], "_python_files", lambda: real + [ghost]
        )

        # Must not raise. The tripwire's own verdict is asserted by the arm
        # below; this one asserts only that a vanished path cannot abort it.
        self.test_no_module_scope_reach_into_memory_api()

    def test_no_module_scope_reach_into_memory_api(self):
        """The tripwire itself. Passes at zero today.

        A failure means some module now calls the memory API at import time, so
        the collection window is live and the conftest scrub must be converted
        to an unconditional module-level scrub.
        """
        offenders = []
        for path in _python_files():
            if path.name == Path(__file__).name:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            except FileNotFoundError:
                # THE WALK AND THE READ ARE NOT ONE OPERATION. A path listed by
                # rglob can stop existing before this line runs: an editor
                # save, a `git checkout`, or a concurrent pytest deleting its
                # own temporary file. A file that no longer exists cannot be an
                # offender, so skipping it is the correct membership decision
                # rather than a swallowed error. NARROW ON PURPOSE — an
                # unreadable file that still exists is a different condition
                # and must stay loud.
                continue
            for lineno, name in _import_time_calls(tree):
                offenders.append(f"{path.relative_to(_PLUGIN_ROOT)}:{lineno} calls {name}()")

        assert offenders == [], (
            "import-time reach into the memory API detected; the collection "
            "window is now LIVE. Convert the conftest scrub to an unconditional "
            "module-level scrub. Offenders:\n  " + "\n  ".join(offenders)
        )


def test_an_untracked_module_cannot_reach_the_memory_api(tmp_path):
    """An untracked scratch module must not join the import-time census."""
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    body = "from store import PACTMemory\n\nPACTMemory().save_memory('x')\n"
    tracked = root / "hooks" / "tracked.py"
    tracked.write_text(body, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"],
                   check=True, capture_output=True)
    needle = root / "hooks" / "zzprobe.py"
    needle.write_text(body, encoding="utf-8")

    population = _python_files(root)

    assert tracked in population, "the tracked control is missing from the population"
    assert needle not in population, (
        f"untracked needle entered the import-time census: {needle}")
    assert _import_time_calls(ast.parse(tracked.read_text(encoding="utf-8"))), (
        "the oracle is not live")
