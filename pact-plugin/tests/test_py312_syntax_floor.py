"""
Location: pact-plugin/tests/test_py312_syntax_floor.py
Summary: Static AST guard keeping the plugin's bare-python3 surfaces
         parseable at the declared Python 3.12 floor -- hooks/,
         skills/pact-memory/scripts/ and scripts/ (the roots a consumer
         can end up running on /usr/bin/python3 rather than a managed
         interpreter). One rule: every scanned .py parses at
         ast feature_version=(3, 12). Pure static analysis: nothing
         scanned is imported or executed.
Used by: pact-plugin test suite (standing merge gate).

THE PASSING VECTORS ARE THE PIN, NOT THE CORPUS ARM. Every corpus file
also parses at (3, 11) today, so the corpus arm alone cannot catch a
stale floor constant -- only the synthetic PASSING vectors can. PEP 695
(type statement + type-parameter lists) parses at (3, 12) and is
measured REJECTED at (3, 9), (3, 10) and (3, 11) with "Type statement is
only supported in Python 3.12 and greater", so reverting the constant to
any pre-3.12 value reddens the PEP 695 arm. It is the only true boundary
discriminator in this file.

HONESTY CAVEAT ON THE FAILING VECTORS, measured on this 3.12.3
interpreter: the t-string (PEP 750) and type-parameter-default (PEP 696)
sources are rejected at EVERY feature_version -- the 3.12.3 parser
grammar does not know those constructs at all, so their rejection comes
from interpreter-grammar absence, not from the gate. They prove the arm
runs and reports, not that the boundary sits at 3.12. Do not cite them
as boundary evidence; cite PEP 695.

feature_version NARROWS, never replaces, live verification. It was
measured false-passing PEP 701 f-strings under a feature_version a real
floor interpreter rejected, and no static scan sees a post-floor API
call at all. A clean scan here is necessary, not sufficient -- the 3.12
CI cell running the whole suite is the floor's live backing.

NO __future__ REQUIREMENT. The 3.9-era annotation-stringification policy
(this file's predecessor's R1-R4) is retired with the 3.9 floor: PEP 604
unions evaluate natively on every 3.12+ interpreter. The corpus's
existing `from __future__ import annotations` directives are inert, not
wrong, and nothing here demands or forbids them.
"""
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent

# Every bare-python3 surface in the plugin: code a consumer can end up running
# on /usr/bin/python3 rather than a managed interpreter. Extension point --
# add further such surfaces here, one line each.
SCANNED_ROOTS: tuple[Path, ...] = (
    PLUGIN_ROOT / "hooks",
    PLUGIN_ROOT / "skills" / "pact-memory" / "scripts",
    PLUGIN_ROOT / "scripts",
)


@dataclass(frozen=True)
class Violation:
    relpath: str
    lineno: int
    rule: str      # "post312_syntax"
    detail: str


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def iter_python_files(roots: Iterable[Path] = SCANNED_ROOTS) -> Iterator[Path]:
    """Yield every .py file under the given roots, sorted for stable ids."""
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            yield path


