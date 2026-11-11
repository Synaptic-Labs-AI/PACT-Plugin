"""Location: pact-plugin/tests/test_hook_emitted_config_root.py
Summary: Pins the config-root literals that hooks EMIT as user-facing text.
         Hook-emitted strings are prose a user or agent reads and acts on, so a
         `~/.claude/` literal in one misdirects every non-default-root install.
Used by: the merge gate only — nothing imports this module.

WHY THIS IS NOT THE PROSE GATE'S JOB: tests/test_prose_root_gate.py scans
agents/commands/skills/protocols. Extending that population to hooks/ would
sweep in ~68 docstring and comment occurrences that are documentation, not
instruction, and would need an allowlist larger than the finding. So the
population here is the EMITTED strings only, and role is decided
syntactically rather than by grep.

THE CLASSIFIER IS THE DIFFERENCE BETWEEN TWO PARSERS, because each is blind to
what the other sees. `ast` never contains comments at all, so a literal in the
token stream but absent from the AST is a comment BY CONSTRUCTION — no `#`
heuristics. Docstrings are named positively via the Constant at body[0].
Everything left is emitted.

TWO TRAPS THIS FILE HANDLES, both live in the files it guards:
  - an f-string is a JoinedStr, NOT a Constant. Its literal segments are
    Constant nodes nested among FormattedValue siblings, so a walk over
    top-level constants silently misses every literal inside one. `ast.walk`
    descends, which is why it is used here.
  - implicit adjacent concatenation ("a" "b") is FOLDED by ast into one
    Constant. That is what we want (we judge the RENDERED string), but it also
    means the rendered sentence is NOT a contiguous substring of the source, so
    no line-oriented grep could pin these messages.
"""
import ast
import io
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.tracked_files import tracked_files as _tracked_files

HOOKS = Path(__file__).parent.parent / "hooks"
LITERAL = re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.claude")

# The ONE emitted literal that must survive: bootstrap_gate's degraded path
# runs BECAUSE an import failed, so it resolves the root from os.environ
# rather than calling the plugin's own resolver, and needs a literal fallback
# for the unset case. Same shape as the resolver contract's mandated
# $HOME/.claude fallback. Keyed on the rendered VALUE, not a line number, so
# it survives edits above it.
ALLOWED_EMITTED = {
    ("hooks/bootstrap_gate.py", "$HOME/.claude"),
}

GUARDED_FILES = (
    "hooks/bootstrap_gate.py",
    "hooks/session_init.py",
    "hooks/shared/claude_md_manager.py",
)


def _tracked_hook_files():
    """The SHIPPED hook modules, from the git index rather than the tree.

    The gate claims to judge what ships. A working-tree walk lets an untracked
    file carrying an emitted literal enter the population and redden a green
    checkout; `known_member` is the positive control that this walk still
    reaches the tree.
    """
    return _tracked_files(
        HOOKS.parent, "hooks", "**/*.py",
        known_member="hooks/bootstrap_gate.py")


def _emitted_literals():
    """Yield (relpath, lineno, rendered) for every EMITTED config-root literal."""
    for path in _tracked_hook_files():
        rel = path.relative_to(HOOKS.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) and body \
                    and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and LITERAL.search(node.value) and id(node) not in docstrings:
                yield rel, node.lineno, node.value


def test_no_hook_emits_a_hardcoded_config_root():
    """The class gate. Docstrings and comments are out of population by role."""
    found = [(rel, ln, val) for rel, ln, val in _emitted_literals()
             if (rel, val.strip()) not in ALLOWED_EMITTED]
    assert not found, (
        "a hook EMITS a hardcoded config root. The user reads this text and "
        "acts on it, so on a non-default root it names a file or directory "
        "that is not the one the code just operated on. Interpolate "
        "get_claude_config_dir() (or, on a degraded path that must not import "
        f"it, os.environ). Sites: {found}"
    )


def test_control_the_gate_ignores_an_untracked_hook_file():
    """An untracked hook must not enter the judged population.

    The probe carries the exact literal the gate matches, so a reverted
    working-tree walk reddens HERE with the probe named rather than passing
    quietly. The tracked-member assertion is the positive control.
    """
    probe = HOOKS / "zzprobe1714_emit.py"
    probe.write_text('X = "~/.claude"\n', encoding="utf-8")
    try:
        assert HOOKS / "bootstrap_gate.py" in _tracked_hook_files()
        # The walk is RECURSIVE, as the rglob it replaced was: the shared
        # subpackage is part of the judged population too.
        assert HOOKS / "shared" / "claude_md_manager.py" in _tracked_hook_files()
        assert probe not in _tracked_hook_files()
        test_no_hook_emits_a_hardcoded_config_root()
    finally:
        probe.unlink()


