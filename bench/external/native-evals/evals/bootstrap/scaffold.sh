#!/usr/bin/env bash
set -euo pipefail
# The seed repo reads no system or global git config, and runs no hooks.
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
printf '# Demo project\n' > README.md
git init -q -b main
git add README.md
git -c user.name=bench -c user.email=bench@example.invalid -c commit.gpgsign=false -c core.hooksPath=/dev/null commit -qm seed
