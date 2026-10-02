"""Certification of the corrected CLAUDE.md write-path containment predicate.

WHAT IS UNDER CERTIFICATION. `_atomic_write_text` decides containment by KERNEL
OBJECT ANCESTRY on a pinned directory descriptor, in both twins
(`hooks/shared/claude_md_manager.py` and
`skills/pact-memory/scripts/working_memory.py`):

    anchor = (st_dev, st_ino) of os.stat(project_root)
    node   = os.open(target.parent, O_RDONLY|O_DIRECTORY)   # FOLLOWS symlinks
    walk up via os.open("..", dir_fd=node) comparing (st_dev, st_ino)
    until the anchor matches (CONTAINED) or a directory is its own parent
    (filesystem root -> REFUSE)

and then performs temp-create, fchmod, fsync, rename and cleanup THROUGH that
same descriptor. There is no `Path.resolve()`, no `os.path.realpath` and no
string comparison anywhere in the decision. The object CHECKED is the object
MUTATED, by construction rather than by agreement.

CERTIFICATION IS BIDIRECTIONAL, WHICH IS THE POINT. The defect being repaired
lives on the ALLOW branch: the superseded predicate both permitted a two-leg
symlink composition AND refused a benign leaf pointing outside. A suite made
only of refusals passes by refusing everything, which is the cardinal
over-block. Every refusal here is paired with a positive control that writes
successfully in the same harness.

--------------------------------------------------------------------------
THREE HAZARDS THAT DEFEAT A CARELESS SUITE. All three are measured, not
theorised, and each is defended against in a specific test below.
--------------------------------------------------------------------------

1. THE WALK IS UNREACHABLE ON A FLAT TOPOLOGY. The loop tests
   `os.fstat(node)` against the anchor and BREAKS before it ever reaches
   `os.open("..", dir_fd=node)`. So when `target.parent` IS the anchor -- the
   legacy `./CLAUDE.md` shape -- that site executes ZERO times. An injection
   aimed there never fires and the write simply succeeds.
   `test_flat_topology_makes_the_walk_site_unreachable` DEMONSTRATES that
   vacuity rather than asserting it, and every capability test states its
   observed hit count instead of inferring firing from a green result.

2. THE CAPABILITY MESSAGES ARE SPLIT ACROSS SOURCE LITERALS. The walk's
   message is built by implicit concatenation of two adjacent string
   literals, so a line-oriented search of the SOURCE for the rendered phrase
   `refusing write: platform lacks directory-descriptor ancestry traversal`
   returns ZERO matches while the AST-joined constant is that full sentence.
   MEASURED: `grep -c` for the rendered phrase is 0 in BOTH twins; the
   fragment `"ancestry traversal"` sits alone on its own source line.
   CONSEQUENCE FOR ANYONE VERIFYING THIS FILE: grepping the twins for a
   message asserted below will give you a FALSE NEGATIVE and make a correct
   assertion look like it references a string that does not exist. Do not
   "fix" it. The only sound instruments are a RUNTIME assertion on `str(exc)`
   (what every test here does) or an AST join (what
   `TestRefusalMessageContract` does). The messages are written here as
   single unsplit literals precisely so that they remain greppable from the
   TEST side even though they are not from the source side.

3. THE CAPABILITY BRANCH HAS NEVER EXECUTED ON ANY SUPPORTED PLATFORM.
   `os.open` is in `os.supports_dir_fd` on every interpreter measured, so no
   natural path reaches any of the four mappings. The injections in
   `TestCapabilityBranchInjection` are the first and only thing that has ever
   run them. Nothing in this file may be read as evidence that the capability
   is absent anywhere real -- the branch is triggered ARTIFICIALLY here.

DEBUGGING PRIOR, from two independent parties. On a branch that has never
executed, a surprising result is more likely to be the instrument than the
code, and the two most likely instrument faults are a FLAT FIXTURE and a
MIS-SPECIFIED INJECTION PREDICATE, in that order. Establish which you are
holding before reporting a finding -- and if the code really is wrong, that is
a real finding and must not be smoothed over.
"""

from __future__ import annotations

import errno
import os
import subprocess
import textwrap
import unicodedata
from pathlib import Path

import pytest

# hooks/ and the pact-memory scripts dir on path (mirrors the sibling test
# files: test_containment_guard.py adds hooks/, test_staleness.py adds the
# scripts dir and imports the twin as a top-level `working_memory`).
_TESTS = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# The six refusal messages, as RENDERED at runtime.
#
# Written as single unsplit literals on purpose -- see hazard 2 in the module
# docstring. In the SOURCE, MSG_CAP_WALK is two adjacent literals, so searching
# the twins for it returns nothing.
# ---------------------------------------------------------------------------

MSG_BOUNDARY = "refusing write: cannot establish the containment boundary"
MSG_EXHAUSTION = "refusing write: containment walk did not terminate"
MSG_ESCAPE = "refusing write: target escapes the project containment boundary"
MSG_CAP_WALK = (
    "refusing write: platform lacks directory-descriptor ancestry traversal"
)
MSG_CAP_TEMP = "refusing write: platform lacks directory-descriptor file creation"
MSG_CAP_RENAME = "refusing write: platform lacks directory-descriptor rename"
# The anchor is validated BEFORE the containment walk, so this refusal reports a
# caller error rather than a boundary result. It is registered here because the
# contract is the full rendered SET: an unregistered message is a refusal path
# nobody certified, which is the gap this file exists to close.
MSG_NO_ANCHOR = "refusing write: no containment anchor was supplied"

ALL_MESSAGES = (
    MSG_BOUNDARY,
    MSG_EXHAUSTION,
    MSG_ESCAPE,
    MSG_CAP_WALK,
    MSG_CAP_TEMP,
    MSG_CAP_RENAME,
    MSG_NO_ANCHOR,
)

CANONICAL_REL = "hooks/shared/claude_md_manager.py"
TWIN_REL = "skills/pact-memory/scripts/working_memory.py"
PLUGIN_ROOT = _TESTS.parent

# The ancestry walk's inline iteration bound. Pinned here because the design
# deliberately made it an INLINE LITERAL rather than a module constant (a
# constant would sit outside the region the twin drift gate compares), so this
# assertion is the only thing outside that gate which notices it changing.
WALK_BOUND = 1024

# A deep in-project path must be ALLOWED at a NAMED depth. The number is the
# whole point: the superseded bound admitted 63 levels, so a test at depth 5 or
# 10 passes while proving nothing about it. The certification floor is 70; this
# clears it with margin.
DEEP_DEPTH = 80


# ---------------------------------------------------------------------------
# Twin parametrisation
# ---------------------------------------------------------------------------

_TWIN_PARAMS = ("canonical", "skill")


def _load_twin(which: str):
    if which == "canonical":
        import shared.claude_md_manager as mod
        return mod
    from scripts import working_memory as mod
    return mod


@pytest.fixture(params=_TWIN_PARAMS)
def twin(request):
    """Both copies of the security function, exercised independently.

    WHAT THIS AXIS IS FOR, stated narrowly. The drift gate guarantees TEXT
    identity of the function body. What it structurally cannot catch is
    IDENTICAL TEXT BEHAVING DIFFERENTLY because of module-level context -- a
    different import set, a different ContainmentError class object, a
    different fcntl availability. That is not hypothetical: `uuid` had to be
    added to BOTH twins when the descriptor design landed, and an asymmetry
    there would have been invisible to a body-only gate.

    So this axis covers the obligations whose outcome depends on the predicate
    executing correctly IN ITS MODULE CONTEXT -- the topology matrix, the four
    capability sites, the negative control. Obligations that test CALL-SITE
    integration (per-site anchors, status strings, skip-status shapes) are
    deliberately SINGLE-twin: the twins have genuinely different call sites, so
    duplicating those would assert sameness where difference is CORRECT, and
    the resulting failure would be "fixed" by making the twins wrongly alike.

    The drift gate also does not prove the skill copy is ever EXERCISED, which
    is how a prior non-vacuity experiment reported a healthy number while the
    skill side had almost no behavioural coverage of its own.
    """
    return _load_twin(request.param)


