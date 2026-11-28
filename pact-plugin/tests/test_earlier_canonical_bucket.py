"""
Location: pact-plugin/tests/test_earlier_canonical_bucket.py
Summary: A canonical bucket of this repository under an earlier id (the origin
renamed, transferred or respelled) is not read, but a read names it once with
the command that moves it into this checkout's id, and `project-id show` gives
it that command. Placement takes only a file row linked by a memory saved after
this checkout's clone (dated), never a file that merely exists here, so an
unrelated bucket sharing a README stays silent, as does one whose rows lie
elsewhere; and a store whose other buckets have no row under this checkout asks
git nothing more.
Used by: pytest; drives PACTMemory._read_project_scopes and the CLI's
`project-id show` on a real repository and store.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo, run_git

from scripts import memory_api
from scripts.database import db_connection, ensure_initialized
from scripts.memory_api import PACTMemory

OLD = "github.com/acme/widget"
NEW = "github.com/acme/renamed"
SHARED = "github.com/a/x"
ELSEWHERE = "github.com/other/thing"
LIVE = "github.com/acme/beta"
NESTED = "github.com/acme/lib"
OLD_STAMP = "2020-01-01T00:00:00+00:00"


def _later() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()


def _memory(conn, memory_id, bucket, stamp, path=None):
    conn.execute(
        "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
        "VALUES (?, 'c', ?, ?, ?)",
        (memory_id, bucket, stamp, stamp),
    )
    if path is not None:
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
            ("f-" + memory_id, str(path), bucket),
        )
        conn.execute(
            "INSERT INTO memory_files (memory_id, file_id) VALUES (?, ?)",
            (memory_id, "f-" + memory_id),
        )


def _fresh(monkeypatch, repo):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for name in ("_LEGACY_SCOPE_CACHE", "_EARLIER_CANONICAL", "_EARLIER_CANONICAL_WARNED"):
        getattr(memory_api, name).clear()


@pytest.fixture
def renamed(tmp_path, monkeypatch):
    """A checkout whose origin was renamed after it saved a memory linked to a
    file in it (and one with no file); a bucket of another repository linked
    (in 2020) to the README this checkout also has; one whose row lies
    elsewhere; and a live repository beta whose memory, saved after this
    clone, links a file here while its other memory links its own file."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "widget", remote="https://github.com/acme/widget.git")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x\n", encoding="utf-8")
    (repo / "README").write_text("r\n", encoding="utf-8")
    elsewhere = tmp_path / "other" / "b.py"
    elsewhere.parent.mkdir()
    elsewhere.write_text("y\n", encoding="utf-8")
    beta = make_repo(tmp_path / "beta", remote="https://github.com/acme/beta.git")
    (beta / "own.py").write_text("z\n", encoding="utf-8")
    lib = make_repo(repo / "vendor" / "lib", remote="https://github.com/acme/lib.git")
    (lib / "l.py").write_text("l\n", encoding="utf-8")
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        _memory(conn, "m1", OLD, _later(), repo / "src" / "a.py")
        _memory(conn, "m2", SHARED, OLD_STAMP, repo / "README")
        _memory(conn, "m3", ELSEWHERE, _later(), elsewhere)
        _memory(conn, "m1b", OLD, _later())
        # This checkout's own memory linked to the same file row: it is not
        # OLD's, so it neither dates OLD nor counts among OLD's linked ones.
        _memory(conn, "m8", NEW, _later())
        conn.execute("INSERT INTO memory_files (memory_id, file_id) VALUES ('m8', 'f-m1')")
        _memory(conn, "m4", LIVE, _later(), repo / "src" / "a.py")
        _memory(conn, "m5", LIVE, _later(), beta / "own.py")
        _memory(conn, "m6", NESTED, _later(), repo / "src" / "a.py")
        _memory(conn, "m7", NESTED, _later(), lib / "l.py")
        conn.commit()
    run_git("remote", "set-url", "origin", "git@github.com:Acme/Renamed.git", cwd=repo)
    _fresh(monkeypatch, repo)
    return repo, store


