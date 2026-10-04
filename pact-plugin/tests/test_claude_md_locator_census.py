"""The bypass census: shipped code may locate PACT's CLAUDE.md markers only
through hooks/shared/claude_md_markers.py.

A scan of every shipped Python file finds each expression that locates a marker
or a PACT heading in text. Every site found must be listed in exactly one
per-commit allowlist under tests/fixtures/claude_md_locator_allowlist/, and every
listed entry must still match a site. A commit that moves a caller onto the
finder deletes that caller's entries in the same commit; the last commit asserts
every allowlist is empty.

An entry is `path<TAB>function<TAB>kind<TAB>expression`, where the expression is
the site's source text with its whitespace collapsed. There are no line numbers,
so moving code does not break an entry; editing a site's own text does.

What counts as a site:
- `in`, `==` and `!=` with a marker on either side;
- the str search methods (find, index, split, partition, startswith, count,
  replace and their kin) given a marker, and re functions given a marker pattern,
  including a compiled pattern's own .search/.match/.finditer/.sub;
- a call passing a marker to a shipped function that searches for one of its
  parameters, directly or through another such function;
- splitting text into lines (.splitlines, .split on a line break) inside a
  function that handles markers;
- a marker passed to `Document.marker_rows`, and a pattern built from a fixed
  block marker (BLOCK_MARKER) passed to `Document.find_lines`, or as the heading
  or terminator of `Document.find_section`. A heading or session-field pattern is
  not a site: the finder locates line patterns on PROSE rows, column 0, with no
  stray rule. Nor is a STALE, `pinned:` or WARNING comment pattern: those are
  per-pin line data, located by position inside a resolved pin or Pinned
  section. `find_section`'s stop prefixes are marker literals by design.
- A regex call whose subject is a parser row's `.content` (`line.content`,
  `doc.lines[k].content`) and whose pattern is a per-pin comment pattern (the
  date or override comment, the STALE mark, the budget WARNING) is not a site
  either: it reads line data on a row the finder has already classified. A
  call with a heading or block-marker pattern stays a site under every method,
  so a fence-blind row scan for a heading is still caught, and so does any call
  on raw text.
- The functions in NAMED_EXEMPT, by name. Each one needs an architect ruling,
  its reason stated beside it, and a site to cover.

"A marker" is a PACT marker comment, a PACT section heading, a session-block
field (`- Resume:` and its siblings), a module-level name holding one (closed
over other such names), a local name bound to one or looping over a collection
of them, or text assembled from literal pieces that spells one. In a module that
handles markers, an anchored `^## `/`\\n### ` heading pattern counts too, and a
bare `## ` prefix counts inside a function that splits text into lines.

A finder lookup (find_block, find_marker, find_section, find_lines, inner,
offsets, scope_known) is an opaque value. Its result is not a marker, so a name
bound from it is not one and comparing its state or rows is not a site, and a
marker in its arguments marks nothing outside the call. An `is` comparison is
never a site.

Named limits, not seen by the scan:
- A search whose needle arrives only as a parameter. The helper's own body
  is not a site; its callers that pass a marker are.
- Markdown command files that tell the model where markers are.
- A heading check on a string that is already one heading (no line split in
  the function), and anything in tests/ or the finder module itself.
- A marker spelled through a variable inside an f-string or a format call (only
  literal pieces are assembled).
- A `find_lines` or `find_section` wrapper that takes its pattern as a
  parameter: its callers are not checked, because most pass headings.
"""

import ast
import collections
import functools
import pathlib
import re

import pytest

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
ALLOWLIST_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "claude_md_locator_allowlist"
COMMITS = ("C2", "C3", "C4", "C5a", "C5b")
FINDER = "hooks/shared/claude_md_markers.py"
TOPS = ("hooks", "skills", "scripts", "bin", "telegram")

# The literals find_block and find_marker locate. The one definition of which
# patterns `find_lines` and `find_section` may not take.
BLOCK_MARKER = re.compile(
    r"<!--\s*(PACT_|SESSION_|Auto-managed)|SESSION_(START|END)\b"
    r"|PACT_(START|END)\b|PACT_MANAGED|PACT_MEMORY")
MARKER_TEXT = re.compile(
    BLOCK_MARKER.pattern + r"|<!--\s*(STALE|pinned|WARNING: Pinned)"
    r"|##\s*(Pinned Context|Working Memory|Retrieved Context|Current Session)"
    r"|# PACT Framework and Managed|pinned:|STALE:"
    r"|-\s*(Resume|Session dir|Started|Team|Plugin root):")
# The per-pin comments a strike on a parser row's content may remove (C-8's line
# data). A pattern built from one of them, and from no block marker, heading or
# session field, is the only pattern the row-content exemption takes.
LINE_DATA = re.compile(r"<!--\s*(STALE|pinned|WARNING: Pinned)|pinned:|STALE:")
# Everything else MARKER_TEXT names: block markers, PACT headings, the managed
# title and session fields.
NON_LINE_DATA = re.compile(
    BLOCK_MARKER.pattern
    + r"|##\s*(Pinned Context|Working Memory|Retrieved Context|Current Session)"
    r"|# PACT Framework and Managed|-\s*(Resume|Session dir|Started|Team|Plugin root):")
