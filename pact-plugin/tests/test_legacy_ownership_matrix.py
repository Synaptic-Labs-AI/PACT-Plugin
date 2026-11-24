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
    bucket: str = B


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
                (memory_id, layout.bucket, session_id),
            )
        for index, path in enumerate(layout.files):
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (f"file-{index}", str(path), layout.bucket),
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
    candidates: list | None
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
        rekey_code = rekey_envelope = candidates = None
        if rekey:
            rekey_code, rekey_envelope = _cli(["project-id", "rekey", f"--from={layout.bucket}", *store], capfd)
            code, listed_candidates = _cli(["project-id", "rekey", "--candidates", *store], capfd)
            assert code == 0, listed_candidates
            candidates = listed_candidates["result"]["candidates"]
        save_code, saved = _cli(["save", json.dumps({"context": "new"}), *store], capfd)
    return Observed(read, legacy_scope, rekey_code, rekey_envelope, candidates, save_code, saved, caplog.text)


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
    """A non-git folder named like the bucket that is marked as a project
    (it holds `.claude`): a rival under rule (b)."""
    root = _repo(tmp_path / "work" / B, canonical)
    (tmp_path / "data" / B / ".claude").mkdir(parents=True)
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
    # Read on session evidence alone: a defaulted --to cannot claim the whole
    # bucket, so the defaulted rekey is refused (an explicit --to still moves it).
    (row_6, (True, "included_unverified", None, "refused"), FOLDER_OK),
    (row_7, VERIFIED, FOLDER_OK),
    (row_8, NO_SESSION, FOLDER_OK),
    (row_9, VERIFIED, FOLDER_OK),
    (row_10, NO_SESSION, FOLDER_OK),
    (row_11, NO_SESSION, FOLDER_OK),
    (row_12, NO_SESSION, FOLDER_OK),
    (row_13, (False, "excluded", "recorded_elsewhere", "refused"), (False, "excluded", "recorded_elsewhere", "refused")),
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
            # Excluded never carries a command: not in the record, not on the
            # candidate row, not in a warning.
            assert seen.legacy_scope["rekey"] is None, seen.legacy_scope
            row = next(c for c in seen.candidates if c["id"] == layout.bucket)
            assert row.get("rekey") is None, row
            assert "project-id rekey" not in seen.warnings, seen.warnings
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
    _assert_texts(seen, reason, FOLDER_KIND.get(builder, "checkout"), layout.bucket)


# The rival kind a foreign_rows row names (section 10.5): a rule-(b) folder
# without `.git` is a "project folder"; every other rival is a "checkout".
FOLDER_KIND: dict = {}


def _assert_texts(seen: Observed, reason, kind: str = "checkout", bucket: str = B) -> None:
    """Sections 3 and 10.5: a text states only what the evidence shows, with
    its count, and names the rival's kind exactly."""
    texts = " ".join((seen.warnings + json.dumps(seen.save_envelope) + json.dumps(seen.rekey_envelope)).split())
    checkout = "1 of its file rows lie in another checkout"
    folder = f"1 of its file rows lie in another project folder named '{bucket}'"
    if reason == "foreign_rows" and kind == "checkout":
        assert checkout in texts and "another project folder" not in texts, texts
    elif reason == "foreign_rows" and kind == "folder":
        assert folder in texts and "another checkout" not in texts, texts
    elif reason == "foreign_rows":
        assert f"1 of its file rows lie in another checkout and 1 lie in another project folder named '{bucket}'" in texts, texts
    else:
        assert "lie in another checkout" not in texts and "another project folder" not in texts, texts
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
    ("canonical", _evidence(neutral_outside=1), _sessions(here=1), (True, None, False, "included_unverified", None)),
    ("canonical", _evidence(contained=1, foreign=1), None, (False, None, False, "unproven", "foreign_rows")),
    ("canonical", _evidence(contained=1), _sessions(elsewhere=1), (False, None, False, "unproven", "recorded_elsewhere")),
    ("canonical", _evidence(foreign=1), None, (False, None, False, "excluded", "foreign_rows")),
    # Section 10.2: elsewhere-only evidence is excluded in every scope.
    ("canonical", _evidence(relative=1), _sessions(elsewhere=1), (False, None, False, "excluded", "recorded_elsewhere")),
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


