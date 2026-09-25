"""
Location: pact-plugin/tests/test_working_memory_projection.py
Summary: Verification tests for rebuilding the Working Memory section of
         CLAUDE.md from pact-memory records rather than prepending one save at
         a time. Covers the formatter's `created_at` parameter (a projected
         entry carries the record's own date, a save still carries now) and
         the parser that turns a stored `created_at` into that stamp.

         Every write in this file goes to a file under `tmp_path`. The
         child-process arms resolve ambiently on purpose, inside a declared
         tmp project, to reach the ambient guards.
Used by: pytest.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


import scripts.working_memory as wm  # noqa: E402
from scripts.memory_api import PACTMemory  # noqa: E402
from scripts.working_memory import (  # noqa: E402
    MAX_WORKING_MEMORIES,
    WORKING_MEMORY_TOKEN_BUDGET,
    SyncResult,
    _estimate_tokens,
    _format_memory_entry,
    _record_timestamp,
    project_memories_to_claude_md,
)
from clock_shift.clock_shift_env import carry_clock_shift
from fixtures.hf_cache import hf_cache_env

_SCAFFOLD = (
    "# Probe\n\n"
    "## Working Memory\n"
    f"{wm.WORKING_MEMORY_COMMENT}\n\n"
    "## Pinned Context\n\nkeep me\n"
)

# The per-entry ceiling `_apply_token_budget` applies before budgeting.
_ENTRY_CEILING = (
    WORKING_MEMORY_TOKEN_BUDGET
    - (MAX_WORKING_MEMORIES - 1) * wm.COMPRESSED_ENTRY_TOKEN_CEILING
)


def _header(entry: str) -> str:
    return entry.split("\n", 1)[0]


def _seed(tmp_path, body: str = _SCAFFOLD):
    target = tmp_path / "project" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def _section_entries(text: str):
    """The Working Memory section's entries, split on their date headers."""
    section = text.split("## Working Memory\n", 1)[1].split("\n## ", 1)[0]
    return [e.strip() for e in section.split("\n### ")[1:]]


def _record(stamp: str, label: str, **fields):
    return {"id": label * 32, "context": f"{label} context",
            "created_at": stamp, **fields}


def _dense_record(stamp: str, label: str):
    """Every free-text field at its 200-char cut, in 1-char words, so the
    formatted entry estimates far past the per-entry ceiling."""
    dense = "a " * 100
    return _record(
        stamp, label, goal=dense, decisions=[dense], lessons_learned=[dense],
        reasoning_chains=[dense], agreements_reached=[dense],
        disagreements_resolved=[dense],
    )


class TestFormatterCreatedAt:
    """`_format_memory_entry` stamps the header from `created_at` when given."""

    def test_created_at_renders_as_the_header(self):
        stamp = datetime(2024, 3, 7, 4, 5, 59)
        entry = _format_memory_entry({"context": "ctx"}, created_at=stamp)
        assert _header(entry) == "### 2024-03-07 04:05"

    def test_without_created_at_the_header_is_now(self):
        before = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        entry = _format_memory_entry({"context": "ctx"})
        after = datetime.now(timezone.utc)
        rendered = datetime.strptime(_header(entry), "### %Y-%m-%d %H:%M")
        rendered = rendered.replace(tzinfo=timezone.utc)
        assert before <= rendered <= after

    def test_created_at_changes_only_the_header(self):
        """Control: the same record renders the same body under either stamp."""
        memory = {"context": "ctx", "goal": "g", "decisions": ["d1", "d2"]}
        stamped = _format_memory_entry(memory, ["a.py"], "a" * 32,
                                       created_at=datetime(2020, 1, 1))
        unstamped = _format_memory_entry(memory, ["a.py"], "a" * 32)
        assert _header(stamped) == "### 2020-01-01 00:00"
        assert _header(stamped) != _header(unstamped)
        assert stamped.split("\n", 1)[1] == unstamped.split("\n", 1)[1]


