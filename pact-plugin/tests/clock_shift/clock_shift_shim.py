"""
Location: pact-plugin/tests/clock_shift/clock_shift_shim.py
Summary: Shifts this interpreter's wall clock by PACT_TEST_CLOCK_SHIFT_SECONDS, so
         a suite run surfaces tests that assume today's date. Does nothing when
         the variable is unset.
Used by: tests/clock_shift/sitecustomize.py, which loads it at interpreter start
         in every process whose PYTHONPATH carries tests/clock_shift, and the
         dependency blocker that tests/test_cli_output_purity.py generates.

Run the sweep from pact-plugin/ with an ABSOLUTE shim directory, because a child
that changes directory resolves a relative PYTHONPATH entry against its own cwd:

    PYTHONPATH="$PWD/tests/clock_shift" PACT_TEST_CLOCK_SHIFT_SECONDS=-86400 \
        PACT_TEST_CLOCK_SHIFT_LEDGER="$TMPDIR/clock-ledger" python3 -m pytest -q

Shifted: datetime.datetime.now, utcnow and today; datetime.date.today;
time.time and time.time_ns; the no-argument forms of time.localtime,
time.gmtime, time.ctime and time.strftime. Not shifted: time.monotonic and
time.perf_counter, which measure intervals, not dates.

datetime.datetime and datetime.date are replaced by subclasses whose isinstance
and issubclass checks accept the real classes, so a value built by C code still
passes `isinstance(value, datetime.datetime)`.
"""
import os

SHIFT_ENV = "PACT_TEST_CLOCK_SHIFT_SECONDS"
LEDGER_ENV = "PACT_TEST_CLOCK_SHIFT_LEDGER"

_SHIFT = os.environ.get(SHIFT_ENV)

if _SHIFT:
    import datetime
    import sys
    import time

    _SECONDS = float(_SHIFT)
    _DELTA = datetime.timedelta(seconds=_SECONDS)
    _real_time = time.time
    _real_time_ns = time.time_ns
    _real_localtime = time.localtime
    _real_gmtime = time.gmtime
    _real_ctime = time.ctime
    _real_strftime = time.strftime

    def _time():
        return _real_time() + _SECONDS

    def _time_ns():
        return _real_time_ns() + int(_SECONDS * 1_000_000_000)

    def _localtime(secs=None):
        return _real_localtime(_time() if secs is None else secs)

    def _gmtime(secs=None):
        return _real_gmtime(_time() if secs is None else secs)

    def _ctime(secs=None):
        return _real_ctime(_time() if secs is None else secs)

    def _strftime(fmt, t=None):
        return _real_strftime(fmt, _localtime() if t is None else t)

    class _AcceptsRealInstances(type):
        def __instancecheck__(cls, obj):
            return isinstance(obj, cls._real)

        def __subclasscheck__(cls, sub):
            return issubclass(sub, cls._real)

    class ShiftedDatetime(datetime.datetime, metaclass=_AcceptsRealInstances):
        _real = datetime.datetime

        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + _DELTA

        @classmethod
        def utcnow(cls):
            return super().now(datetime.timezone.utc).replace(tzinfo=None) + _DELTA

        @classmethod
        def today(cls):
            return cls.now()

    class ShiftedDate(datetime.date, metaclass=_AcceptsRealInstances):
        _real = datetime.date

        @classmethod
        def today(cls):
            return cls.fromtimestamp(_time())

    datetime.datetime = ShiftedDatetime
    datetime.date = ShiftedDate
    time.time = _time
    time.time_ns = _time_ns
    time.localtime = _localtime
    time.gmtime = _gmtime
    time.ctime = _ctime
    time.strftime = _strftime

    # The census reads this ledger: a line from any pid but its own proves a
    # child process loaded the shim.
    _ledger = os.environ.get(LEDGER_ENV)
    if _ledger:
        _argv = getattr(sys, "orig_argv", None) or sys.argv or ["?"]
        _fd = os.open(_ledger, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(_fd, ("%d %s\n" % (os.getpid(), " ".join(_argv[:3]))).encode("utf-8", "replace"))
        finally:
            os.close(_fd)