# ===========================================================================
# Amendment 2 (spec section 10): the rows section 10.8 adds or changes, and
# the C1, SR1, T1 and G1 arms. Rows 13 and 17 changed above. The new rows are
# A21..A30 here, because this module's Bugbot rows already hold 21 and 22.
# ===========================================================================


def _marked(path: Path) -> Path:
    """A non-git folder marked as a project (it holds `.claude`)."""
    (path / ".claude").mkdir(parents=True, exist_ok=True)
    return path


FOLDER_KIND[row_17] = "folder"


def row_a21b(tmp_path, canonical):
    """A gated non-git cwd-marker folder B; the bucket's session was
    recorded at its own subfolder B/sub, which is a subfolder launch
    (neutral), never elsewhere."""
    root = _marked(tmp_path / "x" / B)
    (root / "sub").mkdir()
    _session_at(tmp_path, root / "sub", "subs")
    return Layout(root, tmp_path / "store.db", sessions={"subs": root / "sub"})


def row_a23(tmp_path, canonical):
    """F1 as first measured: a marked non-git plain/victim holds the rows; the
    session runs in a cwd-marker non-git other/victim."""
    victim = _marked(tmp_path / "plain" / "victim")
    root = _marked(tmp_path / "other" / "victim")
    return Layout(root, tmp_path / "store.db", files=[_touch(victim / "x.py")], bucket="victim")


def row_a23b(tmp_path, canonical):
    """c3/data/nv has no marker, but a session record names it; the bucket's
    own memory was written by an unrecorded session, so only the record
    marks the folder. A gated no-origin repository nv runs."""
    holder = tmp_path / "c3" / "data" / "nv"
    files = [_touch(holder / "x.py")]
    _session_at(tmp_path, holder, "other")
    root = make_repo(tmp_path / "r" / "nv")
    return Layout(root, tmp_path / "store.db", files=files, bucket="nv",
                  memories=[(MEMORY_ID, "unrecorded")])


def row_a23c(tmp_path, canonical):
    """An enclosing linked worktree whose `.git` file cannot be read (a FIFO)
    encloses a no-origin repository named like the bucket: its pre-change
    name is unknown, so the carve-out fails closed."""
    main = make_repo(tmp_path / "m" / "main")
    worktree = make_worktree(main, tmp_path / "wt" / "feat")
    files = [_touch(worktree / "src.py")]
    root = make_repo(worktree / "vendor" / B)
    (worktree / ".git").unlink()
    os.mkfifo(worktree / ".git")
    return Layout(root, tmp_path / "store.db", files=files)


def row_a24(tmp_path, canonical):
    """A row under the root and a row under <home>/.config/B (no marker)."""
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db",
                  files=[_touch(root / "src.py"), _touch(Path.home() / ".config" / B / "x")])


def row_a25(tmp_path, canonical):
    """A row under the root and a tracked FILE named like the bucket."""
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=[_touch(root / "src.py"), _touch(tmp_path / "notes" / B)])


def row_a25b(tmp_path, canonical):
    """A tracked FILE named like the bucket whose parent holds `.claude`: the
    marker is on a folder not named B."""
    root = _repo(tmp_path / "work" / B, canonical)
    _marked(tmp_path / "notes")
    return Layout(root, tmp_path / "store.db", files=[_touch(tmp_path / "notes" / B)])


def row_a26(tmp_path, canonical):
    """Rows under a non-git /data/B WITHOUT a marker: neutral."""
    root = _repo(tmp_path / "work" / B, canonical)
    return Layout(root, tmp_path / "store.db", files=[_touch(tmp_path / "data" / B / "x.py")])


PROJ_REMOTE = "https://github.com/o/proj.git"


def _ns4(tmp_path):
    main = make_repo(tmp_path / "proj", remote=PROJ_REMOTE)
    feature = make_worktree(main, tmp_path / "proj-feat")
    return main, feature


