"""
Location: pact-plugin/tests/test_merge_guard_assignment_scan_linear.py
Summary: The assignment patterns that scan a whole command for `NAME=` try a name only
         where a word starts. Tried at every position of one long word, each attempt ran
         to the end of the word, which made the scan quadratic.
Used by: pytest. CPU time, measured with time.process_time, so other load on the
         machine does not count.
"""

import time

from shared import merge_guard_common as mgc


def _cpu_ms(fn, arg):
    t = time.process_time()
    fn(arg)
    return (time.process_time() - t) * 1000


def test_the_assignment_patterns_scan_one_long_word_in_linear_time():
    word = "m" * 64000
    for rx in (mgc._PRINTED_ASSIGNMENT_RE, mgc._QUOTED_ASSIGNMENT_RE):
        # Quadratic, this took seconds; linear, it takes about a millisecond.
        assert _cpu_ms(lambda s: list(rx.finditer(s)), word) < 100, rx.pattern


def test_a_long_message_word_is_classified_in_linear_time():
    # `git commit -mmm…x`: one word; the printed-assignment rewrite scanned it quadratically.
    assert _cpu_ms(mgc.is_dangerous_command, "git commit -" + "m" * 16000 + "x") < 500


def test_an_assignment_still_starts_after_a_non_word_character():
    assert [m.group("name") for m in mgc._PRINTED_ASSIGNMENT_RE.finditer("a=1; X=$(echo hi)")] == ["X"]
    assert [m.group() for m in mgc._QUOTED_ASSIGNMENT_RE.finditer("perl -e '$c=\"x\"'")] == ['c="']
