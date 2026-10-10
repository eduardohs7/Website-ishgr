#!/usr/bin/env bash
# GNU gettext for this Debian 13 cloud environment, without a system install.
set -euo pipefail
if command -v msgfmt >/dev/null && command -v xgettext >/dev/null; then
    exit 0
fi
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_ROOT="${CHAGS_RUNTIME_DIR:-$(dirname "$REPO_ROOT")/.chags-environment}"
GETTEXT_ROOT="$RUNTIME_ROOT/gettext"
if [[ -x "$GETTEXT_ROOT/bin/msgfmt" && -x "$GETTEXT_ROOT/bin/xgettext" ]]; then
    "$GETTEXT_ROOT/bin/msgfmt" --version
    exit 0
fi
source /etc/os-release
if [[ "$ID" != debian || "$VERSION_ID" != 13 ]]; then
    echo 'Instale GNU gettext pelo gerenciador confiável do sistema.' >&2
    exit 1
fi
APT_ROOT="$RUNTIME_ROOT/gettext-apt"
mkdir -p "$APT_ROOT/lists/partial" "$APT_ROOT/cache/archives/partial" "$APT_ROOT/parts" "$GETTEXT_ROOT/bin"
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
export APT_CONFIG="$APT_ROOT/apt.conf"
apt-get update
DOWNLOAD_DIR="$(mktemp -d "$APT_ROOT/download.XXXXXX")"
trap 'rm -rf "$DOWNLOAD_DIR"' EXIT
cd "$DOWNLOAD_DIR"
apt-get download gettext gettext-base
for package in ./*.deb; do
    dpkg-deb --extract "$package" "$GETTEXT_ROOT"
done
for tool in msgfmt xgettext msgmerge msguniq; do
    cat > "$GETTEXT_ROOT/bin/$tool" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec /lib64/ld-linux-x86-64.so.2 --library-path "$GETTEXT_ROOT/usr/lib/x86_64-linux-gnu" "$GETTEXT_ROOT/usr/bin/$tool" "\$@"
EOF
    chmod 755 "$GETTEXT_ROOT/bin/$tool"
done
"$GETTEXT_ROOT/bin/msgfmt" --version
