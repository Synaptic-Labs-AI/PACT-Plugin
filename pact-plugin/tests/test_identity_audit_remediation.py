"""
Audit remediation for the repository-identity change.

Four arms, one per defect the closing audit found in the shipped code:

- the rekey target must be CANONICAL, because moving rows into a basename
  bucket is the misfile the command exists to prevent;
- the save envelope's legacy-candidate disclosure must carry the candidate's
  memory AND file counts and the literal command, not just its id;
- a rendered command carries a STORE-INFLUENCED bucket id, so it must be
  shell-quoted;
- the override reader must not allocate an arbitrarily large file before
  checking its size.

The sync-help arm lives beside its existing pin in
`test_project_dir_resolution.py`, where the phrase it extends is asserted.
"""

import json
import shlex
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import memory_api, project_identity
from scripts.cli import _project_candidates, main
from scripts.database import db_connection, ensure_initialized
from scripts.memory_api import PACTMemory, _DetectedIdentity

_LEGACY = "Legacy-Bucket"


def _make_store(store: Path) -> None:
    with db_connection(store) as conn:
        ensure_initialized(conn)


def _seed(store: Path, project_id: str, memory_id: str, path: str = "") -> None:
    """Insert one memory (and optionally one file row) under `project_id`."""
    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (memory_id, "seed", project_id,
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        if path:
            conn.execute(
                "INSERT INTO files (id, path, project_id, last_modified) "
                "VALUES (?, ?, ?, ?)",
                (memory_id[::-1], path, project_id, "2026-01-01T00:00:00+00:00"),
            )
        conn.commit()


class TestRekeyTargetMustBeCanonical:
    """A move target that names a folder rather than a repository is refused."""

    def test_a_supplied_non_canonical_target_is_bad_input(
        self, tmp_path, capfd
    ):
        store = tmp_path / "rekey.db"
        _make_store(store)
        with pytest.raises(SystemExit) as refused:
            main([
                "project-id", "rekey",
                "--from", "old-bucket",
                "--to", "not/../../a/canonical",  # contains .., so non-canonical
                "--apply",
                "--db-path", str(store),
            ])
        assert refused.value.code == 2, refused.value.code
        envelope = json.loads(capfd.readouterr().err)
        assert envelope["error"] == "INVALID_INPUT", envelope
        assert "canonical" in envelope["message"]

    def test_no_canonical_identity_refuses_with_exit_1(
        self, tmp_path, capfd, monkeypatch
    ):
        """A detected BASENAME is not a target; the command refuses."""
        store = tmp_path / "rekey-noid.db"
        _make_store(store)
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(
                lambda: _DetectedIdentity(_LEGACY, "basename", _LEGACY, None, None)
            ),
        )
        with pytest.raises(SystemExit) as refused:
            main([
                "project-id", "rekey",
                "--from", "old-bucket",
                "--apply",
                "--db-path", str(store),
            ])
        assert refused.value.code == 1, refused.value.code
        envelope = json.loads(capfd.readouterr().err)
        assert envelope["error"] == "PROJECT_IDENTITY_UNRESOLVED", envelope


class TestRenderedRekeyCommandIsQuoted:
    """A bucket id is store-influenced, so it is never rendered raw."""

    def test_a_hostile_bucket_id_round_trips_through_the_rendered_command(
        self, tmp_path
    ):
        store = tmp_path / "candidates.db"
        hostile = "Legacy; rm -rf /tmp/x $(id)"
        _seed(store, hostile, "a" * 32)

        candidates = _project_candidates(store)
        assert len(candidates) == 1, candidates
        entry = candidates[0]
        assert entry["id"] == hostile
        # The rendered command must be RUNNABLE (interpreter + absolute
        # script path, because no `pact-memory` executable exists) and survive
        # shlex parsing as ONE `--from` argument, against THIS store.
        argv = shlex.split(entry["rekey"])
        assert argv[0] == "python3", argv
        assert Path(argv[1]).is_absolute() and argv[1].endswith("cli.py"), argv
        assert argv[2:6] == ["project-id", "rekey", "--from", hostile], argv
        assert argv[argv.index("--db-path") + 1] == str(store), argv
        # And it must not contain the raw, unquoted id as a command fragment.
        assert f"--from {hostile}" not in entry["rekey"]