class TestRecordTimestamp:
    """The stored `created_at` reaches the header in both forms it takes."""

    @pytest.mark.parametrize("stored", [
        "2026-09-05 09:20:42",   # as SQLite `datetime('now')` writes it
        "2026-09-05T09:20:42",   # as `MemoryObject.to_dict()` emits it
    ])
    def test_both_stored_forms_render_the_same_header(self, stored):
        entry = _format_memory_entry(
            {"context": "ctx"}, created_at=_record_timestamp(stored)
        )
        assert _header(entry) == "### 2026-09-05 09:20"

    def test_datetime_and_none_pass_through(self):
        stamp = datetime(2026, 9, 5, 9, 20)
        assert _record_timestamp(stamp) is stamp
        assert _record_timestamp(None) is None

    def test_unparseable_value_falls_back_to_now_and_still_renders(self):
        assert _record_timestamp("not a date") is None
        before = datetime.now(timezone.utc) - timedelta(minutes=1)
        entry = _format_memory_entry(
            {"context": "still here"},
            created_at=_record_timestamp("not a date"),
        )
        rendered = datetime.strptime(_header(entry), "### %Y-%m-%d %H:%M")
        assert rendered.replace(tzinfo=timezone.utc) >= before
        assert "**Context**: still here" in entry


class TestProjectionReplacesTheSection:
    """`project_memories_to_claude_md` writes the section from the list alone."""

    def test_empty_input_is_empty_and_touches_nothing(self, tmp_path):
        target = _seed(tmp_path)
        before = target.read_bytes()
        result = project_memories_to_claude_md([], target=target)
        assert result.reason == SyncResult.EMPTY
        assert target.read_bytes() == before

    def test_the_given_entries_replace_the_hand_written_ones(self, tmp_path):
        target = _seed(tmp_path, _SCAFFOLD.replace(
            "## Pinned Context",
            "### 2025-01-01 01:00\n**Context**: hand one\n\n"
            "### 2024-01-01 01:00\n**Context**: hand two\n\n"
            "## Pinned Context",
        ))
        assert len(_section_entries(target.read_text(encoding="utf-8"))) == 2

        result = project_memories_to_claude_md([
            _record("2026-09-05 09:20:00", "c"),
            _record("2026-09-04 08:10:00", "b"),
            _record("2026-09-03 07:00:00", "a"),
        ], target=target)

        assert result.reason == SyncResult.WROTE
        text = target.read_text(encoding="utf-8")
        entries = _section_entries(text)
        assert [_header(e) for e in entries] == [
            "2026-09-05 09:20", "2026-09-04 08:10", "2026-09-03 07:00",
        ]
        assert "hand one" not in text and "hand two" not in text
        assert "c context" in entries[0] and "**Memory ID**: " + "c" * 32 in entries[0]
        assert text.endswith("## Pinned Context\n\nkeep me\n"), "the rest of the file moved"

    def test_input_past_the_cap_is_cut_to_it(self, tmp_path):
        target = _seed(tmp_path)
        records = [_record(f"2026-01-0{i} 00:00:00", chr(ord("a") + i))
                   for i in range(1, MAX_WORKING_MEMORIES + 3)]
        assert project_memories_to_claude_md(records, target=target)
        entries = _section_entries(target.read_text(encoding="utf-8"))
        assert len(entries) == MAX_WORKING_MEMORIES
        assert _header(entries[0]) == "2026-01-01 00:00"

    def test_the_budget_is_applied_once_across_the_projection(self, tmp_path):
        """Newest entry bounded by the per-entry ceiling and not compressed;
        older entries compressed; section under budget."""
        records = [_dense_record(f"2026-03-0{i} 00:00:00", c)
                   for i, c in ((3, "c"), (2, "b"), (1, "a"))]
        # Non-vacuity: unbudgeted, each entry alone exceeds the ceiling and the
        # three together exceed the section budget.
        raw = [_format_memory_entry(r, None, r["id"]) for r in records]
        assert all(_estimate_tokens(e) > _ENTRY_CEILING for e in raw)
        assert sum(_estimate_tokens(e) for e in raw) > WORKING_MEMORY_TOKEN_BUDGET

        target = _seed(tmp_path)
        assert project_memories_to_claude_md(records, target=target)
        entries = _section_entries(target.read_text(encoding="utf-8"))

        assert len(entries) == 3
        newest, *older = entries
        assert "**Summary**" not in newest
        assert "**Goal**" in newest, "the newest entry was compressed, not cut"
        assert _estimate_tokens("### " + newest) <= _ENTRY_CEILING
        for entry in older:
            assert "**Summary**" in entry and "**Goal**" not in entry
        assert sum(_estimate_tokens("### " + e) for e in entries) <= WORKING_MEMORY_TOKEN_BUDGET


