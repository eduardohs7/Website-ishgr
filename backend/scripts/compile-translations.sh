#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_ROOT="${CHAGS_RUNTIME_DIR:-$(dirname "$REPO_ROOT")/.chags-environment}"
if ! command -v msgfmt >/dev/null; then
    export PATH="$RUNTIME_ROOT/gettext/bin:$PATH"
fi
cd "$REPO_ROOT/backend"
"$REPO_ROOT/.venv/bin/python" manage.py compilemessages -l en -l es --ignore .local
