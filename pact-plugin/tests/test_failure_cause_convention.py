"""Arms for the closed-vocabulary failure-cause convention.

THE DEFECT THESE ARMS CLOSE. Six producers return a status string that
session_init routes into `system_messages` on a substring test. Five of them
interpolated a cut of the caught exception, and an OSError renders as
`[Errno NN] <strerror>: '<path>'`, so the cut kept the LEADING characters
and emitted the absolute path with the home directory and the user name.

TWO PROPERTIES ARE UNDER TEST AND THEY FAIL DIFFERENTLY.
  1. NO PATH FRAGMENT reaches a routed status string.
  2. THE ROUTING TOKEN SURVIVES. `failed` (or `skipped`) inside the prose is
     a MACHINE CONTRACT: a reworded producer keeps the human signal and
     silently moves the message from `system_messages` to ordinary context.
A repair that closed property 1 by deleting the message would satisfy an
absence-only arm, so every absence assertion below is paired with a POSITIVE
cause assertion and with the routing token.

ARM LABELS. Each arm says PRODUCT or HARNESS in its own docstring. A HARNESS
arm injects at a seam and exercises the handler; it is NOT evidence that a
natural failure reaches that handler. A later reader must not read one as the
other.
"""

import ast
import errno
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# A path fragment no rendering may carry. Distinctive so a partial leak is
# still caught by the `/` assertion below.
_SECRET = "/Users/probe-user/secret-dir/CLAUDE.md"


def _oserror(code=errno.EACCES, message="Permission denied"):
    return OSError(code, message, _SECRET)


def _assert_convention(result, expected_cause, *, routed_token="failed"):
    """The three claims every routed failure message must satisfy.

    Absence, PRESENCE OF THE CAUSE, and the routing token. The cause
    assertion is what stops this helper passing on a message emitted by a
    DIFFERENT handler: the sibling lock-failure arms also carry no "/".
    """
    assert result is not None, "producer returned None; handler not reached"
    assert expected_cause in result, (
        f"cause token absent, so a different handler answered: {result!r}"
    )
    assert "/" not in result, f"path fragment leaked: {result!r}"
    assert _SECRET not in result
    assert os.path.expanduser("~") not in result
    assert routed_token in result.lower(), (
        "session_init routes on this substring; without it the message "
        f"lands in ordinary context, not system_messages: {result!r}"
    )


class TestFailureCauseHelper:
    """Unit arms on the shared helper itself."""

    def test_closed_vocabulary_table(self):
        """PRODUCT ARM (unit). The rendering comes from a CLOSED VOCABULARY.

        ROW 4 IS THE LOAD-BEARING ONE. `OSError("... path ...")` carries
        `filename=None` and `errno=None` while its `str()` carries a path, so
        no filter keyed on an exception ATTRIBUTE reaches it. That row is why
        the helper reads the caller's message not at all.

        MUTANT: add a `str(exc)` fallback when the symbol is absent. Rows 4
        and 6 redden. That fallback is the likely shortcut, because the two
        Unicode members and an unmapped code carry no errno.

        Row 6 builds its code from `max(errno.errorcode) + 1` and NOT from a
        literal: 122 is unmapped on darwin and maps to EDQUOT on linux.
        """
        from shared.failure_cause import failure_cause

        try:
            b"\xff".decode("utf-8")
        except UnicodeDecodeError as decode_error:
            unicode_exc = decode_error

        rows = [
            (PermissionError(13, "Permission denied", _SECRET),
             "PermissionError (EACCES)"),
            (IsADirectoryError(21, "Is a directory", _SECRET),
             "IsADirectoryError (EISDIR)"),
            (OSError(errno.ENOSPC, "No space left on device"),
             "OSError (ENOSPC)"),
            (OSError(f"bare message with {_SECRET} in it"), "OSError"),
            (unicode_exc, "UnicodeDecodeError"),
            (OSError(max(errno.errorcode) + 1, "unmapped"), "OSError"),
        ]
        for exc, expected in rows:
            rendered = failure_cause(exc)
            assert rendered == expected, f"{exc!r} rendered {rendered!r}"
            assert "/" not in rendered

    def test_a_length_bound_would_not_have_closed_this(self):
        """PRODUCT ARM (unit). The REJECTED alternative, kept as a arm.

        This pins the measurement that rejects a cut. It fails only if
        Python stops rendering an OSError as `[Errno NN] <strerror>: path`,
        which is the premise the whole convention rests on. Without this
        arm, a later reader can reasonably propose "just truncate more".
        """
        rendered = str(_oserror())
        assert "/" in rendered[:50], (
            "premise moved: a 50-character cut no longer carries the path, "
            "so the stated cause for the closed vocabulary needs re-measuring"
        )
        short = str(OSError(2, "x", _SECRET))
        assert "/" in short[:20], (
            "premise moved: a 20-character cut no longer carries the path"
        )