class TestProjectionRunsThroughTheSameWriteSite:
    """The replace arm inherits the write site's certifications."""

    def test_a_target_outside_the_resolved_base_is_refused(
        self, tmp_path, monkeypatch
    ):
        """Containment differential, mirroring the save path's arm: the
        resolver hands back a base that does not contain the file, the atomic
        write raises, the handler reports `failed`, the file is unchanged."""
        target = _seed(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        before = target.read_bytes()
        monkeypatch.setattr(
            wm, "_resolve_display_claude_md_with_base",
            lambda **_: (target, elsewhere),
        )

        result = project_memories_to_claude_md([_record("2026-01-01 00:00:00", "a")])

        assert result.reason == SyncResult.FAILED
        assert target.read_bytes() == before

    def test_a_crlf_target_stays_crlf(self, tmp_path, monkeypatch):
        target = tmp_path / "project" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(_SCAFFOLD.replace("\n", "\r\n").encode("utf-8"))
        before = target.read_bytes()
        assert before.count(b"\r\n") > 0
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(target.parent))

        result = project_memories_to_claude_md(
            [_record("2026-01-01 00:00:00", "a", goal="one\r\ntwo")]
        )

        assert result.reason == SyncResult.WROTE
        raw = target.read_bytes()
        assert raw != before, "the write did not happen"
        crlf = raw.count(b"\r\n")
        assert crlf > 0 and raw.count(b"\n") == crlf and raw.count(b"\r\r\n") == 0


# ---------------------------------------------------------------------------
# The `sync` verb, in a child process, against both ambient guards
# ---------------------------------------------------------------------------

_CLI = (
    Path(__file__).resolve().parent.parent
    / "skills" / "pact-memory" / "scripts" / "cli.py"
)


