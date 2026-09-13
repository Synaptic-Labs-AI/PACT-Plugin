"""
Location: pact-plugin/tests/test_merge_guard_vendored_baselines.py
Summary: pins the vendored merge_guard_common.py bases that the baked-SHA
         certification files load through `load_vendored`.
Used by: the suite. Path setup is conftest-owned; see tests/test_path_setup_pin.py.

A certification file certifies nothing unless three things hold:
  1. each vendored fixture still has the git blob id it was pinned with, so the
     base under test is the commit the cert names;
  2. every commit a cert file loads is in the vendored table, so no cert reaches
     for a base that is not stored;
  3. no cert file reads git history or carries a skip marker, so a missing base
     is a failure and never a silent pass.
"""

import ast
from pathlib import Path

import pytest

import merge_guard_baseline_loader as loader

TESTS_DIR = Path(__file__).resolve().parent

# Certification files that load their bases through load_vendored. A file joins
# this list in the same commit that removes its skips and its git calls.
_CERT_FILES = [
    "test_merge_guard_1118_recert.py",
    "test_merge_guard_1129_r2_cert.py",
    "test_merge_guard_1129_r3_cert.py",
]


def _cert_tree(name):
    return ast.parse((TESTS_DIR / name).read_text(encoding="utf-8"))


def _loaded_shas(tree):
    """Every commit a module passes to load_vendored, resolving module-level
    string constants. An argument it cannot resolve is reported, not dropped."""
    constants = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    shas = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "load_vendored":
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            shas.add(arg.value)
        elif isinstance(arg, ast.Name) and arg.id in constants:
            shas.add(constants[arg.id])
        else:
            shas.add("<unresolved: %s>" % ast.unparse(arg))
    return shas


def _history_and_skip_sites(tree):
    """Line-tagged skip markers, pytest.skip calls, and subprocess calls whose
    argument names git."""
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "skipif":
            sites.append("L%d skipif" % node.lineno)
        if isinstance(node, ast.Call):
            func = ast.unparse(node.func)
            if func in ("pytest.skip", "skip"):
                sites.append("L%d %s()" % (node.lineno, func))
            if "subprocess" in func or func in ("check_output", "run", "Popen"):
                argv = ast.unparse(node.args[0]) if node.args else ""
                if "'git'" in argv or '"git"' in argv:
                    sites.append("L%d %s(%s)" % (node.lineno, func, argv[:60]))
    return sites


@pytest.mark.parametrize("sha8", sorted(loader._VENDORED))
def test_each_vendored_file_matches_its_blob_id(sha8):
    name, blob_id = loader._VENDORED[sha8]
    path = loader._VENDORED_DIR / name
    assert path.is_file(), "vendored fixture %s is missing" % path
    got = loader._git_blob_id(path.read_bytes())
    assert got == blob_id, (
        "%s has git blob id %s, pinned %s: its bytes are no longer the commit "
        "%s's merge_guard_common.py" % (name, got, blob_id, sha8)
    )


@pytest.mark.parametrize("cert", _CERT_FILES)
def test_every_sha_a_cert_file_loads_is_vendored(cert):
    shas = _loaded_shas(_cert_tree(cert))
    assert shas, "%s loads no base through load_vendored" % cert
    missing = sorted(s for s in shas if s not in loader._VENDORED)
    assert not missing, (
        "%s loads bases with no vendored fixture: %s. Vendor each one, or the "
        "cert fails at import" % (cert, missing)
    )


@pytest.mark.parametrize("cert", _CERT_FILES)
def test_no_cert_file_reads_git_history_or_skips(cert):
    sites = _history_and_skip_sites(_cert_tree(cert))
    assert not sites, (
        "%s still reads git history or skips: %s. A certification file must load "
        "its bases through load_vendored and fail, never skip, when one is "
        "missing" % (cert, sites)
    )


def _copy_fixture(tmp_path, key, change_one_byte):
    source = loader._VENDORED_DIR / "merge_guard_common_b4041ccf.py"
    data = source.read_bytes()
    pinned = loader._git_blob_id(data)
    if change_one_byte:
        data = data[:-1] + (b"#" if data[-1:] != b"#" else b" ")
    copy = tmp_path / ("merge_guard_common_%s.py" % key)
    copy.write_bytes(data)
    return copy, pinned


def test_the_loader_fails_loudly_on_a_drifted_fixture(tmp_path, monkeypatch):
    copy, pinned = _copy_fixture(tmp_path, "drift000", change_one_byte=True)
    monkeypatch.setattr(loader, "_VENDORED_DIR", tmp_path)
    monkeypatch.setitem(loader._VENDORED, "drift000", (copy.name, pinned))
    with pytest.raises(pytest.fail.Exception, match="git blob id mismatch"):
        loader.load_vendored("drift000")


def test_the_loader_loads_an_intact_copy(tmp_path, monkeypatch):
    """The control for the arm above: the same setup with the bytes unchanged
    loads, so that arm fails on the drift and not on the setup."""
    copy, pinned = _copy_fixture(tmp_path, "intact00", change_one_byte=False)
    monkeypatch.setattr(loader, "_VENDORED_DIR", tmp_path)
    monkeypatch.setitem(loader._VENDORED, "intact00", (copy.name, pinned))
    module = loader.load_vendored("intact00")
    assert callable(module.is_dangerous_command)
