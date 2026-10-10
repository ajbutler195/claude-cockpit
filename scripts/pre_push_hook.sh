#!/bin/bash
# Runs the leak scanner before every push. Install once per clone:
#     ln -s ../../scripts/pre_push_hook.sh .git/hooks/pre-push
#
# WHY a pre-PUSH hook and not pre-commit: a commit is still private and fixable. A push
# is publication. The scan therefore covers every commit (--history), including commit
# messages and author lines, not just the files as they are now.
#
# WHY --require-private is conditional: the private word list is optional for a casual
# fork, but a maintainer who has ever set one up must never get a green light just
# because the list went missing. If the folder that holds it exists, the list is
# required and a missing list fails the push.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
ARGS="--history"
if [ -d "$HOME/.config/claude_cockpit_publish" ] || [ -n "${COCKPIT_SCAN_BANNED:-}" ]; then
  ARGS="$ARGS --require-private"
fi
# shellcheck disable=SC2086
if ! python3 "$ROOT/scripts/scan_personal.py" $ARGS; then
  echo ""
  echo "pre-push: the leak scanner found something (or could not run). Push stopped."
  echo "Fix the lines listed above, or see scripts/scan_personal.py --help."
  exit 1
fi