class TestTwinAxisIsNotVacuous:
    """A parametrized axis that silently collapses to ONE target is the same
    vacuity class this suite exists to catch, and it would run green twice
    while certifying half of what its ids claim. These tests are the guard
    against that shape appearing in the guard itself."""

    def test_every_declared_param_resolves_to_a_distinct_module(self):
        """Not circular: the assertion is about the RESOLVED modules, not the
        param names. Two params spelled differently that both import the
        canonical twin would fail here, which is exactly the collapse being
        guarded against."""
        files = {
            param: Path(_load_twin(param).__file__).resolve()
            for param in _TWIN_PARAMS
        }
        assert len(set(files.values())) == len(_TWIN_PARAMS), (
            f"the twin axis collapsed onto fewer modules than it declares: {files}"
        )

    def test_the_params_resolve_to_the_two_expected_twins(self):
        assert Path(_load_twin("canonical").__file__).resolve() == (
            PLUGIN_ROOT / CANONICAL_REL
        ).resolve()
        assert Path(_load_twin("skill").__file__).resolve() == (
            PLUGIN_ROOT / TWIN_REL
        ).resolve()

    def test_the_twins_expose_distinct_function_and_exception_objects(self):
        """`pytest.raises(twin.ContainmentError)` is only a per-twin assertion
        if each module owns its own class object. If the twins ever shared one
        (an import rather than a copy), every capability row would silently
        become a single-twin assertion wearing a two-twin id."""
        canonical, skill = _load_twin("canonical"), _load_twin("skill")
        assert canonical is not skill
        assert canonical._atomic_write_text is not skill._atomic_write_text
        assert canonical.ContainmentError is not skill.ContainmentError


# ---------------------------------------------------------------------------
# Topology helpers
# ---------------------------------------------------------------------------

def _fd_count() -> int:
    """Open descriptors for this process. -1 when /dev/fd is unavailable."""
    try:
        return len(os.listdir("/dev/fd"))
    except OSError:  # pragma: no cover - platform without /dev/fd
        return -1


def _nested_project(root: Path):
    """project/sub/CLAUDE.md -- NESTED, so the ancestry walk actually runs.

    Returns (project, target). Do not replace this with a flat layout: see
    hazard 1. `sub` is one real level below the anchor, which is the minimum
    that makes `os.open("..", dir_fd=)` execute at all.
    """
    project = root / "project"
    holder = project / "sub"
    holder.mkdir(parents=True)
    target = holder / "CLAUDE.md"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    return project, target


def _flat_project(root: Path):
    """project/CLAUDE.md -- FLAT, where target.parent IS the anchor.

    Used ONLY to demonstrate the vacuity hazard. Never use it to certify the
    walk site.
    """
    project = root / "project"
    project.mkdir(parents=True)
    target = project / "CLAUDE.md"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    return project, target


def _two_leg_composition(project: Path, outside: Path) -> Path:
    """The topology that produced the regression, both legs.

    Leg 1: `project/.claude` -> symlink OUT to `outside`.
    Leg 2: `outside/CLAUDE.md` -> symlink back IN to `project/real.md`.

    A predicate that resolves the whole target path lands on `project/real.md`,
    sees it inside the anchor, and ALLOWS. The write traverses only the PARENT
    chain, lands in `outside`, and binds the leaf there as a directory entry --
    so the guard certified a path the write never touched. Returns the
    unresolved target `project/.claude/CLAUDE.md`.
    """
    project.mkdir(parents=True, exist_ok=True)
    outside.mkdir(parents=True, exist_ok=True)
    victim = project / "real.md"
    victim.write_text("IN-PROJECT VICTIM\n", encoding="utf-8")
    os.symlink(str(outside), str(project / ".claude"), target_is_directory=True)
    os.symlink(str(victim), str(outside / "CLAUDE.md"))
    return project / ".claude" / "CLAUDE.md"


def _volume_is_case_insensitive(base: Path) -> bool:
    probe = base / "CaseProbe"
    probe.mkdir()
    return (base / "caseprobe").exists()


def _find_python39():
    """Best-effort discovery of a real 3.9 interpreter, mirroring the
    established pattern in test_bootstrap_gate.py. Returns None when absent --
    callers skip rather than fail."""
    import shutil

    for candidate in (shutil.which("python3.9"), "/usr/bin/python3"):
        if not candidate or not Path(candidate).exists():
            continue
        try:
            probe = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if (probe.stdout + probe.stderr).strip().startswith("Python 3.9"):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Capability injection harness
# ---------------------------------------------------------------------------

class _CapabilityInjector:
    """Raises at EXACTLY ONE capability site.

    MEMBERSHIP PREDICATE, stated because leaving it unstated is what let the
    count be wrong three times. The covered set is EVERY `os.*` call in
    `_atomic_write_text` whose `NotImplementedError` must not reach the caller
    unmapped. That is SIX sites, of which FOUR are MAPPED to a distinct
    `ContainmentError` and TWO are SWALLOWED.

    THE SIXTH ARRIVED WHEN THE LINE-ENDING RESTORE MOVED INTO THIS FUNCTION,
    AND IT IS THE PREDICATE ABOVE DOING ITS JOB. `_detect_line_ending` opens the
    TARGET through the pinned parent descriptor, so it is a new `os.*` call
    whose NotImplementedError must not reach the caller. It is SWALLOWED rather
    than mapped, and that is deliberate: this read chooses a line ending, it
    decides nothing about containment, so a refusal here would be a new
    over-block on an axis that is not containment. A platform without
    directory-descriptor support still refuses the WRITE one moment later at
    site 3, which is where that refusal belongs.

    `dir_fd` was never the discriminator -- only a proxy close enough to look
    like one. Counting "sites that raise ContainmentError" gives four; counting
    "sites the capability contract covers" gives five; and an argument sweep on
    `dir_fd` misses the parent open (which passes none) while a read of the
    mapped-site list misses the cleanup unlink (which passes one). Three
    observers counted three different sets and each believed the list complete.
    Ask of any new `os.*` call: must its NotImplementedError not reach the
    caller? Yes means it belongs here, whatever its arguments look like.

    Four of the six sites are `os.open`, so a naive patch cannot tell them
    apart. The MECHANICAL discriminator -- an implementation detail of this
    injector, not the definition -- is the triple (dir_fd, path, O_CREAT). The
    third term earns its place: sites 3 and 6 agree on the first two, so a
    predicate built on the pair alone fires at BOTH and lands two hits where
    each row asserts one:

        site 1  parent-directory open   os.open,    dir_fd is None      MAPPED
        site 2  ancestry walk           os.open,    dir_fd, path ".."   MAPPED
        site 3  temp create             os.open,    dir_fd, O_CREAT     MAPPED
        site 4  rename                  os.replace                      MAPPED
        site 5  cleanup unlink          os.unlink,  dir_fd            SWALLOWED
        site 6  line-ending detect      os.open,    dir_fd, no O_CREAT
                                                                     SWALLOWED

    Getting this subtly wrong lands the injection on a DIFFERENT site while
    every assertion still passes -- which is why each test asserts the
    site-distinct message rather than merely that a ContainmentError was
    raised.

    Site 5 needs a PRECONDITION the others do not: the cleanup arm only runs
    after something already failed, so site 5 also raises at the rename to
    reach it. The rename raise is scaffolding; `unlink_hits` is what proves the
    site-5 injection itself fired.

    Also records, for non-vacuity and fd accounting:
      * `hits`         - times the chosen site actually fired
      * `unlink_hits`  - times the cleanup unlink fired (site 5 only)
      * `walk_calls`   - times the walk's os.open was reached at all
      * `parent_closes`- closes counted against the CAPTURED parent fd. Exactly
                         1 is correct; 0 is a leak; 2 is a double-close.
                         Counting closes globally cannot distinguish these.
    """

    def __init__(self, site: int, exc_factory):
        self.site = site
        self.exc_factory = exc_factory
        self.hits = 0
        self.unlink_hits = 0
        self.walk_calls = 0
        self.parent_fd = None
        self.parent_closes = 0
        self._real_open = os.open
        self._real_replace = os.replace
        self._real_close = os.close
        self._real_unlink = os.unlink

    def open(self, path, flags, mode=0o777, *, dir_fd=None, **kw):
        is_walk = dir_fd is not None and path == ".."
        is_parent = dir_fd is None
        if is_walk:
            self.walk_calls += 1
        is_create = dir_fd is not None and path != ".." and bool(flags & os.O_CREAT)
        is_detect = dir_fd is not None and path != ".." and not (flags & os.O_CREAT)
        hit = (
            (self.site == 1 and is_parent)
            or (self.site == 2 and is_walk)
            or (self.site == 3 and is_create)
            or (self.site == 6 and is_detect)
        )
        if hit:
            self.hits += 1
            raise self.exc_factory()
        if dir_fd is None:
            fd = self._real_open(path, flags, mode, **kw)
        else:
            fd = self._real_open(path, flags, mode, dir_fd=dir_fd, **kw)
        if is_parent and flags & os.O_DIRECTORY and self.parent_fd is None:
            self.parent_fd = fd
        return fd

    def replace(self, src, dst, **kw):
        # Site 5 raises here too, as the PRECONDITION that reaches the cleanup
        # arm -- the ContainmentError this produces is the one site 5 asserts
        # survives, so it is scaffolding rather than the subject.
        if self.site in (4, 5):
            self.hits += 1
            raise self.exc_factory()
        return self._real_replace(src, dst, **kw)

    def unlink(self, path, *, dir_fd=None, **kw):
        if self.site == 5:
            self.unlink_hits += 1
            raise self.exc_factory()
        return self._real_unlink(path, dir_fd=dir_fd, **kw)

    def close(self, fd):
        if self.parent_fd is not None and fd == self.parent_fd:
            self.parent_closes += 1
        return self._real_close(fd)

    def install(self, monkeypatch):
        monkeypatch.setattr(os, "open", self.open)
        monkeypatch.setattr(os, "replace", self.replace)
        monkeypatch.setattr(os, "close", self.close)
        monkeypatch.setattr(os, "unlink", self.unlink)


