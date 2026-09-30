"""Only a lead writes pact-session-context.json.

session_init treats that file's presence in a session's own directory as proof
that a lead ran there: a lead resumed without `--agent` is recovered from it. So
a writer that runs for a teammate or a no-role session would let that session be
treated as the lead. Two pins keep every writer lead-gated:

- CENSUS: every shipped site that calls a context write API, names the file in
  code, or reaches the context module's path global, compared with a known set.
  A new site fails with instructions. Each scan has a positive control that
  seeds a writer it must find.
- BEHAVIOUR: each known writer, driven for a non-lead frame, writes nothing,
  while a lead control on the same setup writes the file.
"""
import ast
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import bootstrap_marker_writer  # noqa: E402
import session_init  # noqa: E402
import shared.pact_context as pact_context  # noqa: E402
from shared.pact_context import _build_session_path, project_slug  # noqa: E402

_PLUGIN = Path(__file__).resolve().parent.parent
_ROOTS = ("hooks", "skills", "scripts", "bin", "telegram")
FILENAME = "pact-session-context.json"
_WRITE_APIS = frozenset({"persist_context", "write_context"})
_CONTEXT_MODULE = "hooks/shared/pact_context.py"

# (file, enclosing function, write API called). The three lead-gated writers,
# plus write_context itself, which is persist_context behind a path builder.
KNOWN_WRITE_CALLS = frozenset({
    ("hooks/session_init.py", "main", "persist_context"),
    ("hooks/shared/pact_context.py", "heal_context_if_missing", "write_context"),
    ("hooks/bootstrap_marker_writer.py", "_write_back_aligned_team_name", "write_context"),
    ("hooks/shared/pact_context.py", "write_context", "persist_context"),
})

# (file, enclosing function) of every code string naming the file. Each one
# builds the path for a READ, builds the path persist_context writes
# (build_context_cache), or is message text.
KNOWN_NAME_SITES = frozenset({
    ("hooks/dispatch_gate.py", "<module>"),                   # message text
    ("hooks/dispatch_gate.py", "evaluate_dispatch"),          # message text
    ("hooks/session_init.py", "_kept_started_at"),            # read
    ("hooks/session_init.py", "_lead_context_persisted"),     # existence read
    ("hooks/shared/pact_context.py", "build_context_cache"),  # the writers' path
    ("hooks/shared/pact_context.py", "init"),                 # read path
    ("hooks/shared/pact_harvest.py", "main"),                 # CLI help text
    ("skills/pact-memory/scripts/pact_session.py", "_context_file_path"),       # read
    ("skills/pact-memory/scripts/pact_session.py", "_context_record_on_disk"),  # read
})

_INSTRUCTION = (
    "session_init treats pact-session-context.json in a session's own dir as "
    "proof that a lead ran there. If the new site WRITES the file, gate it on "
    "the lead (frame_is_lead / pact_context.is_lead) and add a behaviour arm "
    "below, then add the site to the known set. If it only reads or names the "
    "file, add it to the known set with a comment saying so."
)


class _Census(ast.NodeVisitor):
    """Collect write-API calls, code strings naming the file, and uses of the
    context module's path global, keyed by (file, enclosing function)."""

    def __init__(self, rel):
        self.rel = rel
        self.stack = []
        self.docstrings = set()
        self.write_calls = set()
        self.name_sites = set()
        self.path_refs = set()

    def _where(self):
        return ".".join(self.stack) or "<module>"

    def _note_docstring(self, node):
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            self.docstrings.add(id(body[0].value))

    def visit_Module(self, node):
        self._note_docstring(node)
        self.generic_visit(node)

    def _visit_scope(self, node):
        self._note_docstring(node)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _visit_scope

    def visit_Call(self, node):
        func = node.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute) else None
        )
        if name in _WRITE_APIS:
            self.write_calls.add((self.rel, self._where(), name))
        self.generic_visit(node)

    def visit_Constant(self, node):
        if (
            isinstance(node.value, str)
            and FILENAME in node.value
            and id(node) not in self.docstrings
        ):
            self.name_sites.add((self.rel, self._where()))

    def visit_Attribute(self, node):
        if node.attr == "_context_path" and self.rel != _CONTEXT_MODULE:
            self.path_refs.add((self.rel, self._where()))
        self.generic_visit(node)


