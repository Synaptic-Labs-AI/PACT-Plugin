"""
Location: pact-plugin/tests/test_import_hygiene.py
Summary: Suite-level import-hygiene gate — a strict-mode unused-import sweep
         over every consumer-shipped Python surface (hooks/, scripts/,
         telegram/, skills/*/scripts/), plus the pins that keep the gate
         itself honest: non-vacuity fixtures driven through the gate's own
         entry point, a no-default signature pin on the predicate's
         strictness parameter, and a prose pin on the canonical lint-check.sh
         invocation in the command files.
Used by: pytest suite. This is the dev-repo enforcement tier of the
         import-hygiene ladder; the consumer-facing tier is
         lint-check.sh --files (advisory strictness). Both tiers call the
         SAME predicate module — one substrate, two declared strictness
         tiers. THIS file is where the suite tier's strictness is declared.

Scope boundary — "ships to consumers":
    hooks/, scripts/, telegram/, and skills/*/scripts/ all execute in (or
    ship to) consumer sessions, so dead imports there are product defects.
    tests/ is deliberately NOT swept: test-file dead imports never ship,
    and the pre-existing backlog there is tracked as separate cleanup work.

Suppression contract:
    An intentional unused import (re-export facade, monkeypatch seam,
    availability probe) carries `# noqa: F401  # <category>: <reason>` on
    the import statement's FIRST physical line. The sweep honors exactly
    that convention; an unmarked unused import fails the suite.

Strictness contract (why the fixtures below exist):
    The predicate's try-scope parameter is REQUIRED-EXPLICIT — no default
    anywhere in the chain. The suite gate declares "strict" (try/except-
    scoped imports are checked: the fail-closed try wrapper is this repo's
    standard cross-package import idiom, and skipping it hides real dead
    imports). The consumer tier declares "advisory". The non-vacuity tests
    drive a try-scoped dead import through the gate's OWN entry point, so
    any future edit that weakens the gate's declared strictness turns the
    suite red — asserting the predicate directly with a strict argument
    would only prove the predicate CAN be strict, not that this gate IS.
"""

import importlib.util
import inspect
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent

_PREDICATE_SCRIPT = (
    PLUGIN_ROOT
    / "skills"
    / "pact-coding-standards"
    / "scripts"
    / "check_unused_imports.py"
)