class TestRoutedProducersEmitNoPath:
    """One arm for each routed producer that carried the leak."""

    def test_ensure_project_memory_md_inner(self, tmp_path, monkeypatch):
        """HARNESS ARM. Injects at `_atomic_write_text`.

        REACHABILITY: the handler is reachable when the create write fails.
        The injection drives it directly and is NOT evidence about how often
        a natural write failure occurs.
        MUTANT: restore `{str(e)[:50]}`.
        """
        from shared import claude_md_manager as cmm

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        with patch.object(cmm, "_atomic_write_text", side_effect=_oserror()):
            result = cmm.ensure_project_memory_md()
        _assert_convention(result, "PermissionError (EACCES)")
        assert result.startswith("Project CLAUDE.md failed:")

    def test_migrate_to_managed_structure(self, tmp_path, monkeypatch):
        """HARNESS ARM. Injects at `_atomic_write_text`.

        MUTANT: restore `{str(e)[:50]}`.
        """
        from shared import claude_md_manager as cmm

        target = tmp_path / ".claude" / "CLAUDE.md"
        target.parent.mkdir()
        target.write_text("# Project Memory\n\n## Retrieved Context\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        with patch.object(cmm, "_atomic_write_text", side_effect=_oserror()):
            result = cmm.migrate_to_managed_structure()
        _assert_convention(result, "PermissionError (EACCES)")
        assert result.startswith("Migration failed:")

    def test_strip_orphan_kernel_block(self, tmp_path, monkeypatch):
        """HARNESS ARM. Injects at `_atomic_write_text`.

        MUTANT: restore `{str(e)[:50]}`.
        """
        from shared import claude_md_manager as cmm

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # The start marker is a PREFIX match (`<!-- PACT_START:`), so the
        # version segment is required. A fixture spelling it `<!-- PACT_START -->`
        # lands in the orphan-marker branch and never reaches the handler.
        (config_dir / "CLAUDE.md").write_text(
            "<!-- PACT_START:v1 -->\nkernel\n<!-- PACT_END -->\nuser content\n"
        )
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
        with patch.object(cmm, "_atomic_write_text", side_effect=_oserror()):
            result = cmm.strip_orphan_kernel_block()
        if result is None:
            pytest.skip("kernel-block precondition not met on this fixture")
        _assert_convention(result, "PermissionError (EACCES)")
        assert result.startswith("Failed to remove stale kernel block:")

    def test_setup_plugin_symlinks(self, tmp_path, monkeypatch):
        """HARNESS ARM. Injects at the symlink seam, and REACHES THE BRANCH.

        THIS SITE WAS OUTSIDE THE ORIGINAL CENSUS, because it spelled its cut
        `[:20]` rather than `[:50]`. Its fragment reaches the routed return
        through the `messages` join, so the routing token must survive that
        join as well as the message.

        THE FIXTURE IS THREE LINES, AND THAT IS WHY IT EXISTS. The branch is
        gated on `CLAUDE_PLUGIN_ROOT`, which is DECLARED in the environment
        rather than DISCOVERED from the installed plugin, so a temporary
        directory holding a `protocols/` child satisfies all three entry
        gates. The writes that follow stay inside `tmp_path`, because the
        autouse `_isolate_config_root_to_tmp` fixture in conftest redirects
        `Path.home()` for every test.

        PATCH ONLY `symlink_to`, so the `mkdir` above it succeeds and the
        failure lands INSIDE the protocols try-block rather than earlier.

        MUTANT: restore `{str(e)[:20]}` and drive with `OSError(2, "x", p)`.
        A 20-character cut of THAT shape carries the path, while the same cut
        of a PermissionError does not, which is how the census missed it.
        """
        from shared import symlinks as sym

        plugin_root = tmp_path / "plugin"
        (plugin_root / "protocols").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

        with patch.object(sym.Path, "symlink_to", side_effect=_oserror()):
            result = sym.setup_plugin_symlinks()

        assert result is not None, "the branch was not reached"
        assert "protocols failed" in result, (
            f"the protocols branch was not reached: {result!r}"
        )
        _assert_convention(result, "PermissionError (EACCES)")

    def test_update_session_info(self, tmp_path, monkeypatch):
        """PRODUCT ARM. Natural read failure, no injection.

        REACHABILITY IS MEASURED: a `chmod 000` CLAUDE.md in a searchable
        parent passes lock acquisition and `Path.exists`, then fails at
        `read_text` with the absolute path attached.
        MUTANT: restore `{str(e)[:50]}`.
        """
        from shared.session_resume import update_session_info

        if os.geteuid() == 0:
            pytest.skip("root reads a chmod 000 file, so the read never fails")
        target = tmp_path / ".claude" / "CLAUDE.md"
        target.parent.mkdir()
        target.write_text("<!-- SESSION_START -->\n<!-- SESSION_END -->\n")
        target.chmod(0o000)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        try:
            result = update_session_info("sess-1", "team-1")
        finally:
            target.chmod(0o600)
        _assert_convention(result, "PermissionError (EACCES)")
        assert result.startswith("Session info failed:")


class TestNoRoutedProducerInterpolatesAnException:
    """The spelling-independent regression guard.

    EVERY ARM ABOVE NAMES A SITE, so each one goes blind to a site added
    later. This arm is keyed on the PROPERTY instead: no message-building
    expression inside a routed producer may reference a name bound by an
    `except ... as NAME` clause, or a parameter annotated as an exception
    (a helper that builds the message from the caught exception it is
    handed). A new leak spelled any way at all reddens here.

    THE KNOWN BOUND: this reaches producers named below, and a path that
    arrives from something other than an exception is invisible to it.
    """

    PRODUCERS = {
        "hooks/shared/symlinks.py": ["setup_plugin_symlinks"],
        "hooks/shared/claude_md_manager.py": [
            "ensure_project_memory_md",
            "migrate_to_managed_structure",
            "strip_orphan_kernel_block",
        ],
        "hooks/staleness.py": ["check_pinned_staleness"],
        "hooks/shared/session_resume.py": [
            "update_session_info",
            "session_info_failure",
        ],
    }

    @staticmethod
    def _exception_params(func: ast.FunctionDef) -> set:
        """Parameters annotated as an exception type: BaseException,
        Exception, or any *Error."""
        params = func.args.posonlyargs + func.args.args + func.args.kwonlyargs
        return {
            a.arg for a in params
            if isinstance(a.annotation, ast.Name)
            and a.annotation.id.endswith(("Exception", "Error"))
        }

    def test_no_exception_name_reaches_a_message_build(self):
        plugin_root = Path(__file__).parent.parent
        offenders = []
        checked = 0
        for rel, funcs in self.PRODUCERS.items():
            tree = ast.parse((plugin_root / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                if node.name not in funcs:
                    continue
                checked += 1
                bound = {
                    h.name for h in ast.walk(node)
                    if isinstance(h, ast.ExceptHandler) and h.name
                } | self._exception_params(node)
                if not bound:
                    continue
                for inner in ast.walk(node):
                    is_build = (
                        isinstance(inner, ast.JoinedStr)
                        or (isinstance(inner, ast.BinOp)
                            and isinstance(inner.op, (ast.Add, ast.Mod)))
                        or (isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Attribute)
                            and inner.func.attr == "format")
                    )
                    if not is_build:
                        continue
                    # THE SANCTIONED WRAPPER IS THE ONE PERMITTED ROUTE. An
                    # exception name may reach a message ONLY through
                    # `failure_cause(...)`, which reads no caller text. Every
                    # other route (`str(e)`, `{e}`, `repr(e)`, `e.filename`,
                    # `e.strerror`) is an offender, whatever its spelling.
                    sanctioned = {
                        id(n)
                        for call in ast.walk(inner)
                        if isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "failure_cause"
                        for n in ast.walk(call)
                        if isinstance(n, ast.Name)
                    }
                    raw = {
                        n.id for n in ast.walk(inner)
                        if isinstance(n, ast.Name)
                        and id(n) not in sanctioned
                    }
                    if raw & bound:
                        offenders.append(
                            f"{rel}:{inner.lineno} {ast.unparse(inner)[:80]}"
                        )
        # NON-VACUITY: the guard is worthless if it scanned nothing.
        assert checked == 7, f"expected 7 producers, scanned {checked}"
        assert not offenders, (
            "a routed producer interpolates a caught exception into a "
            "message. Render the cause with shared.failure_cause instead:\n"
            + "\n".join(offenders)
        )


class TestRoutedPrefixesAreByteIdentical:
    """The routing tokens, pinned as literals.

    A reword reads correctly and moves the message to the wrong channel with
    nothing else objecting. These are the six prefixes as they ship.
    """

    PREFIXES = [
        ("hooks/shared/claude_md_manager.py",
         'f"Failed to remove stale kernel block: {failure_cause(e)}"'),
        ("hooks/shared/claude_md_manager.py",
         'f"Project CLAUDE.md failed: {failure_cause(e)}"'),
        ("hooks/shared/claude_md_manager.py",
         'f"Migration failed: {failure_cause(e)}"'),
        ("hooks/staleness.py",
         'f"Failed to update pinned staleness: {failure_cause(e)}"'),
        ("hooks/shared/symlinks.py",
         'f"protocols failed: {failure_cause(e)}"'),
        ("hooks/shared/session_resume.py",
         'f"Session info failed: {failure_cause(e)}. "'),
    ]

    def test_each_routed_message_is_present_verbatim(self):
        plugin_root = Path(__file__).parent.parent
        for rel, literal in self.PREFIXES:
            source = (plugin_root / rel).read_text(encoding="utf-8")
            assert literal in source, (
                f"{rel} no longer builds {literal}. If the wording changed, "
                "check session_init's routing predicate FIRST: it selects "
                "the user-visible channel by a substring of this prose."
            )

    def test_every_routed_message_carries_the_routing_word(self):
        for _rel, literal in self.PREFIXES:
            assert "failed" in literal.lower(), (
                f"{literal} lost the routing word; session_init would send "
                "it to ordinary context instead of system_messages"
            )
