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
time.gmtime, time.ctime and time.strftime; and file timestamps. os.stat,
os.lstat, os.fstat and os.scandir's DirEntry.stat return atime, mtime, ctime
(and birthtime) shifted, and os.utime subtracts the shift from explicit times,
so a file written now reads as shifted-now and an explicit utime reads back
what it set. Not shifted: time.monotonic and time.perf_counter, which measure
intervals, not dates. The posix module is left alone: importlib holds
posix.stat, so bytecode validation stays on the real clock. A zero or unset
shift installs nothing.

datetime.datetime and datetime.date are replaced by subclasses whose isinstance
and issubclass checks accept the real classes, so a value built by C code still
passes `isinstance(value, datetime.datetime)`.
"""
import os

SHIFT_ENV = "PACT_TEST_CLOCK_SHIFT_SECONDS"
LEDGER_ENV = "PACT_TEST_CLOCK_SHIFT_LEDGER"

_SHIFT = os.environ.get(SHIFT_ENV)
_SECONDS = float(_SHIFT) if _SHIFT else 0.0

if _SECONDS:
    import datetime
    import sys
    import time

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

    # File timestamps move with the clock. stat_result is rebuilt through its
    # pickle form, which carries every platform field by name.
    _NS = int(_SECONDS * 1_000_000_000)
    _TIME_FIELDS = ("st_atime", "st_mtime", "st_ctime", "st_birthtime")
    _real_stat, _real_lstat, _real_fstat = os.stat, os.lstat, os.fstat
    _real_scandir, _real_utime = os.scandir, os.utime

    def _shift_stat(st):
        cls, (seq, extras) = st.__reduce__()
        seq = list(seq)
        for index in (7, 8, 9):  # the integer atime, mtime and ctime
            seq[index] += int(_SECONDS)
        for field in _TIME_FIELDS:
            if field in extras:
                extras[field] += _SECONDS
            if field + "_ns" in extras:
                extras[field + "_ns"] += _NS
        return cls(seq, extras)

    def _stat(*args, **kwargs):
        return _shift_stat(_real_stat(*args, **kwargs))

    def _lstat(*args, **kwargs):
        return _shift_stat(_real_lstat(*args, **kwargs))

    def _fstat(*args, **kwargs):
        return _shift_stat(_real_fstat(*args, **kwargs))

    class _ShiftedDirEntry:
        """A DirEntry whose stat() is shifted. Not an os.DirEntry instance, so
        callers that branch on isinstance fall back to os.stat, which is shifted too."""

        __slots__ = ("_entry",)

        def __init__(self, entry):
            self._entry = entry

        def __getattr__(self, name):
            return getattr(self._entry, name)

        def __fspath__(self):
            return self._entry.__fspath__()

        def __repr__(self):
            return repr(self._entry)

        def stat(self, *, follow_symlinks=True):
            return _shift_stat(self._entry.stat(follow_symlinks=follow_symlinks))

    class _ShiftedScandir:
        def __init__(self, iterator):
            self._iterator = iterator

        def __iter__(self):
            return self

        def __next__(self):
            return _ShiftedDirEntry(next(self._iterator))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._iterator.close()

        def close(self):
            self._iterator.close()

    def _scandir(*args, **kwargs):
        return _ShiftedScandir(_real_scandir(*args, **kwargs))

    def _utime(path, times=None, *, ns=None, **kwargs):
        # times=None means the real now, which already reads back shifted.
        if times is not None:
            times = (times[0] - _SECONDS, times[1] - _SECONDS)
        if ns is not None:
            kwargs["ns"] = (ns[0] - _NS, ns[1] - _NS)
        return _real_utime(path, times, **kwargs)

    os.stat = _stat
    os.lstat = _lstat
    os.fstat = _fstat
    os.scandir = _scandir
    os.utime = _utime
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
