"""The legacy-bucket ownership rule, row by row, through every consumer.

Location: pact-plugin/tests/test_legacy_ownership_matrix.py
Summary: each row of the ownership decision matrix builds real git checkouts
in tmp, seeds one legacy bucket named like the project folder, and runs every
consumer of the bucket's verdict: the read scope (`list`), `project-id show`,
the defaulted `project-id rekey` and the save. A canonical scope (the
repository has an origin, so the bucket is its legacy candidate) and a
folder-name scope (no origin, so the bucket is the session's own id) are run
for every row that has an expectation for them. Then the folder-name refusal
remedies (R1-R4) and the structural arms (S1-S3).
Used by: the test suite; no production code imports it.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import shlex
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    make_repo,
    make_worktree,
    run_git,
    seed_session_evidence,
)
from scripts.database import db_connection, ensure_initialized

from scripts import memory_api

B = "widget"
REMOTE = f"https://github.com/o/{B}.git"
CANONICAL = f"github.com/o/{B}"
MEMORY_ID = "a" * 32


# ---------------------------------------------------------------------------
# Fixture plumbing
# ---------------------------------------------------------------------------


@dataclass
class Layout:
    """One row's world: the checkout the session runs in, the store, and what
    the bucket holds."""

    root: Path
    store: Path
    files: list = field(default_factory=list)
    sessions: dict = field(default_factory=dict)  # session id -> recorded dir, or None
    memories: list | None = None  # (memory id, session id); None = one memory by the first session


def _fresh() -> None:
    for name in ("_LEGACY_SCOPE_CACHE", "_EXCLUSION_WARNED", "_UNPROVEN_WARNED", "_CASE_VARIANT_WARNED"):
        getattr(memory_api, name).clear()
    memory_api._PRUNED_SESSION_INDEX.clear()


def _config(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".claude"


def _seed(layout: Layout) -> None:
    memories = layout.memories
    if memories is None:
        memories = [(MEMORY_ID, next(iter(layout.sessions), "unrecorded"))]
    with db_connection(layout.store) as conn:
        ensure_initialized(conn)
        for memory_id, session_id in memories:
            conn.execute(
                "INSERT INTO memories (id, context, project_id, session_id, created_at, updated_at) "
                "VALUES (?, 'legacy row', ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (memory_id, B, session_id),
            )
        for index, path in enumerate(layout.files):
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (f"file-{index}", str(path), B),
            )
        conn.commit()


def _repo(path: Path, canonical: bool) -> Path:
    return make_repo(path, remote=REMOTE if canonical else None)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")
    return path


def _other_checkout(tmp_path: Path) -> Path:
    other = make_repo(tmp_path / "other" / B)
    _touch(other / "src.py")
    return other


def _session_at(tmp_path: Path, directory: Path, session_id: str) -> None:
    seed_session_evidence(_config(tmp_path), directory, session_id)


def _cli(argv, capfd):
    """Run the CLI in process; return (exit code, envelope)."""
    from scripts.cli import main

    _fresh()
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    captured = capfd.readouterr()
    stream = captured.out if exit_info.value.code == 0 else captured.err
    return exit_info.value.code, json.loads(stream)


@dataclass
class Observed:
    read: bool
    legacy_scope: dict | None
    rekey_code: int | None
    rekey_envelope: dict | None
    save_code: int
    save_envelope: dict
    warnings: str


def _run_consumers(layout: Layout, monkeypatch, capfd, caplog, *, rekey: bool) -> Observed:
    monkeypatch.chdir(layout.root)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    store = ["--db-path", str(layout.store)]
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        code, listed = _cli(["list", *store], capfd)
        assert code == 0, listed
        read = any(row.get("id") == MEMORY_ID for row in listed["result"])
        code, shown = _cli(["project-id", "show", *store], capfd)
        assert code == 0, shown
        legacy_scope = shown["result"].get("legacy_scope")
        rekey_code = rekey_envelope = None
        if rekey:
            rekey_code, rekey_envelope = _cli(["project-id", "rekey", f"--from={B}", *store], capfd)
        save_code, saved = _cli(["save", json.dumps({"context": "new"}), *store], capfd)
    return Observed(read, legacy_scope, rekey_code, rekey_envelope, save_code, saved, caplog.text)


def _state(legacy_scope: dict | None) -> str | None:
    """The record's one state: verified, included_unverified, unproven or excluded."""
    if not legacy_scope or not legacy_scope.get("candidate"):
        return None
    if legacy_scope["included"]:
        return "verified" if legacy_scope["verified"] else "included_unverified"
    return "excluded" if legacy_scope["excluded"] else "unproven"


