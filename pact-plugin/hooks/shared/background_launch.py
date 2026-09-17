"""
Location: pact-plugin/hooks/shared/background_launch.py
Summary: The one predicate for "this Bash frame launches background work":
         the harness `run_in_background` flag, OR a command ending in a bare
         `&`.
Used by: shared/background_work.py (imports it normally; the recorder) and
         wait_filler_gate.py (loads this FILE by path; the launch advisory).

IMPORTS NOTHING, AND MUST KEEP IMPORTING NOTHING OUTSIDE THE STANDARD LIBRARY.
wait_filler_gate runs before every Bash call in every consumer session and
loads this file by path, outside the `shared` package, so that it never pays
for the package's `__init__`. A relative import here fails in that load, and
an import of any `shared` module brings back the cost the path load avoids.
Both consumers fail open on a load failure, so a broken import here silences
the advisory rather than raising.
"""

from __future__ import annotations


def _truthy_background(value: object) -> bool:
    return value is True or value == "true" or value == 1


def command_from_frame(input_data: object) -> str:
    tool_input = input_data.get("tool_input") if isinstance(input_data, dict) else None
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def is_harness_background_bash(input_data: object) -> bool:
    """True iff this frame is a Bash launch with run_in_background set."""
    if not isinstance(input_data, dict):
        return False
    if input_data.get("tool_name") != "Bash":
        return False
    tool_input = input_data.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    return _truthy_background(tool_input.get("run_in_background"))


def is_shell_backgrounded_bash(input_data: object) -> bool:
    """True iff this Bash frame's command ends in a bare `&`.

    Complements `is_harness_background_bash`, which reads only the
    `run_in_background` field. Work backgrounded by the SHELL inside a
    foreground Bash call sets no such field, so without this it is invisible
    to every layer.

    DELIBERATELY UNDER-INCLUSIVE, and this is the entire population it adds:
    a command ENDING in a bare `&`, nothing more. Measured misses, each of
    which backgrounds work this predicate still will not see:

        nohup ./gate.sh & echo started
        ( ./gate.sh & )
        ./gate.sh & sleep 1
        setsid ./gate.sh & disown

    So the recorded population is "the flag, OR a command ending in a bare
    `&`". It is NOT "shell-shaped work", and nothing downstream may describe
    it as though it were.

    Excluded because they are not backgrounding, all measured false: `a && b`,
    `2>&1`, a quoted `&`, a heredoc body, `cmd & wait`, `echo done \\&`.

    DO NOT EXTEND THIS INTO INFERENCE ABOUT WHAT THE COMMAND WILL DO. Matching
    command text is admissible here for two reasons, and the second is the one
    that holds. First, `&` is shell GRAMMAR with one meaning, which the string
    answers; "will this run a long time" is a fact about program BEHAVIOUR,
    which it does not. Second and decisive: this predicate only ever ADDS to
    the recorded population, so an over-fire costs one extra row that someone
    can see and discharge. A predicate that SUBTRACTS pays for an over-fire in
    silence, which is the miss the whole mechanism exists to prevent. So the
    presence of text matching here licenses nothing: a new check may widen
    what is recorded and may never narrow it.
    """
    if not isinstance(input_data, dict):
        return False
    if input_data.get("tool_name") != "Bash":
        return False
    command = command_from_frame(input_data).rstrip()
    if not command.endswith("&"):
        return False
    # `&&` is a conjunction and `\&` is a literal ampersand; neither
    # backgrounds.
    return not command.endswith("&&") and not command.endswith("\\&")


def is_background_launch(input_data: object) -> bool:
    """True iff this Bash frame launches background work: the flag, or a
    command ending in a bare `&`. Reads the frame only; says nothing about
    who launched it."""
    return is_harness_background_bash(input_data) or is_shell_backgrounded_bash(
        input_data
    )