def _unsupported():
    return NotImplementedError("simulated: dir_fd unsupported on this platform")


def _eacces():
    return PermissionError(13, "simulated permission failure")


# ===========================================================================
# FORCE THE CAPABILITY BRANCH BY INJECTION.
# ===========================================================================

class TestCapabilityBranchInjection:
    """The four `dir_fd` sites map an unsupported primitive to a fail-CLOSED
    ContainmentError, each with its OWN message.

    THIS BRANCH IS TRIGGERED ARTIFICIALLY AND IS UNREACHABLE ON EVERY PLATFORM
    MEASURED -- `os.open` is in `os.supports_dir_fd` everywhere tested, so no
    natural path raises NotImplementedError. Nothing here is evidence that the
    capability is missing somewhere real.

    WHY THE MESSAGE ASSERTION IS LOAD-BEARING RATHER THAN COSMETIC.
    NotImplementedError subclasses RuntimeError, NOT OSError, while
    ContainmentError subclasses OSError -- so an UNMAPPED one is invisible to
    every caller arm written `except ContainmentError` or `except OSError`, and
    would crash the hook instead of failing closed into the site's opaque skip.
    If two sites shared a message, a test claiming to certify one of them would
    pass when the other fired. So each row asserts the distinct message, the
    observed hit count, the target's bytes and the fd delta.
    """

    @pytest.mark.parametrize(
        "site,expected_message",
        [
            (1, MSG_BOUNDARY),
            (2, MSG_CAP_WALK),
            (3, MSG_CAP_TEMP),
            (4, MSG_CAP_RENAME),
        ],
        ids=["parent-open", "ancestry-walk", "temp-create", "rename"],
    )
    def test_each_dir_fd_site_fails_closed_with_its_own_message(
        self, tmp_path, monkeypatch, twin, site, expected_message
    ):
        """Four EXPLICIT rows, not a quantifier.

        A count is checkable; a quantifier reads as satisfied by any injection
        that raises. An author who injects once, sees a ContainmentError and
        moves on has satisfied "inject at all dir_fd sites" as written -- which
        is exactly how three sites read as covered while a fourth sat unmapped
        and would have crashed the hook.

        NESTED topology is mandatory here: on a flat layout site 2 is never
        reached (hazard 1), and a broad injection would instead fire at site 3
        while this test's name still claimed site 2.
        """
        project, target = _nested_project(tmp_path)
        before = target.read_bytes()
        fds_before = _fd_count()

        injector = _CapabilityInjector(site, _unsupported)
        injector.install(monkeypatch)
        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "NEW PAYLOAD\n", project)
        monkeypatch.undo()

        assert str(excinfo.value) == expected_message, (
            f"site {site} raised the wrong message -- a shared or drifted "
            f"message means this assertion cannot tell which site fired"
        )
        # NON-VACUITY: the injection actually ran. Never infer firing from a
        # green result -- a green result only proves something raised.
        assert injector.hits == 1, (
            f"site {site} injection fired {injector.hits}x, expected exactly 1"
        )
        # The failure mode that matters is not "it raised" but "it raised and
        # left the target or the fd table damaged".
        assert target.read_bytes() == before
        assert _fd_count() == fds_before
        assert not [p for p in target.parent.iterdir() if p.name != target.name], (
            "a stray temp file was left next to the user's CLAUDE.md"
        )

    def test_the_line_ending_detect_site_is_swallowed_and_falls_back_to_lf(
        self, tmp_path, monkeypatch, twin
    ):
        """Site 6 is SWALLOWED, and this row is the one that says so out loud.

        THE OTHER FOUR ROWS ASSERT A REFUSAL. This one asserts that a refusal
        does NOT happen, so it is the row that would be missing if somebody read
        "all dir_fd sites map to ContainmentError" off the four mapped rows and
        applied it here.

        WHY SWALLOWED IS CORRECT. This read chooses a line ending. It decides
        nothing about containment, so a refusal here would be a new over-block
        on an axis that is not containment. The platform capability that is
        missing still stops the WRITE, one moment later at site 3, and that is
        where the refusal belongs.

        THE FALLBACK IS LF, WHICH IS THE SAME ANSWER A TARGET NOT ON DISK GIVES.
        So an unreadable target and an absent one agree, and neither converts a
        document that has no CRLF in it.
        """
        project, target = _nested_project(tmp_path)

        injector = _CapabilityInjector(6, _unsupported)
        injector.install(monkeypatch)
        twin._atomic_write_text(target, "NEW PAYLOAD\n", project)
        monkeypatch.undo()

        # NON-VACUITY: the injection fired. Without this the assertions below
        # hold for a run where site 6 was never reached at all, which is the
        # state a renamed helper or a moved detection would produce.
        assert injector.hits == 1, (
            f"site 6 injection fired {injector.hits}x, expected exactly 1. The "
            f"line-ending detection is no longer reached through the pinned "
            f"parent descriptor, so this row is measuring nothing"
        )
        # The write COMPLETED rather than refused, and it landed the payload.
        assert target.read_text(encoding="utf-8") == "NEW PAYLOAD\n"
        assert target.read_bytes() == b"NEW PAYLOAD\n", (
            "the swallowed capability failure changed the bytes that landed"
        )

    def test_walk_site_is_genuinely_reached_on_the_nested_topology(
        self, tmp_path, monkeypatch, twin
    ):
        """Companion to the ancestry-walk row: the walk really executes here.

        Paired with `test_flat_topology_makes_the_walk_site_unreachable`, this
        is what gives the ancestry-walk row its meaning. Without the flat control, "the
        injection fired" is an unfalsified claim about a site that might have
        been reachable by accident.
        """
        project, target = _nested_project(tmp_path)
        injector = _CapabilityInjector(2, _unsupported)
        injector.install(monkeypatch)
        with pytest.raises(twin.ContainmentError):
            twin._atomic_write_text(target, "NEW\n", project)
        monkeypatch.undo()

        assert injector.walk_calls == 1
        assert injector.hits == 1

    def test_flat_topology_makes_the_walk_site_unreachable(
        self, tmp_path, monkeypatch, twin
    ):
        """DEMONSTRATES the vacuity hazard rather than asserting it.

        On a flat layout `target.parent` IS the anchor, so the ancestry check
        matches on the first iteration and breaks BEFORE the first
        `os.open("..")`. The site-2 injection therefore never fires and the
        write SUCCEEDS. A capability test written against this layout would be
        certifying nothing at all.

        This test is the reason the ancestry-walk row can be trusted. Do not "repair" it
        by making it expect a refusal -- a refusal here would mean the flat
        write path had broken.
        """
        project, target = _flat_project(tmp_path)
        injector = _CapabilityInjector(2, _unsupported)
        injector.install(monkeypatch)
        twin._atomic_write_text(target, "NEW PAYLOAD\n", project)
        monkeypatch.undo()

        # COUPLING LEG FIRST. The two assertions below are ABSENCES, and an
        # absence passes vacuously if the injector was never installed at all.
        # Capturing the parent fd proves `injector.open` really intercepted the
        # parent-directory open, so the zeros that follow are observations
        # rather than an artefact of a dead patch.
        assert injector.parent_fd is not None, (
            "the injector never intercepted the parent open -- the zeros below "
            "would be vacuous"
        )
        assert injector.walk_calls == 0, "the walk ran on a flat topology"
        assert injector.hits == 0, "the injection fired where it cannot be reached"
        assert target.read_text(encoding="utf-8") == "NEW PAYLOAD\n"

    def test_genuine_permission_error_at_the_walk_stays_a_raw_oserror(
        self, tmp_path, monkeypatch, twin
    ):
        """NEGATIVE CONTROL -- the mapping is narrow BY DESIGN.

        Only NotImplementedError is mapped at the walk. A genuine EACCES on an
        ancestor the user cannot read must keep propagating RAW, because
        relabelling it would report a permission failure as a capability
        failure.

        Without this control a future widening of that handler to
        `except OSError` would silently relabel EVERY permission failure as a
        capability failure, and all four rows above would still pass. This is
        the test that makes the narrowness falsifiable.
        """
        project, target = _nested_project(tmp_path)
        before = target.read_bytes()
        fds_before = _fd_count()

        injector = _CapabilityInjector(2, _eacces)
        injector.install(monkeypatch)
        with pytest.raises(OSError) as excinfo:
            twin._atomic_write_text(target, "NEW\n", project)
        monkeypatch.undo()

        # ContainmentError IS an OSError subclass, so `pytest.raises(OSError)`
        # alone would pass on a relabelled error. The exclusion is the assertion.
        assert not isinstance(excinfo.value, twin.ContainmentError), (
            "a genuine EACCES was relabelled as a containment/capability "
            "failure -- the walk's handler has been widened beyond "
            "NotImplementedError"
        )
        assert excinfo.value.errno == 13
        assert injector.hits == 1
        assert injector.parent_closes == 1, (
            f"parent fd closed {injector.parent_closes}x -- 1 is correct, "
            f"0 leaks, 2 is a double-close"
        )
        assert target.read_bytes() == before
        assert _fd_count() == fds_before

    def test_cleanup_unlink_is_swallowed_and_the_original_error_survives(
        self, tmp_path, monkeypatch, twin
    ):
        """SITE 5 -- the SWALLOWED site. Deliberately NOT in the family above.

        The four rows above assert "a ContainmentError with THIS site's own
        message". Site 5 asserts the opposite shape: NO new exception, and the
        exception already in flight arrives INTACT. Parametrizing it alongside
        them would force the shared assertion down to whatever all five satisfy,
        which means dropping the message check that makes the other four
        meaningful -- and a row labelled "site 5" inside a family whose
        docstring says "each site raises a distinct message" would misdescribe
        what it checks.

        WHY IDENTITY AND NOT TYPE. The cleanup runs while an exception is
        already propagating, so anything raised there REPLACES it and the
        handler's bare `raise` never runs. Asserting only that "a
        ContainmentError arrived" would therefore pass even if the cleanup
        raised its OWN ContainmentError -- the precise substitution this site
        exists to forbid, sailing through the test written to forbid it. So the
        assertion is on the MESSAGE: the rename's message, unchanged.

        ARTIFICIAL, like the rest of this class: `os.unlink` supports `dir_fd`
        on every platform measured, so nothing here is evidence the capability
        is missing anywhere real. It proves the SWALLOW, not the branch.

        A STRAY TEMP SURVIVES ON THIS PATH, and that is inherent rather than a
        defect: the primitive being injected IS the cleanup, so nothing
        downstream can remove the file. The fix corrects the exception CLASS; it
        cannot perform a removal the platform cannot perform. Do not "repair"
        this test by asserting the temp is gone -- that assertion can only be
        made true by adding a second cleanup path, which is new machinery on a
        branch that has never executed.
        """
        project, target = _nested_project(tmp_path)
        before = target.read_bytes()
        fds_before = _fd_count()

        injector = _CapabilityInjector(5, _unsupported)
        injector.install(monkeypatch)
        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "NEW PAYLOAD\n", project)
        monkeypatch.undo()

        # IDENTITY, not type: the rename's message must arrive unaltered.
        assert str(excinfo.value) == MSG_CAP_RENAME, (
            "the cleanup substituted its own error for the one it was cleaning "
            "up after -- a stray temp file must never outrank the reason the "
            "write was refused"
        )
        # NON-VACUITY, both legs: the precondition reached the cleanup arm, and
        # the site-5 injection itself fired inside it. Without the second the
        # first would only prove the rename mapping still works.
        assert injector.hits == 1, "the rename precondition did not fire"
        assert injector.unlink_hits == 1, (
            f"the cleanup unlink fired {injector.unlink_hits}x -- the swallow "
            f"was never exercised"
        )
        assert injector.parent_closes == 1, (
            f"parent fd closed {injector.parent_closes}x -- 1 is correct, "
            f"0 leaks, 2 is a double-close"
        )
        assert target.read_bytes() == before
        assert _fd_count() == fds_before

    def test_parent_descriptor_is_closed_exactly_once_on_the_walk_failure(
        self, tmp_path, monkeypatch, twin
    ):
        """fd accounting on the mapped path, counted against the CAPTURED fd.

        The mapping is a NESTED inner try inside the outer try, so the
        ContainmentError it raises still reaches
        `except BaseException: os.close(parent_fd)`. An explicit close in the
        inner arm would therefore DOUBLE-close. A refactor to a sibling arm
        inverts this and would then require the explicit close -- so this
        assertion is what pins the current structure.
        """
        project, target = _nested_project(tmp_path)
        injector = _CapabilityInjector(2, _unsupported)
        injector.install(monkeypatch)
        with pytest.raises(twin.ContainmentError):
            twin._atomic_write_text(target, "NEW\n", project)
        monkeypatch.undo()

        assert injector.parent_closes == 1