def _run_cli(env: dict, cwd: Path, *args: str) -> dict:
    # THE CACHE ENV IS APPLIED HERE, AT THE CHOKE POINT, RATHER THAN AT EACH
    # CALLER. Applying it per-site is what failed: one caller below got it and
    # another, built with `dict(os.environ)`, did not — and the one that missed
    # out was a real `save`. Every child this file spawns routes through this
    # function, so binding it here is the only placement that cannot be missed
    # by the next site somebody adds.
    #
    # THE POPULATION IS NOT "CHILDREN THAT SAVE", WHICH IS THE ASSUMPTION THAT
    # PRODUCED THE GAP. `_ensure_ready()` runs on EVERY verb and calls the
    # embedding catch-up, which loads the model whenever the store holds an
    # unembedded row. So a `sync` or `status` child reading a store that a
    # degraded save left pending can reach the model load too, and `save` is
    # merely the most obvious member.
    #
    # The helper wins over the caller's env deliberately: this is an isolation
    # guarantee rather than a preference, and no caller here sets either key.
    env = {**env, **hf_cache_env()}
    proc = subprocess.run(
        [sys.executable, str(_CLI), *args],
        # 30s rather than 180s: the long budget absorbed a first-run model
        # download, and `hf_cache_env()` removes the download. Measured basis in
        # the sibling refusal suite — slowest child 0.44s, cold-cache offline
        # degrade 0.31s.
        env=carry_clock_shift(env), cwd=str(cwd), capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"rc={proc.returncode}\n{proc.stderr[:600]}"
    payload = json.loads(proc.stdout)
    assert payload.get("ok") is True, payload
    return payload["result"]


class TestSyncVerbReachesTheGuards:
    """`cli.py sync` in a CHILD, so the ambient refusals can fire.

    In-process, `pytest` in `sys.modules` exempts the PYTEST_CURRENT_TEST
    refusal and the redirected-store refusal, so only a
    child can show that the replace arm runs through them. The store is
    redirected with `--db-path` in every arm, and it holds one record.
    """

    @pytest.fixture
    def project(self, tmp_path):
        target = _seed(tmp_path)
        return target.parent

    @pytest.fixture
    def stocked_store(self, memory_store, tmp_path):
        db = memory_store("probe.db")
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(tmp_path / "project")
        _run_cli(env, tmp_path, "save", json.dumps({"context": "one record"}),
                 "--no-sync", "--db-path", str(db))
        return db

    def test_inherited_env_without_an_anchor_is_refused(
        self, project, stocked_store, tmp_path
    ):
        """First guard: PYTEST_CURRENT_TEST is set in the child, no anchor."""
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project)
        assert "PYTEST_CURRENT_TEST" in env
        before = (project / "CLAUDE.md").read_bytes()

        result = _run_cli(env, tmp_path, "sync", "--db-path", str(stocked_store))

        assert (result["sync_status"], result["projected"], result["memory_ids"]) == (
            "refused", 0, [])
        assert (project / "CLAUDE.md").read_bytes() == before

    def test_a_declared_root_permits_the_write(
        self, project, stocked_store, tmp_path
    ):
        """Positive control for both refusals: same child, anchored, writes."""
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project)
        before = (project / "CLAUDE.md").read_bytes()

        result = _run_cli(env, tmp_path, "sync", "--claude-md-root", str(project),
                          "--db-path", str(stocked_store))

        assert result["sync_status"] == "wrote", result
        assert result["projected"] == 1 and len(result["memory_ids"]) == 1
        after = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert after.encode("utf-8") != before
        assert "one record" in after
        assert f"**Memory ID**: {result['memory_ids'][0]}" in after

    def test_a_redirected_store_that_escaped_its_root_is_refused(
        self, project, memory_store, tmp_path
    ):
        """Second guard, the incident shape: a built environment with no
        PYTEST_CURRENT_TEST, a declared directory holding no CLAUDE.md, so
        resolution continues to the working directory's file, outside it.

        THE RECORD IS SAVED UNDER THE SAME DECLARED DIRECTORY. The project id
        derives from it, and `sync` projects only this project's records; a
        record saved under another id leaves nothing to project, and the
        `empty` return precedes the PYTEST_CURRENT_TEST refusal and the
        redirected-store refusal. That is correct, and it would
        make this arm pass without reaching the guard, so the store is
        stocked under the escaping id and the envelope is pinned to `refused`.
        """
        escaped = tmp_path / "declared-but-empty"
        escaped.mkdir()
        # No cache env here: `_run_cli` binds it for every child. Spelling it
        # at this one site is what made the gap look closed while a sibling
        # built with `dict(os.environ)` went without — and a per-site spread
        # would now also imply the protection is per-site, which is the
        # inference that produced the miss.
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path / "home"),
            "CLAUDE_PROJECT_DIR": str(escaped),
        }
        assert "PYTEST_CURRENT_TEST" not in env
        db = memory_store("escaped.db")
        _run_cli(env, tmp_path, "save", json.dumps({"context": "escaping record"}),
                 "--no-sync", "--db-path", str(db))
        before = (project / "CLAUDE.md").read_bytes()

        result = _run_cli(env, project, "sync", "--db-path", str(db))

        # THE WHOLE ENVELOPE, so a dropped key and an added one both fail. The
        # sibling route is pinned whole in the fidelity suite; this one was
        # loosened to three fields when `project_id` arrived and never widened
        # back, leaving the route where an escaped root would surface as the
        # one route that could gain a key in silence.
        expected_id = _run_cli(env, tmp_path, "status", "--db-path", str(db))["project_id"]
        assert expected_id, "status reported no project id; the pin below is vacuous"
        assert result == {
            "sync_status": "refused", "projected": 0, "memory_ids": [],
            "project_id": expected_id,
        }
        assert (project / "CLAUDE.md").read_bytes() == before