def row_a27(tmp_path, canonical):
    """N-S4: the bucket `proj` holds rows in the linked worktree proj-feat,
    whose pre-change name is proj. Canonical: run from the main checkout.
    Folder-name: run from a no-origin repo at proj-feat/vendor/proj."""
    main, feature = _ns4(tmp_path)
    files = [_touch(feature / "src.py")]
    root = main if canonical else make_repo(feature / "vendor" / "proj")
    return Layout(root, tmp_path / "store.db", files=files, bucket="proj")


def row_a28(tmp_path, canonical):
    """N-S4 by session: no file rows; the bucket's session was recorded at
    proj-feat, a folder enclosing the nested repo whose pre-change name is proj."""
    _main, feature = _ns4(tmp_path)
    _session_at(tmp_path, feature, "feat")
    root = make_repo(feature / "vendor" / "proj")
    return Layout(root, tmp_path / "store.db", bucket="proj", sessions={"feat": feature})


def row_a30(tmp_path, canonical):
    """A $HOME-style dotfiles `.git` DIRECTORY (its name is not B) encloses the
    root; a row under its notes and a row under the root. The positive
    control for the pre-change-name carve-out: the notes row is neutral."""
    user = make_repo(tmp_path / "dotfiles")
    root = _repo(user / "code" / B, canonical)
    return Layout(root, tmp_path / "store.db",
                  files=[_touch(user / "notes" / "n.md"), _touch(root / "src.py")])


def row_t1_joined(tmp_path, canonical):
    """T1's joined form: one row in another checkout and one in a marked
    project folder of that name."""
    root = _repo(tmp_path / "work" / B, canonical)
    folder = _marked(tmp_path / "data" / B)
    return Layout(root, tmp_path / "store.db",
                  files=[_other_checkout(tmp_path) / "src.py", _touch(folder / "x.py")])


FOLDER_KIND.update({row_a23: "folder", row_a23b: "folder", row_t1_joined: "joined"})

EXCLUDED_FOREIGN = (False, "excluded", "foreign_rows", "refused")
AMENDED_ROWS = [
    (row_a21b, None, FOLDER_OK),
    (row_a23, None, EXCLUDED_FOREIGN),
    (row_a23b, None, EXCLUDED_FOREIGN),
    (row_a23c, None, EXCLUDED_FOREIGN),
    (row_a24, VERIFIED, FOLDER_OK),
    (row_a25, VERIFIED, FOLDER_OK),
    (row_a25b, NO_SESSION, FOLDER_OK),
    (row_a26, NO_SESSION, FOLDER_OK),
    (row_a27, VERIFIED, EXCLUDED_FOREIGN),
    (row_a28, None, (False, "excluded", "recorded_elsewhere", "refused")),
    # A29 (a dotfiles repository not named B enclosing the root) is row 7.
    (row_a30, VERIFIED, FOLDER_OK),
    (row_t1_joined, EXCLUDED_FOREIGN, EXCLUDED_FOREIGN),
]


def _amended_cases():
    for builder, canonical, folder in AMENDED_ROWS:
        name = builder.__name__.removeprefix("row_")
        if canonical is not None:
            yield pytest.param(builder, True, canonical, id=f"{name}-canonical")
        if folder is not None:
            yield pytest.param(builder, False, folder, id=f"{name}-folder-name")


@pytest.mark.parametrize("builder, canonical, expect", list(_amended_cases()))
def test_every_consumer_follows_the_amended_row(builder, canonical, expect, tmp_path, monkeypatch, capfd, caplog):
    test_every_consumer_follows_the_row(builder, canonical, expect, tmp_path, monkeypatch, capfd, caplog)
    if builder is row_a30:
        # The carve-out's positive control: the dotfiles row is neutral.
        _fresh()
        memory = memory_api.PACTMemory(session_id="s", db_path=tmp_path / "store.db")
        memory._read_project_scopes()
        if canonical:
            assert memory._legacy_scope_state["detail"]["neutral_outside"] == 1, memory._legacy_scope_state


