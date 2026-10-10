#!/usr/bin/env bash
# Rootless PostgreSQL binaries for this Debian 13 cloud machine, not CTIC deploy.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_ROOT="${CHAGS_RUNTIME_DIR:-$(dirname "$REPO_ROOT")/.chags-environment}"
PG_BIN="$RUNTIME_ROOT/postgres/usr/lib/postgresql/17/bin"
if [[ -x "$PG_BIN/postgres" && -x "$PG_BIN/initdb" && -x "$PG_BIN/psql" ]]; then
    "$PG_BIN/postgres" --version
    exit 0
fi

if [[ ! -r /usr/share/keyrings/debian-archive-keyring.gpg ]]; then
    echo 'Instale PostgreSQL 17 pelo gerenciador confiável do sistema.' >&2
    exit 1
fi
source /etc/os-release
if [[ "$ID" != debian || "$VERSION_ID" != 13 ]]; then
    echo 'Este instalador local foi preparado para Debian 13.' >&2
    exit 1
fi

APT_ROOT="$RUNTIME_ROOT/apt"
mkdir -p "$APT_ROOT/lists/partial" "$APT_ROOT/cache/archives/partial" \
    "$APT_ROOT/parts" "$RUNTIME_ROOT/packages" "$RUNTIME_ROOT/postgres"
cat > "$APT_ROOT/apt.conf" <<EOF
Dir::Etc::parts "$APT_ROOT/parts";
Dir::Etc::main "$APT_ROOT/nonexistent.conf";
Dir::Etc::sourcelist "$APT_ROOT/sources.list";
Dir::Etc::sourceparts "-";
Dir::State::lists "$APT_ROOT/lists";
Dir::Cache "$APT_ROOT/cache";
Dir::Cache::archives "$APT_ROOT/cache/archives";
Dir::Log "$APT_ROOT";
APT::Get::List-Cleanup "false";
Acquire::Retries "2";
EOF
cat > "$APT_ROOT/sources.list" <<'EOF'
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://security.debian.org/debian-security trixie-security main
EOF

# Preserve repository signatures and package checksums. Nothing is installed
# into /usr, and package maintainer scripts are not executed.
export APT_CONFIG="$APT_ROOT/apt.conf"
/usr/bin/apt-get update
DOWNLOAD_DIR="$(mktemp -d "$RUNTIME_ROOT/packages/install.XXXXXX")"
trap 'rm -rf "$DOWNLOAD_DIR"' EXIT
cd "$DOWNLOAD_DIR"
/usr/bin/apt-get download postgresql-17 postgresql-client-17 libpq5
for package in ./*.deb; do
    dpkg-deb --extract "$package" "$RUNTIME_ROOT/postgres"
done
if ldd "$PG_BIN/postgres" | rg -q 'not found'; then
    echo 'Faltam bibliotecas do sistema para executar PostgreSQL.' >&2
    exit 1
fi
"$PG_BIN/postgres" --version