def _load_predicate_module():
    spec = importlib.util.spec_from_file_location(
        "check_unused_imports_suite_gate", _PREDICATE_SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cui = _load_predicate_module()


# ─── the gate's single entry point ───────────────────────────────────────────
# The suite tier's strictness is declared HERE and nowhere else. Every check
# in this file — the shipped-tree sweep AND the non-vacuity fixtures — goes
# through this function, so the fixtures exercise the same declaration the
# sweep runs under.

L1_TRY_SCOPE = "strict"


def _gate_check(paths):
    """Run the suite gate over files; returns formatted finding lines."""
    return cui.check_paths([str(p) for p in paths], try_scope=L1_TRY_SCOPE)


# ─── consumer-shipped Python surfaces ────────────────────────────────────────

def _skills_script_files():
    return sorted(PLUGIN_ROOT.glob("skills/*/scripts/**/*.py"))


TARGET_DIR_SETS = {
    "hooks": lambda: sorted((PLUGIN_ROOT / "hooks").rglob("*.py")),
    "scripts": lambda: sorted((PLUGIN_ROOT / "scripts").rglob("*.py")),
    "telegram": lambda: sorted((PLUGIN_ROOT / "telegram").rglob("*.py")),
    "skills-scripts": _skills_script_files,
}


class TestShippedTreeIsClean:
    """The strict-mode sweep over every consumer-shipped Python surface."""

    @pytest.mark.parametrize("label", sorted(TARGET_DIR_SETS))
    def test_surface_has_files_to_scan(self, label):
        """Non-empty-glob guard: a surface that stops resolving would make
        the sweep silently vacuous; catch path drift loudly instead."""
        assert len(TARGET_DIR_SETS[label]()) > 0

    @pytest.mark.parametrize("label", sorted(TARGET_DIR_SETS))
    def test_no_unused_imports(self, label):
        files = TARGET_DIR_SETS[label]()
        assert len(files) > 0  # inline guard: never pass on an empty sweep
        findings = _gate_check(files)
        assert findings == [], (
            f"unused imports in consumer-shipped surface '{label}' — fix, or "
            "mark an intentional re-export/probe with "
            "'# noqa: F401  # <category>: <reason>' on the statement's first "
            "line:\n" + "\n".join(findings)
        )


class TestGateNonVacuity:
    """Prove the gate can go red — each fixture drives a synthetic module
    through the gate's own entry point (`_gate_check`), never through the
    predicate's parameter seam directly."""

    def test_dead_import_is_detected(self, tmp_path):
        mod = tmp_path / "dead_import.py"
        mod.write_text("import os\n", encoding="utf-8")
        assert _gate_check([mod]) == [f"{mod}:1: unused import os"]

    def test_noqa_marked_twin_is_excluded(self, tmp_path):
        mod = tmp_path / "marked_twin.py"
        mod.write_text(
            "import os  # noqa: F401  # re-export: fixture twin\n",
            encoding="utf-8",
        )
        assert _gate_check([mod]) == []

    def test_try_scoped_dead_import_is_detected(self, tmp_path):
        """THE strictness pin: this fixture fails if the gate's declared
        tier ever weakens to advisory (which skips try-scoped imports)."""
        mod = tmp_path / "try_scoped.py"
        mod.write_text(
            "try:\n    import os\nexcept ImportError:\n    pass\n",
            encoding="utf-8",
        )
        assert _gate_check([mod]) == [f"{mod}:2: unused import os"]

    def test_syntax_error_fails_loudly(self, tmp_path):
        """A file the gate cannot parse is a failure, never a silent skip."""
        mod = tmp_path / "broken.py"
        mod.write_text("def broken(:\n", encoding="utf-8")
        findings = _gate_check([mod])
        assert len(findings) == 1
        assert "syntax error" in findings[0]


class TestGateEdgeBehavior:
    """Edge rows for the sweep's carve-outs and bindings, asserted through
    the gate entry point so they document the gate's behavior, not just
    the predicate's."""

    def test_multiline_parenthesized_noqa_on_first_line_excluded(self, tmp_path):
        mod = tmp_path / "multiline.py"
        mod.write_text(
            "from json import (  # noqa: F401  # re-export: fixture\n"
            "    dumps,\n"
            "    loads,\n"
            ")\n",
            encoding="utf-8",
        )
        assert _gate_check([mod]) == []

    def test_dunder_all_reexport_not_flagged(self, tmp_path):
        mod = tmp_path / "all_reexport.py"
        mod.write_text(
            "from json import dumps\n__all__ = [\"dumps\"]\n", encoding="utf-8"
        )
        assert _gate_check([mod]) == []

    def test_future_import_never_flagged(self, tmp_path):
        mod = tmp_path / "future.py"
        mod.write_text("from __future__ import annotations\n", encoding="utf-8")
        assert _gate_check([mod]) == []

    def test_star_import_ignored(self, tmp_path):
        mod = tmp_path / "star.py"
        mod.write_text("from json import *\n", encoding="utf-8")
        assert _gate_check([mod]) == []

    def test_dotted_import_binds_root_name(self, tmp_path):
        used = tmp_path / "dotted_used.py"
        used.write_text(
            "import xml.etree.ElementTree\nprint(xml)\n", encoding="utf-8"
        )
        assert _gate_check([used]) == []

        unused = tmp_path / "dotted_unused.py"
        unused.write_text("import xml.etree.ElementTree\n", encoding="utf-8")
        assert _gate_check([unused]) == [
            f"{unused}:1: unused import xml.etree.ElementTree"
        ]

    def test_underscore_named_import_without_noqa_flagged(self, tmp_path):
        """Underscore-prefixed names get no free pass — an intentional
        underscore re-export needs the noqa marker like any other."""
        mod = tmp_path / "underscore.py"
        mod.write_text("from json import dumps as _dumps\n", encoding="utf-8")
        assert _gate_check([mod]) == [f"{mod}:1: unused import dumps as _dumps"]

    def test_noqa_listing_only_other_codes_does_not_suppress(self, tmp_path):
        mod = tmp_path / "wrong_code.py"
        mod.write_text("import os  # noqa: E501\n", encoding="utf-8")
        assert _gate_check([mod]) == [f"{mod}:1: unused import os"]

    def test_type_checking_else_branch_shares_carve_out_known_limitation(
        self, tmp_path
    ):
        """DOCUMENTED LIMITATION, not an endorsement: the TYPE_CHECKING
        carve-out covers the whole `if TYPE_CHECKING:` statement, so a dead
        import in the ELSE branch (a runtime import) is wrongly exempted
        too. Zero instances exist in the swept surfaces; the pattern itself
        (runtime imports in a TYPE_CHECKING else-arm) is vanishingly rare.
        If this test starts mattering — an else-arm import appears in a
        shipped surface — tighten the carve-out to the if-body only rather
        than deleting this row."""
        mod = tmp_path / "tc_else.py"
        mod.write_text(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    import json\n"
            "else:\n"
            "    import os\n",
            encoding="utf-8",
        )
        assert _gate_check([mod]) == []


class TestRequiredExplicitContract:
    """The no-default contract on the predicate's strictness parameter.
    Removing the default was a deliberate design choice: the fail-safe
    direction genuinely differs per call site, so no call site may inherit
    another's. These pins freeze that contract structurally; the try-scoped
    fixture above freezes it behaviorally for this gate."""

    @pytest.mark.parametrize("func_name", ["find_unused_imports", "check_paths"])
    def test_try_scope_is_keyword_only_with_no_default(self, func_name):
        sig = inspect.signature(getattr(cui, func_name))
        param = sig.parameters["try_scope"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty

    def test_suite_gate_declares_strict(self):
        """The suite tier's declaration literal — change requires changing
        this test, which is the point."""
        assert L1_TRY_SCOPE == "strict"

    def test_unknown_try_scope_rejected_through_gate_substrate(self):
        with pytest.raises(ValueError):
            cui.find_unused_imports("import os\n", try_scope="lenient")


class TestCommandProsePin:
    """The coder-workflow prose ships the canonical lint-check.sh invocation
    in the command files; pin the exact substring and its per-file count so
    the convention cannot silently rot out of the prose."""

    CANONICAL_INVOCATION = (
        "bash {plugin_root}/skills/pact-coding-standards/scripts/"
        "lint-check.sh --files"
    )

    @pytest.mark.parametrize(
        ("command_file", "expected_count"),
        [("orchestrate.md", 1), ("comPACT.md", 2)],
    )
    def test_canonical_invocation_present(self, command_file, expected_count):
        content = (PLUGIN_ROOT / "commands" / command_file).read_text(
            encoding="utf-8"
        )
        assert content.count(self.CANONICAL_INVOCATION) == expected_count
