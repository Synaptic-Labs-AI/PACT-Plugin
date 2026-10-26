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
#     character. Line 1 minus `# ` becomes the annotated tag message
#     verbatim, and what a terminal or web page SHOWS of it must be what the
#     reviewer saw in the diff, so line 1 must also be:
#       - valid UTF-8 (checked with iconv; no stray bytes such as a lone
#         8-bit CSI 0x9b, nothing past U+10FFFF);
#       - free of control characters: C0, DEL and C1 (CR, ESC, BEL, TAB,
#         CSI, ...), which terminals act on;
#       - free of invisible and reordering format characters: bidi
#         embeddings/overrides U+202A-202E and isolates U+2066-2069, the
#         marks U+200E, U+200F and U+061C, zero-width U+200B-200D and
#         U+2060-2064, the BOM U+FEFF, the separators U+2028/2029 and the
#         soft hyphen U+00AD. These can make a tag message display
#         differently from its bytes, and none is visible in a PR diff.
#     Ordinary UTF-8 (accents, em dashes, non-breaking spaces) is accepted.
#   - Line 2 is empty.
#   - Line 3 onward (the release body) has a non-space character. The body
#     is rendered and sanitised by GitHub, so it gets no character rules.
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

line1="$(sed -n 1p "$file")"
summary="${line1#"# v$version: "}"
if [[ $line1 != "# v$version: "* ]]; then
  echo "line 1 must start with '# v$version: ', got: $(printf '%q' "$line1")"
  exit 1
fi
# Valid UTF-8 first, so the byte patterns below only ever see whole
# characters. glibc's iconv rejects stray continuation bytes, invalid lead
# bytes, truncated sequences, overlongs and surrogates; code points past
# U+10FFFF (F4 90.. and up) are refused by the pattern after it. Without
# iconv the check cannot be made, so the file is refused (fail closed).
if ! command -v iconv >/dev/null 2>&1; then
  echo "cannot check line 1 is valid UTF-8: iconv is not installed"
  exit 1
fi
above_max=$'\xf4[\x90-\xbf]'
if ! iconv -f UTF-8 -t UTF-8 <<<"$line1" >/dev/null 2>&1 || [[ $line1 =~ $above_max ]]; then
  echo "line 1 is not valid UTF-8: $(printf '%q' "$line1")"
  exit 1
fi
# [[:cntrl:]] covers the C0 controls and DEL. The UTF-8 encodings of the C1
# controls (U+0080-U+009F, e.g. the CSI some terminals honour) are the byte
# pair C2 80..C2 9F, matched byte-wise under LC_ALL=C.
c1_controls=$'\xc2[\x80-\x9f]'
if [[ $line1 == *[[:cntrl:]]* || $line1 =~ $c1_controls ]]; then
  echo "line 1 has a control character: $(printf '%q' "$line1")"
  exit 1
fi
# The invisible and reordering format characters listed in THE RULES, as
# UTF-8 byte sequences:
#   E2 80 8B-8F  U+200B-200F  ZWSP, ZWNJ, ZWJ, LRM, RLM
#   E2 80 A8-AE  U+2028-202E  LS, PS, LRE, RLE, PDF, LRO, RLO
#   E2 81 A0-A4  U+2060-2064  WJ and the invisible operators
#   E2 81 A6-A9  U+2066-2069  LRI, RLI, FSI, PDI
#   D8 9C        U+061C       ALM
#   EF BB BF     U+FEFF       BOM / ZWNBSP
#   C2 AD        U+00AD       soft hyphen
format_chars=$'\xe2\x80[\x8b-\x8f]|\xe2\x80[\xa8-\xae]|\xe2\x81[\xa0-\xa4]|\xe2\x81[\xa6-\xa9]|\xd8\x9c|\xef\xbb\xbf|\xc2\xad'
if [[ $line1 =~ $format_chars ]]; then
  echo "line 1 has an invisible or bidi format character: $(printf '%q' "$line1")"
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
