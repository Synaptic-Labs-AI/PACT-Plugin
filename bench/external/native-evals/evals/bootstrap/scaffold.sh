#!/usr/bin/env bash
set -euo pipefail
printf '# Demo project\n' > README.md
git init -q -b main
git add README.md
git -c user.name=bench -c user.email=bench@example.invalid -c commit.gpgsign=false commit -qm seed
