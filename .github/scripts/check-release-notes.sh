#!/usr/bin/env bash
# Release-notes checker and extractor: the ONE definition of what a valid
# release section in CHANGELOG.md looks like, and the one parser that turns
# it into a tag message and a release body.
#
# WHAT THIS DOES
#   check-release-notes.sh <version> <changelog> [<msg-out> <body-out>]
#     Exit 0 when <changelog> has a valid section for <version>. With the two
#     output paths it also writes the tag message to <msg-out> and the
#     release body to <body-out>.
#   check-release-notes.sh --list <changelog>
#     Check the whole file and print the version of every section, one per
#     line, newest first as written.
#   Exit 1 with a one-line reason on stdout when the file or section is
#   invalid. Exit 2 on a usage error.
#
# WHO CALLS IT
#   - .github/workflows/release.yml, to check and extract a version's notes
#     before it tags and releases (it reads this script from the checked-out
#     main, the same trust level as the workflow itself).
#   - pact-plugin/tests/test_release_notes.py, on every PR through tests.yml,
#     so a malformed section cannot merge and then block every later release.
#   Both call this file, so the PR-time check and the release-time extraction
#   can never disagree.
#
# THE SECTION
#   A version's section starts at its heading line `## v<version>: <summary>`
#   (at column 0) and ends just before the next column-0 line that starts
#   `## v` and a digit, or at the end of the file. Any other line, including a
#   `## Something` line, is part of the body.
#
# CODE FENCES (CommonMark rules, so the split matches what GitHub renders)
#   A fence opens on a line indented 0-3 spaces followed by 3 or more ` or ~
#   (for `, the rest of the line has no backtick). It closes only on a line
#   indented 0-3 spaces with the SAME character, at least as many, and then
#   only whitespace. Everything between is code: never a section boundary,
#   never promoted. A fence nested in another (a ```` block showing ```) is
#   fine. FAIL CLOSED, anywhere in the file: a `## v<digit>` line inside a
#   fence, a `## v<digit>` heading indented 1-3 spaces (GitHub shows it as a
#   heading, but it would not split sections here), and a fence still open
#   at the end of the file.
#   Known divergence: a fence indented 4 or more spaces inside a list item
#   is code on GitHub but plain text here. Such lines are never boundaries
#   (column 0 only) and never promoted (0-3 spaces only), so they are
#   published exactly as written, which is how GitHub shows them.
#
# THE RULES
#   - <version> is X.Y.Z (digits only).
#   - The file contains no NUL byte and no carriage return anywhere (a NUL
#     would be dropped silently by the shell; a CR would be published as is).
#   - The file contains no HTML comment (`<!--`): GitHub hides commented-out
#     text, so a section inside one would be published unseen.
#   - The fence rules above hold for the whole file.
#   - Exactly one heading line starts `## v<version>: `. None is "missing";
#     two or more is "duplicate"; any other heading naming this version
#     (e.g. `## v<version> summary`) is an error even beside a valid one.
#     In --list mode every `## v<digit>` heading must have the
#     `## vX.Y.Z: ` form.
#   - The heading minus `## ` is the annotated tag message verbatim, so it
#     is an ALLOWLIST: printable ASCII (bytes 0x20-0x7E), the em dash U+2014
#     (E2 80 94) and the en dash U+2013 (E2 80 93) only, with a non-blank
#     summary. An allowlist rules out by construction every control,
#     invalid-UTF-8, invisible, bidi or tag character a terminal, web page
#     or LLM reader could show differently from what a reviewer saw.
#   - The body (the section after its heading, leading and trailing blank
#     lines trimmed) has a non-space character. It is otherwise free-form
#     Markdown, rendered and sanitised by GitHub.
#
# THE RELEASE BODY
#   The body as written in CHANGELOG.md, with each heading of level 3 or
#   deeper (indented 0-3 spaces, outside a fence) promoted by one level
#   (`### Summary` becomes `## Summary`), so a release reads like the
#   hand-cut ones. It ends with exactly one newline.
#
# The file's text is only ever data here: it is read by awk and into
# variables and compared, never evaluated. Reasons are printed through
# printf %q. No command feeds a pipe into a reader that can exit early: under
# pipefail the writer's SIGPIPE would fail the check at random on large
# files (measured once: a 200 KB body falsely rejected 20 times out of 20).

