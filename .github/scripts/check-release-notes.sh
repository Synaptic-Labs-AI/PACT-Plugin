#!/usr/bin/env bash
# Release-notes checker and extractor: the ONE definition of what a valid
# release section in CHANGELOG.md looks like, and the one parser that turns
# it into a tag message and a release body.
#
# WHAT THIS DOES
#   check-release-notes.sh <version> <changelog> [<msg-out> <body-out>]
#   Exit 0 when <changelog> has a valid section for <version>. With the two
#   output paths it also writes the tag message to <msg-out> and the release
#   body to <body-out>. Exit 1 with a one-line reason on stdout when the
#   section is missing or invalid. Exit 2 on a usage error.
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
#   and ends just before the next line that starts `## v` and a digit (the
#   next version's heading), or at the end of the file. Lines inside a ``` or
#   ~~~ fence never start or end a section. Any other line, including a
#   `## Something` line, is part of the body.
#
# THE RULES
#   - <version> is X.Y.Z (digits only).
#   - The file contains no NUL byte anywhere (a NUL would be silently
#     dropped by the shell, so what is checked would not be what is
#     published).
#   - Exactly one heading line starts `## v<version>: `. None is "missing";
#     two or more is "duplicate"; a heading for this version in any other
#     form (e.g. `## v<version> summary`) is reported as malformed.
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
#   deeper outside a ``` or ~~~ fence promoted by one level (`### Summary`
#   becomes `## Summary`), so a release reads like the hand-cut ones. It ends
#   with exactly one newline.
#
# The file's text is only ever data here: it is read by awk and into
# variables and compared, never evaluated. Reasons are printed through
# printf %q. No command feeds a pipe into a reader that can exit early: under
# pipefail the writer's SIGPIPE would fail the check at random on large
# files (measured once: a 200 KB body falsely rejected 20 times out of 20).

set -euo pipefail
export LC_ALL=C

if [ "$#" -ne 2 ] && [ "$#" -ne 4 ]; then
  echo "usage: check-release-notes.sh <version> <changelog> [<msg-out> <body-out>]" >&2
  exit 2
fi
version="$1"
file="$2"

if ! [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "version is not X.Y.Z: $(printf '%q' "$version")"
  exit 1
fi
if [ ! -f "$file" ]; then
  echo "no such file: $(printf '%q' "$file")"
  exit 1
fi

# NUL anywhere: tr keeps only NUL bytes and wc counts them; wc reads to EOF,
# so tr cannot be cut off by SIGPIPE. Checked before awk or bash read any
# line, because bash drops NUL from command substitutions.
if [ "$(tr -cd '\000' < "$file" | wc -c)" -ne 0 ]; then
  echo "file contains a NUL byte"
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# One pass: copy this version's heading and section lines out (fence lines
# are body lines, and a fence toggles only the heading scan), and count the
# well-formed headings for it (n) and any other heading naming it (bad). A
# heading for a different version, e.g. v4.8.1 when asked for v4.8.12, is
# told apart by the character after the version string.
awk -v want="## v$version: " -v loose="## v$version" \
    -v head_out="$work/heading" -v body_out="$work/section" -v count_out="$work/count" '
  BEGIN { n = 0; bad = 0; in_section = 0; fenced = 0; printf "" > head_out; printf "" > body_out }
  /^(```|~~~)/ { fenced = !fenced }
  !fenced && /^## v[0-9]/ {
    in_section = 0
    if (index($0, want) == 1) {
      n++
      if (n == 1) { print > head_out; in_section = 1 }
    } else if (index($0, loose) == 1 && substr($0, length(loose) + 1, 1) !~ /[0-9.]/) {
      bad++
    }
    next
  }
  in_section { print > body_out }
  END { print n, bad > count_out }
' "$file"
read -r n bad < "$work/count"

if [ "$n" -eq 0 ]; then
  if [ "$bad" -gt 0 ]; then
    echo "the heading for v$version must be '## v$version: <summary>'"
  else
    echo "no section for v$version (a line starting '## v$version: ')"
  fi
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

# Trim blank lines at both ends and promote headings outside fences. awk
# exits 1 when nothing but blank lines is left.
if ! awk -v out="$work/body" '
  { line[NR] = $0 }
  END {
    first = 1; while (first <= NR && line[first] ~ /^[[:space:]]*$/) first++
    last = NR; while (last >= first && line[last] ~ /^[[:space:]]*$/) last--
    if (last < first) exit 1
    printf "" > out
    fenced = 0
    for (i = first; i <= last; i++) {
      l = line[i]
      if (l ~ /^(```|~~~)/) fenced = !fenced
      else if (!fenced && l ~ /^###+([ \t]|$)/) l = substr(l, 2)
      print l > out
    }
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
