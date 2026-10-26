#!/usr/bin/env bash
# Release-notes validator: the ONE definition of what a valid
# `.github/release-notes/v<version>.md` looks like.
#
# WHAT THIS DOES
#   check-release-notes.sh <version> <file>
#   Exit 0 when <file> is valid notes for <version>. Exit 1 with a one-line
#   reason on stdout when it is not. Exit 2 on a usage error.
#
# WHO CALLS IT
#   - .github/workflows/release.yml, before it tags and releases a version
#     (it reads this script from the checked-out main, the same trust level
#     as the workflow itself).
#   - pact-plugin/tests/test_release_notes.py, on every PR through tests.yml,
#     so a malformed notes file cannot merge and then block every later
#     release.
#   Both call this file, so the PR-time check and the release-time check can
#   never disagree.
#
# THE RULES
#   - <version> is X.Y.Z (digits only).
#   - The file contains no NUL byte anywhere (a NUL would be silently
#     dropped by the shell here, so what is checked would not be what is
#     published).
#   - Line 1 is `# v<version>: <summary>`; the summary has a non-space
#     character. Line 1 minus `# ` becomes the annotated tag message
#     verbatim, so line 1 is an ALLOWLIST: it may contain only printable
#     ASCII (bytes 0x20-0x7E), the em dash U+2014 (E2 80 94) and the en dash
#     U+2013 (E2 80 93). Every tag message cut so far fits that set. An
#     allowlist rules out by construction every control, invalid-UTF-8,
#     invisible, bidi or tag character a terminal, web page or LLM reader
#     could show differently from what a reviewer saw in the diff; a
#     blocklist of such characters can never be complete.
#   - Line 2 is empty.
#   - Line 3 onward (the release body) has a non-space character. The body
#     is free-form Markdown, rendered and sanitised by GitHub, so apart from
#     the NUL rule it gets no character rules.
#
# The file's text is only ever data here: it is read into variables and
# compared, never evaluated. Reasons are printed through printf %q.
# No command here feeds a pipe into a reader that can exit early: under
# pipefail the writer's SIGPIPE would fail the check at random on large
# files (measured: a 200 KB body was falsely rejected 20 times out of 20).

set -euo pipefail
export LC_ALL=C

if [ "$#" -ne 2 ]; then
  echo "usage: check-release-notes.sh <version> <file>" >&2
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

# NUL anywhere: tr keeps only NUL bytes and wc counts them; wc reads to
# EOF, so tr cannot be cut off by SIGPIPE. Checked before any line is read,
# because bash drops NUL from command substitutions.
if [ "$(tr -cd '\000' < "$file" | wc -c)" -ne 0 ]; then
  echo "file contains a NUL byte"
  exit 1
fi

line1="$(sed -n 1p "$file")"
summary="${line1#"# v$version: "}"
if [[ $line1 != "# v$version: "* ]]; then
  echo "line 1 must start with '# v$version: ', got: $(printf '%q' "$line1")"
  exit 1
fi
# The allowlist, byte-wise under LC_ALL=C: delete the two exact dash
# sequences, then every remaining byte must be 0x20-0x7E. A partial or
# neighbouring sequence (a stray E2, E2 80, or U+2012/U+2015) is left
# behind by the deletion and fails the range check.
em_dash=$'\xe2\x80\x94'
en_dash=$'\xe2\x80\x93'
rest="${line1//"$em_dash"/}"
rest="${rest//"$en_dash"/}"
if ! [[ $rest =~ ^[\ -~]*$ ]]; then
  echo "line 1 may contain only printable ASCII, em dash or en dash: $(printf '%q' "$line1")"
  exit 1
fi
if ! [[ $summary =~ [^[:space:]] ]]; then
  echo "line 1 has an empty summary"
  exit 1
fi
if [ -n "$(sed -n 2p "$file")" ]; then
  echo "line 2 must be blank"
  exit 1
fi
if ! awk 'NR >= 3 && /[^[:space:]]/ { found = 1; exit } END { exit !found }' "$file"; then
  echo "no release notes after line 2"
  exit 1
fi
exit 0
