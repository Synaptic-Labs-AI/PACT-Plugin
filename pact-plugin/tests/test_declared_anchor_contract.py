"""
Location: pact-plugin/tests/test_declared_anchor_contract.py
Summary: The declared containment anchor: that it is CONSULTED rather than
         re-derived, that omitting it changes nothing, and the module-level
         population bounds that two dated observations were resting on.

         WHY A DECLARED ANCHOR AT ALL. The CLAUDE.md target is resolved
         AMBIENTLY -- from CLAUDE_PROJECT_DIR, then two git anchors, then the
         working directory. An anchor COMPUTED from that same resolution agrees
         with the target by construction, so it cannot refuse anything. The
         anchor has to arrive from outside the resolution to mean anything, and
         these tests exist to prove it still does after a refactor that would
         quietly reintroduce a lookup.

         THE VERDICT IS THE OBSERVABLE, NEVER THE ANCHOR'S VALUE. An
         implementation that re-derived the anchor would often produce the same
         STRING as a correct one, so a value comparison passes while
         independence is gone. Every arm below asserts what the write DID.
Used by: pytest.
"""
import ast
import json
import subprocess
from pathlib import Path

import pytest

from scripts.working_memory import (  # noqa: E402
    ContainmentError,
    SyncResult,
    _atomic_write_text,
    sync_to_claude_md,
)
from fixtures.hf_cache import hf_cache_env


@pytest.fixture(autouse=True)
def _pin_the_model_cache(monkeypatch):
    """Point this module's children at the operator cache, and refuse network.

    SET ON THE PARENT PROCESS RATHER THAN ON A CHILD `env` DICT, because the
    children here are spawned by PRODUCTION code — `archive_pin._run_memory_cli`
    — which inherits this process's environment. There is no child dict to bind,
    and putting test-cache variables into production would be the wrong fix.

    MODULE-SCOPED AND NOT GLOBAL, deliberately. A conftest-wide autouse would
    also force offline on IN-PROCESS loads elsewhere in the suite, where a
    fixture loads a real model on purpose. That is a different decision with a
    different blast radius and it is not this one.

    Measured: this module's children attempted 2 model loads without it.
    """
    for _k, _v in hf_cache_env().items():
        monkeypatch.setenv(_k, _v)


_REPO = Path(__file__).resolve().parent.parent
_ARCHIVE_PIN = _REPO / "scripts" / "archive_pin.py"
_MEMORY_CLI = _REPO / "skills" / "pact-memory" / "scripts" / "cli.py"

SCAFFOLD = (
    "# {title}\n\n"
    "## Working Memory\n"
    "<!-- Auto-managed by pact-memory skill. -->\n"
)


# ---------------------------------------------------------------------------
# The control itself: a missing anchor is refused AT the control.
# ---------------------------------------------------------------------------

class TestMissingAnchorIsRefusedAtTheControl:
    """`_atomic_write_text` must not accept a None anchor from anyone.

    None is not reachable through either writer today. That is the point: the
    containment guarantee rested on two callers guarding correctly PLUS a
    resolver returning a PAIRED (None, None), and both of those guards test the
    TARGET rather than the anchor. A control whose safety depends on its
    callers' discipline is one refactor from being unguarded.

    AND THE OLD FAILURE WAS ACCIDENTAL. `os.stat(str(None))` stats the literal
    relative path "None" and raises -- until a directory named `None` exists in
    the working directory, at which point it resolves and that directory
    silently becomes the boundary every write is measured against.
    """

    def test_none_anchor_raises_containment_error(self, tmp_path):
        target = tmp_path / "CLAUDE.md"
        target.write_text("before", encoding="utf-8")

        with pytest.raises(ContainmentError):
            _atomic_write_text(target, "after", None)

        assert target.read_text(encoding="utf-8") == "before", (
            "the refused write still modified the file"
        )

    def test_a_real_anchor_still_writes(self, tmp_path):
        """POSITIVE CONTROL. Without it, an `_atomic_write_text` that refused
        EVERYTHING would satisfy the arm above."""
        target = tmp_path / "CLAUDE.md"
        target.write_text("before", encoding="utf-8")

        _atomic_write_text(target, "after", tmp_path)

        assert target.read_text(encoding="utf-8") == "after"

    def test_a_directory_literally_named_None_cannot_become_the_anchor(
        self, tmp_path, monkeypatch
    ):
        """The accident, made concrete.

        Create the directory whose existence used to convert a fail-closed
        raise into a silently-accepted boundary, then confirm the refusal is
        now keyed on the VALUE rather than on the filesystem.
        """
        (tmp_path / "None").mkdir()
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "None" / "CLAUDE.md"
        target.write_text("before", encoding="utf-8")

        with pytest.raises(ContainmentError):
            _atomic_write_text(target, "after", None)

        assert target.read_text(encoding="utf-8") == "before"


# ---------------------------------------------------------------------------
# The anchor is additive: omitting it changes nothing.
# ---------------------------------------------------------------------------