class TestSyncRefusesAProjectWithNoId:
    """`sync` with no project id projects NOTHING rather than everything.

    `list_memories` applies its `project_id = ?` condition only when the id
    is non-None, so a None reaching that query selects the newest records of
    EVERY project, and `sync` would write those foreign records over this
    file's section. The guard answers `empty` before the query runs, which is
    also the right answer on its own terms: a project with no id has no
    records of its own to project.
    """

    def test_a_none_project_id_projects_nothing_and_touches_nothing(
        self, memory_store, tmp_path, monkeypatch
    ):
        target = _seed(tmp_path)
        project = target.parent
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        db = memory_store("no-project.db")

        # THE STORE MUST HOLD A RECORD UNDER SOME OTHER ID, or a green arm
        # would prove only that the store was empty. This is the record an
        # unfiltered query would find and project.
        stocked = PACTMemory(db_path=db)
        assert stocked.project_id is not None
        stocked.save({"context": "a record saved under a resolved project"},
                     sync_to_claude=False)

        memory = PACTMemory(db_path=db)
        memory._project_id = None
        before = target.read_bytes()

        # `claude_md_root` bounds the write: with the guard removed, the
        # projection lands on this tmp project's file, which is what the
        # byte comparison below catches. It cannot reach a file outside it.
        projected = memory.sync(claude_md_root=project)

        # THE FILE FIRST, because it is the harm. The envelope assertions
        # below would also catch the regression, but they would catch it
        # before this line ran and so would not show that foreign records
        # reached the file.
        assert target.read_bytes() == before
        assert projected == []
        assert memory.last_sync_status == "empty"


# ---------------------------------------------------------------------------
# Every production caller of a CLAUDE.md resolver calls the escape guard
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_DISPLAY_RESOLVER = "_resolve_display_claude_md_with_base"
_DISPLAY_GUARD = "_refuse_ambient_sync_on_declared_scope_escape"

# (scope, resolver, guard, exempt). A function in scope that calls `resolver`
# must call `guard` on a later line. The display resolver is searched in every
# production file, so a new caller anywhere joins the population. archive_pin's
# resolver is the hooks' shared read resolver, which read-only callers use
# freely, so only archive_pin itself, which acts on what it resolves, is held
# to it.
_GUARDED_RESOLVERS = (
    (
        "production",
        _DISPLAY_RESOLVER,
        _DISPLAY_GUARD,
        # The path-only wrapper returns no base, so no write can be
        # containment-checked through it.
        frozenset({"_resolve_display_claude_md_path"}),
    ),
    (
        "scripts/archive_pin.py",
        "get_project_claude_md_path",
        "_stays_in_declared_project",
        frozenset(),
    ),
)

_KNOWN_GUARDED_CALLERS = {
    "sync_to_claude_md",
    "sync_retrieved_to_claude_md",
    "resolve_claude_md",
}


def _production_files():
    for path in sorted(_PLUGIN_ROOT.rglob("*.py")):
        rel = path.relative_to(_PLUGIN_ROOT)
        if "tests" in rel.parts or path.name.startswith("test_") or path.name == "conftest.py":
            continue
        yield path


def _unguarded_callers(source, resolver, guard, exempt=frozenset()):
    """Return (callers, unguarded): the functions that call `resolver`, and those
    among them with no `guard` call on a line after their first `resolver` call."""
    import ast

    callers, unguarded = [], []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in exempt:
            continue
        lines = {resolver: [], guard: []}
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in lines:
                lines[name].append(call.lineno)
        if not lines[resolver]:
            continue
        callers.append(node.name)
        if not any(line > min(lines[resolver]) for line in lines[guard]):
            unguarded.append(node.name)
    return callers, unguarded


class TestEveryResolverCallerCallsTheEscapeGuard:
    """The escape guard sits at the CALLERS, not inside the resolver, because it
    needs `target` and `claude_md_root`, which the resolver does not take. So a
    new caller would not inherit it. This pins that every caller does call it.
    """

    def test_every_production_caller_of_a_resolver_calls_the_escape_guard(self):
        """MUTANT that reddens this arm: delete the guard call from
        `sync_to_claude_md` or `sync_retrieved_to_claude_md`, or the
        `_stays_in_declared_project` call from `archive_pin.resolve_claude_md`.
        The failure names the unguarded caller."""
        members, unguarded = [], []
        for scope, resolver, guard, exempt in _GUARDED_RESOLVERS:
            files = (
                list(_production_files()) if scope == "production"
                else [_PLUGIN_ROOT / scope]
            )
            for path in files:
                callers, missing = _unguarded_callers(
                    path.read_text(encoding="utf-8"), resolver, guard, exempt
                )
                rel = path.relative_to(_PLUGIN_ROOT)
                members += [f"{rel}::{name}" for name in callers]
                unguarded += [f"{rel}::{name}" for name in missing]

        found = {member.split("::")[1] for member in members}
        assert _KNOWN_GUARDED_CALLERS <= found and len(members) >= len(_KNOWN_GUARDED_CALLERS), (
            f"the scan found {members}; it must reach every known caller, "
            "or it is not reading the files it claims to"
        )
        assert unguarded == [], (
            f"these callers resolve a CLAUDE.md without the escape guard after "
            f"it: {unguarded}"
        )

    def test_the_scan_catches_an_unguarded_and_a_misordered_caller(self):
        """The live control: the scan above can report a caller at all."""
        import textwrap

        source = textwrap.dedent(f"""
            def unguarded():
                path, base = {_DISPLAY_RESOLVER}()

            def misordered():
                {_DISPLAY_GUARD}(None, None, None, None)
                path, base = {_DISPLAY_RESOLVER}()

            def guarded():
                path, base = {_DISPLAY_RESOLVER}()
                {_DISPLAY_GUARD}(None, None, base, path)
        """)
        callers, unguarded = _unguarded_callers(source, _DISPLAY_RESOLVER, _DISPLAY_GUARD)
        assert callers == ["unguarded", "misordered", "guarded"]
        assert unguarded == ["unguarded", "misordered"]