def test_a_renamed_origins_placed_bucket_is_named_with_its_command(renamed, caplog):
    """RED WHEN an earlier canonical bucket placed in this checkout is hidden
    with no notice, or when another repository's bucket is named: one whose
    only row here is a README it shares (linked before this clone), or one
    whose rows lie outside this checkout."""
    _repo, store = renamed
    memory = PACTMemory(session_id="s", db_path=store)
    with caplog.at_level(logging.WARNING):
        assert memory._read_project_scopes() == (NEW,)
        memory._read_project_scopes()
    notices = [r.getMessage() for r in caplog.records if "is not this checkout's id" in r.getMessage()]
    assert len(notices) == 1, notices
    assert f"project bucket {OLD!r} holds 2 memories, 1 linked to files in this checkout" in notices[0], notices
    assert "may be this repository under an earlier id" in notices[0], notices
    assert "transferred" not in notices[0], notices
    assert f"--from={OLD}" in notices[0] and f"--to={NEW}" in notices[0], notices
    assert SHARED not in caplog.text and ELSEWHERE not in caplog.text
    assert LIVE not in caplog.text, "a live repository cross-linking one file here was named"
    assert NESTED not in caplog.text, "a checkout nested in this one was named"


def test_project_id_show_gives_the_earlier_id_a_rekey_command(renamed, capsys):
    from scripts.cli import main

    _repo, store = renamed
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "show", "--db-path", str(store)])
    assert exit_info.value.code == 0
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)["result"]["candidates"]}
    assert rows[OLD]["relation"] == "earlier_id", rows
    assert f"--from={OLD}" in rows[OLD]["rekey"] and f"--to={NEW}" in rows[OLD]["rekey"], rows
    for other in (SHARED, ELSEWHERE, LIVE, NESTED):
        assert (rows[other]["relation"], rows[other]["rekey"]) == ("other", None), rows


@pytest.mark.parametrize("apply", [False, True], ids=["dry-run", "apply"])
def test_the_earlier_id_moves_into_this_checkouts_id_with_no_override(renamed, capfd, apply):
    """An earlier id is this repository's own bucket, so its printed move runs
    with no --from-another-repository, while another repository's canonical
    bucket into the same id is refused. RED WHEN the earlier id is refused, or
    the foreign one is not."""
    from scripts.cli import main

    _repo, store = renamed
    for source, expected in ((ELSEWHERE, 1), (OLD, 0)):
        with pytest.raises(SystemExit) as exit_info:
            main(["project-id", "rekey", f"--from={source}", f"--to={NEW}",
                  "--db-path", str(store), *(["--apply"] if apply else [])])
        captured = capfd.readouterr()
        envelope = json.loads(captured.out or captured.err)
        assert exit_info.value.code == expected, envelope
    assert envelope["result"]["memories"] == 2, envelope


@pytest.mark.parametrize("source, reason", [
    (SHARED, "(its 1 file row here comes only from memories saved before this clone and does not show"),
    (ELSEWHERE, "(no file row here places it)"),
])
def test_the_refusal_says_why_a_canonical_bucket_is_no_detected_earlier_id(renamed, capfd, source, reason):
    """A repository cloned again after its rename has its old rows inside the
    checkout, saved before this clone, so they do not make the bucket a
    detected earlier id; the refusal says that, not that no row is here.
    RED WHEN the reason is not the bucket's own."""
    from scripts.cli import main

    _repo, store = renamed
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "rekey", f"--from={source}", f"--to={NEW}", "--db-path", str(store)])
    captured = capfd.readouterr()
    envelope = json.loads(captured.out or captured.err)
    assert exit_info.value.code == 1 and envelope["error"] == "REKEY_REFUSED", envelope
    assert reason in envelope["message"], envelope
    assert "--from-another-repository" in envelope["message"], envelope
    assert "earlier id of this repository that could not be detected" in envelope["message"], envelope
    hint = "cloned again, whose rows here predate this clone" if source == SHARED else "that has no file row here"
    assert hint in envelope["message"], envelope
    assert envelope["message"].count(", so ") <= 1, envelope