def _shipped(root, pattern):
    for path in sorted(root.rglob(pattern)):
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and "tests" not in path.parts
            and not path.name.startswith("test_")
        ):
            yield path


def _python_census(plugin):
    """Return (files scanned, write calls, name sites, path-global refs)."""
    census_sets = (set(), set(), set())
    scanned = 0
    for root in _ROOTS:
        for path in _shipped(plugin / root, "*.py"):
            census = _Census(path.relative_to(plugin).as_posix())
            census.visit(ast.parse(path.read_text(encoding="utf-8")))
            census_sets[0].update(census.write_calls)
            census_sets[1].update(census.name_sites)
            census_sets[2].update(census.path_refs)
            scanned += 1
    return (scanned, *census_sets)


def _text_census(plugin):
    """Return (files scanned, non-Python, non-markdown files naming the file)."""
    hits, scanned = set(), 0
    for root in _ROOTS:
        for path in _shipped(plugin / root, "*"):
            if path.suffix in (".py", ".md", ".pyc"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            if FILENAME in text:
                hits.add(path.relative_to(plugin).as_posix())
    return scanned, hits


class TestCensus:
    def test_the_python_census_finds_a_seeded_writer(self, tmp_path):
        """Positive control: both a write-API writer and a writer that builds the
        path itself, in a seeded hook module, are found."""
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "seeded.py").write_text(
            "from pathlib import Path\n"
            "import shared.pact_context as pact_context\n\n"
            "def via_api():\n"
            "    pact_context.write_context('t', 's', 'p', 'r')\n\n"
            "def direct(session_dir):\n"
            "    (Path(session_dir) / f'pact-session-context.json').write_text('{}')\n"
        )
        scanned, write_calls, name_sites, _ = _python_census(tmp_path)
        assert scanned == 1
        assert write_calls == {("hooks/seeded.py", "via_api", "write_context")}
        assert name_sites == {("hooks/seeded.py", "direct")}

    def test_the_known_writers_are_found_in_the_shipped_tree(self):
        scanned, write_calls, _, _ = _python_census(_PLUGIN)
        assert scanned > 50, f"the census scanned only {scanned} files"
        assert ("hooks/session_init.py", "main", "persist_context") in write_calls, (
            "the census cannot see session_init's writer, so it cannot see a new one"
        )

    def test_every_write_api_call_is_a_known_lead_gated_writer(self):
        _, write_calls, _, _ = _python_census(_PLUGIN)
        assert write_calls == KNOWN_WRITE_CALLS, (
            f"new: {sorted(write_calls - KNOWN_WRITE_CALLS)}; "
            f"gone: {sorted(KNOWN_WRITE_CALLS - write_calls)}. {_INSTRUCTION}"
        )

    def test_every_code_string_naming_the_file_is_known(self):
        _, _, name_sites, _ = _python_census(_PLUGIN)
        assert name_sites == KNOWN_NAME_SITES, (
            f"new: {sorted(name_sites - KNOWN_NAME_SITES)}; "
            f"gone: {sorted(KNOWN_NAME_SITES - name_sites)}. {_INSTRUCTION}"
        )

    def test_nothing_outside_the_context_module_reaches_its_path_global(self):
        _, _, _, path_refs = _python_census(_PLUGIN)
        assert path_refs == set(), f"{sorted(path_refs)}. {_INSTRUCTION}"

    def test_the_text_census_finds_a_seeded_shell_writer(self, tmp_path):
        """Positive control for the non-Python scan."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "seeded.sh").write_text(
            'echo "{}" > "$SESSION_DIR/pact-session-context.json"\n'
        )
        assert _text_census(tmp_path) == (1, {"bin/seeded.sh"})

    def test_no_shipped_non_python_file_names_the_file(self):
        scanned, hits = _text_census(_PLUGIN)
        assert scanned > 0, "the text census scanned no files"
        assert hits == set(), f"{sorted(hits)}. {_INSTRUCTION}"


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

    def _seed_and_run(self, monkeypatch, project, agent_type):
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
        bootstrap_marker_writer._try_write_marker(_frame(agent_type))
        return before, _context_file(project).read_text()

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