# ===========================================================================
# The two-leg composition through real production entry points
# ===========================================================================

class TestTwoLegCompositionThroughProductionEntryPoints:
    """The regression topology, driven through each of the THREE production
    entry points that regressed -- named individually so this cannot be
    discharged by testing one of them.

    The verdict is keyed on the RETURNED STATUS STRING, not on a filesystem
    delta: an absent file cannot distinguish "refused" from "no-opped for an
    unrelated reason", so a delta-only assertion would pass against a function
    that never reached the write at all.
    """

    _LEGACY = "# Project Memory\n\n## Pinned Context\n\n## Working Memory\n"
    _STALE = (
        "# Project Memory\n\n## Pinned Context\n\n"
        "### Old Feature (PR #100, merged 2020-01-01)\n- detail\n\n"
    )

    def test_migrate_to_managed_structure_refuses(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import migrate_to_managed_structure

        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        target = _two_leg_composition(project, outside)
        # Legacy content reachable THROUGH both legs, so the write path is
        # reached rather than short-circuited by a no-op branch.
        target.write_text(self._LEGACY, encoding="utf-8")
        victim_before = (project / "real.md").read_bytes()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = migrate_to_managed_structure()

        assert result == (
            "Migration skipped: project CLAUDE.md path precondition not met."
        )
        assert (project / "real.md").read_bytes() == victim_before
        assert "PACT_MANAGED_START" not in (outside / "CLAUDE.md").read_text(
            encoding="utf-8"
        )

    def test_update_session_info_refuses(self, tmp_path, monkeypatch):
        from shared.session_resume import update_session_info

        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        _two_leg_composition(project, outside)
        victim_before = (project / "real.md").read_bytes()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = update_session_info("sid-123", "PACT-team")

        # EXACT string. `result is None or "skipped" in result` would pass on
        # the None returned when no project dir is set -- a disjunction that
        # cannot fail for the right reason.
        assert result == "Session info skipped: path precondition not met."
        assert (project / "real.md").read_bytes() == victim_before
        assert "SESSION_START" not in (outside / "CLAUDE.md").read_text(
            encoding="utf-8"
        )

    def test_check_pinned_staleness_refuses(self, tmp_path):
        from unittest.mock import patch

        from session_init import check_pinned_staleness

        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        target = _two_leg_composition(project, outside)
        target.write_text(self._STALE, encoding="utf-8")
        victim_before = (project / "real.md").read_bytes()

        with patch("session_init._get_project_claude_md_path", return_value=target), \
             patch("staleness._get_project_claude_md_path", return_value=target):
            result = check_pinned_staleness()

        assert result == "Pinned staleness skipped: path precondition not met."
        assert (project / "real.md").read_bytes() == victim_before


# ===========================================================================
# Refusals, each paired with a positive control
# ===========================================================================

class TestPredicateRefusals:
    """Every refusal below is paired with `test_positive_control_*`, which
    writes successfully through the SAME twin in the same harness. Without the
    pairing a refusal assertion is satisfied by a predicate that refuses
    everything."""

    def test_positive_control_plain_nested_write_is_allowed(self, tmp_path, twin):
        """A plain nested write, and the non-vacuity control for this whole class."""
        project, target = _nested_project(tmp_path)
        twin._atomic_write_text(target, "PAYLOAD\n", project)
        assert target.read_text(encoding="utf-8") == "PAYLOAD\n"

    def test_two_leg_composition_refused_at_the_predicate(self, tmp_path, twin):
        """The two-leg composition at the predicate level, for BOTH twins.

        The entry-point tests above drive only the canonical copy, because the
        three regressing entry points live there. This is the skill twin's
        independent exercise of the same topology.
        """
        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        target = _two_leg_composition(project, outside)
        victim_before = (project / "real.md").read_bytes()
        outside_before = (outside / "CLAUDE.md").read_bytes()

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "ESCAPED\n", project)

        assert str(excinfo.value) == MSG_ESCAPE
        assert (project / "real.md").read_bytes() == victim_before
        assert (outside / "CLAUDE.md").read_bytes() == outside_before

    def test_symlinked_parent_out_of_project_refused(self, tmp_path, twin):
        """The single-leg parent-out escape."""
        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        project.mkdir()
        outside.mkdir()
        os.symlink(str(outside), str(project / ".claude"), target_is_directory=True)
        target = project / ".claude" / "CLAUDE.md"

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "ESCAPED\n", project)

        assert str(excinfo.value) == MSG_ESCAPE
        assert not (outside / "CLAUDE.md").exists()

    def test_sibling_prefix_refused_structurally(self, tmp_path, twin):
        """`/abc` is refused under anchor `/ab`.

        RATIONALE UPDATED FOR THE CORRECTED PREDICATE. This case used to be
        explained by the choice of `os.path.commonpath` over `str.startswith`;
        that primitive has left the write path entirely. The refusal is now
        STRUCTURAL and has nothing to do with text: `/abc` simply never walks
        up to `/ab`'s inode. The string-prefix trap it guards against cannot
        even be expressed in the current predicate.
        """
        anchor = tmp_path / "ab"
        anchor.mkdir()
        sibling = tmp_path / "abc"  # shares the STRING prefix, not a path child
        sibling.mkdir()
        target = sibling / "CLAUDE.md"
        target.write_text("orig\n", encoding="utf-8")

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "new\n", anchor)

        assert str(excinfo.value) == MSG_ESCAPE
        assert target.read_text(encoding="utf-8") == "orig\n"

    def test_absent_parent_refused_at_check_time(self, tmp_path, twin):
        """An absent parent -- strictness, by construction rather than by policy.

        The superseded predicate lexically completed a non-existent parent and
        certified it CONTAINED; if the parent then appeared as an outward
        symlink the write escaped, deterministically and without a race.
        `os.open` on an absent parent raises, so the fail-open closes for free.
        """
        project = tmp_path / "proj"
        project.mkdir()
        target = project / "does-not-exist" / "CLAUDE.md"

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "new\n", project)

        # The boundary could not be established at all -- distinct from escape.
        assert str(excinfo.value) == MSG_BOUNDARY

    def test_symlink_loop_in_target_parent_chain_refused(self, tmp_path, twin):
        """A symlink loop in the TARGET parent chain.

        A loop reaches the kernel as ELOOP -- an OSError -- on every
        interpreter, because there is no second, userspace resolver here to
        disagree about whether a loop is even an error. The superseded
        predicate inherited `Path.resolve()`'s version-dependent behaviour on
        exactly this input.
        """
        project = tmp_path / "proj"
        project.mkdir()
        os.symlink(str(project / "loop_b"), str(project / "loop_a"))
        os.symlink(str(project / "loop_a"), str(project / "loop_b"))
        target = project / "loop_a" / "CLAUDE.md"

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "new\n", project)

        assert str(excinfo.value) == MSG_BOUNDARY

    def test_symlink_loop_in_anchor_chain_refused(self, tmp_path, twin):
        """A symlink loop in the ANCHOR chain.

        The anchor is `os.stat(project_root)`, so a loop there raises in the
        same handler. Both sides use ONE resolver -- the kernel.
        """
        project, target = _nested_project(tmp_path)
        anchor_a = tmp_path / "anchor_a"
        anchor_b = tmp_path / "anchor_b"
        os.symlink(str(anchor_b), str(anchor_a))
        os.symlink(str(anchor_a), str(anchor_b))

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "new\n", anchor_a)

        assert str(excinfo.value) == MSG_BOUNDARY