HEADING_ANCHORED = re.compile(r"^(\(\?m\))?(\^|\\n|\n)#{1,3}( |\\s|\s)")
HEADING_PLAIN = re.compile(r"^#{1,3}( |$)")
SHELL_MARKER = re.compile(
    r"<!--\s*(PACT_|SESSION_|STALE|pinned)|SESSION_(START|END)\b|PACT_MANAGED|PACT_MEMORY"
    r"|##\s*(Pinned Context|Working Memory|Retrieved Context|Current Session)")
FIND = {"find", "rfind", "index", "rindex", "split", "rsplit", "partition", "rpartition",
        "startswith", "endswith", "count", "replace", "removeprefix", "removesuffix"}
RE_FUN = {"search", "match", "fullmatch", "finditer", "findall", "sub", "subn", "split"}
NOT_HELPERS = FIND | RE_FUN | {"compile", "get", "write", "write_text", "read_text", "join", "format"}
LINE_BREAKS = ("\n", "\r\n", "\r")
# The finder's lookups. Their results carry states and rows, never marker text,
# and the markers in their arguments are the finder's to locate.
FINDER_CALLS = {"find_block", "find_marker", "find_section", "find_lines", "inner", "offsets", "scope_known"}

# Functions whose marker reads are exempt by name, with their nested functions.
# A closed set: an entry needs an architect ruling and its reason here.
NAMED_EXEMPT = frozenset({
    # Rule U's region R is fence-blind by the certified reference configuration.
    # Its correctness is certified by the pin-growth populations and the clause
    # mutants, not by this census.
    ("hooks/shared/pin_growth.py", "clause_region_r"),
})


# --- the scanner ----------------------------------------------------------------

def _text(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bytes):
            return node.value.decode("latin-1")
    return None


def _is_finder_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in FINDER_CALLS


def _walk(node):
    """ast.walk that does not enter a finder call: such a call is an opaque value."""
    todo = [node]
    while todo:
        n = todo.pop()
        if not _is_finder_call(n):
            yield n
            todo.extend(ast.iter_child_nodes(n))


def _literal(node):
    if isinstance(node, ast.Constant):
        return True
    return isinstance(node, (ast.Tuple, ast.List, ast.Set)) and all(
        isinstance(e, ast.Constant) for e in node.elts)


def _pieces(node):
    """The string constants under node, in source order, outside finder calls."""
    if _is_finder_call(node):
        return []
    t = _text(node)
    if t is not None:
        return [t]
    return [p for child in ast.iter_child_nodes(node) for p in _pieces(child)]