class TestSaveEnvelopeDisclosesTheCandidate:
    """Surface 4 carries the candidate's size and the literal command."""

    def test_the_legacy_scope_carries_counts_and_the_command(
        self, tmp_path, monkeypatch
    ):
        store = tmp_path / "surface4.db"
        _make_store(store)
        _seed(store, _LEGACY, "b" * 32, path="relative/linked.py")
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(
                lambda: _DetectedIdentity(
                    "host/owner/repo", "canonical", _LEGACY, None, tmp_path / "repo"
                )
            ),
        )
        with patch("scripts.memory_api._ensure_ready"):
            memory = PACTMemory(session_id="s", db_path=store)
            memory.save({"context": "surface-4"}, sync_to_claude=False)

        scope = memory.last_project_scope
        assert scope is not None
        legacy = scope["legacy_scope"]
        assert legacy["candidate"] == _LEGACY
        assert legacy["memory_count"] == 1, legacy
        # DISTINCT PATHS, via `count_project_file_paths`, not file rows.
        assert legacy["file_paths"] == 1, legacy
        rekey_argv = shlex.split(legacy["rekey"])
        assert rekey_argv[0] == "python3", rekey_argv
        assert rekey_argv[2:6] == ["project-id", "rekey", "--from", _LEGACY], rekey_argv
        assert rekey_argv[rekey_argv.index("--db-path") + 1] == str(store), rekey_argv
        assert legacy["canonical_count"] == 0, (
            "the disclosure exists because the canonical bucket was empty"
        )


class TestOverrideReadIsBounded:
    """A huge override file is classified without being allocated whole."""

    def test_an_oversized_file_reads_as_too_long(self, tmp_path):
        common = tmp_path / "common"
        common.mkdir()
        (common / "pact-project-id").write_bytes(b"a" * 8192)
        read = project_identity.read_override(common)
        assert read.value is None
        assert read.reason == "too_long"

    def test_a_usable_file_still_reads(self, tmp_path):
        """Positive control: the bound must not break the ordinary file."""
        common = tmp_path / "common"
        common.mkdir()
        (common / "pact-project-id").write_text("host/Owner/Repo\n", encoding="utf-8")
        read = project_identity.read_override(common)
        assert read.value == "host/Owner/Repo"
        assert read.reason is None

class TestExclusionDisclosureIsQuoted:
    """S2-sec: the exclusion warning is a render surface like the others.

    A bucket id is store-influenced, and this warning hands the reader a
    command to paste. An unquoted `--from <id>` there is a command-injection
    surface even though every sibling renderer quotes.
    """

    def test_the_exclusion_warning_renders_a_quoted_command(
        self, tmp_path, monkeypatch, caplog
    ):
        import logging

        from scripts import memory_api as memory_api_module

        hostile = "Legacy; echo PWNED"
        store = tmp_path / "excluded.db"
        _make_store(store)
        # An ABSOLUTE file row that lies OUTSIDE the identity root: the gate
        # excludes the bucket, which is what emits the warning.
        _seed(store, hostile, "c" * 32, path=str(tmp_path / "elsewhere" / "f.py"))

        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(
                lambda: _DetectedIdentity(
                    "host/owner/repo", "canonical", hostile, None, tmp_path / "repo"
                )
            ),
        )
        memory_api_module._EXCLUSION_WARNED.clear()
        memory = PACTMemory(session_id="s", db_path=store)
        with caplog.at_level(logging.WARNING):
            memory._read_project_scopes()

        text = caplog.text
        expected = memory_api_module.render_rekey_command(hostile, db_path=store)
        assert expected in text, f"the warning did not carry the quoted command: {text!r}"
        argv = shlex.split(expected)
        assert argv[0] == "python3", argv
        assert argv[2:6] == ["project-id", "rekey", "--from", hostile], argv
        assert argv[argv.index("--db-path") + 1] == str(store), argv
        assert f"--from {hostile}" not in text, (
            "the warning rendered the store-influenced id unquoted"
        )


class TestSuppliedIdentityDoesNotWiden:
    """A supplied id has no legacy candidate, so there is no gate root.

    PINNED, not coded around: the accessor returns the one-element scope and
    leaves the disclosure state unset.
    """

    def test_a_supplied_id_is_a_one_element_scope(self, tmp_path):
        store = tmp_path / "supplied.db"
        _make_store(store)
        memory = PACTMemory(project_id="supplied.example.com/me/x", db_path=store)
        assert memory._read_project_scopes() == ("supplied.example.com/me/x",)
        assert memory._legacy_scope_state is None