@pytest.mark.parametrize("verb", [["list"], ["search", "c"]])
def test_list_and_search_name_the_earlier_id_on_stdout(renamed, capfd, verb):
    """The earlier id's bucket is left out of the read, so list and search
    name it on stdout with its rekey command, beside result. RED WHEN only
    stderr names it."""
    from scripts.cli import main

    _repo, store = renamed
    with pytest.raises(SystemExit) as exit_info:
        main([*verb, "--db-path", str(store)])
    assert exit_info.value.code == 0
    envelope = json.loads(capfd.readouterr().out)
    earlier = {row["id"]: row["rekey"] for row in envelope["earlier_ids"]}
    assert list(earlier) == [OLD], envelope
    assert f"--from={OLD}" in earlier[OLD] and f"--to={NEW}" in earlier[OLD], earlier


def test_buckets_with_no_row_under_this_checkout_cost_no_git_call(tmp_path, monkeypatch):
    """Forty other canonical buckets whose rows lie elsewhere, and a live
    repository that cross-links one file here: the scan asks for the gate
    roots (a `git worktree list`) not once. RED WHEN the scan walks every
    canonical bucket with file rows, or one with an existing file outside."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/o/proj.git")
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        for index in range(40):
            _memory(conn, f"m{index}", f"github.com/o/other{index}", _later(),
                    tmp_path / "elsewhere" / f"{index}.py")
        # A live repository cross-linking one file here: its own file outside
        # rules it out before any git call.
        own = tmp_path / "beta" / "own.py"
        own.parent.mkdir()
        own.write_text("b\n", encoding="utf-8")
        (repo / "x.py").write_text("x\n", encoding="utf-8")
        _memory(conn, "mx", "github.com/o/beta", _later(), repo / "x.py")
        _memory(conn, "my", "github.com/o/beta", _later(), own)
        conn.commit()
    _fresh(monkeypatch, repo)
    asked = []
    real = PACTMemory._legacy_gate_roots
    monkeypatch.setattr(PACTMemory, "_legacy_gate_roots",
                        lambda self, root: asked.append(root) or real(self, root))
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._earlier_canonical_buckets("github.com/o/proj") == {}
    assert asked == [], asked


def test_an_old_shared_file_alone_costs_no_git_call(tmp_path, monkeypatch):
    """A bucket whose only row here is a README this checkout shares, linked
    by a memory older than the clone, can never be named: a read asks for the
    gate roots (a `git worktree list`) not once. RED WHEN the pre-git filter
    keeps a bucket with no row linked after the clone."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/o/proj.git")
    (repo / "README").write_text("r\n", encoding="utf-8")
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        _memory(conn, "m1", SHARED, OLD_STAMP, repo / "README")
        conn.commit()
    _fresh(monkeypatch, repo)
    asked = []
    real = PACTMemory._legacy_gate_roots
    monkeypatch.setattr(PACTMemory, "_legacy_gate_roots",
                        lambda self, root: asked.append(root) or real(self, root))
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._read_project_scopes() == ("github.com/o/proj",)
    assert memory._earlier_canonical == {}
    assert asked == [], asked


def test_a_checkout_with_no_description_names_no_earlier_id(tmp_path, monkeypatch, caplog):
    """No `.git/description` dates the clone, so no row is dated and no
    earlier id is named: neither an old bucket on a shared README nor one
    linked today. RED WHEN a missing description counts as dated on the
    earlier-id path, in the evidence walk (see the bucket_evidence test) or
    the pre-git filter (a gate-roots call). The rename test above is the
    control with a description."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/o/proj.git")
    (repo / "README").write_text("r\n", encoding="utf-8")
    (repo / "a.py").write_text("a\n", encoding="utf-8")
    os.remove(repo / ".git" / "description")
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        _memory(conn, "m1", SHARED, OLD_STAMP, repo / "README")
        _memory(conn, "m2", OLD, _later(), repo / "a.py")
        conn.commit()
    _fresh(monkeypatch, repo)
    asked = []
    real = PACTMemory._legacy_gate_roots
    monkeypatch.setattr(PACTMemory, "_legacy_gate_roots",
                        lambda self, root: asked.append(root) or real(self, root))
    memory = PACTMemory(session_id="s", db_path=store)
    with caplog.at_level(logging.WARNING):
        memory._read_project_scopes()
    assert memory._earlier_canonical == {}
    assert "is not this checkout's id" not in caplog.text
    assert asked == [], "the pre-git filter let an undated bucket ask git"


def test_a_bucket_with_no_memories_is_not_an_earlier_id(tmp_path, monkeypatch):
    """A canonical bucket whose only content is a file row here, linked by a
    memory of ANOTHER bucket saved after the clone, has nothing of its own
    to move. RED WHEN it enters the earlier-id map (and project-id show's
    earlier_id row) with a rekey command."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/o/proj.git")
    (repo / "a.py").write_text("a\n", encoding="utf-8")
    store = tmp_path / "store.db"
    stamp = _later()
    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
            "VALUES ('m1', 'c', 'github.com/o/proj', ?, ?)",
            (stamp, stamp),
        )
        conn.execute("INSERT INTO files (id, path, project_id) VALUES ('f1', ?, ?)",
                     (str(repo / "a.py"), OLD))
        conn.execute("INSERT INTO memory_files (memory_id, file_id) VALUES ('m1', 'f1')")
        conn.commit()
    _fresh(monkeypatch, repo)
    memory = PACTMemory(session_id="s", db_path=store)
    memory._read_project_scopes()
    assert memory._earlier_canonical == {}


