#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_ROOT="${CHAGS_RUNTIME_DIR:-$(dirname "$REPO_ROOT")/.chags-environment}"
cd "$REPO_ROOT"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$RUNTIME_ROOT/uv-cache}"
if ! command -v uv > /dev/null; then
    echo 'Este setup requer uv; consulte backend/README.md para a alternativa com pip.' >&2
    exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
    uv venv .venv --python 3.12
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Python 3.12 na .venv"'
uv pip sync --python .venv/bin/python --require-hashes backend/requirements.txt
bash backend/scripts/install-gettext-runtime.sh
bash backend/scripts/compile-translations.sh
if [[ -z "${CHAGS_PG_BIN:-}" ]]; then
    bash backend/scripts/install-postgres-runtime.sh
fi
bash backend/scripts/postgres-dev.sh start
.venv/bin/python backend/manage.py check
.venv/bin/python backend/manage.py migrate --noinput
