"""Pin that ``embeddings.py`` emits nothing at WARNING or above.

THE PROPERTY. The CLI puts its JSON error envelope on stderr, so any free-text
line on that stream corrupts a caller's parse. The package configures NO logging
handler anywhere, so ``logging.lastResort`` handles every record -- and
lastResort is a stderr handler at WARNING. A ``logger.warning`` in a module the
CLI imports therefore reaches stderr with no further wiring. ``debug`` and
``info`` sit below that threshold and reach nothing.

WHY A STATIC GUARD WHEN A BEHAVIOURAL ONE ALREADY EXISTS, which is the first
objection a reader should raise. ``test_cli_output_purity.py`` drives the real
CLI as a subprocess and asserts stderr is pure JSON, including an arm that
asserts stderr is EMPTY on a successful run. That is a stronger kind of test and
it is not redundant with this one -- MEASURED: adding ``logger.warning`` to
``embeddings.py``'s model-load path leaves that file at 7 passed, unchanged.
Its arms do not reach the model load, so the strongest guard in the tree is
blind to the module this one covers. A behavioural arm that drove the load
would be better than this file and would make it deletable; until one exists,
this is what stands between the fix and a silent regression.

WHAT THIS DOES NOT PIN, stated because the gap is wide and easy to miss. This
covers ONE module. Seven modules in this package contain WARNING-or-above calls
-- database (6), memory_api (5), working_memory (4), models (2), memory_init
(2), setup_memory (1), search (1) -- and every one of them reaches stderr by the
SAME lastResort path, because there is no handler anywhere to distinguish them.
Those 21 sites are not swept in here: they are longstanding, several are
deliberate operator-facing signals, and a guard that reddened on correct code
would be disabled within a week and would then protect nothing. MEASURED, so
that the exclusion is not mistaken for those modules being safe: a real CLI save
whose filed project differs from the working directory's repository emits
``memory_api``'s divergence warning as free text on stderr. The stderr-purity
property is therefore NOT established package-wide by this file, and nobody
should read a green here as meaning it is.

AST RATHER THAN GREP, deliberately. The count that motivated this guard was
taken with ``grep -cE 'logger\\.(warning|error|critical|exception)'``, which
also matches the phrase inside a COMMENT -- and this module's comments discuss
lastResort and log levels at length, so a future comment could turn the guard
green or red for no behavioural reason. Parsing calls avoids that. The tradeoff
is that an aliased logger (``log = logger``) or a ``getattr`` call escapes this;
both would be unusual here and neither is worth the complexity today.
"""

import ast
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "pact-memory" / "scripts"

#: Levels that ``logging.lastResort`` will put on stderr.
STDERR_REACHING_LEVELS = frozenset({"warning", "error", "critical", "exception"})

#: The module this guard covers.
GUARDED = "embeddings.py"

#: A module known to call at these levels, used as the guard's positive control.
#: Not itself guarded -- see the module docstring.
CONTROL = "memory_api.py"


def _stderr_reaching_calls(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, level) for every ``logger.<level>(...)`` at WARNING+."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in STDERR_REACHING_LEVELS:
            continue
        # `logger.warning(...)` -- the module-level logger, not an arbitrary
        # object that happens to have a `.warning` attribute.
        if isinstance(func.value, ast.Name) and func.value.id == "logger":
            found.append((node.lineno, func.attr))
    return sorted(found)


def test_the_detector_can_find_calls_at_all():
    """POSITIVE CONTROL, and it is the whole test.

    A search that returns zero is worth nothing until it has been shown capable
    of returning non-zero. Without this, the guard below passes identically if
    the AST walk is broken, the path is wrong, or the level set is misspelled --
    and it would pass LOUDEST at the moment it stopped working.

    Asserted here rather than checked once by hand, because a control that is
    not re-run is a control that stops holding the moment anything moves.
    """
    control = SCRIPTS / CONTROL
    assert control.exists(), f"control module missing: {control}"
    hits = _stderr_reaching_calls(control)
    assert hits, (
        f"the detector found NO WARNING-or-above calls in {CONTROL}, which is "
        "known to contain several. The detector is broken, not the subject -- "
        "treat any green from the guard below as meaningless until this passes."
    )


def test_embeddings_emits_nothing_that_reaches_stderr():
    """The guard: no WARNING-or-above call in the module.

    Reads as: this module must not be able to write free text to the stream the
    CLI's JSON envelope uses. `debug` and `info` are unrestricted -- they sit
    below lastResort's threshold and reach nothing.
    """
    target = SCRIPTS / GUARDED
    assert target.exists(), f"guarded module missing: {target}"
    hits = _stderr_reaching_calls(target)
    assert hits == [], (
        f"{GUARDED} has {len(hits)} logger call(s) at WARNING or above: "
        + ", ".join(f"line {ln} ({lvl})" for ln, lvl in hits)
        + ". The package configures no logging handler, so logging.lastResort "
        "puts these on stderr, where the CLI writes its JSON error envelope -- "
        "a caller doing json.loads(result.stderr) then fails on a line it did "
        "not expect. Use logger.debug or logger.info, which are below "
        "lastResort's WARNING threshold and reach nothing."
    )


@pytest.mark.parametrize("level", sorted(STDERR_REACHING_LEVELS))
def test_every_guarded_level_is_one_lastresort_actually_emits(level):
    """The level set must match logging's threshold, not a guess at it.

    If someone adds a level here that lastResort does not emit, the guard starts
    forbidding something harmless; if a real one is dropped, it stops catching a
    real regression. Both are silent, so the set is checked against the logging
    module rather than against this file's own opinion.
    """
    import logging

    numeric = logging.getLevelName(level.upper())
    if level == "exception":  # logger.exception() logs at ERROR
        numeric = logging.ERROR
    assert isinstance(numeric, int), f"{level} is not a real logging level"
    assert numeric >= logging.lastResort.level, (
        f"{level} ({numeric}) is below lastResort's threshold "
        f"({logging.lastResort.level}), so it cannot reach stderr and does not "
        "belong in this guard"
    )