class TestOmittingTheAnchorChangesNothing:
    """Additive with a default. Every existing caller omits it."""

    def test_omitted_anchor_writes_exactly_as_before(self, tmp_path, monkeypatch):
        md = tmp_path / "CLAUDE.md"
        md.write_text(SCAFFOLD.format(title="Ambient"), encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = sync_to_claude_md({"context": "no anchor given"}, memory_id="a" * 32)

        assert result.reason == SyncResult.WROTE
        assert "no anchor given" in md.read_text(encoding="utf-8")

    def test_an_anchor_that_contains_the_target_permits(self, tmp_path, monkeypatch):
        md = tmp_path / "CLAUDE.md"
        md.write_text(SCAFFOLD.format(title="Anchored"), encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = sync_to_claude_md(
            {"context": "anchor contains target"},
            memory_id="b" * 32,
            claude_md_root=tmp_path,
        )

        assert result.reason == SyncResult.WROTE
        assert "anchor contains target" in md.read_text(encoding="utf-8")

    def test_an_anchor_that_excludes_the_target_refuses(self, tmp_path, monkeypatch):
        """THE DISCRIMINATION, in-process.

        The target still resolves to the project file -- the anchor does NOT
        steer resolution -- so the write is REFUSED rather than redirected. An
        anchor that redirected would write somewhere unexpected and report
        success, which is worse than either outcome here.
        """
        md = tmp_path / "CLAUDE.md"
        md.write_text(SCAFFOLD.format(title="Excluded"), encoding="utf-8")
        before = md.read_bytes()
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = sync_to_claude_md(
            {"context": "must not land"},
            memory_id="c" * 32,
            claude_md_root=sandbox,
        )

        assert result.reason == SyncResult.FAILED
        assert md.read_bytes() == before, "the excluded target was written anyway"


# ---------------------------------------------------------------------------
# Item 5: enforceability THROUGH THE TEST WIRING, at the _run_memory_cli level.
# ---------------------------------------------------------------------------

class TestAnchorIsEnforceableThroughTheCliWiring:
    """Plumbing and containment THROUGH THE TEST WIRING. NOT a production replay.

    Production does not reach the sync on this path: the archive save carries
    `--no-sync` and the other leg is a read. What these arms certify is that
    when a caller DOES declare an anchor through this wiring, the declaration
    is consulted at the write and can refuse it.

    THE PRE-EXISTING SUPPRESSION ARM CERTIFIES PLUMBING, NOT ENFORCEABILITY --
    its fixture creates a CLAUDE.md beside the declared directory, so the
    containment check passes trivially and would pass just as well against an
    anchor that had been re-derived from the resolution. These arms put the
    target OUTSIDE the declared root, which is the only shape in which a
    declared anchor and a derived one give different answers.

    THE VERDICT IS THE OBSERVABLE. `wrote` versus `failed` -- never a
    comparison of anchor strings, which a re-deriving implementation would
    often pass.
    """

    @pytest.fixture
    def nested(self, tmp_path):
        """A repo with a CLAUDE.md, and a sandbox nested inside it with none.

        The git anchor is what makes the target resolve OUT of the sandbox and
        INTO the repo; without it the resolver finds nothing and the arm would
        measure an unresolved target rather than a refused one.
        """
        repo = tmp_path / "repo"
        (repo / "sandbox").mkdir(parents=True)
        (repo / "CLAUDE.md").write_text(
            SCAFFOLD.format(title="Repo"), encoding="utf-8"
        )
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.email", "t@e.com"],
            ["git", "config", "user.name", "T"],
        ):
            subprocess.run(cmd, cwd=str(repo), capture_output=True, check=True)
        return repo

    def _archive_pin(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_ap_probe", str(_ARCHIVE_PIN)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _save_through_wiring(self, ap, cwd, db_path, tag):
        """`db_path` IS A STORE THAT IS PRESENT. `cli.main` refuses a
        `--db-path` naming a store that is absent, for each command other
        than `setup`, so callers pass the `memory_store` fixture result."""
        rc, stdout, stderr = ap._run_memory_cli(
            ["save", "--stdin"],
            db_path=str(db_path),
            stdin_data=json.dumps({"context": tag}),
            cwd=str(cwd),
        )
        assert rc == 0, f"CLI failed: {stderr[:300]}"
        return json.loads(stdout)["result"]

    def test_target_outside_the_declared_root_is_refused(
        self, nested, memory_store
    ):
        """The declared root is the sandbox; the target resolves to the repo."""
        ap = self._archive_pin()
        repo_md = nested / "CLAUDE.md"
        before = repo_md.read_bytes()

        result = self._save_through_wiring(
            ap, nested / "sandbox", memory_store("probe.db"), "must be refused"
        )

        assert result["sync_status"] == "failed", (
            f"a target outside the declared root was not refused: {result}"
        )
        assert repo_md.read_bytes() == before, "the repo CLAUDE.md was written"

    def test_positive_control_an_anchor_containing_the_target_permits(
        self, nested, memory_store
    ):
        """POSITIVE CONTROL, AND IT IS LOAD-BEARING RATHER THAN TIDY.

        `failed` is reachable by a broken harness -- a bad temp path, a
        permissions accident, a CLI that cannot start. Without this arm the
        refusal arm above passes while proving nothing, which is vacuity
        arriving inside the test built to prove enforceability.

        Same wiring, same child, anchor moved to the repo so it CONTAINS the
        target. This must both report `wrote` AND change the file.
        """
        ap = self._archive_pin()
        repo_md = nested / "CLAUDE.md"
        before = repo_md.read_bytes()

        result = self._save_through_wiring(
            ap, nested, memory_store("probe.db"), "must be written"
        )

        assert result["sync_status"] == "wrote", (
            f"the control did not write, so the refusal arm proves nothing: {result}"
        )
        assert repo_md.read_bytes() != before, (
            "the control reported `wrote` but the file is unchanged"
        )


# ---------------------------------------------------------------------------
# Item 4 + addendum: the population bounds two dated observations rested on.
# ---------------------------------------------------------------------------

def _module_calls(path: Path, func: str):
    """Every call to `func` in the module, by AST rather than by grep.

    MODULE-LEVEL ON PURPOSE. A spy installed around one function bounds THAT
    FUNCTION's call population and says nothing about the module's -- which is
    how the existing argv pin came to look stronger than it is.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else None)
            if name == func:
                out.append(n.lineno)
    return out


class TestModuleLevelCallPopulations:
    """Bounds, not samples. The residual's wording leans on these."""

    def test_run_memory_cli_has_exactly_one_call_site_in_archive_pin(self):
        """A second, unwrapped call site would bypass the anchor supply.

        The automatic `--claude-md-root` is added inside `_run_memory_cli`, so
        every route through it is anchored. A caller that spawned the CLI
        directly would not be, and this bound is what makes that visible.
        """
        calls = _module_calls(_ARCHIVE_PIN, "_run_memory_cli")
        assert len(calls) == 1, (
            f"expected exactly 1 module-level call to _run_memory_cli, found "
            f"{len(calls)} at lines {calls}. A new call site must be reviewed "
            f"for anchor supply, not merely counted."
        )

    def test_the_save_leg_still_suppresses_its_sync(self):
        """Production's archive save must not start syncing.

        Read from the SOURCE rather than from a spy, so it bounds the module.
        """
        src = _ARCHIVE_PIN.read_text(encoding="utf-8")
        assert '"--no-sync"' in src or "'--no-sync'" in src, (
            "the archive save leg no longer carries --no-sync, so the "
            "production path now reaches the sync"
        )


class TestCliSearchDoesNotSync:
    """Item 4a. `cmd_search` passing `sync_to_claude=False` was UNPINNED.

    The only related test asserted the API DEFAULT is True -- the opposite
    direction -- so nothing held the CLI's behaviour in place. Several comments
    in the tree lean on it as a dated observation; this is what makes it a fact.
    """

    def test_cmd_search_suppresses_the_retrieved_context_sync(self, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from scripts import cli

        fake = MagicMock()
        fake.search.return_value = []
        with patch.object(cli, "PACTMemory", return_value=fake), \
             patch.object(cli, "_success"):
            cli.cmd_search(
                SimpleNamespace(query="q", limit=5, current_file=None,
                                claude_md_root=None),
                db_path=str(tmp_path / "x.db"),
            )

        assert fake.search.call_args.kwargs["sync_to_claude"] is False, (
            "cmd_search no longer suppresses its sync -- the CLI's search verb "
            "can now write to CLAUDE.md, which several comments assume it cannot"
        )


class TestE2ESubprocessSavesAreBounded:
    """Addendum pin 2. E2E subprocess saves must suppress or declare.

    THIS PINS A CATEGORY BEFORE IT GROWS AN ASSERTION. Those children inherit
    PYTEST_CURRENT_TEST and have no pytest module, so the guard refuses them
    today; save() swallows it as non-critical and the tests pass on the
    envelope. They are ALREADY in the silently-refused state and that is not a
    regression this design introduces.

    The day one of them asserts "CLAUDE.md unchanged", it passes FOR THE WRONG
    REASON -- the refusal, not the suppression -- which is the vacuity failure
    that emptied a negative control elsewhere, in a class nobody is watching.
    """

    def test_every_cli_subprocess_save_suppresses_or_declares(self):
        src = (Path(__file__).parent / "test_memory_cli.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)

        offenders = []
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            if "Subprocess" not in cls.name:
                continue
            for n in ast.walk(cls):
                if not isinstance(n, ast.List):
                    continue
                parts = [
                    e.value for e in n.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                if "save" not in parts:
                    continue
                if "--no-sync" in parts or "--claude-md-root" in parts:
                    continue
                offenders.append((cls.name, n.lineno))

        assert offenders == [], (
            "E2E subprocess saves that neither suppress nor declare a root: "
            f"{offenders}. Each is silently REFUSED by the ambient guard today, "
            "so any 'CLAUDE.md unchanged' assertion added to one would pass for "
            "the refusal rather than for the suppression. Add --no-sync or "
            "--claude-md-root when adding such a save."
        )
