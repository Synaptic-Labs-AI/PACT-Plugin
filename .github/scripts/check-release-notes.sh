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
#   (at column 0) and ends just before the next such canonical heading, or
#   at the end of the file. Any other line, including a `## Something`
#   subheading, is part of the body.
#
# A RESTRICTED GRAMMAR, ON PURPOSE
#   The checker splits sections on raw lines while GitHub renders Markdown.
#   Rather than model Markdown (code fences, list and quote containers, HTML
#   blocks, setext headings), it REFUSES the constructs where the two could
#   disagree about what is a heading, what is code and what is hidden.
#   Release notes have never needed any of them. Every rule holds for the
#   whole file, in both modes, so a bad line anywhere fails the PR check
#   (--list) and the release job (which then falls back to the copy on main).
#
# THE RULES
#   - <version> is X.Y.Z (digits only).
#   - No NUL byte and no carriage return anywhere (a NUL would be dropped
#     silently by the shell; a CR would be published as is).
#   - No HTML comment (`<!--`) anywhere: GitHub hides commented-out text.
#   - No code fence: no line contains ``` or ~~~ anywhere (inline `code`
#     with single backticks is fine).
#   - No HTML block: no line starts with `<` after 0-3 spaces.
#   - No setext underline, thematic break or front matter: no line is only
#     `=` characters or only `-` characters after 0-3 spaces.
#   - Version headings are canonical: every line that looks like a heading
#     naming a version must be exactly `## vX.Y.Z: <summary>` at column 0.
#     "Looks like" means: after any indent (spaces or tabs, so indented code
#     counts too) and any `>` / `-` / `*` / `+` / `1.` / `1)` prefixes, 1+ `#`,
#     then optional `[`, then either a digit (with optional `v`/`V` before
#     it) or `v`/`V` followed by anything but a lowercase letter. That
#     catches `## V4.8.12:`, `##v4.8.12:`, `## [4.8.12] - date`,
#     `# v4.8.12:`, `### v4.8.12:`, `## vNEXT`, an indented `## v...`,
#     `> ## v...` and `- ## v...`, while `## Validation` stays a subheading.
#   - Exactly one heading line starts `## v<version>: `. None is "missing";
#     two or more is "duplicate".
#   - The heading minus `## ` is the annotated tag message verbatim, so it
#     is an ALLOWLIST: printable ASCII (bytes 0x20-0x7E), the em dash U+2014
#     (E2 80 94) and the en dash U+2013 (E2 80 93) only, with a non-blank
#     summary. An allowlist rules out by construction every control,
#     invalid-UTF-8, invisible, bidi or tag character a terminal, web page
#     or LLM reader could show differently from what a reviewer saw.
#   - The body (the section after its heading, leading and trailing blank
#     lines trimmed) has a non-space character. Within these rules it is
#     free-form Markdown, rendered and sanitised by GitHub.
#
# THE RELEASE BODY
#   The body as written in CHANGELOG.md, with each heading of level 3 or
#   deeper (indented 0-3 spaces) promoted by one level
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

# One pass over the whole file: enforce the grammar on every line, list or
# count the canonical version headings, and copy this version's heading and
# its section (headings already promoted) out. No Markdown is modelled.
awk -v mode="$mode" -v want="## v$version: " \
    -v head_out="$work/heading" -v body_out="$work/section" \
    -v count_out="$work/count" -v err_out="$work/error" -v list_out="$work/list" '
  function fail(msg) { if (err == "") err = "line " NR ": " msg; exit }
  BEGIN {
    n = 0; in_section = 0; err = ""
    printf "" > head_out; printf "" > body_out; printf "" > list_out
  }
  {
    line = $0
    match(line, /^ */); indent = RLENGTH; rest = substr(line, indent + 1)
    if (index(line, "```") || index(line, "~~~"))
      fail("code fences (``` or ~~~) are not allowed in CHANGELOG.md")
    if (indent <= 3 && rest ~ /^</)
      fail("HTML blocks (a line starting with <) are not allowed in CHANGELOG.md")
    if (indent <= 3 && rest ~ /^(=+|-+)[ \t]*$/)
      fail("a line of only = or - (setext heading, rule or front matter) is not allowed")
    canonical = (line ~ /^## v[0-9]+\.[0-9]+\.[0-9]+: /)
    if (!canonical) {
      r = line
      sub(/^[ \t]*/, "", r)
      while (sub(/^(>|[-*+]|[0-9]+[.)])[ \t]*/, "", r)) {}
      if (r ~ /^#+[ \t]*\[?[vV]?[ \t]*[0-9]/ || r ~ /^#+[ \t]*\[?[vV]([^a-z]|$)/)
        fail("a version heading must be exactly \"## vX.Y.Z: <summary>\" at column 0")
    }
    if (canonical) {
      in_section = 0
      if (mode == "list") {
        v = substr(line, 5); sub(/: .*/, "", v); print v > list_out
      } else if (index(line, want) == 1) {
        n++
        if (n == 1) { print line > head_out; in_section = 1 }
      }
      next
    }
    if (indent <= 3 && rest ~ /^###+([ \t]|$)/) line = substr(line, 1, indent) substr(rest, 2)
    if (in_section) print line > body_out
  }
  END {
    print n > count_out
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

read -r n < "$work/count"
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