def test_control_the_classifier_can_see_an_emitted_literal():
    """A zero from the gate above is only meaningful if the walk reaches code.

    Without this, a broken HOOKS path or a walk that never descends into
    JoinedStr yields an empty `found` — byte-identical to a clean tree.
    """
    allowed = [(rel, ln, val) for rel, ln, val in _emitted_literals()
               if (rel, val.strip()) in ALLOWED_EMITTED]
    assert allowed, (
        "the classifier found NO emitted literal at all, not even the "
        "allowlisted degraded-path fallback — the walk or the pattern is dead"
    )


def test_control_docstrings_and_comments_are_out_of_population():
    """The population is EMITTED strings. If this arm ever fails, the
    classifier has started counting documentation and the gate above will red
    on ~68 occurrences that are correct as written."""
    emitted = list(_emitted_literals())
    # symlinks.py carries the literal ONLY in a docstring and a comment.
    assert not [r for r in emitted if r[0].endswith("shared/symlinks.py")], (
        "classifier is counting docstrings/comments as emitted"
    )


@pytest.mark.parametrize("rel", GUARDED_FILES)
def test_guarded_files_still_exist(rel):
    """A renamed file silently shrinks the population above to nothing."""
    assert (HOOKS.parent / rel).is_file(), f"{rel} moved — re-point this gate"


# --------------------------------------------------------------------------
# Behavioural arms — the literal being absent is not the same as the RESOLVED
# root being present. These drive the real producers under an alternate root.


@pytest.fixture
def alt_root(tmp_path, monkeypatch):
    root = tmp_path / "alt-config"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    return root


def test_additional_directories_tip_names_the_resolved_dirs(alt_root):
    """The tip must name the SAME directories its membership test checked.

    The defect this replaces: the check ran on the resolved path while the
    message printed a tilde literal, so on a non-default root the user was
    told to add a path that could never satisfy the check — following the tip
    did not silence the tip.
    """
    from session_init import check_additional_directories

    (alt_root / "settings.json").write_text(
        json.dumps({"permissions": {"additionalDirectories": []}}), encoding="utf-8")

    tip = check_additional_directories()
    assert tip is not None
    assert str(alt_root / "teams") in tip
    assert str(alt_root / "pact-sessions") in tip
    assert str(alt_root / "settings.json") in tip
    assert "~/.claude" not in tip


def test_malformed_settings_warning_names_the_file_it_read(alt_root):
    """The warning must name the settings.json that was actually parsed."""
    from session_init import check_settings_well_formed

    (alt_root / "settings.json").write_text("{not valid json", encoding="utf-8")

    warning = check_settings_well_formed()
    assert warning is not None
    assert str(alt_root / "settings.json") in warning
    assert "~/.claude" not in warning


def test_kernel_strip_status_names_the_file_it_operated_on(alt_root):
    """strip_orphan_kernel_block reads get_claude_config_dir()/CLAUDE.md, so a
    user told to inspect ~/.claude/CLAUDE.md would inspect the wrong file."""
    from shared.claude_md_manager import strip_orphan_kernel_block

    target = alt_root / "CLAUDE.md"
    target.write_text("# Home\n\n<!-- PACT_START:v3 -->\nkernel body\n", encoding="utf-8")

    status = strip_orphan_kernel_block()
    assert status is not None
    assert str(target) in status
    assert "~/.claude" not in status


def test_degraded_gate_warning_names_the_configured_root(alt_root):
    """bootstrap_gate's degraded path resolves from os.environ, never from the
    plugin resolver — this arm proves the env value actually reaches the text."""
    import bootstrap_gate

    with patch("sys.stdout", new_callable=io.StringIO) as out:
        with pytest.raises(SystemExit) as exc:
            bootstrap_gate._emit_degraded_warning("import", RuntimeError("boom"), "Read")

    assert exc.value.code == 0, "degraded path must exit 0 or the JSON is voided"
    reason = json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecisionReason"]
    assert f"{alt_root}/plugins/cache/" in reason
    assert "~/.claude" not in reason