# ===========================================================================
# The ALLOW branch, where the repaired defect lives
# ===========================================================================

class TestPredicateAllows:
    """The over-block certification. A guard tested only for refusal passes by
    refusing everything, and the shipped defect was on this branch."""

    def test_benign_symlinked_dot_claude_parent_absolute_allowed(self, tmp_path, twin):
        """A benign symlinked `.claude` PARENT -- and this is the test that FAILS if anyone adds
        O_NOFOLLOW to the parent open.

        `.claude` is a symlink whose target is INSIDE the project. Both the
        superseded predicate and the code before it allowed this. O_NOFOLLOW on
        the parent open would refuse ANY symlinked final component of the
        parent path, which is a NEW over-block on an axis that is not
        containment -- and it would add nothing, because the ancestry test runs
        ON the opened descriptor, so there is no check-then-open gap to close.
        """
        project = tmp_path / "proj"
        real_dir = project / "config" / "claude"
        real_dir.mkdir(parents=True)
        os.symlink(str(real_dir), str(project / ".claude"), target_is_directory=True)
        target = project / ".claude" / "CLAUDE.md"

        twin._atomic_write_text(target, "PAYLOAD\n", project)

        assert (real_dir / "CLAUDE.md").read_text(encoding="utf-8") == "PAYLOAD\n"

    def test_benign_symlinked_dot_claude_parent_relative_allowed(self, tmp_path, twin):
        """A benign symlinked `.claude` PARENT, RELATIVE form -- what a committed repo symlink
        actually looks like. A repo cannot commit an absolute symlink, so this
        is the shape that ships; certifying only the absolute form would leave
        the realistic one uncovered."""
        project = tmp_path / "proj"
        real_dir = project / "config" / "claude"
        real_dir.mkdir(parents=True)
        os.symlink("config/claude", str(project / ".claude"), target_is_directory=True)
        target = project / ".claude" / "CLAUDE.md"

        twin._atomic_write_text(target, "PAYLOAD\n", project)

        assert (real_dir / "CLAUDE.md").read_text(encoding="utf-8") == "PAYLOAD\n"

    def test_deep_in_project_path_allowed_at_depth_80(self, tmp_path, twin):
        """Regression test for the superseded depth bound.

        THE NUMBER IS THE WHOLE POINT. The defective bound admitted 63 levels,
        so a test at depth 5 or 10 passes while proving nothing about it. The
        certification floor is 70; this runs at 80.

        The bound is now a LIVENESS backstop rather than a policy ceiling:
        reaching it means the filesystem is misreporting "..", not that a path
        is legitimately deep.
        """
        project = tmp_path / "proj"
        deep = project
        for i in range(DEEP_DEPTH):
            deep = deep / f"d{i}"
        deep.mkdir(parents=True)
        target = deep / "CLAUDE.md"

        twin._atomic_write_text(target, "DEEP\n", project)

        assert target.read_text(encoding="utf-8") == "DEEP\n"

    def test_case_variant_anchor_allowed_on_case_insensitive_volume(
        self, tmp_path, twin
    ):
        """A case-variant anchor -- both string predicates over-block here.

        Nothing is compared as text, so a case-variant spelling of the anchor
        stats to the SAME inode and is contained. On a case-SENSITIVE volume
        the variant spelling is a genuinely different, non-existent directory
        and `os.stat` raises -- fail-closed and correct -- so the case is
        skipped there rather than asserted.
        """
        if not _volume_is_case_insensitive(tmp_path):
            pytest.skip("case-sensitive volume: the variant anchor does not exist")

        project = tmp_path / "MixedCase"
        (project / "sub").mkdir(parents=True)
        target = project / "sub" / "CLAUDE.md"
        variant_anchor = tmp_path / "mixedcase"

        twin._atomic_write_text(target, "PAYLOAD\n", variant_anchor)

        assert target.read_text(encoding="utf-8") == "PAYLOAD\n"

    def test_nfd_variant_anchor_allowed_when_the_volume_normalises(
        self, tmp_path, twin
    ):
        """Unicode normalisation form.

        Same mechanism as the case variant: the kernel resolves both spellings
        to one inode, and the predicate never compares the text. Skipped where
        the volume does not treat the two forms as one name.
        """
        nfc = unicodedata.normalize("NFC", "café-proj")
        nfd = unicodedata.normalize("NFD", "café-proj")
        project = tmp_path / nfc
        (project / "sub").mkdir(parents=True)
        target = project / "sub" / "CLAUDE.md"
        variant_anchor = tmp_path / nfd
        try:
            os.stat(str(variant_anchor))
        except OSError:
            pytest.skip("volume does not normalise NFC/NFD to one name")

        twin._atomic_write_text(target, "PAYLOAD\n", variant_anchor)

        assert target.read_text(encoding="utf-8") == "PAYLOAD\n"