# ---------------------------------------------------------------------------
# Every production call of the scope predicate passes the worktree record
# ---------------------------------------------------------------------------

_SCOPE_PREDICATE = "stays_in_declared_project"
_KNOWN_PREDICATE_CALLERS = {
    "_refuse_ambient_sync_on_declared_scope_escape",
    "resolve_claude_md",
}


def _predicate_calls_without_record(source):
    """Return (callers, missing): the functions that call the scope predicate,
    under its own name, an imported alias or a module attribute, and those among
    them with a call that passes no worktree record."""
    import ast

    tree = ast.parse(source)
    names = {_SCOPE_PREDICATE}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {a.asname or a.name for a in node.names if a.name == _SCOPE_PREDICATE}
    callers, missing = [], []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [
            call for call in ast.walk(function)
            if isinstance(call, ast.Call) and (
                (isinstance(call.func, ast.Name) and call.func.id in names)
                or (isinstance(call.func, ast.Attribute) and call.func.attr == _SCOPE_PREDICATE)
            )
        ]
        if not calls:
            continue
        callers.append(function.name)
        if any(
            len(call.args) < 4 and not any(k.arg == "worktree_identity" for k in call.keywords)
            for call in calls
        ):
            missing.append(function.name)
    return callers, missing


class TestEveryScopePredicateCallPassesTheRecord:
    """The worktree record decides a removed declaration only where a caller
    passes it, so a caller that omits it judges that layout differently from
    the others."""

    def test_every_production_call_of_the_scope_predicate_passes_the_worktree_record(self):
        """MUTANT that reddens this arm: drop `worktree_identity=` from the call
        in `_refuse_ambient_sync_on_declared_scope_escape` or in
        `archive_pin.resolve_claude_md`. The failure names that caller."""
        members, missing = [], []
        for path in _production_files():
            callers, lacking = _predicate_calls_without_record(path.read_text(encoding="utf-8"))
            rel = path.relative_to(_PLUGIN_ROOT)
            members += [f"{rel}::{name}" for name in callers]
            missing += [f"{rel}::{name}" for name in lacking]

        found = {member.split("::")[1] for member in members}
        assert _KNOWN_PREDICATE_CALLERS <= found, (
            f"the scan found {members}; it must reach every known caller, "
            "or it is not reading the files it claims to"
        )
        assert missing == [], (
            f"these callers judge project scope without the worktree record: {missing}"
        )

    def test_the_scan_catches_a_call_without_the_record_under_any_name(self):
        """The live control: the scan above finds an aliased and an attribute
        call, and tells a call that passes the record from one that does not."""
        import textwrap

        source = textwrap.dedent(f"""
            from shared.project_scope import {_SCOPE_PREDICATE} as _renamed
            import shared.project_scope as scope

            def aliased():
                _renamed(a, b, c)

            def attribute():
                scope.{_SCOPE_PREDICATE}(a, b, c)

            def by_keyword():
                _renamed(a, b, c, worktree_identity=record)

            def by_position():
                scope.{_SCOPE_PREDICATE}(a, b, c, record)
        """)
        callers, missing = _predicate_calls_without_record(source)
        assert callers == ["aliased", "attribute", "by_keyword", "by_position"]
        assert missing == ["aliased", "attribute"]