def _relpath(path: Path) -> str:
    try:
        return path.relative_to(PLUGIN_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# Detector — pure (source -> list[Violation]) so the non-vacuity tests can
# drive it with synthetic strings.
# ---------------------------------------------------------------------------

def check_py312_syntax_floor(source: str, relpath: str) -> List[Violation]:
    """The file must parse at feature_version=(3, 12). Catches syntax newer
    than the declared floor that a modern dev interpreter would accept
    silently. Best-effort per CPython policy: a construct this interpreter's
    grammar does not know rejects at every feature_version (see the honesty
    caveat in the module docstring), and a post-floor API call is invisible
    to any parse -- the 3.12 CI cell remains the live backing."""
    try:
        ast.parse(source, feature_version=(3, 12))
    except SyntaxError as exc:
        return [Violation(
            relpath=relpath, lineno=exc.lineno or 1, rule="post312_syntax",
            detail="not parseable at feature_version=(3, 12): {}".format(exc.msg),
        )]
    return []


def _format_violations(violations: List[Violation]) -> str:
    return "\n".join(
        "{}:{} [{}] {}".format(v.relpath, v.lineno, v.rule, v.detail)
        for v in violations
    )


_SCANNED_FILES = list(iter_python_files())


# ---------------------------------------------------------------------------
# Live-tree rules
# ---------------------------------------------------------------------------

class TestDiscoveryFloor:
    """Discovery must never silently collapse to an empty scan."""

    def test_scanned_file_count_floor(self):
        # If a root in SCANNED_ROOTS resolves empty (directory rename, anchor
        # break), the per-file parameter set degrades to a single skip each
        # and the aggregate rules pass vacuously over those files — silently
        # green. Floor rather than exact count so adding files never breaks
        # it; bump the floor as the scanned roots grow, never lower it
        # without a deliberate scope decision. History: lowered 63 -> 55
        # when the dormant hooks/refresh/ package (8 modules) was removed;
        # raised 55 -> 71 when skills/pact-memory/scripts and scripts were
        # added; raised 71 -> 106 when this guard was rebuilt for the 3.12
        # floor (measured 2026-10-30 at f1c44200: hooks 86 +
        # skills/pact-memory/scripts 14 + scripts 6).
        #
        # COUNTING RULE, so the next reader RE-DERIVES instead of inheriting a
        # number: _SCANNED_FILES is every *.py that Path.rglob finds under
        # each entry of SCANNED_ROOTS. Measure it; never carry a figure
        # forward from this comment. Any count written here is true only on
        # the day it was written.
        #
        # SLACK IS NOT A PROPERTY THIS COMMENT CAN PIN: it is today's count
        # minus 106, and it WIDENS on its own as files are added. The wider
        # the slack, the more of a root can disappear unnoticed. Re-measure
        # and raise the floor when it has drifted.
        #
        # LIMIT, stated rather than implied: a count floor cannot protect a
        # SMALL root. Losing one whose file count is under the current slack
        # leaves the total above the floor, indistinguishable from normal
        # churn. This test guards against a LARGE root collapsing, not
        # against every root.
        assert len(_SCANNED_FILES) >= 106

    def test_every_scanned_root_resolves_non_empty(self):
        # The count floor above cannot see a root vanish when that root is
        # smaller than its slack; this can, for every root and at any slack.
        # Reuses iter_python_files so the guard and the scan cannot drift.
        for root in SCANNED_ROOTS:
            assert any(iter_python_files((root,))), root


class TestPy312SyntaxFloor:
    """The floor rule over every scanned file, one test per file."""

    @pytest.mark.parametrize(
        "path", _SCANNED_FILES, ids=[_relpath(p) for p in _SCANNED_FILES]
    )
    def test_parses_at_py312_feature_version(self, path):
        source = path.read_text(encoding="utf-8")
        violations = check_py312_syntax_floor(source, _relpath(path))
        assert not violations, _format_violations(violations)


# ---------------------------------------------------------------------------
# Detector non-vacuity — synthetic sources prove the rule actually fires
# (and stays silent where it must). A detector that flags everything or
# nothing fails the silent cases.
# ---------------------------------------------------------------------------

class TestDetectorNonVacuity:

    def test_tstring_fires_syntax_floor(self):
        # PEP 750 t-strings are 3.14 syntax. Honesty caveat (module docstring):
        # rejected at EVERY feature_version on this interpreter — proves the
        # arm runs, not the boundary.
        source = 'x = t"hello {name}"\n'
        violations = check_py312_syntax_floor(source, "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "post312_syntax"

    def test_pep696_type_param_default_fires_syntax_floor(self):
        # PEP 696 type-parameter defaults are 3.13 syntax. Same honesty
        # caveat: rejected at every feature_version on this interpreter.
        source = "class C[T = int]:\n    pass\n"
        violations = check_py312_syntax_floor(source, "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "post312_syntax"

    def test_garbage_fires_syntax_floor(self):
        # The baseline: non-syntax never parses at any floor value.
        violations = check_py312_syntax_floor("def broken(:\n", "synthetic.py")
        assert len(violations) == 1
        assert violations[0].rule == "post312_syntax"

    def test_pep695_type_statement_and_generics_pass(self):
        # PEP 695 is 3.12 syntax and THE boundary discriminator: measured
        # rejected at (3, 9)/(3, 10)/(3, 11), accepted at (3, 12). If this
        # arm reddens, the floor constant has regressed below 3.12.
        source = (
            "type Alias = int | str\n"
            "\n"
            "def f[T](x: T) -> T:\n"
            "    return x\n"
        )
        assert check_py312_syntax_floor(source, "synthetic.py") == []

    def test_match_case_passes_syntax_floor(self):
        # match/case is 3.10 syntax — legal at a 3.12 floor and deliberately
        # NOT a rejection vector (issue #1681 directive).
        source = (
            "def f(x):\n"
            "    match x:\n"
            "        case 1:\n"
            "            return 'one'\n"
            "    return 'other'\n"
        )
        assert check_py312_syntax_floor(source, "synthetic.py") == []

    def test_plain_39_era_module_passes_syntax_floor(self):
        # Ordinary pre-3.12 code must parse: the floor FORBIDS newer syntax,
        # it does not DEMAND new syntax.
        source = (
            "import json\n"
            "from typing import Optional\n"
            "\n"
            "def f(x):\n"
            "    return [i for i in x if i]\n"
            "\n"
            "class A(object):\n"
            "    def m(self, *args, **kwargs):\n"
            "        return None\n"
        )
        assert check_py312_syntax_floor(source, "synthetic.py") == []

    def test_pep695_boundary_mechanism_at_311(self):
        # Mechanism arm (one assert): the PEP 695 sample is accepted by the
        # (3, 12) detector while a raw parse at (3, 11) raises with the
        # version NAMED in the message — the diagnosability evidence that
        # the passing vector is a real boundary pin, not a vacuous silence.
        pep695 = "type Alias = int | str\n"
        assert check_py312_syntax_floor(pep695, "synthetic.py") == []
        with pytest.raises(SyntaxError, match="3.12"):
            ast.parse(pep695, feature_version=(3, 11))
