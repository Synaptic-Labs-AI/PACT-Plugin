"""
Location: pact-plugin/tests/test_import_hygiene.py
Summary: Suite-level import-hygiene gate — a strict-mode unused-import sweep
         over every consumer-shipped Python surface (hooks/, scripts/,
         telegram/, skills/*/scripts/) plus the dev-repo tests/ tree, and
         the pins that keep the gate itself honest: non-vacuity fixtures
         driven through the gate's own entry point, a no-default signature
         pin on the predicate's strictness parameter, and a prose pin on the
         canonical lint-check.sh invocation in the command files.
Used by: pytest suite. This is the dev-repo enforcement tier of the
         import-hygiene ladder; the consumer-facing tier is
         lint-check.sh --files (advisory strictness). Both tiers call the
         SAME predicate module — one substrate, two declared strictness
         tiers. THIS file is where the suite tier's strictness is declared.

Scope boundary — what is swept and why:
    hooks/, scripts/, telegram/, and skills/*/scripts/ all execute in (or
    ship to) consumer sessions, so dead imports there are product defects.
    tests/ never ships, but is swept too: the backlog that justified its
    day-one exclusion has been cleared, and the CI ruff leg lints all of
    pact-plugin/ — this gate sweeps tests/ as well, so the two enforcement
    layers cannot disagree on tests/ scope.

Suppression contract:
    An intentional unused import (re-export facade, monkeypatch seam,
    availability probe) carries `# noqa: F401  # <category>: <reason>` on
    the import statement's FIRST physical line, which covers the whole
    statement, or on the physical line of the one name it keeps inside a
    multi-line import, which covers the names on that line only. The sweep
    honors exactly that convention; an unmarked unused import fails the
    suite — with ONE
    exception, which this file's own liveness filter introduced: a
    CONCURRENT run's planted probe is removed from the tests/ surface
    before the gate ever sees it. See `_is_foreign_live_probe`.

Strictness contract (why the non-vacuity fixtures exist):
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

import ast
import importlib.util
import inspect
import io
import os
import re
import subprocess
import sys
import tokenize
import uuid
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
# OF THAT TIER goes through this function — the shipped-tree sweep and the
# non-vacuity fixtures alike — so the fixtures exercise the same declaration
# the sweep runs under. Two checks bypass it DELIBERATELY, to exercise other
# tiers: the advisory flip-direction assertion, and the invalid-tier
# rejection. Neither can route through a function that hardcodes the tier —
# that is the point of them, not a gap.

L1_TRY_SCOPE = "strict"


def _gate_check(paths):
    """Run the suite gate over files; returns formatted finding lines."""
    return cui.check_paths([str(p) for p in paths], try_scope=L1_TRY_SCOPE)


# ─── swept Python surfaces ───────────────────────────────────────────────────
# hooks/, scripts/, telegram/, skills-scripts ship to consumers; tests/ is
# the dev-repo-only surface, swept so this gate and the CI ruff leg (which
# lints all of pact-plugin/) cannot disagree on tests/ scope. CI's tree is
# a strict superset of these globs (e.g. skills/*/ top-level test
# files are CI-linted only) — the safe direction: a divergence there is a
# loud CI red, never a silent gate pass.

def _skills_script_files():
    return sorted(PLUGIN_ROOT.glob("skills/*/scripts/**/*.py"))


_PROBE_STEM = "_f401_planted_probe_delete_me_"
_PROBE_RE = re.compile(re.escape(_PROBE_STEM) + r"(\d+)_[0-9a-f]{32}\.py")


def _is_foreign_live_probe(path):
    """True for a probe planted by a DIFFERENT, still-running process.

    CROSS-RUN OBSERVATION, which a unique name does not fix. Uniqueness stops
    two runs sharing a path, so neither can clobber or delete the other's file
    — but a foreign probe is still a file with an unused import, so the tests/
    sweep reports it and reddens a test that never mentions PROBE. That is the
    ORIGINAL reported failure, and its signature is exactly that: the red lands
    on a different test than the one at fault.

    LIVENESS, NOT THE NAME, is what separates a concurrent run's probe from one
    LEAKED by a crash. As strings the two are indistinguishable; as processes
    they are not, and the OS maintains that distinction without anything having
    to be remembered or cleaned up.

    THE ONLY CASE THAT REMOVES A FILE FROM THE SWEEP is a foreign probe whose
    owner is definitely ALIVE. Everything else keeps it — not probe-shaped,
    our own, definitely gone, or an error that leaves liveness unknown — so
    the sweep still reports it. Never skip on an unknown: a filter that skips
    when it cannot tell is the silent-skip surface this module exists to catch.

    KNOWN CEILING, and it is a regression rather than parity: BEFORE this
    filter existed, a leaked probe was caught unconditionally — it was just a
    file with an unused import, and the sweep reported every one of those.
    Now, a leak whose pid has since been
    reassigned to a LIVE process is read as live and skipped, so detection is
    intermittent rather than certain on any single run. It is self-correcting
    across runs — the file persists, and the next run whose reading differs
    reports it — but "usually caught" is weaker than "always caught". Closing
    it needs an identity a recycled pid cannot forge (a process start time, or
    a lock held on a per-run owner file), which is more machinery than the
    residual costs.
    """
    m = _PROBE_RE.fullmatch(path.name)
    if not m or int(m.group(1)) == os.getpid():
        return False  # not a probe, or OURS — P1 needs our own probe swept
    try:
        os.kill(int(m.group(1)), 0)
    except ProcessLookupError:
        return False  # owner is gone: a LEAK, and the sweep must name it
    except PermissionError:
        return True  # alive, owned by another user
    except OSError:
        return False  # cannot tell: assume leak and report, never skip silently
    return True


TARGET_DIR_SETS = {
    "hooks": lambda: sorted((PLUGIN_ROOT / "hooks").rglob("*.py")),
    "scripts": lambda: sorted((PLUGIN_ROOT / "scripts").rglob("*.py")),
    "telegram": lambda: sorted((PLUGIN_ROOT / "telegram").rglob("*.py")),
    "skills-scripts": _skills_script_files,
    # SOLE FILTER — do not add another without reading this.
    # No arm plants a dead-owner probe on disk: such a file is unfiltered BY
    # CONSTRUCTION and would poison every concurrent run, which is the defect
    # this module exists to stop. Leak coverage is therefore COMPOSED —
    # `TestProbeNameMatchingIsAnchored` proves the predicate declines to drop
    # a dead owner, and the planting arms prove a file the predicate declines
    # IS swept and reported. That composition holds only while this predicate
    # is the ONLY filter applied here. A second filter treating dead-owner
    # probes differently from lookalikes breaks the composed property with
    # NOTHING going red — both halves still pass on their own.
    "tests": lambda: sorted(
        p
        for p in (PLUGIN_ROOT / "tests").rglob("*.py")
        if not _is_foreign_live_probe(p)
    ),
}


class TestSweptTreeIsClean:
    """The strict-mode sweep over every swept Python surface —
    consumer-shipped code plus the dev-repo tests/ tree."""

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
            f"unused imports in swept surface '{label}' — fix, or "
            "mark an intentional re-export/probe with "
            "'# noqa: F401  # <category>: <reason>' on the statement's first "
            "line, or on the kept name's own line in a multi-line import:\n"
            + "\n".join(findings)
        )


class TestTestsSurfaceEnforcement:
    """Counter-tests proving the tests/ surface is genuinely enforced —
    each asserts POSITIVE membership in the live TARGET_DIR_SETS surface —
    our probe, this module — so a reverted, retargeted, or
    recursion-narrowed glob makes the asserted member absent and turns these
    red rather than silently vacuous. Positive membership is the property,
    not `_gate_check`: the reachability pin calls no gate and still delivers
    it. A NEGATIVE assertion cannot, which is why the foreign-probe arm
    carries its own depth-derived control.

    ONE EXCEPTION, named rather than left silent:
    `test_kept_ensure_loaded_seam_survives_widened_gate` passes a hand-built
    one-element list. It localises a noqa-recognition divergence in the
    PREDICATE, not surface enforcement, so a retargeted glob should NOT
    redden it. It is outside this paragraph's scope by design.

    Probes are planted in the live tests/ tree one directory
    level DOWN (tests/fixtures/) — the sweep only sees them through
    recursive descent, so an rglob→glob mis-narrowing fails here instead
    of silently dropping subdirectory files. Each probe is removed in
    the same test.

    ISOLATION — the guarantee that actually holds, rather than an
    assumption about how the suite is invoked. The probe filename carries
    a pid and a full uuid4 hex suffix, and the two do DIFFERENT jobs. The
    uuid makes the path unique, so no run can clobber or delete another's
    file. The pid names an OWNER whose liveness `_is_foreign_live_probe`
    can test, so a concurrent run's probe is dropped from the swept surface
    and cannot be OBSERVED either. Uniqueness alone closed only the first
    half: a foreign probe is still a file with an unused import.

    The previous wording claimed the suite runs single-process; nothing
    enforces that, and two concurrent runs collided deterministically.
    The collision reddened tests that never mention PROBE — the tests/
    sweep sees a foreign file and reports it — so the poisonable set was
    every test that sweeps tests/, not only the arms that plant here.

    A probe leaked by a hard crash stays in tests/fixtures/, and
    test_no_unused_imports[tests] fails on it by name — UNLESS its pid has
    since been reassigned to a live process, which reads as a concurrent run
    and is skipped. That exception is the known ceiling documented on
    `_is_foreign_live_probe`; it is named rather than restated here, so the
    two cannot drift. Uniqueness removes the collision, not the need to
    clean up."""

    PROBE = (
        PLUGIN_ROOT
        / "tests"
        / "fixtures"
        / f"{_PROBE_STEM}{os.getpid()}_{uuid.uuid4().hex}.py"
    )

    def test_tests_surface_includes_this_gate_file(self):
        """Reachability pin: the tests glob must resolve the LIVE tests/
        tree — proven by the sweep seeing this very file. A glob pointing
        at a wrong or empty directory passes >0-file guards; it cannot
        pass this one."""
        files = TARGET_DIR_SETS["tests"]()
        assert Path(__file__).resolve() in [p.resolve() for p in files]

    def test_planted_dead_import_fails_tests_sweep_end_to_end(self):
        """A genuinely unused import planted in the live tests/ tree must
        be picked up by the surface glob AND flagged by the gate — the
        end-to-end proof that new tests/ files are enforced."""
        assert not self.PROBE.exists(), (
            "THIS RUN's probe already exists — the sibling planting test "
            "leaked it, meaning its `finally` did not run. A crashed "
            "EARLIER run does not reach this assertion: its probe carries a "
            "different suffix, and surfaces instead as a tests/ sweep "
            "finding naming the file — unless its pid was reassigned to a "
            "live process, the ceiling on `_is_foreign_live_probe`."
        )
        self.PROBE.write_text("import os\n", encoding="utf-8")
        try:
            files = TARGET_DIR_SETS["tests"]()
            assert self.PROBE in files
            assert f"{self.PROBE}:1: unused import os" in _gate_check(files)
        finally:
            self.PROBE.unlink()
        assert self.PROBE not in TARGET_DIR_SETS["tests"]()

    def test_strict_flip_would_miss_try_scoped_probe(self):
        """Strict-flip simulation for the widened surface: a try-scoped
        dead import planted in the live tests/ tree is caught by the gate
        under its declared strict tier, and would be MISSED under
        advisory — proving the sweep's strictness declaration is
        load-bearing for tests/, not incidental."""
        assert not self.PROBE.exists()
        self.PROBE.write_text(
            "try:\n    import os\nexcept ImportError:\n    pass\n",
            encoding="utf-8",
        )
        try:
            files = TARGET_DIR_SETS["tests"]()
            assert self.PROBE in files
            assert f"{self.PROBE}:2: unused import os" in _gate_check(files)
            # The flip direction: the same file under the advisory tier
            # produces no finding at all.
            assert cui.check_paths([str(self.PROBE)], try_scope="advisory") == []
        finally:
            self.PROBE.unlink()

    def test_foreign_probe_of_a_live_owner_is_not_observed(self):
        """A CONCURRENT run's probe must not redden this run's sweep.

        The half a unique name does not fix, and the mechanism behind the
        original report: the red lands on the whole-surface sweep, which never
        mentions PROBE, so it reads as unrelated to the test at fault.

        The precondition is asserted, not assumed. If the borrowed pid were
        dead, this arm would exercise the LEAK branch while still passing —
        green, and testing the opposite of what it names.
        """
        owner = os.getppid()
        os.kill(owner, 0)
        foreign = self.PROBE.parent / f"{_PROBE_STEM}{owner}_{uuid.uuid4().hex}.py"
        foreign.write_text("import os\n", encoding="utf-8")
        try:
            files = TARGET_DIR_SETS["tests"]()
            # The `foreign not in files` assertion is NEGATIVE: it passes
            # surface that never reached the probe. Non-empty is not enough:
            # under an rglob->glob narrowing this module is still swept while
            # tests/fixtures/ is not. Derived from `foreign` so it cannot drift
            # to the wrong depth the way a named file would.
            assert any(p.parent == foreign.parent for p in files), (
                "the swept surface never reached the probe's own directory"
            )
            assert foreign not in files, "foreign live probe reached the swept surface"
        finally:
            foreign.unlink()

    def test_kept_ensure_loaded_seam_survives_widened_gate(self):
        """The one reasoned keep in tests/ (an ensure-loaded monkeypatch
        seam marked with a first-line noqa) passes the gate individually.
        Localizes the failure if the predicate's noqa recognition ever
        diverges from the marker convention the keep uses — the sweep
        would also fail, but this row names the reconciliation."""
        kept = PLUGIN_ROOT / "tests" / "test_emitter_idempotency.py"
        assert kept.exists()
        assert _gate_check([kept]) == []


class TestProbeNameMatchingIsAnchored:
    """`_PROBE_RE` must match a probe name WHOLE, never as a substring — an
    over-matching pattern drops an ordinary file from the sweep, which is a
    silent skip.

    PURE-FUNCTION ARMS, DELIBERATELY, and the reason is the subject of this
    whole file. Proving it end-to-end would mean planting a lookalike in the
    live tests/ tree — and a lookalike is BY CONSTRUCTION not matched by the
    filter, so it is precisely the unprotected foreign file whose
    sweep-poisoning this module exists to prevent. A concurrent run would go
    red on a test that never mentions it, and the `finally` would delete the
    evidence. The arm proving foreign probes are handled would itself be an
    unhandleable one.

    The end-to-end half needs no second planted file: the planting arms in
    `TestTestsSurfaceEnforcement` already show that a file the filter
    declines to drop IS swept. This supplies the other half — that a
    lookalike is declined — and composition covers the rest.

    Every arm here passes a path that DOES NOT EXIST, which also pins that
    the predicate reads only `path.name` and never touches disk.

    MUTATION THAT REDDENS: `.fullmatch` -> `.search` in the predicate.
    """

    def _name(self, prefix, pid):
        return PLUGIN_ROOT / "tests" / "fixtures" / (
            f"{prefix}{_PROBE_STEM}{pid}_{uuid.uuid4().hex}.py"
        )

    def test_a_name_merely_containing_the_stem_is_not_a_probe(self):
        assert not _is_foreign_live_probe(self._name("zz", os.getppid()))

    def test_a_whole_probe_name_of_a_live_owner_still_matches(self):
        """The control. Without it a predicate that always returned False
        would satisfy the substring arm and drop the anchoring property."""
        owner = os.getppid()
        os.kill(owner, 0)
        assert _is_foreign_live_probe(self._name("", owner))

    def test_a_probe_of_a_DEAD_owner_is_not_dropped(self):
        """LEAK DETECTION, the property a name-only exclusion would have cost
        — a leak and a live run's probe are identical as strings.

        PURE for the same reason as its sibling arms, and more dangerous to
        plant than the lookalike: the filter deliberately does NOT drop a
        dead-owner probe, so a file on disk here is visible to every
        concurrent run by construction. Planting it to prove leak detection
        would poison the sweeps that leak detection exists to protect.

        The end-to-end half is carried by the planting arms, which show that a
        file the predicate declines to drop IS swept and IS reported by name.
        Both cases take the same branch — predicate False, file kept — so what
        remains to prove here is only that a dead owner reaches it.

        The precondition is DIAGNOSTIC, not anti-vacuity: a live pid would
        fail this arm's own assertion anyway. Do not weaken it to a skip.
        """
        done = subprocess.Popen([sys.executable, "-c", ""])
        done.wait()
        with pytest.raises(ProcessLookupError):
            os.kill(done.pid, 0)
        assert not _is_foreign_live_probe(self._name("", done.pid))


_ARRANGEMENT_RE = re.compile(
    r"\b(above|below|preceding|the following)\b"
    r"|\b(?:first|second|third|fourth|fifth|last)\s+"
    r"(?:arm|method|test|class|paragraph|sentence|one|line)\b",
    re.I,
)


def _prose_sentences(source):
    """Yield (label, sentence) for every docstring and comment in `source`.

    DERIVED FROM THE SOURCE, never listed. A hardcoded roster of prose blocks
    would be this check committing the defect it exists to catch: prose named
    by where it sits. `ast` supplies every module, class and function
    docstring; `tokenize` supplies every comment — including trailing ones,
    which a `startswith("#")` scan misses, and excluding a `#` inside a string
    literal, which such a scan wrongly matches.
    """
    blocks = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                blocks.append((getattr(node, "name", "<module>"), doc))
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            blocks.append(("<comment>", tok.string))
    for label, text in blocks:
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
            if sentence.strip():
                yield label, sentence.strip()


class TestProseNamesThingsNotPlaces:
    """This module's prose must not refer to anything by WHERE it sits.

    A positional reference is falsified by an edit that never touches the
    sentence carrying it, so it rots silently — the defect class this module
    exists to catch, arriving in its own documentation. Two shipped here and
    were caught from outside: a docstring pointing at an arm the same commit
    deleted, and a count that went stale when an arm was added.

    SCOPE IS THIS FILE. The false-positive profile was measured here and
    nowhere else, so a wider rule would assert a profile nobody has measured.

    CARDINALITY IS DELIBERATELY NOT CHECKED, and that is a measurement rather
    than an omission. Every count in this module names its members in the
    sentence carrying it, which is what makes a count stable; separating those
    from counts that stand in for their members needs semantics this rule does
    not have, and a rule with false positives is deleted by the first person
    it annoys. The HANDOFF carries the numbers.
    """

    SHIPPED_DEFECTS = (
        "the leak arm above already shows that a file the filter declines to drop IS swept.",
        "PURE for the same reason as the arms above, and this one is the more dangerous.",
    )

    def test_no_prose_refers_to_a_position(self):
        found = [
            (label, s)
            for label, s in _prose_sentences(Path(__file__).read_text(encoding="utf-8"))
            if _ARRANGEMENT_RE.search(s)
        ]
        assert not found, (
            "prose refers to something by its ARRANGEMENT rather than by what it is; "
            "an edit elsewhere falsifies it with nothing going red. Name the thing: %s"
            % found
        )

    def test_the_scan_reaches_this_module(self):
        """Non-vacuity. An extractor returning nothing makes the arm that uses
        it pass on any file, which is the silent-skip outcome this module is
        about. The label is derived from the class itself, so it cannot drift."""
        labels = {label for label, _ in _prose_sentences(Path(__file__).read_text(encoding="utf-8"))}
        assert type(self).__name__ in labels
        assert "<comment>" in labels

    def test_the_rule_flags_what_this_module_actually_shipped(self):
        """The red case, and it is real rather than invented — both strings
        shipped in this file and were reported by external review.

        Held as LITERALS, not read from the commit that carried them: arms in
        this repo that reach for a commit self-skip once squash-merge makes it
        unreachable, so a git-sourced fixture would quietly stop testing."""
        # Pyright reports this always-true: it can see the literal. Kept anyway
        # — it fires when someone EMPTIES the tuple, which is the edit that
        # turns this arm permanently green, and a future edit is exactly what a
        # static check cannot model. Do not delete it to silence the warning.
        assert self.SHIPPED_DEFECTS, "no red case left: this arm would pass on any rule"
        for defect in self.SHIPPED_DEFECTS:
            assert _ARRANGEMENT_RE.search(defect), defect


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

    def test_latin1_coding_cookie_file_is_checked(self, tmp_path):
        """PEP 263: legal non-UTF8 Python is read per its declared coding
        cookie and checked normally — the gate neither crashes nor emits a
        false unable-to-read line for it. The finding proves the file was
        actually analyzed, not silently passed over."""
        mod = tmp_path / "latin1.py"
        mod.write_bytes("# coding: latin-1\nimport os\nx = 'é'\n".encode("latin-1"))
        assert _gate_check([mod]) == [f"{mod}:2: unused import os"]

    def test_undecodable_file_fails_loudly_without_killing_the_batch(self, tmp_path):
        """A file whose bytes decode under no detected encoding is a loud
        per-file failure line — and sibling files in the same invocation
        are still checked (a crash here once discarded the whole batch)."""
        bad = tmp_path / "bad.py"
        bad.write_bytes(b"import os\nx = '\xff\xfe\x9c'\n")
        dead = tmp_path / "dead.py"
        dead.write_text("import json\n", encoding="utf-8")
        findings = _gate_check([bad, dead])
        assert any(
            f.startswith(f"{bad}:0:") and "unable to read file" in f
            for f in findings
        )
        assert f"{dead}:1: unused import json" in findings


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

    def test_multiline_parenthesized_noqa_on_the_names_own_line_excluded(
        self, tmp_path
    ):
        mod = tmp_path / "own_line.py"
        mod.write_text(
            "from json import (\n"
            "    dumps,  # noqa: F401  # re-export: fixture\n"
            ")\n",
            encoding="utf-8",
        )
        assert _gate_check([mod]) == []

    def test_own_line_noqa_leaves_an_unmarked_name_in_the_statement_flagged(
        self, tmp_path
    ):
        """The control for the own-line suppression: it covers its line only."""
        mod = tmp_path / "own_line_control.py"
        mod.write_text(
            "from json import (\n"
            "    dumps,  # noqa: F401  # re-export: fixture\n"
            "    loads,\n"
            ")\n",
            encoding="utf-8",
        )
        assert f"{mod}:1: unused import loads" in _gate_check([mod])

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
    fixture freezes it behaviorally for this gate."""

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
