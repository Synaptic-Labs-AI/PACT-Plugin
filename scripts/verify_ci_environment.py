#!/usr/bin/env python3
"""Check that the running interpreter's environment is the pinned CI set.

Compares the distributions this interpreter can import (along its own
sys.path) with constraints-ci.txt at the repository root:

- every pinned name is installed at its pinned version (pip exempt);
- nothing is installed outside the pins' Requires-Dist closure, plus pip
  and RUNNER_BASELINE;
- no distribution is installed twice along the path (the first copy is the
  one Python imports, so that is the one checked);
- `python3` on PATH is this interpreter, so a child started as `python3`
  imports from the same environment.

CI runs it immediately before the test step and as the job's last step;
the test suite runs the same check inside its own process, at the start
and end of the pytest session. It is dev tooling and does not ship with
the plugin.

Not covered:
- isolated-environment tools started from Python code (the word check reads only workflow, action, `.cursor/install.sh` and tracked shell and Makefile text);
- removal by any route other than `pip uninstall`;
- integrity and hashes: the same version from a different build or index is outside the honest-mistake threat;
- `pip wheel`, `pip download` and package builds, which do not change the tested environment;
- vendored code without distribution metadata;
- transitive versions, which float by design (only pinned names are version-checked).

Usage: python scripts/verify_ci_environment.py [--report] [--constraints PATH]
Exit codes: 0 ok (always 0 under --report), 1 drift, 2 a constraints line
that is not an exact pin, or a usage error (2 even under --report).

Import-time dependencies are stdlib only; `packaging` is imported inside
functions, falling back to pip's vendored copy.

No `from __future__ import annotations`: with string annotations, the
dataclass below fails to build when a caller loads this file by path
without registering it in sys.modules, which is how the tests load it.
"""

import argparse
import dataclasses
import importlib.metadata
import os
import pathlib
import re
import shutil
import sys
from collections.abc import Mapping, Sequence

# Normalized distribution name -> reason. Distributions the CI runner image
# ships with. They are closure ROOTS, so their own dependencies are allowed
# too. Measured EMPTY on the CI runner (Python 3.12.15, ubuntu-latest): the
# image adds nothing outside the pins' closure. If a runner-image update adds
# a package, CI goes red with it under UNEXPECTED; confirm with a --report run
# that it comes from the image, not from an install, then add it here with
# the reason.
RUNNER_BASELINE: dict[str, str] = {}

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CONSTRAINTS = REPO_ROOT / "constraints-ci.txt"

_PIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9._+!-]+")
_TRAILING_COMMENT_RE = re.compile(r"\s+#.*$")
_NO_PYTHON3 = "<no python3 on PATH>"


class ConstraintsError(ValueError):
    """constraints-ci.txt has a line that is not an exact pin."""