def test_a_submodule_is_not_an_earlier_id_of_its_superproject(tmp_path, monkeypatch, caplog):
    """A real submodule with its own origin: its session's dated memory on a
    file in it is the submodule's bucket, not the superproject's under an
    earlier id. RED WHEN the earlier-id path counts submodule rows as the
    superproject's (the submodule exemption)."""
    assert_outside_repository(tmp_path)
    upstream = make_repo(tmp_path / "libup", remote="https://github.com/acme/lib.git")
    sup = make_repo(tmp_path / "super", remote="https://github.com/acme/super.git")
    run_git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
            str(upstream), "lib", cwd=sup)
    lib = sup / "lib"
    assert (lib / ".git").is_file()
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        _memory(conn, "m1", "github.com/acme/lib", _later(), lib / "README")
        conn.commit()
    _fresh(monkeypatch, sup)
    memory = PACTMemory(session_id="s", db_path=store)
    with caplog.at_level(logging.WARNING):
        assert memory._read_project_scopes() == ("github.com/acme/super",)
    assert memory._earlier_canonical == {}
    assert "github.com/acme/lib" not in caplog.text


def test_a_submodule_row_does_not_hide_a_superprojects_earlier_id(tmp_path, monkeypatch, caplog):
    """The superproject's earlier bucket links a file of its own and a file
    in its submodule, both after the clone: the submodule row is skipped,
    neither placing nor ruling out the bucket, so the earlier id is named.
    RED WHEN a submodule row counts as a row outside this checkout."""
    assert_outside_repository(tmp_path)
    upstream = make_repo(tmp_path / "libup", remote="https://github.com/acme/lib.git")
    sup = make_repo(tmp_path / "super", remote="https://github.com/acme/super.git")
    run_git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
            str(upstream), "lib", cwd=sup)
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        _memory(conn, "m1", "github.com/acme/oldsuper", _later(), sup / "README")
        _memory(conn, "m2", "github.com/acme/oldsuper", _later(), sup / "lib" / "README")
        conn.commit()
    _fresh(monkeypatch, sup)
    memory = PACTMemory(session_id="s", db_path=store)
    with caplog.at_level(logging.WARNING):
        memory._read_project_scopes()
    assert "github.com/acme/oldsuper" in memory._earlier_canonical