# ===========================================================================
# The leaf-outside-root coupling tripwire
# ===========================================================================

class TestLeafOutsideRootCouplingTripwire:
    """A leaf symlink pointing OUTSIDE the root is ALLOWED, and that is the
    correct verdict -- a derivation, not a concession.

    Containment is entirely a property of the PARENT chain, because the parent
    chain is the only part the kernel traverses on the way to the write.
    `os.replace` is renameat(2): it unlinks whatever entry sits at the final
    name and binds the temp file's inode there, never opening and never
    following the leaf. So the payload lands at the in-project entry and any
    outside victim keeps its bytes.

    THIS IS A COUPLING TRIPWIRE AND MUST NOT BE "FIXED" BY MAKING IT REFUSE.
    The ALLOW is sound ONLY while the write replaces the leaf ENTRY rather than
    writing THROUGH it. Two rewrites would silently convert it into a real
    escape with the predicate untouched: resolving the target once and using it
    downstream (which makes check and act agree by making the WRITE follow the
    leaf), or replacing temp-plus-rename with an open/truncate on the target.
    A failure here means THE WRITE SHAPE CHANGED. Making the guard refuse the
    topology would re-introduce the cardinal over-block this work removed.

    WHAT THIS ADDS OVER THE EXISTING IN-PROJECT TRIPWIRE, stated narrowly: the
    in-project half is already pinned by an executing test. This pins the
    OUT-OF-PROJECT topology, which could not be pinned before, because the
    superseded predicate refused it outright.
    """

    def test_leaf_pointing_outside_is_allowed_and_lands_in_project(
        self, tmp_path, twin
    ):
        project = tmp_path / "proj"
        project.mkdir()
        (project / "sub").mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        victim = outside / "victim.md"
        victim.write_text("OUTSIDE VICTIM\n", encoding="utf-8")
        victim_before = victim.read_bytes()
        victim_ino_before = victim.stat().st_ino

        entry = project / "sub" / "CLAUDE.md"
        os.symlink(str(victim), str(entry))

        # ALLOWED -- containment never consults the leaf.
        twin._atomic_write_text(entry, "PAYLOAD\n", project)

        # 1. the payload landed at the IN-PROJECT entry
        assert entry.read_text(encoding="utf-8") == "PAYLOAD\n"
        # 2. the outside victim is byte-unchanged -- and same inode, so it was
        #    not replaced either. Byte-equality alone would ALSO hold under a
        #    refusal, so on its own it cannot distinguish ALLOW from REFUSE;
        #    assertion 3 is what pins the ALLOW.
        assert victim.read_bytes() == victim_before
        assert victim.stat().st_ino == victim_ino_before
        # 3. the in-project entry is no longer a symlink -- the write HAPPENED,
        #    and it replaced the link rather than following it.
        assert not entry.is_symlink()


# ===========================================================================
# Exhaustion is distinguishable from escape
# ===========================================================================

