"""
Location: pact-plugin/tests/test_python_source_has_no_raw_invisible_characters.py
Summary: No Python file under pact-plugin/ holds a raw invisible or
         layout-changing character: a format character (Cf: the byte order
         mark, zero-width characters, bidi controls), a line or paragraph
         separator (Zl, Zp), a control character other than tab, newline and
         carriage return (Cc), a space other than U+0020 (Zs), or a byte that
         is not UTF-8 (read as a lone surrogate, Cs).
Used by: pytest.

Each of these is invisible or looks like a plain space, so an editor, a
formatter or a tool round trip can drop or replace one in a string literal
without anyone seeing the change: a constant meant to hold the byte order mark
becomes "", and `startswith` on it is always true. Write each as an escape
(`\\ufeff`, `\\u2028`, `\\x85`) instead. Fixture data files (.md, .json) are
not Python source and may hold these characters as test input.

The scan must reach hooks/shared/claude_md_markers.py, which holds the
parser's byte order mark constant, and every top-level directory that holds
Python source, so a scan rooted in the wrong directory, one that skips a
directory, or one that finds no files at all, fails instead of passing.
"""

import unicodedata
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
MUST_SCAN = PLUGIN / "hooks" / "shared" / "claude_md_markers.py"
SOURCE_DIRS = {"hooks", "scripts", "skills", "telegram", "tests"}
INVISIBLE_CATEGORIES = {"Cc", "Cf", "Cs", "Zl", "Zp", "Zs"}
ALLOWED = {"\t", "\n", "\r", " "}
# Format characters added after Unicode 13.0, the database Python 3.9 reads,
# named so every interpreter CI runs refuses the same characters.
NEWER_FORMAT = {0x0890, 0x0891, *range(0x13439, 0x13440)}


def is_invisible(char):
    return char not in ALLOWED and (
        unicodedata.category(char) in INVISIBLE_CATEGORIES or ord(char) in NEWER_FORMAT)


def python_files(root):
    """Each .py file under `root`, sorted. __pycache__ is skipped."""
    return [path for path in sorted(root.rglob("*.py")) if "__pycache__" not in path.parts]


def raw_invisible_lines(root):
    """(path relative to `root`, line number, "U+XXXX") for each raw invisible
    character in a .py file under `root`. A byte that is not UTF-8 reads as a
    lone surrogate, U+DC80 to U+DCFF. Lines are counted at "\\n" only, as an
    editor counts them."""
    out = []
    for path in python_files(root):
        text = path.read_bytes().decode("utf-8", "surrogateescape")
        for number, line in enumerate(text.split("\n"), 1):
            out.extend((path.relative_to(root).as_posix(), number, f"U+{ord(char):04X}")
                       for char in line if is_invisible(char))
    return out


def test_no_python_file_under_the_plugin_holds_a_raw_invisible_character():
    files = python_files(PLUGIN)
    assert MUST_SCAN in files, f"the scan of {PLUGIN} did not reach {MUST_SCAN}"
    missed = SOURCE_DIRS - {path.relative_to(PLUGIN).parts[0] for path in files}
    assert not missed, f"the scan of {PLUGIN} found no .py file in {sorted(missed)}"
    lines = raw_invisible_lines(PLUGIN)
    assert not lines, (
        "Write each of these characters in Python source as an escape, such as \\u2028:\n"
        + "\n".join(f"{path}:{number} {char}" for path, number, char in lines))


PLANTED = {
    "byte order mark": "\ufeff",
    "right-to-left override": "\u202e",
    "zero-width space": "\u200b",
    "format character after Unicode 13": "\u0890",
    "line separator": "\u2028",
    "paragraph separator": "\u2029",
    "escape control": "\x1b",
    "next line control": "\x85",
    "no-break space": "\xa0",
}


@pytest.mark.parametrize("char", list(PLANTED.values()), ids=list(PLANTED))
def test_each_invisible_character_is_named_by_file_line_and_code_point(tmp_path, char):
    (tmp_path / "planted.py").write_bytes(f'x = 1\nS = "a{char}b"\n'.encode("utf-8"))
    assert raw_invisible_lines(tmp_path) == [("planted.py", 2, f"U+{ord(char):04X}")]


def test_a_byte_that_is_not_utf8_and_a_leading_byte_order_mark_are_named(tmp_path):
    (tmp_path / "latin1.py").write_bytes(b'x = 1\nS = "caf\xe9"\n')
    (tmp_path / "signed.py").write_bytes("\ufeffx = 1\n".encode("utf-8"))
    assert raw_invisible_lines(tmp_path) == [("latin1.py", 2, "U+DCE9"), ("signed.py", 1, "U+FEFF")]


def test_tabs_line_endings_plain_spaces_escapes_and_visible_text_are_not_flagged(tmp_path):
    escaped = "".join(f"\\u{ord(char):04x}" for char in PLANTED.values())
    (tmp_path / "clean.py").write_bytes(
        f'S = "a\tb"\r\nT = "a b"\r\nU = "{escaped}"\nV = "\u26a0\ufe0f \u0438\u043c\u044f"\n'.encode("utf-8"))
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_bytes("\ufeff\u2028".encode("utf-8"))
    assert raw_invisible_lines(tmp_path) == []