def test_a21_a_home_landing_id_is_ungated(tmp_path, monkeypatch, capfd):
    """B1: cwd ~/scratch lands on $HOME (it holds .claude). The bucket is the
    home folder's name and a session recorded at ~/scratch wrote it. Read and
    two saves in a row go through: the user chose that id."""
    assert_outside_repository(tmp_path)
    home = Path.home()
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    scratch = home / "scratch"
    scratch.mkdir()
    _session_at(tmp_path, scratch, "s1")
    layout = Layout(scratch, tmp_path / "store.db", bucket=home.name, sessions={"s1": scratch})
    _seed(layout)
    monkeypatch.chdir(scratch)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    store = ["--db-path", str(layout.store)]
    code, listed = _cli(["list", *store], capfd)
    assert code == 0 and any(row["id"] == MEMORY_ID for row in listed["result"]), listed
    for _ in range(2):
        code, saved = _cli(["save", json.dumps({"context": "new"}), *store], capfd)
        assert code == 0, saved


def _user_setting(value: str) -> None:
    path = memory_api.get_user_memory_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"project_id": value}), encoding="utf-8")


def test_a22_a_user_setting_id_is_ungated(tmp_path, tmp_path_factory, monkeypatch, capfd):
    """B2: the user setting names 'notes'; a row in that bucket was written by
    a session recorded at work/d1, and the read runs from work/d2. d1 lies
    outside the folder the cwd walk lands on (the test root carries a
    `.claude`), so the session is elsewhere evidence a gate would act on."""
    assert_outside_repository(tmp_path)
    d1 = tmp_path_factory.mktemp("elsewhere") / "work" / "d1"
    d2 = tmp_path / "work" / "d2"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    _session_at(tmp_path, d1, "s1")
    _user_setting("notes")
    layout = Layout(d2, tmp_path / "store.db", bucket="notes", sessions={"s1": d1})
    _seed(layout)
    monkeypatch.chdir(d2)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    store = ["--db-path", str(layout.store)]
    code, listed = _cli(["list", *store], capfd)
    assert code == 0 and any(row["id"] == MEMORY_ID for row in listed["result"]), listed
    code, saved = _cli(["save", json.dumps({"context": "new"}), *store], capfd)
    assert code == 0, saved


def _g1_home(tmp_path, outside):
    home = Path.home()
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    cwd = home / "scratch"
    cwd.mkdir()
    rival = make_repo(outside / "rival" / home.name)
    return cwd, home.name, [_touch(rival / "src.py")], {}


def _g1_setting(tmp_path, outside):
    cwd = tmp_path / "work" / "d2"
    cwd.mkdir(parents=True)
    _user_setting("notes")
    rival = make_repo(outside / "rival" / "notes")
    return cwd, "notes", [_touch(rival / "src.py")], {}


def _g1_supplied(tmp_path, outside):
    root = make_repo(tmp_path / "work" / B)
    return root, B, [_other_checkout(tmp_path) / "src.py"], {"project_id": B}


@pytest.mark.parametrize("source", [_g1_home, _g1_setting, _g1_supplied], ids=["home-landing", "user-setting", "supplied"])
def test_g1_an_ungated_source_is_never_refused(source, tmp_path, tmp_path_factory, monkeypatch, capfd):
    """Each source the user chose keeps the pre-change behaviour even with
    foreign evidence present: no legacy_scope record, and no
    FOLDER_NAME_BUCKET_REFUSED. (A cwd-marker folder with the same evidence IS
    refused: row A23.)"""
    assert_outside_repository(tmp_path)
    # The rival lies outside the test root, which the cwd walk can land on.
    cwd, bucket, files, payload = source(tmp_path, tmp_path_factory.mktemp("outside"))
    layout = Layout(cwd, tmp_path / "store.db", files=files, bucket=bucket)
    _seed(layout)
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    store = ["--db-path", str(layout.store)]
    code, shown = _cli(["project-id", "show", *store], capfd)
    assert code == 0, shown
    if not payload:
        assert shown["result"].get("legacy_scope") is None, shown["result"].get("legacy_scope")
    code, saved = _cli(["save", json.dumps({"context": "new", **payload}), *store], capfd)
    assert code == 0, saved