class TestEveryRenderSurfaceUsesTheHelper:
    """STRUCTURAL PIN: a sentinel in, the sentinel out, on EVERY surface.

    A hostile-id round-trip per surface cannot catch a future bypass: four
    spellings of a correct string all pass. Replacing the helper with a
    sentinel can, because a surface that hand-rolls the join renders the real
    command and the assertion reddens.
    """

    def test_a_sentinel_reaches_every_render_surface(
        self, tmp_path, monkeypatch, caplog, capfd
    ):
        import logging

        from scripts import cli as cli_module
        from scripts import memory_api as memory_api_module

        sentinel = "SENTINEL-REKEY-COMMAND"
        # Every call site may pass `db_path=`/`prog=`, so the stub accepts
        # keywords; a stub that took only the id would fail on the surfaces
        # that carry the store and redden for the wrong reason.
        monkeypatch.setattr(
            memory_api_module, "render_rekey_command", lambda _id, **_kw: sentinel
        )
        # `cli` imported the name into its own namespace, so the CLI surfaces
        # need their reference patched too.
        monkeypatch.setattr(
            cli_module, "render_rekey_command", lambda _id, **_kw: sentinel
        )

        hostile = "Legacy; echo PWNED"
        store = tmp_path / "surfaces.db"
        _make_store(store)
        _seed(store, hostile, "d" * 32, path=str(tmp_path / "elsewhere" / "f.py"))
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(
                lambda: _DetectedIdentity(
                    "host/owner/repo", "canonical", hostile, None, tmp_path / "repo"
                )
            ),
        )

        def _run(argv):
            with pytest.raises(SystemExit) as excinfo:
                main(argv)
            captured = capfd.readouterr()
            text = captured.out.strip() or captured.err.strip()
            return excinfo.value.code, json.loads(text)

        memory_api_module._EXCLUSION_WARNED.clear()
        memory = PACTMemory(session_id="s", db_path=store)

        # SITE 3 — the exclusion warning (memory_api.py's state render).
        with caplog.at_level(logging.WARNING):
            memory._read_project_scopes()
        assert sentinel in caplog.text, (
            "the exclusion warning bypassed the shared render helper"
        )

        # SITE 1 — the candidate list, driven through BOTH commands that
        # render it rather than by calling the helper's consumer directly.
        # A regression that hand-rolls the list inside `show` or inside
        # `rekey --candidates` would pass a direct `_project_candidates()`
        # assertion and reddens here.
        code, payload = _run(["project-id", "show", "--db-path", str(store)])
        assert code == 0
        assert [entry["rekey"] for entry in payload["result"]["candidates"]] == [
            sentinel
        ], "the show surface bypassed the shared render helper"
        code, payload = _run(
            ["project-id", "rekey", "--candidates", "--db-path", str(store)]
        )
        assert code == 0
        assert [entry["rekey"] for entry in payload["result"]["candidates"]] == [
            sentinel
        ], "the rekey --candidates surface bypassed the shared render helper"

        # SITE 4 — the save envelope.
        with patch("scripts.memory_api._ensure_ready"):
            memory.save({"context": "surface"}, sync_to_claude=False)
        legacy = memory.last_project_scope["legacy_scope"]
        assert legacy["rekey"] == sentinel, (
            "the save envelope bypassed the shared render helper"
        )

        # SITE 2 — the status envelope.
        with patch("scripts.memory_api._ensure_ready"):
            status = memory.get_status()
        rekeys = [entry["rekey"] for entry in status["project_candidates"]]
        assert rekeys and all(rekey == sentinel for rekey in rekeys), (
            f"the status envelope bypassed the shared render helper: {rekeys!r}"
        )

        # SITE 5 — the CLI examples, a separate render call in build_parser.
        assert sentinel in cli_module.build_parser().format_help(), (
            "the CLI examples bypassed the shared render helper"
        )

    def test_the_save_envelope_quotes_a_hostile_bucket_id(
        self, tmp_path, monkeypatch
    ):
        """The RENDERING arm beside the sentinel's MECHANISM arm.

        The sentinel above proves every surface calls the helper; it is
        identity under quoting, so it cannot catch the helper itself losing
        the quotes. This arm seeds a hostile bucket id and requires the save
        envelope's command to survive `shlex.split` as ONE `--from`
        argument.
        """
        hostile = "Legacy; echo PWNED"
        store = tmp_path / "hostile-save.db"
        _make_store(store)
        _seed(store, hostile, "e" * 32, path="relative/linked.py")
        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(
                lambda: _DetectedIdentity(
                    "host/owner/repo", "canonical", hostile, None, tmp_path / "repo"
                )
            ),
        )

        with patch("scripts.memory_api._ensure_ready"):
            memory = PACTMemory(session_id="s", db_path=store)
            memory.save({"context": "hostile envelope"}, sync_to_claude=False)

        legacy = memory.last_project_scope["legacy_scope"]
        assert legacy["candidate"] == hostile
        argv = shlex.split(legacy["rekey"])
        assert argv[0] == "python3", argv
        assert argv[2:6] == ["project-id", "rekey", "--from", hostile], argv
        assert argv[argv.index("--db-path") + 1] == str(store), (
            f"the save envelope did not carry the store the bucket was read "
            f"from: {argv!r}"
        )
        assert f"--from {hostile}" not in legacy["rekey"], (
            "the save envelope rendered the store-influenced id unquoted"
        )