def test_a_bucket_mostly_in_a_submodule_gets_no_rekey_command(tmp_path, monkeypatch, caplog, capsys):
    """Five memories link a file in the submodule and one links the
    superproject's README, all after the clone: the bucket is still named,
    but its submodule rows are left out of the linked count, the notice says
    they may be the submodule's, and no rekey command is offered (a whole-
    bucket rekey would move them into the superproject). RED WHEN a bucket
    with submodule rows is offered the command or counts them as linked here."""
    from scripts.cli import main

    assert_outside_repository(tmp_path)
    upstream = make_repo(tmp_path / "libup", remote="https://github.com/acme/lib.git")
    (upstream / "x.py").write_text("x\n", encoding="utf-8")
    run_git("add", "x.py", cwd=upstream)
    run_git("commit", "-qm", "x", cwd=upstream)
    sup = make_repo(tmp_path / "super", remote="https://github.com/acme/super.git")
    run_git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
            str(upstream), "lib", cwd=sup)
    store = tmp_path / "store.db"
    bucket = "github.com/acme/oldlib"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute("INSERT INTO files (id, path, project_id) VALUES ('fx', ?, ?)",
                     (str(sup / "lib" / "x.py"), bucket))
        for index in range(5):
            _memory(conn, f"s{index}", bucket, _later())
            conn.execute("INSERT INTO memory_files (memory_id, file_id) VALUES (?, 'fx')",
                         (f"s{index}",))
        _memory(conn, "r1", bucket, _later(), sup / "README")
        conn.commit()
    _fresh(monkeypatch, sup)
    memory = PACTMemory(session_id="s", db_path=store)
    with caplog.at_level(logging.WARNING):
        memory._read_project_scopes()
    assert bucket in memory._earlier_canonical
    assert memory._earlier_canonical[bucket] is None
    notice = [r.getMessage() for r in caplog.records if bucket in r.getMessage()]
    assert len(notice) == 1, notice
    assert "holds 6 memories, 1 linked to files in this checkout outside its submodules" in notice[0]
    assert "1 of its file rows lies in a submodule of this checkout" in notice[0]
    assert "--from=" not in notice[0], notice[0]
    _fresh(monkeypatch, sup)
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "show", "--db-path", str(store)])
    assert exit_info.value.code == 0
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)["result"]["candidates"]}
    assert (rows[bucket]["relation"], rows[bucket]["rekey"]) == ("earlier_id", None), rows


def test_many_submodule_rows_do_not_hide_the_notice(tmp_path, monkeypatch):
    """Twenty file rows in the submodule, on a connection whose SQLite allows
    fifteen bound variables: the bucket is still named, with one linked
    memory. RED WHEN the submodule rows are bound one per variable into the
    linked-count query, which then fails and hides every earlier id."""
    assert_outside_repository(tmp_path)
    upstream = make_repo(tmp_path / "libup", remote="https://github.com/acme/lib.git")
    sup = make_repo(tmp_path / "super", remote="https://github.com/acme/super.git")
    run_git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
            str(upstream), "lib", cwd=sup)
    store = tmp_path / "store.db"
    bucket = "github.com/acme/oldlib"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        for index in range(20):
            _memory(conn, f"s{index}", bucket, _later(), sup / "lib" / f"f{index}.py")
        _memory(conn, "r1", bucket, _later(), sup / "README")
        conn.commit()
    _fresh(monkeypatch, sup)
    memory = PACTMemory(session_id="s", db_path=store)
    conn = sqlite3.connect(str(store))  # the standard module: it can lower the limit
    try:
        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 15)
        assert memory._earlier_canonical_buckets("github.com/acme/super", conn) == {bucket: None}
    finally:
        conn.close()