def test_g1_control_the_same_evidence_refuses_a_gated_folder(tmp_path, tmp_path_factory, monkeypatch, capfd):
    """Control for G1: with the user setting gone, the same folder and the same
    rival are judged, and the save is refused."""
    assert_outside_repository(tmp_path)
    outside = tmp_path_factory.mktemp("outside")
    cwd = _marked(tmp_path / "work" / "notes")
    rival = make_repo(outside / "rival" / "notes")
    layout = Layout(cwd, tmp_path / "store.db", files=[_touch(rival / "src.py")], bucket="notes")
    _seed(layout)
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    code, saved = _cli(["save", json.dumps({"context": "new"}), "--db-path", str(layout.store)], capfd)
    assert code == 1 and saved["error"] == "FOLDER_NAME_BUCKET_REFUSED", saved


def test_c1_a_store_that_moves_during_the_read_is_decided_again(tmp_path, monkeypatch):
    """Section 10.6: when the store's stamp moves during the guarded read, the
    locked repeat decides again (a second census) and its answer stands.
    Here the first census says this repository wrote the bucket and the
    second says another folder did, so the write guard must refuse."""
    assert_outside_repository(tmp_path)
    layout = row_6(tmp_path, False)
    _seed(layout)
    monkeypatch.chdir(layout.root)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    calls = []
    real = memory_api._legacy_session_census

    def census(*args, **kwargs):
        counts = real(*args, **kwargs)
        calls.append(dict(counts))
        if len(calls) > 1:
            counts = {**counts, "here": 0, "elsewhere": counts["here"] + counts["elsewhere"]}
        return counts

    stamps = iter(range(10_000))
    monkeypatch.setattr(memory_api, "_legacy_session_census", census)
    monkeypatch.setattr(memory_api, "_store_stamp", lambda store: (next(stamps), 0, 0, False))
    _fresh()
    with memory_api.detection_pass():
        memory = memory_api.PACTMemory(session_id="s", db_path=layout.store)
        with pytest.raises(memory_api.FolderNameBucketRefusedError):
            memory._project_id_for_write()
    assert len(calls) == 2, calls


def test_c1_an_unmoved_store_runs_one_census_per_save(tmp_path, monkeypatch, capfd):
    assert_outside_repository(tmp_path)
    layout = row_6(tmp_path, False)
    _seed(layout)
    monkeypatch.chdir(layout.root)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    calls = []
    real = memory_api._legacy_session_census
    monkeypatch.setattr(memory_api, "_legacy_session_census", lambda *a, **k: calls.append(1) or real(*a, **k))
    code, saved = _cli(["save", json.dumps({"context": "new"}), "--db-path", str(layout.store)], capfd)
    assert code == 0, saved
    assert len(calls) == 1, len(calls)


SR1_DECLINED = [
    "https://local.invalid/abc/x",
    "https://LOCAL.INVALID/abc/x",
    "https://local.invalid:2222/abc/x",
    "git@local.invalid:x/y",
    "ssh://u@local.invalid/x",
    "ssh://invalid/o/r",
]


@pytest.mark.parametrize("url", SR1_DECLINED)
def test_sr1_an_invalid_host_derives_no_id(url):
    """Section 10.7: no remote can derive a local.invalid/... id (RFC 2606)."""
    from scripts import project_identity as pi

    assert pi.normalize_remote_url(url) is None
    outcome, _value = pi.classify_remote_origin(url)
    assert outcome == pi.OUTCOME_DECLINE, outcome


@pytest.mark.parametrize("url, expected", [
    ("https://example.com/o/r", "example.com/o/r"),
    ("https://invalid-host.example/o/r", "invalid-host.example/o/r"),
])
def test_sr1_control_other_hosts_still_canonicalise(url, expected):
    from scripts import project_identity as pi

    assert pi.normalize_remote_url(url) == expected


def test_sr1_a_local_invalid_pin_is_still_accepted(tmp_path):
    from scripts import project_identity as pi

    common = tmp_path / "common"
    common.mkdir()
    (common / "pact-project-id").write_text("local.invalid/0123456789ab/x\n", encoding="utf-8")
    assert pi.read_override(common).value == "local.invalid/0123456789ab/x"
