"""Site 3's docs/ probe: the states an empty listing can mean.

`git worktree remove` deletes `docs/` irrecoverably, and `docs/` is gitignored,
so an instrument that returns empty over a full directory authorises the
removal of artifacts nobody harvested. `skills/worktree-cleanup/SKILL.md`
Step 1.5 answers that with TWO commands — a presence marker and an ignore-blind
listing — because neither alone separates the states.

THESE ARMS EXECUTE THE SHIPPED COMMANDS. Both are extracted from the fence and
run, unmodified apart from the path substitution the step itself specifies,
against directory states built on disk. That is what makes them survive a
reword: a phrase pin over the same block disarms silently when the prose is
correctly rewritten, and this arc rewrote that prose three times.

THE OBSERVATION TRIPLE IS THE GUARD, not a separate verdict assertion. The step
routes `DIR_ABSENT` straight to removal, so asserting that a state which may
hold unreadable artifacts reports `CANNOT_OBSERVE` rather than `DIR_ABSENT` IS
the assertion that it does not reach the destructive branch.

BINARY RESOLUTION, stated because it bounds what these arms prove: the commands
run under `/bin/bash` with `PATH` pinned to `/usr/bin:/bin`, so `find` is the
system `find` and not whatever an operator has shimmed onto their own `PATH`.
This skill ships to consumers, so the system binary is the honest subject. A
consumer whose `find` is a different implementation is outside what is measured
here.

EUID: the mode-000 arms depend on the process not being root. As root the
permission bits do not bite and those arms fail LOUDLY on the marker value
rather than passing silently, so no skip is registered for them — read this
line before deleting an arm that reddens on a root CI runner.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest
from clock_shift.clock_shift_env import carry_clock_shift

SKILL_FILE = (
    Path(__file__).resolve().parents[1] / "skills" / "worktree-cleanup" / "SKILL.md"
)
BASH = Path("/bin/bash")
SAFE_PATH = "/usr/bin:/bin"

_FENCE = re.compile(r"```bash\n(.*?)```", re.DOTALL)

pytestmark = pytest.mark.skipif(not BASH.exists(), reason="no /bin/bash")


def step_1_5(skill_text):
    start = skill_text.find("### Step 1.5:")
    assert start != -1, "no Step 1.5 in worktree-cleanup SKILL.md"
    block = skill_text[start:]
    end = block.find("### Step 2:")
    assert end != -1, "Step 1.5 does not run into Step 2 — the file changed shape"
    return block[:end]


def probe_commands(skill_text):
    """The marker command and the listing command, in the order the step gives.

    Exactly one fence must sit in Step 1.5. A second would make `search` take
    whichever came first and the arms would measure a command nobody meant.
    """
    block = step_1_5(skill_text)
    fences = _FENCE.findall(block)
    assert len(fences) == 1, (
        f"expected exactly one bash fence in a {len(block)}-char Step 1.5; "
        f"found {len(fences)}"
    )
    lines = [ln for ln in fences[0].strip().splitlines() if ln.strip()]
    assert len(lines) == 2, f"expected a marker line and a listing line; got {lines}"
    marker, listing = lines
    assert "-d " in marker, f"the presence marker is gone: {marker!r}"
    assert marker.count("echo") == 3, (
        f"the marker no longer reports three states — an unobservable parent "
        f"and an absent directory may have collapsed onto one answer: {marker!r}"
    )
    assert listing.startswith("find "), f"the listing is not `find`: {listing!r}"
    assert " -L" in listing, (
        f"`find` no longer follows symlinks, so a symlinked docs/ lists nothing "
        f"and reads as empty: {listing!r}"
    )
    return marker, listing


@pytest.fixture
def probe():
    marker, listing = probe_commands(SKILL_FILE.read_text())

    def run(worktree):
        def shell(command):
            done = subprocess.run(
                [str(BASH), "-c", command.replace("{abs_worktree}", str(worktree))],
                capture_output=True, text=True, env=carry_clock_shift({"PATH": SAFE_PATH}),
                # Deliberately NOT the worktree: the step specifies an absolute
                # path precisely so a wrong CWD cannot produce a false empty,
                # and running from elsewhere is how that gets measured.
                cwd="/",
            )
            return done.returncode, done.stdout.strip()

        _, mark = shell(marker)
        code, out = shell(listing)
        return mark, code, len([ln for ln in out.splitlines() if ln.strip()])

    return run


@pytest.fixture
def worktree(tmp_path):
    """A worktree whose `docs/` each arm shapes for itself."""
    root = tmp_path / "wt"
    root.mkdir()
    try:
        yield root
    finally:
        # The permission arms would otherwise defeat tmp_path cleanup. Root
        # FIRST: a child of a non-traversable parent cannot be chmod'd.
        for path in (root, root / "docs", root / "docs" / "architecture"):
            if path.exists():
                os.chmod(path, 0o755)


def populate(docs, count=3):
    docs.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (docs / f"artifact-{i}.md").write_text("phase artifact\n")


def _absent(worktree):
    pass  # `absent` is constructed precisely by not creating the path.


def _empty(worktree):
    (worktree / "docs").mkdir()


def _populated(worktree):
    populate(worktree / "docs")


def _symlink_inward(worktree):
    target = worktree / "inner"
    populate(target, count=2)
    (worktree / "docs").symlink_to(target, target_is_directory=True)


def _unreadable_docs(worktree):
    populate(worktree / "docs")
    os.chmod(worktree / "docs", 0o000)


def _nested_unreadable(worktree):
    """A readable artifact beside an unreadable subtree: `find` lists AND fails."""
    populate(worktree / "docs", count=1)
    (worktree / "docs" / "architecture").mkdir()
    (worktree / "docs" / "architecture" / "design.md").write_text("unharvestable\n")
    os.chmod(worktree / "docs" / "architecture", 0o000)


def _parent_000(worktree):
    populate(worktree / "docs")
    os.chmod(worktree, 0o000)


def _parent_400(worktree):
    populate(worktree / "docs")
    os.chmod(worktree, 0o400)


def _parent_100(worktree):
    populate(worktree / "docs", count=1)
    os.chmod(worktree, 0o100)


# Every state an empty listing can mean, with the triple the probe must report.
# The listing is always run from `/`, never from the worktree, so each
# populated row doubles as the wrong-CWD arm: a path that had become relative
# would list nothing here.
STATES = [
    ("absent", _absent, ("DIR_ABSENT", 1, 0)),
    ("empty", _empty, ("DIR_PRESENT", 0, 0)),
    # `docs/` is gitignored in the real tree, so an ignore-aware instrument
    # returns empty over exactly this state. The listing must find all three.
    ("populated-gitignored", _populated, ("DIR_PRESENT", 0, 3)),
    # A `find` without `-L` reports zero files here — indistinguishable from
    # empty, and it proceeds to removal.
    ("symlink-inward", _symlink_inward, ("DIR_PRESENT", 0, 2)),
    # Marker says present, listing fails. Exit status ALONE cannot separate
    # this from `absent` — both are 1 — which is why the marker exists.
    ("unreadable-docs", _unreadable_docs, ("DIR_PRESENT", 1, 0)),
    # The only state where `find` LISTS files and still exits non-zero. The
    # listed file is harvestable and the unreadable subtree is not, so harvest
    # and removal-licence must diverge here -- which is why they are two
    # decisions rather than one. No other state separates them.
    ("nested-unreadable", _nested_unreadable, ("DIR_PRESENT", 1, 1)),
    ("parent-000", _parent_000, ("CANNOT_OBSERVE", 1, 0)),
    # DO NOT DROP AS REDUNDANT. Measured: `parent-000` passes under a marker
    # whose second predicate is `-r` too, because at mode 000 BOTH `-r` and
    # `-x` read false — so it pins only that SOME third state exists.
    # `parent-400` is the one that pins WHICH predicate: it is the sole state
    # where the two disagree. `-r` reads TRUE there and would license proceed
    # over a populated `docs/` the instrument cannot enter; `-x` reads false
    # and routes to the warning. It looks exactly like the sibling above it,
    # which is what makes it the deletion risk.
    ("parent-400", _parent_400, ("CANNOT_OBSERVE", 1, 0)),
    # The counterpart that bounds the claim: execute-without-read is
    # traversable, so the marker's FIRST predicate resolves it and the state
    # is genuinely observable. The refusal is confined to what cannot be
    # entered rather than to what cannot be read.
    ("parent-100", _parent_100, ("DIR_PRESENT", 0, 1)),
]


class TestDocsProbeSeparatesTheStates:
    """Every state an empty listing can mean, and what the probe reports."""

    @pytest.mark.parametrize(
        "setup,expected", [(s, e) for _, s, e in STATES], ids=[i for i, _, _ in STATES]
    )
    def test_probe_reports_the_expected_triple(self, probe, worktree, setup, expected):
        setup(worktree)
        assert probe(worktree) == expected


class TestRemovalLicenceAdmitsOnlyTheSafeStates:
    """The routing, which the observation arms above cannot see.

    THE TABLE IS THE PIN, because the table is the enumeration: one row per
    observable state, with harvest and removal decided separately. This asserts
    what each row ADMITS -- derived from the observation itself -- rather than
    which words the row contains. A clause that gains a permission is the way a
    routing rule fails, and a token-presence check only ever catches a clause
    that LOSES a token.

    REPLACES an arm that pinned the literal `**Any other result**`. That arm
    went red when the prose was restructured even though the property it
    guarded still held -- a pin on wording rather than on meaning. It is not
    repaired, it is retired: the catch-all it named no longer exists, because
    the restructure made harvest and removal two decisions and there is nothing
    left for a reader to fall through.
    """

    # Removal is safe exactly when the probe was CONCLUSIVE about the
    # directory's contents. Two ways to be conclusive, and only two:
    #   DIR_ABSENT  -- there is no docs/ at all, so nothing can be unread.
    #                  `find` exits non-zero here and that is not a failure,
    #                  it is the absence being reported.
    #   DIR_PRESENT with `find` exiting zero -- the walk completed, so the
    #                  listing is the whole contents.
    # Everything else may hide an artifact the instrument could not read, and
    # removal is irrecoverable.
    @staticmethod
    def _licensed(marker, exit_code):
        if marker == "DIR_ABSENT":
            return True
        return marker == "DIR_PRESENT" and exit_code == 0

    def _rows(self):
        """The Step 1.5 table as {(marker, exit-word, files-word): removal cell}."""
        block = step_1_5(SKILL_FILE.read_text())
        rows = {}
        for line in block.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 5 or not cells[0].startswith("`DIR") and not cells[0].startswith("`CANNOT"):
                continue
            rows[(cells[0].strip("`"), cells[1], cells[2])] = cells[4]
        assert rows, "no table rows parsed out of Step 1.5 -- the parser is blind"
        return rows

    def test_every_observed_state_has_a_row_admitting_the_right_outcome(self, probe, worktree, request):
        """Every state the probe can produce is routed, and routed correctly."""
        rows = self._rows()
        for name, setup, (marker, exit_code, count) in STATES:
            key = (marker, "zero" if exit_code == 0 else "non-zero",
                   "none" if count == 0 else "some")
            assert key in rows, f"{name}: observation {key} has no row in the table"
            cell = rows[key]
            licensed = "refused" not in cell
            assert licensed == self._licensed(marker, exit_code), (
                f"{name}: observation {key} is routed to {cell!r}, which "
                f"{'licenses' if licensed else 'refuses'} removal; a state that "
                f"may hold unread artifacts must refuse"
            )

    def test_the_prose_licence_names_exactly_what_the_table_licenses(self):
        """The table and the prose both state the rule; they must not drift.

        Bullet 2 restates the licensing condition in prose, so pinning only the
        table would leave the prose free to widen with nothing red -- and an
        agent reads the prose. This is the drift check, not a second pin: the
        table stays the source and this asserts the sentence agrees with it.
        """
        block = step_1_5(SKILL_FILE.read_text())
        bullet = next((ln for ln in block.splitlines()
                       if "Removal is licensed only if" in ln), "")
        assert bullet, "no removal-licence bullet in Step 1.5"

        licensed_markers = {m for (m, _, _), cell in self._rows().items()
                            if "refused" not in cell}
        for marker in ("DIR_ABSENT", "DIR_PRESENT", "CANNOT_OBSERVE"):
            in_prose = marker in bullet
            assert in_prose == (marker in licensed_markers), (
                f"the prose licence and the table disagree about {marker}: "
                f"prose {'names' if in_prose else 'omits'} it, table "
                f"{'licenses' if marker in licensed_markers else 'refuses'} it"
            )
        assert "exiting zero" in bullet, (
            f"the prose licence no longer qualifies DIR_PRESENT by the find "
            f"exit status, so it now admits the non-zero rows the table "
            f"refuses: {bullet!r}"
        )