# ---------------------------------------------------------------------------
# The rows. Each builder makes its world for one scope kind.
# ---------------------------------------------------------------------------


def row_1(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=[_touch(root / "src.py")])


def row_2(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    worktree = make_worktree(root, tmp_path / "wt" / B)
    return Layout(root, tmp_path / "store.db", files=[_touch(worktree / "src.py")])


def row_3(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=[_other_checkout(tmp_path) / "src.py"])


def row_4(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(
        root, tmp_path / "store.db",
        files=[_touch(root / "src.py"), _other_checkout(tmp_path) / "src.py"],
    )


def row_5(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=[_touch(tmp_path / "etc_like" / "x.conf")])


def row_6(tmp_path, canonical):
    layout = row_5(tmp_path, canonical)
    _session_at(tmp_path, layout.root, "here")
    layout.sessions["here"] = layout.root
    return layout


def _home_repo(tmp_path, canonical):
    """A $HOME-style dotfiles repository that encloses the project root."""
    user = make_repo(tmp_path / "user")
    root = _repo(user / "projects" / B, canonical)
    return user, root


def row_7(tmp_path, canonical):
    user, root = _home_repo(tmp_path, canonical)
    return Layout(
        root, tmp_path / "store.db",
        files=[_touch(user / "notes" / "n.txt"), _touch(root / "src.py")],
    )


def row_8(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    nested = make_repo(root / "vendor" / "lib")
    return Layout(root, tmp_path / "store.db", files=[_touch(nested / "a.py")])


def row_9(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    source = make_repo(tmp_path / "libsrc")
    run_git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "sub", cwd=root)
    run_git("commit", "-qm", "add submodule", cwd=root)
    return Layout(root, tmp_path / "store.db", files=[_touch(root / "sub" / "a.py")])


def row_10(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=[_other_checkout(tmp_path) / "gone.py"])


def row_11(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=[str(tmp_path / "nul\x00row.py")])


def row_12(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=["src/rel.py"])


def row_13(tmp_path, canonical):
    layout = row_12(tmp_path, canonical)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _session_at(tmp_path, elsewhere, "far")
    layout.sessions["far"] = elsewhere
    return layout


def row_14(tmp_path, canonical):
    layout = row_12(tmp_path, canonical)
    worktree = make_worktree(layout.root, tmp_path / "wt" / "gone")
    _session_at(tmp_path, worktree, "wts")
    run_git("worktree", "remove", "--force", str(worktree), cwd=layout.root)
    layout.sessions["wts"] = None
    return layout


def row_15(tmp_path, canonical):
    layout = row_1(tmp_path, canonical)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _session_at(tmp_path, elsewhere, "far")
    layout.sessions["far"] = elsewhere
    return layout


def row_16(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", memories=[])


def row_17(tmp_path, canonical):
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=[_touch(tmp_path / "data" / B / "x.py")])


def row_18(tmp_path, canonical):
    victim = make_repo(tmp_path / "v" / B)
    root = _repo(victim / "vendor" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=[_touch(victim / "src.py")])


def row_19(tmp_path, canonical):
    user, root = _home_repo(tmp_path, canonical)
    return Layout(
        root, tmp_path / "store.db",
        files=[
            _touch(root / "src.py"),
            _touch(tmp_path / "etc_like" / "x.conf"),
            _touch(user / ".config" / "app.conf"),
        ],
    )


def row_21(tmp_path, canonical):
    """A row whose path IS another checkout's root (a tracked "." or project
    directory stores exactly that) beside a row under this root: that
    checkout's own `.git` sits in the row's directory, not above it."""
    root = _repo(tmp_path / "work" / B, canonical)
    rival = make_repo(tmp_path / "rival" / "proj")
    return Layout(root, tmp_path / "store.db", files=[_touch(root / "src.py"), rival])


def row_22(tmp_path, canonical):
    """Row 21's checkout-root row alone: positive foreign evidence, no own."""
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=[make_repo(tmp_path / "rival" / "proj")])


# (row, canonical expectation, folder-name expectation). An expectation is
# (read, state, reason, rekey_or_save) where rekey_or_save is "ok" or
# "refused"; None means the row has no expectation for that scope.
VERIFIED = (True, "verified", None, "ok")
NO_SESSION = (False, "unproven", "no_session", "refused")
FOLDER_OK = (True, None, None, "ok")
ROWS = [
    (row_1, VERIFIED, FOLDER_OK),
    (row_2, VERIFIED, FOLDER_OK),
    (row_3, (False, "excluded", "foreign_rows", "refused"), (False, "excluded", "foreign_rows", "refused")),
    (row_4, (False, "unproven", "foreign_rows", "refused"), (False, "unproven", "foreign_rows", "refused")),
    (row_5, NO_SESSION, FOLDER_OK),
    (row_6, (True, "included_unverified", None, "ok"), FOLDER_OK),
    (row_7, VERIFIED, FOLDER_OK),
    (row_8, NO_SESSION, FOLDER_OK),
    (row_9, VERIFIED, FOLDER_OK),
    (row_10, NO_SESSION, FOLDER_OK),
    (row_11, NO_SESSION, FOLDER_OK),
    (row_12, NO_SESSION, FOLDER_OK),
    (row_13, (False, "unproven", "recorded_elsewhere", "refused"), (False, "excluded", "recorded_elsewhere", "refused")),
    (row_14, NO_SESSION, FOLDER_OK),
    (row_15, (False, "unproven", "recorded_elsewhere", "refused"), (False, "unproven", "recorded_elsewhere", "refused")),
    (row_16, None, (False, None, None, "ok")),  # empty: nothing to read
    (row_17, (False, "excluded", "foreign_rows", "refused"), (False, "excluded", "foreign_rows", "refused")),
    (row_18, (False, "excluded", "foreign_rows", "refused"), (False, "excluded", "foreign_rows", "refused")),
    (row_19, VERIFIED, FOLDER_OK),
    (row_21, (False, "unproven", "foreign_rows", "refused"), (False, "unproven", "foreign_rows", "refused")),
    (row_22, (False, "excluded", "foreign_rows", "refused"), (False, "excluded", "foreign_rows", "refused")),
]


def _cases():
    for builder, canonical, folder in ROWS:
        number = builder.__name__.split("_")[1]
        if canonical is not None:
            yield pytest.param(builder, True, canonical, id=f"{number}-canonical")
        if folder is not None:
            yield pytest.param(builder, False, folder, id=f"{number}-folder-name")


@pytest.mark.parametrize("builder, canonical, expect", list(_cases()))
def test_every_consumer_follows_the_row(builder, canonical, expect, tmp_path, monkeypatch, capfd, caplog):
    assert_outside_repository(tmp_path)
    layout = builder(tmp_path, canonical)
    _seed(layout)
    read, state, reason, action = expect
    seen = _run_consumers(layout, monkeypatch, capfd, caplog, rekey=canonical)
    assert seen.read is read, (seen.legacy_scope, seen.warnings)
    if canonical:
        assert _state(seen.legacy_scope) == state, seen.legacy_scope
        if reason is not None:
            assert seen.legacy_scope["unproven_reason"] == reason, seen.legacy_scope
        if action == "ok":
            assert seen.rekey_code == 0, seen.rekey_envelope
        else:
            assert seen.rekey_code == 1 and seen.rekey_envelope["error"] == "REKEY_REFUSED", seen.rekey_envelope
        if state == "excluded":
            assert seen.legacy_scope["rekey"] is None, seen.legacy_scope
        assert seen.save_code == 0, seen.save_envelope  # a save binds the canonical id
    else:
        if action == "ok":
            assert seen.save_code == 0, seen.save_envelope
        else:
            assert seen.save_code == 1, seen.save_envelope
            assert seen.save_envelope["error"] == "FOLDER_NAME_BUCKET_REFUSED", seen.save_envelope
        if state is not None and seen.legacy_scope is not None:
            assert _state(seen.legacy_scope) == state, seen.legacy_scope
            assert seen.legacy_scope["unproven_reason"] == reason, seen.legacy_scope
    _assert_texts(seen, reason)


def _assert_texts(seen: Observed, reason) -> None:
    """Section 3: a text states only what the evidence shows, with its count."""
    texts = " ".join((seen.warnings + json.dumps(seen.save_envelope) + json.dumps(seen.rekey_envelope)).split())
    if reason == "foreign_rows":
        assert "1 of its file rows lie in another checkout" in texts, texts
    else:
        assert "lie in another checkout" not in texts, texts
    if reason == "recorded_elsewhere":
        assert "1 of its memories were written by sessions recorded at another existing folder" in texts, texts


def test_row_16_an_empty_bucket_reads_empty_and_saves(tmp_path, monkeypatch, capfd, caplog):
    """Row 16, folder-name: no rows and no sessions; the read is empty and the
    save lands in the folder-name bucket."""
    assert_outside_repository(tmp_path)
    layout = row_16(tmp_path, False)
    _seed(layout)
    seen = _run_consumers(layout, monkeypatch, capfd, caplog, rekey=False)
    assert seen.read is False and seen.save_code == 0, seen.save_envelope
    with sqlite3.connect(str(layout.store)) as conn:
        assert conn.execute("SELECT project_id FROM memories").fetchall() == [(B,)]


def test_row_20_each_copy_of_a_no_origin_project_is_refused_with_its_local_pin(
    tmp_path, monkeypatch, capfd, caplog
):
    """Row 20: one no-origin project at two existing paths, each with recorded
    sessions; each copy sees the other's sessions as elsewhere."""
    assert_outside_repository(tmp_path)
    copies = [make_repo(tmp_path / "c1" / B), make_repo(tmp_path / "c2" / B)]
    for index, copy in enumerate(copies):
        _session_at(tmp_path, copy, f"s{index}")
    layout = Layout(copies[0], tmp_path / "store.db", memories=[("a" * 32, "s0"), ("b" * 32, "s1")])
    _seed(layout)
    for copy in copies:
        seen = _run_consumers(Layout(copy, layout.store), monkeypatch, capfd, caplog, rekey=False)
        assert seen.read is False, seen.warnings
        assert seen.save_code == 1 and seen.save_envelope["error"] == "FOLDER_NAME_BUCKET_REFUSED", seen.save_envelope
        assert memory_api.local_pin_id(copy) in seen.save_envelope["message"], seen.save_envelope
        if seen.legacy_scope is not None:
            assert _state(seen.legacy_scope) == "unproven", seen.legacy_scope
            assert seen.legacy_scope["unproven_reason"] == "recorded_elsewhere", seen.legacy_scope


def test_a_folder_name_save_refusal_reports_the_sync_status_refused(tmp_path, monkeypatch):
    """The folder-name refusal is a refusal like its siblings
    (ProjectIdentityUnresolvedError): `last_sync_status` reads REFUSED after
    it, not the cleared None, so a caller reading only the status sees a
    deliberate refusal."""
    from scripts.working_memory import SyncResult

    assert_outside_repository(tmp_path)
    layout = row_3(tmp_path, False)
    _seed(layout)
    monkeypatch.chdir(layout.root)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _fresh()
    memory = memory_api.PACTMemory(session_id="s", db_path=layout.store)
    with pytest.raises(memory_api.FolderNameBucketRefusedError):
        memory.save({"context": "new"})
    assert memory.last_sync_status == SyncResult.REFUSED, memory.last_sync_status


# ---------------------------------------------------------------------------
# R1-R4: the folder-name refusal remedy, by layout (row 3's bucket)
# ---------------------------------------------------------------------------


def _refusal(root: Path, tmp_path: Path, monkeypatch, capfd, errors=("FOLDER_NAME_BUCKET_REFUSED",)) -> dict:
    store = tmp_path / "store.db"
    _seed(Layout(root, store, files=[_other_checkout(tmp_path) / "src.py"]))
    monkeypatch.chdir(root)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    code, envelope = _cli(["save", json.dumps({"context": "new"}), "--db-path", str(store)], capfd)
    assert code == 1 and envelope["error"] in errors, envelope
    return envelope


def _printed_pin_command(message: str) -> str:
    """The `printf ... > <file>` command the refusal prints."""
    import re

    match = re.search(r"printf '%s\\n' \S+ > \S+", message)
    assert match is not None, message
    return match.group(0).rstrip(".,;")


def test_r1_an_own_git_directory_gets_a_runnable_pin_for_this_checkout_only(tmp_path, monkeypatch, capfd):
    assert_outside_repository(tmp_path)
    root = make_repo(tmp_path / "work" / B)
    message = _refusal(root, tmp_path, monkeypatch, capfd)["message"]
    command = _printed_pin_command(message)
    subprocess.run(["sh", "-c", command], check=True, cwd=tmp_path)
    pin = (root / ".git" / "pact-project-id").read_text(encoding="utf-8").strip()
    assert pin == memory_api.local_pin_id(root)
    other = tmp_path / "other" / B / ".git"
    assert not (other / "pact-project-id").exists()


def test_r2_a_linked_worktree_pins_the_main_checkouts_git_directory(tmp_path, monkeypatch, capfd):
    assert_outside_repository(tmp_path)
    main = make_repo(tmp_path / "main" / B)
    worktree = make_worktree(main, tmp_path / "work" / "wt")
    message = _refusal(worktree, tmp_path, monkeypatch, capfd)["message"]
    command = _printed_pin_command(message)
    target = str((main / ".git").resolve() / "pact-project-id")
    assert command.endswith(shlex.quote(target)), command


def _separate_git_dir(tmp_path):
    root = tmp_path / "work" / B
    run_git("init", "-q", f"--separate-git-dir={tmp_path / 'store-git'}", str(root), cwd=tmp_path)
    return root


def _forged_pointer(tmp_path):
    victim = make_repo(tmp_path / "victim" / "proj")
    root = tmp_path / "work" / B
    root.mkdir(parents=True)
    (root / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")
    return root


def _symlinked_git(tmp_path):
    victim = make_repo(tmp_path / "victim" / "proj")
    root = tmp_path / "work" / B
    root.mkdir(parents=True)
    (root / ".git").symlink_to(victim / ".git", target_is_directory=True)
    return root


def _core_worktree(tmp_path):
    store = make_repo(tmp_path / "admin" / "repo")
    root = tmp_path / "work" / B
    root.mkdir(parents=True)
    run_git("config", "core.worktree", str(root), cwd=store)
    (root / ".git").write_text(f"gitdir: {store / '.git'}\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("layout", [_separate_git_dir, _forged_pointer, _symlinked_git, _core_worktree])
def test_r3_an_external_git_directory_gets_no_command_and_no_path(layout, tmp_path, monkeypatch, capfd):
    """A layout whose git directory lies outside the checkout. Where the
    identity itself declines (a redirected marker), the refusal is the
    unresolved-identity one; either way no pin command and no path into that
    git directory is printed, and the text leaves the pin to the user."""
    assert_outside_repository(tmp_path)
    root = layout(tmp_path)
    envelope = _refusal(
        root, tmp_path, monkeypatch, capfd,
        errors=("FOLDER_NAME_BUCKET_REFUSED", "PROJECT_IDENTITY_UNRESOLVED"),
    )
    message = envelope["message"]
    assert "printf" not in message and "pact-project-id" not in message, message
    assert "decision" in message, message


def test_r4_a_non_git_folder_names_project_id_and_an_origin_remote(tmp_path, monkeypatch, capfd):
    assert_outside_repository(tmp_path)
    root = tmp_path / "work" / B
    (root / ".claude").mkdir(parents=True)
    message = _refusal(root, tmp_path, monkeypatch, capfd)["message"]
    assert "rev-parse" not in message, message
    assert "project_id" in message and "origin" in message, message


# ---------------------------------------------------------------------------
# S1-S3: structure
# ---------------------------------------------------------------------------


def _function(tree, qualified: str):
    parts = qualified.split(".")
    scope = tree.body
    found = None
    for part in parts:
        found = next(
            node for node in scope
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == part
        )
        scope = found.body
    return found


def _names_called(function) -> set:
    called = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            func = node.func
            called.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None))
    return called


def _defs(*trees) -> dict:
    """{name: def} for every function and method of the given modules."""
    found = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.setdefault(node.name, node)
    return found


def _reaches(function, target: str, defs: dict, depth: int = 4) -> bool:
    """Whether `function` calls `target`, directly or through the modules'
    own functions, within `depth` calls."""
    seen, frontier = set(), [function]
    for _ in range(depth):
        called = set()
        for node in frontier:
            called |= _names_called(node)
        if target in called:
            return True
        frontier = [defs[name] for name in called - seen if name in defs]
        seen |= called
    return False


@pytest.mark.parametrize("module, qualified", [
    ("memory_api", "PACTMemory._read_project_scopes"),
    ("memory_api", "PACTMemory._project_id_for_write"),
    ("cli", "_project_id_rekey"),
])
def test_s1_every_consumer_takes_the_one_verdict(module, qualified):
    """Each consumer reaches decide_bucket through the module's own functions,
    and none calls the retired gate or compares its outcome string."""
    from scripts import cli

    trees = {name: ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
             for name, mod in (("memory_api", memory_api), ("cli", cli))}
    function = _function(trees[module], qualified)
    assert _reaches(function, "decide_bucket", _defs(*trees.values())), qualified
    text = ast.unparse(function)
    assert "legacy_candidate_plausibility" not in text, qualified
    for outcome in ('"verified"', '"unverified"', '"excluded"'):
        assert f"outcome == {outcome}" not in text and f"outcome != {outcome}" not in text, (qualified, outcome)


def test_s2_the_census_runs_at_most_once_per_command(tmp_path, monkeypatch, capfd):
    """Row 6 needs the session census; list and save each run it at most
    once. Row 3's foreign file row decides first, so the census never runs."""
    from shared.pact_context import reconstruct_session_dir

    assert_outside_repository(tmp_path)
    layout = row_6(tmp_path, False)
    _seed(layout)
    sessions_root = Path(reconstruct_session_dir(str(layout.root), "x")).parent.parent
    listings = []
    real = os.listdir

    def counting(path="."):
        if Path(path) == sessions_root:
            import traceback

            listings.append("".join(traceback.format_stack(limit=8)[:-1]))
        return real(path)

    monkeypatch.setattr(memory_api.os, "listdir", counting)
    monkeypatch.chdir(layout.root)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for argv in (["list"], ["save", json.dumps({"context": "new"})]):
        listings.clear()
        code, envelope = _cli([*argv, "--db-path", str(layout.store)], capfd)
        assert code == 0, envelope
        assert len(listings) <= 1, (argv, len(listings), listings)


def test_s2_the_census_is_skipped_when_file_rows_decide_foreign(tmp_path, monkeypatch, capfd):
    from shared.pact_context import reconstruct_session_dir

    assert_outside_repository(tmp_path)
    layout = row_3(tmp_path, True)
    _session_at(tmp_path, layout.root, "here")
    layout.sessions["here"] = layout.root
    _seed(layout)
    sessions_root = Path(reconstruct_session_dir(str(layout.root), "x")).parent.parent
    listings = []
    real = os.listdir

    def counting(path="."):
        if Path(path) == sessions_root:
            listings.append(path)
        return real(path)

    monkeypatch.setattr(memory_api.os, "listdir", counting)
    monkeypatch.chdir(layout.root)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    code, envelope = _cli(["list", "--db-path", str(layout.store)], capfd)
    assert code == 0, envelope
    assert listings == [], len(listings)


_EVIDENCE_KEYS = {"contained": "contained_rows", "foreign": "foreign_rows"}


def _evidence(**counts):
    """bucket_evidence's counts dict; every file row is absolute unless relative."""
    base = dict.fromkeys(
        ("file_rows", "absolute_rows", "contained_rows", "foreign_rows", "neutral_outside",
         "vanished", "unresolvable", "relative"), 0,
    )
    for name, value in counts.items():
        base[_EVIDENCE_KEYS.get(name, name)] = value
    base["file_rows"] = sum(value for name, value in base.items() if name not in ("file_rows", "absolute_rows"))
    base["absolute_rows"] = base["file_rows"] - base["relative"]
    base["roots"] = 1
    return base


def _sessions(**counts):
    base = dict.fromkeys(("here", "elsewhere", "subfolder", "description_newer", "unattributed"), 0)
    base.update(counts)
    return base


# The 2.4 table: (scope, evidence, sessions or None when the census is skipped)
# -> (read, save, rekey_default, state, reason). Save is None for a canonical
# scope (n/a), rekey_default is None for a folder-name scope (n/a).
DECISIONS = [
    ("canonical", _evidence(contained=1), _sessions(), (True, None, True, "verified", None)),
    ("canonical", _evidence(neutral_outside=1), _sessions(here=1), (True, None, True, "included_unverified", None)),
    ("canonical", _evidence(contained=1, foreign=1), None, (False, None, False, "unproven", "foreign_rows")),
    ("canonical", _evidence(contained=1), _sessions(elsewhere=1), (False, None, False, "unproven", "recorded_elsewhere")),
    ("canonical", _evidence(foreign=1), None, (False, None, False, "excluded", "foreign_rows")),
    ("canonical", _evidence(relative=1), _sessions(elsewhere=1), (False, None, False, "unproven", "recorded_elsewhere")),
    ("canonical", _evidence(neutral_outside=1), _sessions(), (False, None, False, "unproven", "no_session")),
    ("canonical", _evidence(vanished=1), _sessions(subfolder=1), (False, None, False, "unproven", "subfolder_launch")),
    ("canonical", _evidence(unresolvable=1), _sessions(description_newer=1), (False, None, False, "unproven", "description_newer")),
    ("folder_name", _evidence(neutral_outside=1), _sessions(), (True, True, None, None, None)),
    ("folder_name", _evidence(contained=1), _sessions(here=1), (True, True, None, None, None)),
    ("folder_name", _evidence(), _sessions(), (True, True, None, None, None)),
    ("folder_name", _evidence(contained=1, foreign=1), None, (False, False, None, "unproven", "foreign_rows")),
    ("folder_name", _evidence(contained=1), _sessions(elsewhere=1), (False, False, None, "unproven", "recorded_elsewhere")),
    ("folder_name", _evidence(foreign=1), None, (False, False, None, "excluded", "foreign_rows")),
    ("folder_name", _evidence(relative=1), _sessions(elsewhere=1), (False, False, None, "excluded", "recorded_elsewhere")),
]


@pytest.mark.parametrize("scope, evidence, sessions, expected", DECISIONS)
def test_s3_decide_bucket_follows_the_table(scope, evidence, sessions, expected):
    verdict = memory_api.decide_bucket(scope, evidence, sessions)
    got = (verdict.read, verdict.save, verdict.rekey_default, verdict.state, verdict.reason)
    want = list(expected)
    if scope == "canonical":
        got = (got[0], None, *got[2:])  # save is n/a for a canonical scope
    else:
        got = (*got[:2], None, *got[3:])
    assert got == tuple(want), (scope, evidence, sessions, verdict)


def test_s3_decide_bucket_is_pure():
    """The same inputs give the same verdict and the inputs are not changed."""
    evidence, sessions = _evidence(contained=1, foreign=1), None
    before = json.dumps(evidence, sort_keys=True)
    first = memory_api.decide_bucket("canonical", evidence, sessions)
    assert memory_api.decide_bucket("canonical", evidence, sessions) == first
    assert json.dumps(evidence, sort_keys=True) == before
