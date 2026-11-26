"""
Location: pact-plugin/tests/test_dynamic_access_forms.py
Summary: The pinned dynamic-access table (fixtures/code_readers.py
_DYNAMIC_ACCESS_FORMS) and dynamic_reaches, run on a synthetic module: each
kind of form a census bans is found, a known name that is no anchor is
spared, and a standard module's forms join the table only when the files
import that module.
Used by: pytest; reads tests/fixtures/code_readers.py.
"""

from __future__ import annotations

from fixtures.code_readers import dynamic_access_forms, dynamic_reaches

SOURCE = (
    "import importlib\n"
    "import os\n"
    "\n"
    "\n"
    "def lookups(name, code):\n"
    "    namespace = vars(os)\n"
    "    unknown = getattr(os, name)\n"
    "    known = getattr(os, 'sep')\n"
    "    evaluated = eval(code)\n"
    "    table = os.__dict__\n"
    "    module = importlib.import_module(name)\n"
    "    return namespace, unknown, known, evaluated, table, module\n"
)


def _reaches(tmp_path, source):
    path = tmp_path / "module.py"
    path.write_text(source, encoding="utf-8")
    forms = dynamic_access_forms([path])
    found = dynamic_reaches([path], forms, lambda kind, value: value == "ANCHOR")
    return forms, {use[2] for use in found}


def test_each_kind_of_form_is_found_and_a_known_name_is_spared(tmp_path):
    """RED WHEN a namespace form stops counting as a reach, or a table row
    for getattr, vars, eval, __dict__ or import_module is lost."""
    _forms, found = _reaches(tmp_path, SOURCE)
    assert found == {
        "vars(os)", "getattr(os, name)", "eval(code)", "os.__dict__",
        "importlib.import_module(name)",
    }, found


def test_a_module_form_joins_only_when_the_files_import_its_module(tmp_path):
    """A standard module's forms are the table's rows for the modules the
    files import; the builtin and object-attribute rows always join."""
    with_import, _found = _reaches(tmp_path, SOURCE)
    without, _found = _reaches(tmp_path, SOURCE.replace("import importlib\n", ""))
    assert "importlib.import_module" in with_import
    assert "importlib.import_module" not in without
    assert {"builtins.getattr", ".__dict__"} <= set(without)