def test_another_buckets_memories_do_not_date_an_earlier_id(tmp_path, monkeypatch):
    """Bucket widget has one memory and no link of its own; its file row here
    is linked only by memories of this checkout's own bucket, saved after the
    clone. RED WHEN any bucket's memory dates the row, in the pre-git filter
    (a gate-roots call) or in the evidence walk (widget is named)."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/acme/renamed.git")
    (repo / "a.py").write_text("a\n", encoding="utf-8")
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        _memory(conn, "w1", OLD, _later())
        conn.execute("INSERT INTO files (id, path, project_id) VALUES ('fw', ?, ?)",
                     (str(repo / "a.py"), OLD))
        for index in range(3):
            _memory(conn, f"n{index}", NEW, _later())
            conn.execute("INSERT INTO memory_files (memory_id, file_id) VALUES (?, 'fw')",
                         (f"n{index}",))
        conn.commit()
    _fresh(monkeypatch, repo)
    asked = []
    real = PACTMemory._legacy_gate_roots
    monkeypatch.setattr(PACTMemory, "_legacy_gate_roots",
                        lambda self, root: asked.append(root) or real(self, root))
    memory = PACTMemory(session_id="s", db_path=store)
    memory._read_project_scopes()
    assert memory._earlier_canonical == {}
    assert asked == [], "the pre-git filter let another bucket's memory date it"
    monkeypatch.setattr(PACTMemory, "_legacy_gate_roots", real)
    _fresh(monkeypatch, repo)
    memory = PACTMemory(session_id="s", db_path=store)
    monkeypatch.setattr(memory, "_project_common_dir", None)
    assert memory._earlier_canonical_buckets(NEW) == {}, "the evidence walk dated it"


def test_a_quiet_first_read_does_not_silence_a_later_one(renamed, caplog):
    """RED WHEN a read under legacy_notices_quiet (rekey --apply) caches the
    answer and the next read in the process never names the bucket."""
    _repo, store = renamed
    memory = PACTMemory(session_id="s", db_path=store)
    with caplog.at_level(logging.WARNING):
        with memory_api.legacy_notices_quiet():
            memory._read_project_scopes()
        assert "is not this checkout's id" not in caplog.text
        memory._read_project_scopes()
    assert f"project bucket {OLD!r}" in caplog.text


def test_the_legacy_check_reuses_the_earlier_scans_gate_roots(renamed, monkeypatch):
    """A legacy candidate (the folder's old name) and an earlier id in one
    read: the gate roots (a `git worktree list`) are asked for once. RED WHEN
    the legacy verdict asks again."""
    repo, store = renamed
    with db_connection(store) as conn:
        _memory(conn, "lg", "widget", OLD_STAMP, repo / "src" / "a.py")
        conn.commit()
    _fresh(monkeypatch, repo)
    asked = []
    real = PACTMemory._legacy_gate_roots
    monkeypatch.setattr(PACTMemory, "_legacy_gate_roots",
                        lambda self, root: asked.append(root) or real(self, root))
    memory = PACTMemory(session_id="s", db_path=store)
    scopes = memory._read_project_scopes()
    assert OLD in memory._earlier_canonical
    assert "widget" in scopes, scopes
    assert len(asked) == 1, asked


def test_a_reads_shared_worktree_list_does_not_outlive_it(renamed):
    """The earlier-id scan's worktree list, shared with the legacy check in
    the same read, is gone when the read returns, so a later write-path
    verdict lists again. RED WHEN it is cleared only by the next read."""
    _repo, store = renamed
    memory = PACTMemory(session_id="s", db_path=store)
    memory._read_project_scopes()
    assert OLD in memory._earlier_canonical
    assert getattr(memory, "_read_gate_roots", None) is None


def test_a_new_store_stamp_replaces_the_cached_answer(renamed):
    """RED WHEN each store stamp adds a cache entry that is never trimmed."""
    _repo, store = renamed
    memory = PACTMemory(session_id="s", db_path=store)
    memory._read_project_scopes()
    with db_connection(store) as conn:
        _memory(conn, "m9", ELSEWHERE, _later())
        conn.commit()
    memory._read_project_scopes()
    assert len(memory_api._EARLIER_CANONICAL) == 1, list(memory_api._EARLIER_CANONICAL)


def test_a_bucket_with_no_memories_is_not_named(caplog):
    with caplog.at_level(logging.WARNING):
        memory_api._note_earlier_canonical("s.db", OLD, NEW, 0, 0, "cmd")
    assert not caplog.records


def test_a_row_that_climbs_out_of_the_checkout_is_no_row_here(renamed, capfd):
    """The refusal's "file rows here" count is the gate's own row rule: a
    stored path that climbs out of this checkout with '..' is not here, even
    though its string starts with the checkout's path. RED WHEN the count
    compares the stored string."""
    from scripts.cli import main

    repo, store = renamed
    other = repo.parent / "other" / "b.py"
    with db_connection(store) as conn:
        _memory(conn, "m9", "github.com/z/climb", _later())
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES ('f-climb', ?, 'github.com/z/climb')",
            (str(repo) + "/../other/b.py",),
        )
        conn.execute("INSERT INTO memory_files (memory_id, file_id) VALUES ('m9', 'f-climb')")
        conn.commit()
    assert other.exists()
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "rekey", "--from=github.com/z/climb", f"--to={NEW}", "--db-path", str(store)])
    captured = capfd.readouterr()
    envelope = json.loads(captured.out or captured.err)
    assert exit_info.value.code == 1, envelope
    assert "(no file row here places it)" in envelope["message"], envelope