class _FakeStat:
    """Minimal stand-in: the walk reads only st_dev and st_ino."""

    def __init__(self, dev, ino):
        self.st_dev = dev
        self.st_ino = ino


class TestWalkExhaustion:
    def test_exhaustion_raises_its_own_message_not_the_escape_message(
        self, tmp_path, monkeypatch, twin
    ):
        """Exhaustion and escape are both ContainmentError, so only the message
        separates them.

        If exhaustion reused the escape message, a future debugger would hunt a
        phantom symlink for a walk that simply ran out. Nothing escaped here.

        HOW EXHAUSTION IS FORCED. A real 1024-deep tree is not reachable
        (PATH_MAX) and opening 1024 real descriptors would hit the process fd
        limit and raise EMFILE -- which would surface as a RAW OSError and make
        this test assert the wrong thing entirely. So the walk is synthesised:
        every `os.open("..")` returns a sentinel descriptor whose fstat reports
        a unique (st_dev, st_ino), so neither the anchor match nor the
        self-parent match can ever fire. That is precisely the condition the
        bound exists to survive -- a filesystem misreporting "..".
        """
        project, target = _nested_project(tmp_path)
        real_open, real_fstat, real_close = os.open, os.fstat, os.close
        sentinel_base = 1 << 30
        state = {"next": sentinel_base, "walk_opens": 0}

        def fake_open(path, flags, mode=0o777, *, dir_fd=None, **kw):
            if dir_fd is not None and path == "..":
                state["walk_opens"] += 1
                fd = state["next"]
                state["next"] += 1
                return fd
            if dir_fd is None:
                return real_open(path, flags, mode, **kw)
            return real_open(path, flags, mode, dir_fd=dir_fd, **kw)

        def fake_fstat(fd):
            if fd >= sentinel_base:
                # Unique every time: never the anchor, never its own parent.
                return _FakeStat(-1, fd)
            return real_fstat(fd)

        def fake_close(fd):
            if fd >= sentinel_base:
                return None
            return real_close(fd)

        monkeypatch.setattr(os, "open", fake_open)
        monkeypatch.setattr(os, "fstat", fake_fstat)
        monkeypatch.setattr(os, "close", fake_close)
        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "new\n", project)
        monkeypatch.undo()

        assert str(excinfo.value) == MSG_EXHAUSTION
        assert str(excinfo.value) != MSG_ESCAPE, (
            "exhaustion reported itself as an escape -- nothing escaped, the "
            "walk ran out"
        )
        # Pins the INLINE literal bound. The design deliberately kept it inline
        # rather than a module constant so it stays inside the twin drift gate;
        # this is the only assertion outside that gate which notices it change.
        assert state["walk_opens"] == WALK_BOUND


# ===========================================================================
# Mode determinism under a restrictive umask
# ===========================================================================

class TestModeDeterminism:
    def test_final_mode_is_0600_under_restrictive_umask(self, tmp_path, twin):
        """Pins the fchmod whose rationale was corrected.

        The fchmod is NOT defending against over-permissiveness: umask can only
        CLEAR bits, so `os.open(..., 0o600)` cannot yield anything more
        permissive than 0o600. Its actual effect is the opposite -- it RESTORES
        an owner-write bit that a restrictive umask removed. 0o277 is the case
        that would otherwise leave 0o400, which is why the test uses it rather
        than a milder value.
        """
        import stat

        project, target = _nested_project(tmp_path)
        old_umask = os.umask(0o277)
        try:
            twin._atomic_write_text(target, "PAYLOAD\n", project)
        finally:
            os.umask(old_umask)

        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert target.read_text(encoding="utf-8") == "PAYLOAD\n"


# ===========================================================================
# Descriptor hygiene, allow and refuse counted SEPARATELY
# ===========================================================================

class TestDescriptorHygiene:
    """The prior arc leaked a descriptor in this function; the delta assertion
    is the durable guard.

    Counted SEPARATELY rather than in aggregate, and with a NAMED count. "A
    batch" is satisfiable by two calls, and an aggregate delta lets a leak on
    one path be masked by the other path being clean.
    """

    CALLS = 120  # certification floor is 100 each; the measured baseline was 300

    def test_successful_writes_leak_no_descriptors(self, tmp_path, twin):
        project, target = _nested_project(tmp_path)
        twin._atomic_write_text(target, "warmup\n", project)  # settle lazy imports

        before = _fd_count()
        for i in range(self.CALLS):
            twin._atomic_write_text(target, f"payload {i}\n", project)
        after = _fd_count()

        assert after - before == 0, f"leaked {after - before} fds over {self.CALLS} writes"
        assert target.read_text(encoding="utf-8") == f"payload {self.CALLS - 1}\n"

    def test_refused_writes_leak_no_descriptors(self, tmp_path, twin):
        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        project.mkdir()
        outside.mkdir()
        os.symlink(str(outside), str(project / ".claude"), target_is_directory=True)
        target = project / ".claude" / "CLAUDE.md"
        with pytest.raises(twin.ContainmentError):
            twin._atomic_write_text(target, "warmup\n", project)

        before = _fd_count()
        refusals = 0
        for _ in range(self.CALLS):
            with pytest.raises(twin.ContainmentError):
                twin._atomic_write_text(target, "nope\n", project)
            refusals += 1
        after = _fd_count()

        assert refusals == self.CALLS  # the loop really ran; not a vacuous 0
        assert after - before == 0, (
            f"leaked {after - before} fds over {self.CALLS} refusals"
        )


class TestACloseAfterTheRenameIsNotAFailedWrite:
    """Closing the parent directory is the only step after the rename. Once
    the rename has put the new file in place, a failure there is not a failed
    write; after a failed write it must not replace the error that stopped it.
    """

    @staticmethod
    def _close_fails_once_after_replace(monkeypatch, replace_error=None):
        real_replace, real_close = os.replace, os.close
        state = {"armed": False, "fired": 0}

        def replace(*args, **kwargs):
            state["armed"] = True
            if replace_error is not None:
                raise replace_error
            return real_replace(*args, **kwargs)

        def close(fd):
            real_close(fd)
            if state["armed"]:
                state["armed"] = False
                state["fired"] += 1
                raise OSError(errno.EIO, "injected close failure")

        monkeypatch.setattr(os, "replace", replace)
        monkeypatch.setattr(os, "close", close)
        return state

    def test_a_close_that_fails_after_the_rename_reports_success(
        self, tmp_path, monkeypatch, twin
    ):
        project, target = _nested_project(tmp_path)
        twin._atomic_write_text(target, "warmup\n", project)
        before = _fd_count()
        state = self._close_fails_once_after_replace(monkeypatch)

        twin._atomic_write_text(target, "NEW\n", project)

        assert state["fired"] == 1
        assert target.read_text(encoding="utf-8") == "NEW\n"
        assert _fd_count() - before == 0

    def test_a_close_that_fails_after_a_failed_write_keeps_the_write_error(
        self, tmp_path, monkeypatch, twin
    ):
        project, target = _nested_project(tmp_path)
        state = self._close_fails_once_after_replace(
            monkeypatch, OSError(errno.ENOSPC, "injected replace failure")
        )

        with pytest.raises(OSError) as raised:
            twin._atomic_write_text(target, "NEW\n", project)

        assert raised.value.errno == errno.ENOSPC
        assert state["fired"] == 1
        assert target.read_text(encoding="utf-8") == "ORIGINAL\n"