def normalize(name: str) -> str:
    """PEP 503 name: lowercase, runs of `-`, `_` and `.` become one `-`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path: pathlib.Path) -> dict[str, str]:
    """Normalized name -> exact version, one `name==version` per line.

    Blank and full-line-comment lines are skipped and a trailing ` # comment`
    is stripped. Any other line (a wildcard, marker, extras, URL, option such
    as `-r` or `-i`) raises ConstraintsError naming its line number, and so
    does a name pinned twice.
    """
    pins: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = _TRAILING_COMMENT_RE.sub("", raw).strip()
        if not line or line.startswith("#"):
            continue
        if not _PIN_RE.fullmatch(line):
            raise ConstraintsError(
                f"{path}:{number}: not an exact `name==version` pin: {raw.strip()!r}")
        name, version = line.split("==", 1)
        key = normalize(name)
        if key in pins:
            raise ConstraintsError(f"{path}:{number}: {name!r} is pinned twice")
        pins[key] = version
    return pins


@dataclasses.dataclass(frozen=True)
class Verdict:
    wrong_version: dict[str, tuple[str, str]]   # name -> (installed effective, pinned)
    missing: tuple[str, ...]                    # pinned names not installed
    unexpected: tuple[str, ...]                 # installed, outside the closure
    shadowed: dict[str, tuple[str, ...]]        # name -> every location, sys.path order
    interpreter_mismatch: tuple[str, str] | None  # (which python3, sys.executable), unresolved

    @property
    def ok(self) -> bool:
        return not (self.wrong_version or self.missing or self.unexpected
                    or self.shadowed or self.interpreter_mismatch)

    def render(self) -> str:
        if self.ok:
            return "CI environment matches constraints-ci.txt."
        out: list[str] = []
        remedies: list[str] = []
        if self.wrong_version:
            out.append("WRONG VERSION (installed, pinned):")
            out += [f"  {n}: {got} != {want}"
                    for n, (got, want) in sorted(self.wrong_version.items())]
        if self.missing:
            out.append("MISSING (pinned, not installed):")
            out += [f"  {n}" for n in self.missing]
        if self.wrong_version or self.missing:
            remedies.append(
                "install the pinned set: python -m pip install -r constraints-ci.txt")
        if self.unexpected:
            out.append("UNEXPECTED (installed, outside the pinned set's dependency closure):")
            out += [f"  {n}" for n in self.unexpected]
            remedies.append(
                "an unpinned package: pin it in constraints-ci.txt (refresh procedure "
                "in its header) or remove the install that added it")
            remedies.append(
                "a distribution that ships with the runner image: add it to "
                "RUNNER_BASELINE in scripts/verify_ci_environment.py with a reason")
        if self.shadowed:
            out.append("SHADOWED (installed more than once; the first is imported):")
            for n, locations in sorted(self.shadowed.items()):
                out.append(f"  {n}:")
                out += [f"    {loc}" for loc in locations]
            remedies.append(
                "a shadowed or out-of-path package: remove the extra path or install")
        if self.interpreter_mismatch:
            which, exe = self.interpreter_mismatch
            out.append("INTERPRETER MISMATCH (python3 on PATH is not this interpreter):")
            out.append(f"  python3 on PATH: {which}")
            out.append(f"  sys.executable:  {exe}")
            remedies.append(
                "an interpreter mismatch: run children through sys.executable, or fix PATH")
        out.append("Remedies:")
        out += [f"  - {r}" for r in remedies]
        return "\n".join(out)


def _requirement_class():
    try:
        from packaging.requirements import Requirement
    except ImportError:
        from pip._vendor.packaging.requirements import Requirement
    return Requirement


def _same_version(installed: str, pinned: str) -> bool:
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        from pip._vendor.packaging.version import InvalidVersion, Version
    try:
        return Version(installed) == Version(pinned)
    except InvalidVersion:
        return installed == pinned


def _bin_dir(executable: str) -> str:
    """The real directory an interpreter path sits in.

    Compared instead of the interpreter's own realpath: a venv's python is a
    symlink to the base interpreter, so a realpath comparison reads a venv
    and the system python3 as the same interpreter.
    """
    return os.path.realpath(os.path.dirname(os.path.abspath(executable)))


def _effective_distributions(path: Sequence[str]):
    """(name -> first distribution along `path`, name -> every location)."""
    effective: dict[str, importlib.metadata.Distribution] = {}
    locations: dict[str, list[str]] = {}
    for entry in path:
        for dist in importlib.metadata.distributions(path=[entry]):
            raw_name = dist.metadata["Name"]
            if not raw_name:
                continue
            name = normalize(raw_name)
            where = os.path.realpath(str(getattr(dist, "_path", entry)))
            seen = locations.setdefault(name, [])
            if where not in seen:  # the same dist-info reached twice is one copy
                seen.append(where)
            effective.setdefault(name, dist)
    return effective, locations


def _closure(roots: Sequence[str], effective) -> set[str]:
    """Names reachable from `roots` through Requires-Dist, honouring the
    extras each requirement requests."""
    Requirement = _requirement_class()
    done: dict[str, frozenset[str]] = {}
    work = [(name, frozenset()) for name in roots]
    while work:
        name, extras = work.pop()
        if name in done and extras <= done[name]:
            continue
        extras = done.get(name, frozenset()) | extras
        done[name] = extras
        dist = effective.get(name)
        if dist is None:
            continue
        for spec in dist.requires or []:
            try:
                req = Requirement(spec)
            except Exception:
                continue  # unparseable: its target stays outside the closure (fails closed)
            if req.marker is not None and not any(
                    req.marker.evaluate({"extra": e}) for e in {""} | extras):
                continue
            work.append((normalize(req.name), frozenset(req.extras)))
    return set(done)


def check_environment(
    pins: dict[str, str],
    *,
    path: Sequence[str] | None = None,
    baseline: Mapping[str, str] = RUNNER_BASELINE,
    check_interpreter: bool = True,
) -> Verdict:
    effective, locations = _effective_distributions(sys.path if path is None else path)
    roots = list(pins) + ["pip"] + [normalize(n) for n in baseline]
    closure = _closure(roots, effective)
    wrong_version: dict[str, tuple[str, str]] = {}
    missing: list[str] = []
    for name, pinned in sorted(pins.items()):
        if name == "pip":
            continue
        dist = effective.get(name)
        if dist is None:
            missing.append(name)
        elif not _same_version(dist.version, pinned):
            wrong_version[name] = (dist.version, pinned)
    mismatch = None
    if check_interpreter:
        found = shutil.which("python3")
        if found is None or _bin_dir(found) != _bin_dir(sys.executable):
            mismatch = (os.path.abspath(found) if found else _NO_PYTHON3,
                        os.path.abspath(sys.executable))
    return Verdict(
        wrong_version=wrong_version,
        missing=tuple(missing),
        unexpected=tuple(sorted(set(effective) - closure)),
        shadowed={n: tuple(locs) for n, locs in sorted(locations.items()) if len(locs) > 1},
        interpreter_mismatch=mismatch,
    )


def measurement(
    pins: dict[str, str],
    *,
    path: Sequence[str] | None = None,
    baseline: Mapping[str, str] = RUNNER_BASELINE,
) -> str:
    """The one-line measurement the CLI prints first: the interpreter, the
    python3 on PATH, and how many distributions are effective and allowed.
    The test session prints the same string, so the two lines always match."""
    effective, _ = _effective_distributions(sys.path if path is None else path)
    closure = _closure(list(pins) + ["pip"] + [normalize(n) for n in baseline], effective)
    return (f"sys.executable={sys.executable} which python3={shutil.which('python3')} "
            f"effective distributions={len(effective)} closure={len(closure)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check this interpreter's environment against constraints-ci.txt.")
    parser.add_argument("--report", action="store_true",
                        help="print the verdict and every distribution, but exit 0")
    parser.add_argument("--constraints", type=pathlib.Path, default=DEFAULT_CONSTRAINTS,
                        help="constraints file (default: constraints-ci.txt at the repo root)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on a usage error, 0 on --help
        return 2 if exc.code else 0
    try:
        pins = read_pins(args.constraints)
    except (ConstraintsError, OSError) as exc:
        print(f"CONSTRAINTS ERROR: {exc}", file=sys.stderr)
        return 2
    effective, _ = _effective_distributions(sys.path)
    print(measurement(pins) + (" (report only)" if args.report else ""))
    verdict = check_environment(pins)
    if args.report:
        for name in sorted(effective):
            print(f"{name}=={effective[name].version}")
    print(verdict.render())
    if verdict.ok or args.report:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
