#!/usr/bin/env bash
# Private Unix socket with peer authentication; no TCP listener or password.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_ROOT="${CHAGS_RUNTIME_DIR:-$(dirname "$REPO_ROOT")/.chags-environment}"
PG_BIN="${CHAGS_PG_BIN:-$RUNTIME_ROOT/postgres/usr/lib/postgresql/17/bin}"
STATE_ROOT="$REPO_ROOT/backend/.local/postgres"
DATA_DIR="$STATE_ROOT/data"
SOCKET_DIR="$STATE_ROOT/socket"
DB_ADMIN="$(id -un)"

if [[ ! -x "$PG_BIN/pg_ctl" ]]; then
    echo 'Execute bash backend/scripts/install-postgres-runtime.sh ou configure CHAGS_PG_BIN.' >&2
    exit 1
fi
case "${1:-start}" in
    status)
        exec "$PG_BIN/pg_ctl" -D "$DATA_DIR" status
        ;;
    stop)
        exec "$PG_BIN/pg_ctl" -D "$DATA_DIR" -w stop
        ;;
    start) ;;
    *) echo 'Uso: postgres-dev.sh [start|status|stop]' >&2; exit 2 ;;
esac

umask 077
mkdir -p "$STATE_ROOT" "$SOCKET_DIR"
chmod 700 "$STATE_ROOT" "$SOCKET_DIR"
if [[ ! -f "$DATA_DIR/PG_VERSION" ]]; then
    "$PG_BIN/initdb" -D "$DATA_DIR" -U "$DB_ADMIN" \
        --encoding=UTF8 --locale=C.UTF-8 --auth-local=peer --auth-host=scram-sha-256
    python3 - "$DATA_DIR" "$SOCKET_DIR" "$DB_ADMIN" <<'PY'
import re
import sys
from pathlib import Path
data, socket, user = sys.argv[1:]
if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*', user):
    raise SystemExit('Nome de usuário do sistema não suportado pelo mapa local.')
with (Path(data) / 'postgresql.conf').open('a') as stream:
    stream.write("\n# CHAGS local development only\n")
    stream.write("listen_addresses = ''\nport = 55432\n")
    stream.write("unix_socket_directories = '" + socket.replace("'", "''") + "'\n")
    # JIT is unnecessary for development queries; LLVM is not installed here.
    stream.write("jit = off\n")
(Path(data) / 'pg_hba.conf').write_text(
    'local all chags_dev peer map=chags_local\nlocal all all peer\n'
)
(Path(data) / 'pg_ident.conf').write_text(f'chags_local {user} chags_dev\n')
PY
fi
if ! "$PG_BIN/pg_ctl" -D "$DATA_DIR" status > /dev/null 2>&1; then
    "$PG_BIN/pg_ctl" -D "$DATA_DIR" -l "$STATE_ROOT/postgres.log" -w start
fi

"$PG_BIN/psql" -X -h "$SOCKET_DIR" -p 55432 -U "$DB_ADMIN" -d postgres \
    --set=ON_ERROR_STOP=1 <<'SQL'
SELECT 'CREATE ROLE chags_dev LOGIN CREATEDB NOSUPERUSER NOCREATEROLE'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'chags_dev') \gexec
SELECT 'CREATE DATABASE chags_dev OWNER chags_dev'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'chags_dev') \gexec
SQL
"$PG_BIN/psql" -X -h "$SOCKET_DIR" -p 55432 -U chags_dev -d chags_dev \
    --set=ON_ERROR_STOP=1 -c 'SELECT current_database(), current_user;'