class TestALockReleaseFailureIsNotTheBodysFailure:
    """file_lock releases with flock(LOCK_UN) and then a close. A failure in
    either comes after the locked work is done: it must not turn a body that
    succeeded into a failure, nor replace the error of a body that failed."""

    @staticmethod
    def _release_step_fails_once(monkeypatch, step):
        import fcntl
        real_flock, real_close = fcntl.flock, os.close
        state = {"locked_fd": None, "fired": 0}

        def flock(fd, operation):
            real_flock(fd, operation)
            if operation & fcntl.LOCK_UN == 0:
                state["locked_fd"] = fd
            elif step == "unlock" and fd == state["locked_fd"]:
                state["fired"] += 1
                raise OSError(errno.EIO, "injected unlock failure")

        def close(fd):
            real_close(fd)
            if step == "close" and fd == state["locked_fd"]:
                state["locked_fd"] = None
                state["fired"] += 1
                raise OSError(errno.EIO, "injected close failure")

        monkeypatch.setattr(fcntl, "flock", flock)
        monkeypatch.setattr(os, "close", close)
        return state

    @staticmethod
    def _lock_is_free(target):
        import fcntl
        fd = os.open(str(target.parent / f".{target.name}.lock"), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        return True

    @pytest.mark.parametrize("step", ["unlock", "close"])
    def test_a_failing_release_after_a_good_body_raises_nothing(
        self, tmp_path, monkeypatch, twin, step
    ):
        project, target = _nested_project(tmp_path)
        with twin.file_lock(target):
            pass
        before = _fd_count()
        state = self._release_step_fails_once(monkeypatch, step)

        with twin.file_lock(target):
            target.write_text("NEW\n", encoding="utf-8")

        assert state["fired"] == 1
        monkeypatch.undo()
        assert target.read_text(encoding="utf-8") == "NEW\n"
        assert _fd_count() - before == 0
        assert self._lock_is_free(target)

    def test_a_failing_release_keeps_the_bodys_own_error(
        self, tmp_path, monkeypatch, twin
    ):
        project, target = _nested_project(tmp_path)
        state = self._release_step_fails_once(monkeypatch, "unlock")

        with pytest.raises(ValueError, match="the body failed"):
            with twin.file_lock(target):
                raise ValueError("the body failed")

        assert state["fired"] == 1


# ===========================================================================
# The message contract itself
# ===========================================================================

class TestRefusalMessageContract:
    """The per-site messages are load-bearing for the certification, not just
    for logs: they are the only thing that lets a capability test say WHICH
    site fired. If two of them ever collapse to one string, the injection rows
    above keep passing while proving strictly less. This class is what makes
    that collapse a test failure."""

    def test_all_refusal_messages_are_pairwise_distinct(self):
        assert len(set(ALL_MESSAGES)) == len(ALL_MESSAGES), (
            "two refusal messages collapsed -- a capability row can no longer "
            "discriminate the site that raised"
        )

    @pytest.mark.parametrize("rel", [CANONICAL_REL, TWIN_REL])
    def test_source_renders_exactly_these_messages(self, rel):
        """Read the messages out of the SOURCE by AST join and compare.

        WHY AST AND NOT GREP -- this is hazard 2 made executable. The walk's
        message is two adjacent string literals in the source, so a
        line-oriented search for the rendered phrase finds NOTHING while the
        joined constant is the full sentence. `ast.literal_eval` of the node
        sees what Python sees. A verifier who reaches for grep here will
        conclude, wrongly, that the asserted message does not exist.
        """
        import ast

        source = (PLUGIN_ROOT / rel).read_text(encoding="utf-8")
        fn = next(
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and node.name == "_atomic_write_text"
        )
        rendered = {
            node.exc.args[0].value
            for node in ast.walk(fn)
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and node.exc.args
            and isinstance(node.exc.args[0], ast.Constant)
        }
        assert rendered == set(ALL_MESSAGES)

    def test_the_walk_message_is_split_in_source_and_unfindable_by_grep(self):
        """Pins the hazard itself, so it cannot silently stop being true.

        If someone later joins the literal onto one line this test fails, which
        is the signal to delete the warnings in this file's docstring. If the
        message text drifts, the AST test above fails instead. Between them the
        documentation cannot rot into a false statement.
        """
        source = (PLUGIN_ROOT / CANONICAL_REL).read_text(encoding="utf-8")
        assert MSG_CAP_WALK not in source, (
            "the walk message is now contiguous in source -- the split-literal "
            "warnings in this file's docstring are stale and should be removed"
        )
        assert "ancestry traversal" in source  # the fragment IS findable


# ===========================================================================
# Loop handling at the version floor
# ===========================================================================

_FLOOR_PROBE = '''
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, {hooks!r})
from shared.claude_md_manager import _atomic_write_text, ContainmentError

root = Path(tempfile.mkdtemp())
results = []

# target-parent chain loop
p = root / "t" ; p.mkdir()
os.symlink(str(p / "b"), str(p / "a")) ; os.symlink(str(p / "a"), str(p / "b"))
try:
    _atomic_write_text(p / "a" / "CLAUDE.md", "x\\n", p)
    results.append("target:NO-RAISE")
except ContainmentError as e:
    results.append("target:ContainmentError:" + str(e))
except BaseException as e:
    results.append("target:RAW:" + type(e).__name__)

# anchor chain loop
q = root / "q" / "sub" ; q.mkdir(parents=True)
os.symlink(str(root / "ab"), str(root / "aa")) ; os.symlink(str(root / "aa"), str(root / "ab"))
try:
    _atomic_write_text(q / "CLAUDE.md", "x\\n", root / "aa")
    results.append("anchor:NO-RAISE")
except ContainmentError as e:
    results.append("anchor:ContainmentError:" + str(e))
except BaseException as e:
    results.append("anchor:RAW:" + type(e).__name__)

print("|".join(results))
'''


class TestVersionFloorLoopHandling:
    def test_loops_refuse_identically_on_a_real_39_interpreter(self):
        """Loop handling on BOTH available interpreters.

        CI runs the interpreters declared by `python-version` in
        .github/workflows/tests.yml -- read them there rather than from a copy
        here, because no gate couples this docstring to that list: the workflow's
        own prose is checked against the matrix, this file is not. This arm
        discovers a real 3.9 and SKIPS when absent, which is what happens on
        every cell above the floor.

        ON THE FLOOR CELL IT DOES NOT SKIP, AND THAT IS THE CASE TO WATCH.
        Discovery returns a 3.9 while the running interpreter is also 3.9, so the
        two endpoints coincide: the subprocess probe below and the in-process
        assertions in the symlink-loop tests earlier in this file measure the same
        interpreter twice. Nothing asserts they differ, so the arm still passes
        while covering half of what it reads as covering. Measured on 3.9.6.

        WHAT THIS DOES AND DOES NOT ESTABLISH. It measures the two ENDPOINTS,
        3.9 and the running interpreter. 3.10-3.13 are unmeasured, and the
        claim that the verdict is version-invariant across the middle rests on
        CONSTRUCTION -- no `Path.resolve()` call exists in this function, so the
        RuntimeError class that splits 3.9-3.12 from 3.13+ is unreachable
        rather than caught. Never state this as "identical across 3.9-3.14".

        SCOPE LIMIT, because the natural summary overclaims: `file_lock` runs
        BEFORE this guard at every call site and does its own unprotected
        `resolve()`. This makes the GUARD version-invariant; it does NOT make
        the write path version-invariant end to end.
        """
        interpreter = _find_python39()
        if interpreter is None:
            pytest.skip("no real 3.9 interpreter available")

        probe = _FLOOR_PROBE.format(hooks=str(PLUGIN_ROOT / "hooks"))
        result = subprocess.run(
            [interpreter, "-c", textwrap.dedent(probe)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        rows = dict(
            (part.split(":", 1)[0], part.split(":", 1)[1])
            for part in result.stdout.strip().split("|")
        )
        assert rows["target"] == f"ContainmentError:{MSG_BOUNDARY}"
        assert rows["anchor"] == f"ContainmentError:{MSG_BOUNDARY}"
