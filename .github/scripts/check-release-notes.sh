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
#   - Line 1 is `# v<version>: <summary>`; the summary has a non-space
#     character; line 1 has no control character (CR, ESC, BEL, TAB, ...),
#     because line 1 minus `# ` becomes the annotated tag message verbatim
#     and terminals interpret escape sequences when they show it.
#   - Line 2 is empty.
#   - Line 3 onward (the release body) has a non-space character.
#
# The file's text is only ever data here: it is read into variables and
# compared, never evaluated. Reasons are printed through printf %q.

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

line1="$(sed -n 1p "$file")"
summary="${line1#"# v$version: "}"
if [[ $line1 != "# v$version: "* ]]; then
  echo "line 1 must start with '# v$version: ', got: $(printf '%q' "$line1")"
  exit 1
fi
# [[:cntrl:]] covers the C0 controls and DEL. The UTF-8 encodings of the C1
# controls (U+0080-U+009F, e.g. the one-byte CSI some terminals honour) are
# the byte pair C2 80..C2 9F, matched byte-wise under LC_ALL=C.
c1_controls=$'\xc2[\x80-\x9f]'
if [[ $line1 == *[[:cntrl:]]* || $line1 =~ $c1_controls ]]; then
  echo "line 1 has a control character: $(printf '%q' "$line1")"
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
if ! tail -n +3 "$file" | grep -q '[^[:space:]]'; then
  echo "no release notes after line 2"
  exit 1
fi
exit 0