def _assembled(node):
    """Text an expression builds from literal pieces: `+`, f-strings and join
    concatenate them in order; `%` and `.format` with literal operands render."""
    try:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            template = _text(node.left)
            operands = [_text(r) for r in (node.right.elts if isinstance(node.right, ast.Tuple) else [node.right])]
            if template is not None and None not in operands:
                return template % tuple(operands)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            template = _text(node.func.value)
            operands = [_text(a) for a in node.args]
            if template is not None and None not in operands and not node.keywords:
                return template.format(*operands)
    except (TypeError, ValueError, IndexError, KeyError):
        return ""
    if isinstance(node, (ast.BinOp, ast.JoinedStr)) or (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join"):
        return "".join(_pieces(node))
    return ""


def _is_marker(s, headings=False, plain=False, text=MARKER_TEXT):
    if s is None or len(s) >= 400:
        return False
    if text.search(s):
        return True
    return headings and (bool(HEADING_ANCHORED.match(s)) or (plain and bool(HEADING_PLAIN.match(s))))


def _mentions(node, names, aliases=(), headings=False, plain=False, text=MARKER_TEXT):
    for x in _walk(node):
        if isinstance(x, ast.Name) and (x.id in names or x.id in aliases):
            return True
        if isinstance(x, ast.Attribute) and x.attr in names:
            return True
        if _is_marker(_text(x), headings, plain, text):
            return True
    return _is_marker(_assembled(node), headings, plain, text)


def _module_level(body):
    for n in body:
        yield n
        if isinstance(n, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            for field in ("body", "orelse", "finalbody"):
                yield from _module_level(getattr(n, field, None) or [])
            for handler in getattr(n, "handlers", None) or []:
                yield from _module_level(handler.body)
        elif isinstance(n, ast.ClassDef):
            yield from _module_level(n.body)


def _targets(stmt):
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    return [x.id for t in targets for x in ast.walk(t) if isinstance(x, ast.Name)]


def _marker_names(trees, handles, text=MARKER_TEXT):
    names = set()
    changed = True
    while changed:
        changed = False
        for rel, tree in trees.items():
            for stmt in _module_level(tree.body):
                if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
                    tn = _targets(stmt)
                    if tn and not set(tn) <= names and _mentions(stmt.value, names, headings=handles[rel], text=text):
                        names.update(tn)
                        changed = True
    return names


def _functions(tree):
    out = []

    def walk(body, prefix):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((prefix + n.name, n))
                walk(n.body, prefix + n.name + ".")
            elif isinstance(n, ast.ClassDef):
                walk(n.body, prefix + n.name + ".")
            else:
                for field in ("body", "orelse", "finalbody"):
                    walk(getattr(n, field, None) or [], prefix)
                for handler in getattr(n, "handlers", None) or []:
                    walk(handler.body, prefix)

    walk(tree.body, "")
    return out


def _own_nodes(fn):
    out, todo = [], list(fn.body)
    while todo:
        n = todo.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        out.append(n)
        todo.extend(ast.iter_child_nodes(n))
    return out


def _re_imports(tree):
    aliases, funcs = {"re"}, {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "re":
                    aliases.add(a.asname or "re")
        elif isinstance(n, ast.ImportFrom) and n.module == "re":
            for a in n.names:
                if a.name in RE_FUN:
                    funcs[a.asname or a.name] = a.name
    return aliases, funcs


def _callee(call):
    if isinstance(call.func, ast.Name):
        name = call.func.id
    elif isinstance(call.func, ast.Attribute):
        name = call.func.attr
    else:
        return None
    return None if name in NOT_HELPERS else name


def _call_args(call):
    return list(call.args) + [k.value for k in call.keywords]


def _is_line_split(call):
    f = call.func
    return isinstance(f, ast.Attribute) and (
        f.attr == "splitlines" or (f.attr == "split" and call.args and _text(call.args[0]) in LINE_BREAKS))


def _bindings(nodes, pred):
    """Local names bound from a marker value or looping over a collection of them,
    and names bound to a searching bound method."""
    aliases, finders = set(), {}
    for _ in range(4):
        for n in nodes:
            if isinstance(n, ast.Assign):
                if pred(n.value, aliases):
                    aliases.update(_targets(n))
                if isinstance(n.value, ast.Attribute) and n.value.attr in FIND | RE_FUN:
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            finders[t.id] = n.value
            elif isinstance(n, (ast.For, ast.comprehension)) and pred(n.iter, aliases):
                aliases.update(x.id for x in ast.walk(n.target) if isinstance(x, ast.Name))
    return aliases, finders


def _arg(call, index, keyword):
    """The positional argument at index, else that keyword's value, else a None constant."""
    if len(call.args) > index:
        return call.args[index]
    return next((k.value for k in call.keywords if k.arg == keyword), ast.Constant(None))


def _subject(call, is_re_module):
    """The text argument a regex call searches: after the pattern for a `re`
    function, after the replacement for `sub`/`subn`."""
    index = (1 if is_re_module else 0) + (1 if call.func.attr in ("sub", "subn") else 0)
    if len(call.args) > index:
        return call.args[index]
    return next((k.value for k in call.keywords if k.arg == "string"), None)


def _reads_row_content(call, is_re_module, line_data):
    """A per-pin comment pattern applied to a parser row's `.content`: line
    data, not a locator. Only with a pattern `line_data` accepts."""
    subject = _subject(call, is_re_module)
    if not (isinstance(subject, ast.Attribute) and subject.attr == "content"):
        return False
    pattern = call.args[0] if is_re_module and call.args else call.func.value
    return line_data(pattern)


def _locators(nodes, needle, re_aliases, re_funcs, finders, splits_marker_text, strict=False, block=None,
              line_data=lambda x: False):
    """(kind, node) for each searching expression whose needle satisfies `needle`.
    strict: only the searched-for operand counts (left of `in`, the first argument,
    the pattern) and a value tested against constants is not a search. `block`
    tests a `find_lines` or `find_section` pattern; None skips both. `line_data`
    accepts a pattern for the row-content strike exemption; the default refuses
    the exemption everywhere."""
    out = []
    for n in nodes:
        kind = None
        if isinstance(n, ast.Compare):
            sides = [n.left] + list(n.comparators)
            if strict and any(_literal(x) for x in sides):
                pass
            elif any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops):
                if needle(n.left) or (not strict and any(needle(c) for c in n.comparators)):
                    kind = "in"
            elif any(isinstance(o, (ast.Eq, ast.NotEq)) for o in n.ops) and any(needle(x) for x in sides):
                kind = "=="
        elif isinstance(n, ast.Call):
            args = _call_args(n)
            argm = any(needle(a) for a in (args[:1] if strict else args))
            f = n.func
            if isinstance(f, ast.Attribute):
                recv = f.value
                is_re = isinstance(recv, ast.Name) and recv.id in re_aliases
                if is_re and f.attr in RE_FUN and argm:
                    if not _reads_row_content(n, True, line_data):
                        kind = "re." + f.attr
                elif not is_re and f.attr in RE_FUN - {"split"} and needle(recv):
                    if not _reads_row_content(n, False, line_data):
                        kind = "." + f.attr
                elif f.attr in FIND and argm:
                    kind = "." + f.attr
                elif f.attr == "marker_rows" and needle(_arg(n, 0, "literal")):
                    kind = ".marker_rows"
                elif f.attr == "find_lines" and block and block(_arg(n, 0, "pattern")):
                    kind = ".find_lines"
                elif f.attr == "find_section" and block and (
                        block(_arg(n, 0, "heading")) or block(_arg(n, 1, "terminator"))):
                    kind = ".find_section"
                elif f.attr == "split" and not is_re and not strict and not argm and needle(recv):
                    kind = ".split(pattern)"
                elif splits_marker_text and _is_line_split(n):
                    kind = "line split"
            elif isinstance(f, ast.Name):
                if f.id in re_funcs and argm:
                    kind = "re." + re_funcs[f.id]
                elif f.id in finders:
                    bound = finders[f.id]
                    if argm or (bound.attr in RE_FUN and needle(bound.value)):
                        kind = "bound ." + bound.attr
        if kind:
            out.append((kind, n))
    return out


def _parametric_locators(trees):
    """Names of functions that search for a needle taken from a parameter, directly
    or by passing the parameter on to another such function."""
    direct, passes = set(), []
    for tree in trees.values():
        re_aliases, re_funcs = _re_imports(tree)
        for _q, fn in _functions(tree):
            params = {a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs}
            if not params or fn.name in NOT_HELPERS:
                continue
            nodes = _own_nodes(fn)
            is_param = lambda x, p=params: any(isinstance(y, ast.Name) and y.id in p for y in _walk(x))
            _, finders = _bindings(nodes, lambda v, al: False)
            if _locators(nodes, is_param, re_aliases, re_funcs, finders, False, strict=True):
                direct.add(fn.name)
            callees = {_callee(n) for n in nodes if isinstance(n, ast.Call)
                       and any(is_param(a) for a in _call_args(n))}
            passes.append((fn.name, callees - {None}))
    helpers = set(direct)
    changed = True
    while changed:
        changed = False
        for name, callees in passes:
            if name not in helpers and callees & helpers:
                helpers.add(name)
                changed = True
    return helpers


def _segment(source, node):
    text = ast.get_source_segment(source, node)
    assert text is not None, f"no source position for {ast.dump(node)[:80]}"
    return " ".join(text.split())


def _exempt(rel, qual, exempt):
    return any(rel == path and (qual == name or qual.startswith(name + "."))
               for path, name in exempt)


def census_sources(sources, exempt=NAMED_EXEMPT):
    """Every site in {path: python source}, as a Counter of entry tuples,
    leaving out the functions `exempt` names."""
    trees = {rel: ast.parse(src) for rel, src in sources.items()}
    handles = {rel: any(_is_marker(_text(x)) for x in ast.walk(t)) for rel, t in trees.items()}
    names = _marker_names(trees, handles)
    block_names = _marker_names(trees, dict.fromkeys(trees, False), BLOCK_MARKER)
    data_names = _marker_names(trees, dict.fromkeys(trees, False), LINE_DATA)
    other_names = _marker_names(trees, dict.fromkeys(trees, True), NON_LINE_DATA)
    for rel, t in trees.items():
        handles[rel] = handles[rel] or any(isinstance(x, ast.Name) and x.id in names for x in ast.walk(t))
    helpers = _parametric_locators(trees)
    sites = collections.Counter()
    for rel, tree in sorted(trees.items()):
        re_aliases, re_funcs = _re_imports(tree)
        for qual, fn in _functions(tree):
            if _exempt(rel, qual, exempt):
                continue
            nodes = _own_nodes(fn)
            splits = any(isinstance(n, ast.Call) and _is_line_split(n) for n in nodes)
            pred = lambda x, al, rel=rel, splits=splits: _mentions(
                x, names, al, headings=handles[rel], plain=splits)
            aliases, finders = _bindings(nodes, pred)
            needle = lambda x, al=aliases, pred=pred: pred(x, al)
            handles_markers = any(needle(n) for n in nodes if isinstance(n, ast.expr))
            block_pred = lambda x, al: _mentions(x, block_names, al, text=BLOCK_MARKER)
            block_aliases, _ = _bindings(nodes, block_pred)
            block_marker = lambda x, al=block_aliases, pred=block_pred: pred(x, al)
            block = None
            if any(isinstance(n, ast.Attribute) and n.attr in ("find_lines", "find_section") for n in nodes):
                block = block_marker
            # A strike pattern is line data when it names a per-pin comment and no
            # other marker: a block marker, a heading or a session field.
            data_pred = lambda x, al: _mentions(x, data_names, al, text=LINE_DATA)
            data_aliases, _ = _bindings(nodes, data_pred)
            other_pred = lambda x, al: _mentions(x, other_names, al, headings=True, text=NON_LINE_DATA)
            other_aliases, _ = _bindings(nodes, other_pred)
            line_data = (lambda x, da=data_aliases, oa=other_aliases:
                         data_pred(x, da) and not other_pred(x, oa))
            found = _locators(nodes, needle, re_aliases, re_funcs, finders, handles_markers, block=block,
                              line_data=line_data)
            found += [("helper call", n) for n in nodes if isinstance(n, ast.Call)
                      and _callee(n) in helpers and any(needle(a) for a in _call_args(n))]
            for kind, node in found:
                sites[(rel, qual, kind, _segment(sources[rel], node))] += 1
    return sites


def _shipped_sources():
    out = {}
    for top in TOPS:
        for f in sorted((PLUGIN / top).rglob("*.py")):
            rel = f.relative_to(PLUGIN).as_posix()
            if "/tests/" in "/" + rel or f.name.startswith("test_") or rel == FINDER:
                continue
            out[rel] = f.read_text(encoding="utf-8")
    return out


@functools.lru_cache(maxsize=1)
def shipped_census():
    return census_sources(_shipped_sources())


def shell_marker_lines(sources):
    """(path, stripped line) for each shell line carrying a marker literal."""
    out = collections.Counter()
    for rel, text in sources.items():
        for line in text.splitlines():
            if SHELL_MARKER.search(line):
                out[(rel, " ".join(line.split()))] += 1
    return out


def load_allowlists():
    """{commit: Counter of entries}; `#` starts a comment line."""
    out = {}
    for commit in COMMITS:
        entries = collections.Counter()
        path = ALLOWLIST_DIR / f"{commit}.txt"
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip() or raw.startswith("#"):
                continue
            parts = raw.split("\t")
            assert len(parts) == 4, f"{path.name}: malformed entry {raw!r}"
            entries[tuple(parts)] += 1
        out[commit] = entries
    return out


def _fmt(entries):
    return "\n".join("\t".join(e) + (f"  (x{n})" if n > 1 else "") for e, n in sorted(entries.items()))


# --- the census over the shipped tree ---------------------------------------------

def test_every_locator_site_is_listed_in_a_commit_allowlist():
    allowed = sum(load_allowlists().values(), collections.Counter())
    unlisted = shipped_census() - allowed
    assert not unlisted, (
        "These shipped sites locate a PACT marker without the finder "
        "(hooks/shared/claude_md_markers.py). Route them through it, or, only for a "
        "site that predates the migration, list it in its commit's allowlist:\n"
        + _fmt(unlisted))


def test_every_allowlist_entry_still_matches_a_site():
    allowed = sum(load_allowlists().values(), collections.Counter())
    stale = allowed - shipped_census()
    assert not stale, (
        "These allowlist entries match no shipped site. A commit that moves a caller "
        "onto the finder deletes its entries; delete these:\n" + _fmt(stale))


def test_no_site_is_listed_by_two_commits():
    lists = load_allowlists()
    seen = {}
    doubled = []
    for commit, entries in lists.items():
        for entry in entries:
            if entry in seen:
                doubled.append((entry, seen[entry], commit))
            seen[entry] = commit
    assert not doubled, doubled


def test_the_scan_reads_the_shipped_tree_and_skips_the_finder():
    sources = _shipped_sources()
    assert len(sources) >= 50, "the scan read implausibly few files; is it reading the tree?"
    assert FINDER not in sources and (PLUGIN / FINDER).is_file()
    assert not any(rel == FINDER for rel, *_ in shipped_census())


def test_shipped_shell_scripts_carry_no_marker_literal():
    sources = {f.relative_to(PLUGIN).as_posix(): f.read_text(encoding="utf-8", errors="replace")
               for f in sorted(PLUGIN.rglob("*.sh")) if "tests" not in f.relative_to(PLUGIN).parts}
    assert sources, "no shipped shell script found; the scan is not reading the tree"
    assert not shell_marker_lines(sources)


# --- seeded positives and negatives -----------------------------------------------

_CONSTS = (
    'SESSION_START_MARKER = "<!-- SESSION_START -->"\n'
    'SESSION_END_MARKER = "<!-- SESSION_END -->"\n'
    'import re\n')


def _kinds(body):
    """Kinds of the sites found in a seeded module whose function is `body`."""
    src = _CONSTS + "def f(text, lines, data, block):\n" + "".join("    " + l + "\n" for l in body.splitlines())
    return sorted(kind for (_r, _f, kind, _e) in census_sources({"hooks/seeded.py": src}).elements())


@pytest.mark.parametrize("shape, body, kind", [
    ("in", "return SESSION_START_MARKER in text", "in"),
    ("str method", "return text.find(SESSION_END_MARKER)", ".find"),
    ("re function", 'return re.search(SESSION_START_MARKER + ".*", text)', "re.search"),
    ("concatenated pieces", 'return ("<!-- SESS" + "ION_START -->") in text', "in"),
    ("joined pieces", 'return "".join(["<!-- SESSION", "_END -->"]) in text', "in"),
    ("percent format", 'return ("<!-- %s_START -->" % "SESSION") in text', "in"),
    ("str.format", 'return "<!-- {}_END -->".format("SESSION") in text', "in"),
    ("f-string of literals", 'return f"<!-- {\'SESSION\'}_START -->" in text', "in"),
    ("call-derived constant", None, "in"),
    ("bytes literal", 'return b"<!-- SESSION_START -->" in data', "in"),
    ("encoded marker", "return SESSION_START_MARKER.encode() in data", "in"),
    ("equality", "return [l for l in lines if l.strip() == SESSION_END_MARKER]", "=="),
    ("loop over markers", "for m in (SESSION_START_MARKER, SESSION_END_MARKER):\n    if m in text:\n        return m", "in"),
    ("stored bound method", "find = text.find\nreturn find(SESSION_START_MARKER)", "bound .find"),
    ("regex module alias", None, "re.search"),
    ("session field", 'return re.search(r"- Resume: `([^`]+)`", text)', "re.search"),
    ("line split in a marker function", "if SESSION_START_MARKER in text:\n    return text.splitlines()", "line split"),
    ("line split beside a finder call",
     "doc = parse(text)\nloc = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER)\nreturn loc, text.splitlines()",
     "line split"),
    ("marker_rows", "return parse(text).marker_rows(SESSION_START_MARKER)", ".marker_rows"),
    ("marker_rows by keyword", "return parse(text).marker_rows(scope=(0, 1), literal=SESSION_END_MARKER)",
     ".marker_rows"),
    ("find_lines on a block marker", "return parse(text).find_lines(re.compile(re.escape(SESSION_START_MARKER)))",
     ".find_lines"),
    ("find_lines on a local block pattern",
     'pat = re.compile("^" + re.escape(SESSION_END_MARKER))\nreturn parse(text).find_lines(pat)', ".find_lines"),
    ("find_lines on a block marker literal", 'return parse(text).find_lines(re.compile(r"^<!-- PACT_MEMORY_END -->"))',
     ".find_lines"),
    ("find_section heading built from a block marker",
     "return parse(text).find_section(re.compile(re.escape(SESSION_START_MARKER)), None)", ".find_section"),
    ("find_section terminator built from a block marker",
     ('return parse(text).find_section(re.compile(r"^## Pinned Context\\s*$"), '
      're.compile(r"#{1,2}\\s|" + re.escape(SESSION_END_MARKER)))'), ".find_section"),
    ("find_section terminator by keyword",
     'return parse(text).find_section(heading=re.compile(r"^### "), terminator=re.compile("<!-- PACT_MEMORY_END"))',
     ".find_section"),
    ("alias of a marker literal", "m = SESSION_START_MARKER\nreturn [l for l in lines if l.strip() == m]", "=="),
    ("marker search beside a finder result",
     ("loc = parse(text).find_block(SESSION_START_MARKER, SESSION_END_MARKER)\n"
      "return loc.state == State.FOUND and SESSION_START_MARKER in text"), "in"),
    ("marker compared with a finder result",
     "return parse(text).find_marker(SESSION_START_MARKER).state == SESSION_END_MARKER", "=="),
    ("pin comment pattern on raw text", 'return re.compile(r"<!-- pinned: .*-->").sub("", text)', ".sub"),
    ("block marker pattern on row content",
     "return re.compile(re.escape(SESSION_START_MARKER)).match(parse(text).lines[0].content)", ".match"),
    ("block marker re function on row content",
     "return [re.search(SESSION_END_MARKER, line.content) for line in parse(text).lines]", "re.search"),
    ("block marker strike on every row",
     'return [re.compile(re.escape(SESSION_START_MARKER)).sub("", line.content) for line in parse(text).lines]',
     ".sub"),
    ("heading strike on row content",
     'return re.compile(r"^## Pinned Context\\s*$").sub("", parse(text).lines[0].content)', ".sub"),
])
def test_seeded_shapes_are_caught(shape, body, kind):
    if shape == "call-derived constant":
        src = (_CONSTS + "_SS = SESSION_START_MARKER.strip()\n"
               "def f(text):\n    return _SS in text\n")
        found = [k for (_r, _f, k, _e) in census_sources({"hooks/seeded.py": src})]
    elif shape == "regex module alias":
        src = ('import re as regex\nfrom re import search as s\n'
               'MARK = "<!-- PACT_MANAGED_START -->"\n'
               "def f(text):\n    return regex.search(MARK, text), s(MARK, text)\n")
        found = [k for (_r, _f, k, _e) in census_sources({"hooks/seeded.py": src})]
        assert found.count("re.search") == 2, found
    else:
        found = _kinds(body)
    assert kind in found, (shape, found)


def test_a_helper_searching_for_its_parameter_is_caught_at_its_marker_call():
    src = (_CONSTS +
           "def _line_of(text, literal):\n"
           "    return [i for i, l in enumerate(text.split('\\n')) if l.rstrip() == literal]\n"
           "def _via(text, literal):\n"
           "    return _line_of(text, literal)\n"
           "def caller(text):\n"
           "    return _via(text, SESSION_START_MARKER)\n")
    found = census_sources({"hooks/seeded.py": src})
    assert [(f, k) for (_r, f, k, _e) in found] == [("caller", "helper call")], found


def test_a_helper_searching_raw_text_for_its_parameter_stays_a_locator():
    src = (_CONSTS +
           "def _has(text, lit):\n    return lit in text\n"
           "def caller(text):\n    return _has(text, SESSION_START_MARKER)\n")
    found = census_sources({"hooks/seeded.py": src})
    assert [(f, k) for (_r, f, k, _e) in found] == [("caller", "helper call")], found


@pytest.mark.parametrize("src", [
    # The helper's parameter reaches only a finder call, so the helper is no locator.
    ("def _is_found(doc, literal):\n    return doc.find_marker(literal).state == State.FOUND\n"
     "def caller(text):\n    return _is_found(parse(text), SESSION_START_MARKER)\n"),
    # A helper comparing rows, called with a scope taken from a finder result.
    ("def _pair_state(doc, scope, heading, last):\n"
     "    pair = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER, scope)\n"
     "    return pair.spans[0] == (heading - 1, last + 1)\n"
     "def plan(text):\n"
     "    doc = parse(text)\n"
     "    mem = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER)\n"
     "    scope = (mem.spans[0][0] + 1, mem.spans[0][1] - 1)\n"
     "    return _pair_state(doc, scope, 3, 4)\n"),
])
def test_helpers_that_only_read_finder_results_are_not_sites(src):
    assert census_sources({"hooks/seeded.py": _CONSTS + src}) == collections.Counter()


def test_a_marker_rows_wrapper_searching_for_its_parameter_is_caught_at_its_marker_call():
    src = (_CONSTS +
           "def _locate(doc, literal):\n    return doc.marker_rows(literal)\n"
           "def caller(text):\n    return _locate(parse(text), SESSION_START_MARKER)\n"
           "def unrelated(text):\n    return _locate(parse(text), '<!-- an ordinary comment -->')\n")
    found = census_sources({"hooks/seeded.py": src})
    assert [(f, k) for (_r, f, k, _e) in found] == [("caller", "helper call")], found


def test_a_fence_blind_row_scan_on_row_content_is_caught():
    """A match or search over every row's `.content` reads FENCE and CODE rows
    too, so it is a site even though its subject is a parser row; only a strike
    of a per-pin comment is exempt."""
    src = ('import re\nfrom shared.claude_md_markers import parse\n'
           'MEMORY_START_MARKER = "<!-- PACT_MEMORY_START -->"\n'
           '_PINNED = re.compile(r"^## Pinned Context\\s*$")\n'
           '_START_RE = re.compile(re.escape(MEMORY_START_MARKER))\n'
           '_DATE_COMMENT_RE = re.compile(r"<!--\\s*pinned:\\s*.+?-->")\n'
           'def heading_row(content):\n'
           '    for line in parse(content).lines:\n'
           '        if _PINNED.match(line.content):\n'
           '            return line.row\n'
           'def strike_starts(content):\n'
           '    return [_START_RE.sub("", line.content) for line in parse(content).lines]\n'
           'def strike_dates(doc, first, last):\n'
           '    return [_DATE_COMMENT_RE.sub("", line.content) for line in doc.lines[first:last + 1]]\n'
           '_OVERRIDE_ROW = re.compile(r"\\s*<!--\\s*pinned:.*pin-size-override:.*-->")\n'
           'def override_row(doc, row):\n'
           '    return _OVERRIDE_ROW.fullmatch(doc.lines[row].content)\n')
    found = sorted((f, k) for (_r, f, k, _e) in census_sources({"hooks/seeded.py": src}))
    assert found == [("heading_row", ".match"), ("strike_starts", ".sub")], found


def test_a_named_exemption_drops_its_function_and_only_it():
    src = (_CONSTS +
           "def exempt(text):\n    return SESSION_START_MARKER in text\n"
           "def kept(text):\n    return SESSION_END_MARKER in text\n")
    named = frozenset({("hooks/seeded.py", "exempt")})
    assert sorted(f for (_r, f, _k, _e) in census_sources({"hooks/seeded.py": src}, named)) == ["kept"]
    assert sorted(f for (_r, f, _k, _e) in census_sources({"hooks/seeded.py": src}, frozenset())) == [
        "exempt", "kept"]


def test_every_named_exemption_names_a_function_that_still_has_a_site():
    """A rename leaves an entry pointing at nothing, and a function moved onto
    the finder leaves an entry exempting nothing: either one fails here."""
    sources = _shipped_sources()
    unexempted = census_sources(sources, frozenset())
    for path, name in sorted(NAMED_EXEMPT):
        assert path in sources, f"{path} is not shipped; drop its NAMED_EXEMPT entry"
        quals = {qual for qual, _fn in _functions(ast.parse(sources[path]))}
        assert name in quals, f"{path} has no function {name}; drop or rename its NAMED_EXEMPT entry"
        assert any(_exempt(rel, qual, frozenset({(path, name)})) for (rel, qual, _k, _e) in unexempted), (
            f"{path}::{name} holds no census site any more; drop its NAMED_EXEMPT entry")


def test_a_module_level_block_pattern_is_caught_at_find_lines():
    src = (_CONSTS + "_START_RE = re.compile(re.escape(SESSION_START_MARKER))\n"
           "def f(text):\n    return parse(text).find_lines(_START_RE)\n")
    found = census_sources({"hooks/seeded.py": src})
    assert [k for (_r, _f, k, _e) in found] == [".find_lines"], found


def test_an_anchored_heading_pattern_is_caught_in_a_marker_module():
    src = (_CONSTS + "_ENTRY = re.compile(r'^### ', re.MULTILINE)\n"
           "def entries(body):\n    return list(_ENTRY.finditer(body))\n")
    found = census_sources({"hooks/seeded.py": src})
    assert [k for (_r, _f, k, _e) in found] == [".finditer"], found


@pytest.mark.parametrize("body", [
    "from shared.claude_md_markers import parse\nreturn parse(text).find_block(SESSION_START_MARKER, SESSION_END_MARKER)",
    'return "\\n".join([SESSION_START_MARKER, block, SESSION_END_MARKER])',
    "return text.splitlines()",
    'return text.find("ordinary words")',
    'return data == "<!-- an ordinary comment -->"',
    'return parse(text).find_lines(re.compile(r"^## Pinned Context\\s*$"))',
    ("doc = parse(text)\nloc = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER)\n"
     'return doc.find_lines(re.compile(r"- Resume: "), scope=loc.spans[0])'),
    'return parse(text).find_lines(re.compile(r"<!-- pinned: \\d{4}-\\d\\d-\\d\\d -->"))',
    'return parse(text).find_lines(re.compile("<!-- STALE"))',
    # A section located the one sanctioned way: the scope comes from a FOUND block,
    # and the stop prefixes are marker literals.
    ("doc = parse(text)\nloc = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER)\n"
     'return doc.find_section(re.compile(r"^## Pinned Context\\s*$"), re.compile(r"#{1,2}\\s"), loc.spans[0], '
     'stop_prefixes=("<!-- PACT_MEMORY_", SESSION_START_MARKER), unique=True)'),
    'return parse(text).find_section(re.compile(r"^### "), None)',
    # The section header comment: any comment that names no PACT marker.
    'return parse(text).find_lines(re.compile(r"<!--(?!\\s*(?:PACT_|SESSION_))[^>]*-->\\s*$"), scope=(4, 4))',
    # Reading a finder result: its state, rows and offsets name no marker.
    "loc = parse(text).find_block(SESSION_START_MARKER, SESSION_END_MARKER)\nreturn loc.state == State.FOUND",
    ("loc = parse(text).find_block(SESSION_START_MARKER, SESSION_END_MARKER)\n"
     "return loc.state in (State.DUPLICATE, State.MALFORMED)"),
    "loc = parse(text).find_marker(SESSION_START_MARKER)\nreturn loc.spans[0] == (3, 3)",
    "return parse(text + 'x').find_block(SESSION_START_MARKER, SESSION_END_MARKER).state != State.FOUND",
    ("start = parse(text).find_marker(SESSION_START_MARKER).state\n"
     "end = parse(text).find_marker(SESSION_END_MARKER).state\n"
     "return {start, end} == {State.FOUND, State.ABSENT}"),
    ("doc = parse(text)\nloc = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER)\n"
     "rows = doc.inner(loc)\nstart, end = doc.offsets(rows[0], rows[-1])\n"
     "return rows == (1, 2), end != start, doc.scope_known(loc.spans[0]) == True"),
    # An identity test is never a site, on a finder result or on a marker.
    "loc = parse(text).find_block(SESSION_START_MARKER, SESSION_END_MARKER)\nreturn loc.state is State.FOUND",
    "m = SESSION_END_MARKER\nreturn [l for l in lines if l is m]",
    # Line data: a per-pin comment pattern applied to a row the finder classified.
    'return [re.compile(r"<!-- pinned: .*-->").sub("", line.content) for line in parse(text).lines]',
    'return re.sub(r"<!-- pinned: .*-->", "", parse(text).lines[1].content)',
    'return re.compile(r"<!-- STALE: .*-->").search(parse(text).lines[2].content)',
    'return re.compile(r"\\s*<!--\\s*pinned:.*pin-size-override:.*-->").fullmatch(parse(text).lines[0].content)',
])
def test_finder_routed_calls_writers_and_unrelated_searches_are_not_sites(body):
    assert _kinds(body) == []


def test_a_marker_literal_in_a_shell_script_is_caught():
    assert shell_marker_lines({"x.sh": "grep -n '<!-- SESSION_START -->' CLAUDE.md\necho ok\n"}) == \
        collections.Counter({("x.sh", "grep -n '<!-- SESSION_START -->' CLAUDE.md"): 1})

