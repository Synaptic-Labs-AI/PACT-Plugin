"""
Location: pact-plugin/tests/test_python_source_has_no_raw_bom.py
Summary: No Python file under pact-plugin/ holds a raw U+FEFF (byte order
         mark) character, an encoding signature at byte 0 included.
Used by: pytest.

The character is invisible, so an editor, a formatter or a tool round trip can
drop it from a string literal without anyone seeing the change: a constant
meant to hold the mark becomes "", and `startswith` on it is always true.
Write it as the escape `\\ufeff` instead. Fixture data files (.md, .json)
are not Python source and may hold the mark as test input.

The scan must reach hooks/shared/claude_md_markers.py, which holds the
parser's byte order mark constant, and every top-level directory that holds
Python source, so a scan rooted in the wrong directory, one that skips a
directory, or one that finds no files at all, fails instead of passing.
"""

from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
RAW_BOM = b"\xef\xbb\xbf"
MUST_SCAN = PLUGIN / "hooks" / "shared" / "claude_md_markers.py"
SOURCE_DIRS = {"hooks", "scripts", "skills", "telegram", "tests"}


def python_files(root):
    """Each .py file under `root`, sorted. __pycache__ is skipped."""
    return [path for path in sorted(root.rglob("*.py")) if "__pycache__" not in path.parts]


def raw_bom_lines(root):
    """(path relative to `root`, line number) for each line of a .py file
    under `root` that holds a raw U+FEFF."""
    out = []
    for path in python_files(root):
        for number, line in enumerate(path.read_bytes().split(b"\n"), 1):
            if RAW_BOM in line:
                out.append((path.relative_to(root).as_posix(), number))
    return out


def test_no_python_file_under_the_plugin_holds_a_raw_bom():
    files = python_files(PLUGIN)
    assert MUST_SCAN in files, f"the scan of {PLUGIN} did not reach {MUST_SCAN}"
    missed = SOURCE_DIRS - {path.relative_to(PLUGIN).parts[0] for path in files}
    assert not missed, f"the scan of {PLUGIN} found no .py file in {sorted(missed)}"
    lines = raw_bom_lines(PLUGIN)
    assert not lines, (
        "Write each U+FEFF in Python source as the escape \\ufeff:\n"
        + "\n".join(f"{path}:{number}" for path, number in lines))


def test_a_raw_bom_is_named_by_file_and_line(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "escaped.py").write_text('BOM = "\\ufeff"\n', encoding="utf-8")
    (package / "planted.py").write_bytes(b'x = 1\nBOM = "' + RAW_BOM + b'"\n')
    (package / "signed.py").write_bytes(RAW_BOM + b"x = 1\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_bytes(RAW_BOM)
    assert raw_bom_lines(tmp_path) == [("pkg/planted.py", 2), ("pkg/signed.py", 1)]
