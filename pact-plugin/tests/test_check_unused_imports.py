"""
Location: pact-plugin/tests/test_check_unused_imports.py
Summary: Unit tests for the unused-import predicate in
         skills/pact-coding-standards/scripts/check_unused_imports.py —
         the pure function's carve-outs, suppression mechanics, try-scope
         strictness contract, and the CLI's exit/output contract.
Used by: pytest suite. The suite-level import-hygiene gate over the shipped
         tree lives in its own test module (TEST-phase work); these tests
         cover the predicate itself.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).parent.parent
    / "skills"
    / "pact-coding-standards"
    / "scripts"
    / "check_unused_imports.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_unused_imports", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cui = _load_module()


def _names(source, try_scope="strict"):
    """Finding names only, for terse assertions."""
    return [f.name for f in cui.find_unused_imports(source, try_scope=try_scope)]


class TestBasicDetection:
    def test_unused_import_detected(self):
        assert _names("import os\n") == ["os"]

    def test_used_import_not_flagged(self):
        assert _names("import os\nprint(os.sep)\n") == []

    def test_attribute_usage_counts(self):
        assert _names("import os.path\nx = os.path.sep\n") == []

    def test_dotted_import_binds_root_name(self):
        # `import a.b.c` binds `a`; usage of the root marks it used...
        assert _names("import xml.etree.ElementTree\nprint(xml)\n") == []
        # ...and an unused dotted import reports the full dotted form.
        assert _names("import xml.etree.ElementTree\n") == ["xml.etree.ElementTree"]

    def test_from_import_detected_and_used(self):
        assert _names("from json import dumps\n") == ["dumps"]
        assert _names("from json import dumps\ndumps({})\n") == []

    def test_alias_tracks_bound_name_and_reports_source_form(self):
        assert _names("import json as j\n") == ["json as j"]
        assert _names("import json as j\nj.dumps({})\n") == []

    def test_underscore_named_import_without_noqa_still_flagged(self):
        assert _names("from json import dumps as _dumps\n") == ["dumps as _dumps"]

    def test_finding_carries_statement_lineno(self):
        findings = cui.find_unused_imports(
            "x = 1\nimport os\n", try_scope="strict"
        )
        assert findings == [cui.Finding(2, "os")]

    def test_multiple_findings_sorted_by_line(self):
        src = "import os\nimport sys\n"
        assert [f.lineno for f in cui.find_unused_imports(src, try_scope="strict")] == [1, 2]


class TestNoqaSuppression:
    def test_noqa_f401_suppresses(self):
        assert _names("import os  # noqa: F401\n") == []

    def test_bare_noqa_suppresses(self):
        assert _names("import os  # noqa\n") == []

    def test_noqa_with_reason_comment_suppresses(self):
        assert _names("import os  # noqa: F401  # re-export: seam\n") == []

    def test_noqa_other_code_does_not_suppress(self):
        assert _names("import os  # noqa: E501\n") == ["os"]

    def test_noqa_f401_with_trailing_prose_suppresses(self):
        # A reason written without a comma or a second `#` must not defeat
        # the suppression — codes are parsed as letter+digit tokens, so the
        # prose cannot bleed into the code list.
        assert _names("import os  # noqa: F401 optional dependency probe\n") == []

    def test_noqa_longer_code_sharing_f401_prefix_does_not_suppress(self):
        # Token-level (not substring) matching: F4011 is a different code.
        assert _names("import os  # noqa: F4011\n") == ["os"]

    def test_noqa_on_first_line_of_parenthesized_import_suppresses_all(self):
        src = (
            "from json import (  # noqa: F401  # re-export: facade\n"
            "    dumps,\n"
            "    loads,\n"
            ")\n"
        )
        assert _names(src) == []

    def test_noqa_on_inner_line_of_parenthesized_import_is_invisible(self):
        # Contract: suppression is read from the statement's FIRST physical
        # line only — per-name noqa inside the parens does not suppress.
        src = (
            "from json import (\n"
            "    dumps,  # noqa: F401\n"
            ")\n"
        )
        assert _names(src) == ["dumps"]


class TestCarveOuts:
    def test_future_import_never_flagged(self):
        assert _names("from __future__ import annotations\n") == []

    def test_type_checking_block_never_flagged(self):
        src = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from json import dumps\n"
        )
        assert _names(src) == []

    def test_typing_attribute_type_checking_block_never_flagged(self):
        src = (
            "import typing\n"
            "if typing.TYPE_CHECKING:\n"
            "    from json import dumps\n"
        )
        assert _names(src) == []

    def test_dunder_all_reexport_not_flagged(self):
        src = "from json import dumps\n__all__ = ['dumps']\n"
        assert _names(src) == []

    def test_dunder_all_augassign_reexport_not_flagged(self):
        src = "from json import dumps\n__all__ = []\n__all__ += ['dumps']\n"
        assert _names(src) == []

    def test_star_import_ignored(self):
        assert _names("from json import *\n") == []


class TestTryScopeStrictness:
    _TRY_SRC = "try:\n    import os\nexcept ImportError:\n    os = None\n"

    def test_strict_flags_try_scoped_import(self):
        # `os = None` in the handler binds a Name in Store context; the
        # usage walk adds Name nodes regardless of context, so build a
        # variant with no handler binding to isolate the try-scope rule.
        src = "try:\n    import os\nexcept ImportError:\n    pass\n"
        assert _names(src, try_scope="strict") == ["os"]

    def test_advisory_skips_try_scoped_import(self):
        src = "try:\n    import os\nexcept ImportError:\n    pass\n"
        assert _names(src, try_scope="advisory") == []

    def test_module_level_import_flagged_in_both_modes(self):
        assert _names("import os\n", try_scope="strict") == ["os"]
        assert _names("import os\n", try_scope="advisory") == ["os"]

    def test_try_scope_is_required_no_default(self):
        # Structural no-default contract: omitting the parameter is a
        # TypeError from the signature itself, not a fallback.
        with pytest.raises(TypeError):
            cui.find_unused_imports("import os\n")

    def test_try_scope_is_keyword_only(self):
        with pytest.raises(TypeError):
            cui.find_unused_imports("import os\n", "strict")

    def test_invalid_try_scope_value_raises(self):
        with pytest.raises(ValueError):
            cui.find_unused_imports("import os\n", try_scope="lenient")


class TestSyntaxErrorBehavior:
    def test_pure_function_propagates_syntax_error(self):
        with pytest.raises(SyntaxError):
            cui.find_unused_imports("def broken(:\n", try_scope="strict")

    def test_check_paths_reports_syntax_error_loudly(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text("def broken(:\n")
        lines = cui.check_paths([str(bad)], try_scope="strict")
        assert len(lines) == 1
        assert "syntax error" in lines[0]
        assert lines[0].startswith(str(bad))


class TestCheckPathsContract:
    def test_clean_file_yields_no_lines(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("import os\nprint(os.sep)\n")
        assert cui.check_paths([str(f)], try_scope="strict") == []

    def test_finding_line_format(self, tmp_path):
        f = tmp_path / "dirty.py"
        f.write_text("import os\n")
        lines = cui.check_paths([str(f)], try_scope="strict")
        assert lines == [f"{f}:1: unused import os"]

    def test_missing_file_reported_loudly(self, tmp_path):
        missing = tmp_path / "nope.py"
        lines = cui.check_paths([str(missing)], try_scope="strict")
        assert len(lines) == 1
        assert "unable to read file" in lines[0]

    def test_non_py_path_refused(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("import os\n")
        lines = cui.check_paths([str(f)], try_scope="strict")
        assert len(lines) == 1
        assert "not a .py file" in lines[0]

    def test_latin1_coding_cookie_file_is_checked_not_crashed(self, tmp_path):
        # PEP 263: legal non-UTF8 Python is read per its declared encoding
        # and checked like any other file. The emitted finding (not merely
        # the absence of a crash) proves the file was actually analyzed.
        f = tmp_path / "latin1.py"
        f.write_bytes("# coding: latin-1\nimport os\nx = 'é'\n".encode("latin-1"))
        lines = cui.check_paths([str(f)], try_scope="strict")
        assert lines == [f"{f}:2: unused import os"]

    def test_undecodable_file_reported_loudly_and_batch_continues(self, tmp_path):
        # Bytes that decode under no detected encoding are a loud per-file
        # failure line — and must not abort the rest of the batch (findings
        # in sibling files still come out).
        bad = tmp_path / "bad.py"
        bad.write_bytes(b"import os\nx = '\xff\xfe\x9c'\n")
        dead = tmp_path / "dead.py"
        dead.write_text("import json\n", encoding="utf-8")
        lines = cui.check_paths([str(bad), str(dead)], try_scope="strict")
        assert any(
            line.startswith(f"{bad}:0:") and "unable to read file" in line
            for line in lines
        )
        assert f"{dead}:1: unused import json" in lines


class TestCli:
    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, str(_SCRIPT), *argv],
            capture_output=True,
            text=True,
        )

    def test_cli_requires_try_scope_flag(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("import os\n")
        proc = self._run(str(f))
        assert proc.returncode == 2  # argparse usage error, not a silent default
        assert "--try-scope" in proc.stderr

    def test_cli_clean_file_exits_zero(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("import os\nprint(os.sep)\n")
        proc = self._run("--try-scope", "strict", str(f))
        assert proc.returncode == 0
        assert proc.stdout == ""

    def test_cli_findings_exit_one_with_contract_format(self, tmp_path):
        f = tmp_path / "dirty.py"
        f.write_text("import os\n")
        proc = self._run("--try-scope", "strict", str(f))
        assert proc.returncode == 1
        assert proc.stdout.strip() == f"{f}:1: unused import os"

    def test_cli_syntax_error_exits_one_loudly(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def broken(:\n")
        proc = self._run("--try-scope", "strict", str(f))
        assert proc.returncode == 1
        assert "syntax error" in proc.stdout