class TestDeclaredRungDisclosesAnOperatorPin:
    """A pin that decides a DECLARED rung must disclose as `override file`.

    Reporting the declaration label tells a reader the environment chose the
    id, when in fact a human did.
    """

    def test_a_declared_repo_pinned_by_override_reports_override_file(
        self, tmp_path, monkeypatch
    ):
        repo = tmp_path / "repo"
        common = repo / ".git"
        common.mkdir(parents=True)
        (common / "pact-project-id").write_text(
            "operator/pinned-id\n", encoding="utf-8"
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        monkeypatch.setattr(
            "scripts.memory_api.main_repo_common_dir", lambda *a, **k: common
        )
        monkeypatch.setattr(
            "scripts.memory_api.main_repo_root", lambda *a, **k: repo
        )

        detected = PACTMemory._detect_project_identity_with_legacy()

        assert detected.project_id == "operator/pinned-id"
        assert detected.source == "override file", (
            f"the declaration label leaked instead of the pin: {detected.source!r}"
        )


class TestCwdRungDisclosesAnOperatorPin:
    """A pin that decides the CWD rung must disclose as `override file`.

    The declared-rung arm above binds the disclosure at one of the two sites
    the review named; this one binds the OTHER. Without it, reverting the
    cwd-rung call to the literal `"cwd"` leaves the suite green — the
    unbound-helper class that failed the earlier cycle.
    """

    def test_a_cwd_repo_pinned_by_override_reports_override_file(
        self, tmp_path, monkeypatch
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        # A REAL repository with a REAL override file, per the lead's ruling:
        # the arm's value is that a reviewer can read why the cwd rung won.
        subprocess.run(
            ["git", "init", "-q", "."],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        common = repo / ".git"
        (common / "pact-project-id").write_text(
            "operator/cwd-pinned\n", encoding="utf-8"
        )
        # THE PROCESS CWD MOVES OUT OF THE MARKER TREE. The git rung now
        # DECLINES when the process cwd carries a `.git` marker git cannot
        # answer for, so leaving the cwd on this repo would make Strategy 3
        # unreachable by construction. The walk is pinned to the repo (its
        # subject here is the disclosure, not the walk), and the explicit
        # cwd-root lookup below still runs git for real.
        plain = tmp_path / "plain-cwd"
        plain.mkdir()
        monkeypatch.chdir(plain)
        monkeypatch.setattr(
            PACTMemory, "_find_project_root", staticmethod(lambda start: repo.resolve())
        )
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        # NEUTRALISE THE HIGHER RUNGS so the cwd walk is the one that answers:
        # no session record and no user-scope setting. The PROCESS-repo
        # (Strategy 0) lookup must also fail, because a real repo at the
        # process cwd would otherwise read this pin FIRST and the cwd rung
        # would be unreachable by construction. Only the no-start call is
        # neutralised; the explicit cwd-root lookup below runs git for real.
        monkeypatch.setattr(
            "scripts.memory_api.get_project_dir_from_session_record", lambda: ""
        )
        monkeypatch.setattr(
            "scripts.memory_api.read_user_setting", lambda *a, **k: None
        )

        real_common_dir = memory_api.main_repo_common_dir

        def _process_repo_must_not_answer(start=None):
            return None if start is None else real_common_dir(start)

        monkeypatch.setattr(
            "scripts.memory_api.main_repo_common_dir",
            _process_repo_must_not_answer,
        )

        # POSITIVE PROOF OF THE RUNG: the walk lands on this repo, and the
        # explicit cwd-root lookup resolves the REAL `.git`, so the source
        # assertion cannot be satisfied by a higher rung or by a mocked
        # common dir.
        assert PACTMemory._find_project_root(Path.cwd()) == repo.resolve()
        assert memory_api.main_repo_common_dir(str(repo)) == common.resolve()

        detected = PACTMemory._detect_project_identity_with_legacy()

        assert detected.project_id == "operator/cwd-pinned"
        assert detected.source == "override file", (
            f"the cwd label leaked instead of the pin: {detected.source!r}"
        )