set -euo pipefail
export LC_ALL=C

usage() {
  echo "usage: check-release-notes.sh <version> <changelog> [<msg-out> <body-out>]" >&2
  echo "       check-release-notes.sh --list <changelog>" >&2
  exit 2
}
if [ "$#" -eq 2 ] && [ "$1" = "--list" ]; then
  mode=list
  version=""
  file="$2"
elif [ "$#" -eq 2 ] || [ "$#" -eq 4 ]; then
  mode=check
  version="$1"
  file="$2"
  if ! [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "version is not X.Y.Z: $(printf '%q' "$version")"
    exit 1
  fi
else
  usage
fi
if [ ! -f "$file" ]; then
  echo "no such file: $(printf '%q' "$file")"
  exit 1
fi

# Whole-file byte rules. tr keeps only the given byte and wc counts it; wc
# reads to EOF, so tr cannot be cut off by SIGPIPE. Checked before awk or
# bash read any line, because bash drops NUL from command substitutions.
if [ "$(tr -cd '\000' < "$file" | wc -c)" -ne 0 ]; then
  echo "file contains a NUL byte"
  exit 1
fi
if [ "$(tr -cd '\r' < "$file" | wc -c)" -ne 0 ]; then
  echo "file contains a carriage return (CRLF line ending?)"
  exit 1
fi
# grep -c reads the whole file (no early exit); it exits 1 on zero matches.
comments="$(grep -c -F -- '<!--' "$file" || true)"
if [ "$comments" -ne 0 ]; then
  echo "file contains an HTML comment ('<!--'); GitHub hides its text"
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# One pass over the whole file, the only place fences are modelled: track
# CommonMark fences, enforce the fence rules, list or count version
# headings, and copy this version's heading and its section (headings
# already promoted) out. A heading for a different version, e.g. v4.8.1
# when asked for v4.8.12, is told apart by the character after the version.
awk -v mode="$mode" -v want="## v$version: " -v loose="## v$version" \
    -v head_out="$work/heading" -v body_out="$work/section" \
    -v count_out="$work/count" -v err_out="$work/error" -v list_out="$work/list" '
  function fail(msg) { if (err == "") err = msg }
  # Sets fchar/flen when rest (indent removed) opens a fence.
  function opens(rest,    info) {
    if (match(rest, /^`+/) && RLENGTH >= 3) {
      info = substr(rest, RLENGTH + 1)
      if (index(info, "`") == 0) { fchar = "`"; flen = RLENGTH; return 1 }
      return 0
    }
    if (match(rest, /^~+/) && RLENGTH >= 3) { fchar = "~"; flen = RLENGTH; return 1 }
    return 0
  }
  function closes(rest,    ok) {
    if (fence_char == "`") ok = match(rest, /^`+/); else ok = match(rest, /^~+/)
    if (!ok || RLENGTH < fence_len) return 0
    return substr(rest, RLENGTH + 1) ~ /^[ \t]*$/
  }
  function emit(text) { if (in_section) print text > body_out }
  BEGIN {
    n = 0; bad = 0; in_section = 0; fence_char = ""; fence_len = 0; err = ""
    printf "" > head_out; printf "" > body_out; printf "" > list_out
  }
  {
    line = $0
    match(line, /^ */); indent = RLENGTH; rest = substr(line, indent + 1)
    if (fence_char != "") {
      if (indent <= 3 && closes(rest)) { fence_char = ""; emit(line); next }
      if (indent <= 3 && rest ~ /^## v[0-9]/) {
        fail("line " NR ": a \"## v\" heading inside the code fence opened on line " fence_line)
        exit
      }
      emit(line); next
    }
    if (indent <= 3 && opens(rest)) {
      fence_char = fchar; fence_len = flen; fence_line = NR; emit(line); next
    }
    if (line ~ /^## v[0-9]/) {
      in_section = 0
      if (mode == "list") {
        if (line !~ /^## v[0-9]+\.[0-9]+\.[0-9]+: /) {
          fail("line " NR ": a version heading must be \"## vX.Y.Z: <summary>\"")
          exit
        }
        v = substr(line, 5); sub(/: .*/, "", v); print v > list_out
      } else if (index(line, want) == 1) {
        n++
        if (n == 1) { print line > head_out; in_section = 1 }
      } else if (index(line, loose) == 1 && substr(line, length(loose) + 1, 1) !~ /[0-9.]/) {
        bad++
      }
      next
    }
    if (indent >= 1 && indent <= 3 && rest ~ /^## v[0-9]/) {
      fail("line " NR ": a \"## v\" heading must start at column 0")
      exit
    }
    if (indent <= 3 && rest ~ /^###+([ \t]|$)/) line = substr(line, 1, indent) substr(rest, 2)
    emit(line)
  }
  END {
    if (err == "" && fence_char != "") fail("the code fence opened on line " fence_line " is never closed")
    print n, bad > count_out
    printf "%s", err > err_out
  }
' "$file"

if [ -s "$work/error" ]; then
  cat "$work/error"
  echo
  exit 1
fi
if [ "$mode" = "list" ]; then
  if [ ! -s "$work/list" ]; then
    echo "no '## vX.Y.Z: ' sections"
    exit 1
  fi
  cat "$work/list"
  exit 0
fi

read -r n bad < "$work/count"
if [ "$bad" -gt 0 ]; then
  echo "a heading for v$version is not in the form '## v$version: <summary>'"
  exit 1
fi
if [ "$n" -eq 0 ]; then
  echo "no section for v$version (a line starting '## v$version: ')"
  exit 1
fi
if [ "$n" -gt 1 ]; then
  echo "duplicate section: $n headings start '## v$version: '"
  exit 1
fi
heading="$(cat "$work/heading")"
message="${heading#"## "}"
summary="${heading#"## v$version: "}"
# The allowlist, byte-wise under LC_ALL=C: delete the two exact dash
# sequences, then every remaining byte must be 0x20-0x7E. A partial or
# neighbouring sequence (a stray E2, E2 80, or U+2012/U+2015) is left
# behind by the deletion and fails the range check.
em_dash=$'\xe2\x80\x94'
en_dash=$'\xe2\x80\x93'
rest="${heading//"$em_dash"/}"
rest="${rest//"$en_dash"/}"
if ! [[ $rest =~ ^[\ -~]*$ ]]; then
  echo "the heading may contain only printable ASCII, em dash or en dash: $(printf '%q' "$heading")"
  exit 1
fi
if ! [[ $summary =~ [^[:space:]] ]]; then
  echo "the heading has an empty summary"
  exit 1
fi

# Trim blank lines at both ends (headings were promoted in the first
# pass). awk exits 1 when nothing but blank lines is left.
if ! awk -v out="$work/body" '
  { line[NR] = $0 }
  END {
    first = 1; while (first <= NR && line[first] ~ /^[[:space:]]*$/) first++
    last = NR; while (last >= first && line[last] ~ /^[[:space:]]*$/) last--
    if (last < first) exit 1
    printf "" > out
    for (i = first; i <= last; i++) print line[i] > out
  }
' "$work/section"; then
  echo "no release notes under the v$version heading"
  exit 1
fi

if [ "$#" -eq 4 ]; then
  printf '%s\n' "$message" > "$3"
  cp "$work/body" "$4"
fi
exit 0
