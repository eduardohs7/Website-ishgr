"""Private PostgreSQL backups and restore drills; no restore into an existing DB."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

from django.conf import settings
from django.core.management.base import CommandError
from django.db import connections
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


FORMAT = 1
LIBPQ_OPTIONS = {
    "connect_timeout", "sslmode", "sslrootcert", "sslcert", "sslkey", "sslcrl",
    "sslcrldir", "ssl_min_protocol_version", "ssl_max_protocol_version",
}


def connection_parameters(alias):
    config = connections[alias].settings_dict
    if config["ENGINE"] != "django.db.backends.postgresql":
        raise CommandError("Estes comandos exigem PostgreSQL.")
    options = config.get("OPTIONS", {})
    if set(options) - LIBPQ_OPTIONS:
        raise CommandError("A conexão possui OPTIONS não suportadas pelos comandos de backup.")
    return {**options, **{key: config[field] for key, field in (
        ("dbname", "NAME"), ("user", "USER"), ("password", "PASSWORD"),
        ("host", "HOST"), ("port", "PORT"),
    ) if config.get(field)}}


def tools_directory(directory=None):
    directory = directory or os.environ.get("CHAGS_PG_BIN")
    if not directory:
        executable = shutil.which("pg_dump")
        directory = (Path(executable).parent if executable else
                     settings.BASE_DIR.parent.parent / ".chags-environment/postgres/usr/lib/postgresql/17/bin")
    suffix = ".exe" if os.name == "nt" else ""
    tools = {name: Path(directory) / (name + suffix) for name in ("pg_dump", "pg_restore")}
    if not all(path.is_file() for path in tools.values()):
        raise CommandError("Configure --pg-bin com o diretório de pg_dump e pg_restore.")
    return tools


def run_client(tool, arguments, environment=None):
    try:
        result = subprocess.run(
            [str(tool), *arguments], env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CommandError("Cliente PostgreSQL indisponível ou fora do prazo de 30 minutos.") from None
    if result.returncode:
        # Client errors may contain connection details or database contents.
        raise CommandError(f"{tool.stem} falhou (código {result.returncode}); confira acesso, espaço e versão.")
    return result.stdout


def client_major(tool):
    match = re.search(rb"PostgreSQL\) (\d+)\.", run_client(tool, ["--version"]))
    if not match:
        raise CommandError("Não foi possível identificar a versão do cliente PostgreSQL.")
    return int(match[1])


@contextmanager
def client_connection(parameters):
    # Avoid inherited PG* variables changing the host, credentials or TLS policy.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    public = {key: value for key, value in parameters.items() if key != "password"}
    with tempfile.TemporaryDirectory(prefix="chags-pg-auth-") as directory:
        passfile = Path(directory) / "pgpass"
        password = parameters.get("password", "")
        if any(character in password for character in "\r\n"):
            raise CommandError("A senha do banco contém caracteres incompatíveis com pgpass.")
        escaped = password.replace("\\", "\\\\").replace(":", "\\:")
        fd = os.open(passfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(f"*:*:*:*:{escaped}\n")
        environment["PGPASSFILE"] = str(passfile)
        yield make_conninfo(**public), environment


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def database_inventory(connection):
    """Counts and schema guards only; never puts participant data in the manifest."""
    tables = connection.execute("""
        SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename
    """).fetchall()
    counts = {name: connection.execute(
        sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier("public"), sql.Identifier(name))
    ).fetchone()[0] for (name,) in tables}
    guards = connection.execute("""
        SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid)
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND NOT t.tgisinternal ORDER BY c.relname, t.tgname
    """).fetchall()
    constraints = connection.execute("""
        SELECT c.relname, k.conname, k.contype, k.convalidated, k.condeferrable, k.condeferred
        FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' ORDER BY c.relname, k.conname
    """).fetchall()
    return {"tables": counts, "triggers": [list(row) for row in guards],
            "constraints": [list(row) for row in constraints]}


def create_backup(*, output_dir, alias="default", pg_bin=None):
    parameters, tools = connection_parameters(alias), tools_directory(pg_bin)
    parent = Path(output_dir).resolve()
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = "chags-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex
    bundle = parent / name
    bundle.mkdir(mode=0o700)
    try:
        dump = bundle / "database.dump"
        fd = os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with psycopg.connect(**parameters) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            major = connection.info.server_version // 10000
            if client_major(tools["pg_dump"]) != major:
                raise CommandError("Use pg_dump da mesma versão principal do servidor.")
            snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
            inventory = database_inventory(connection)
            with client_connection(parameters) as (connection_string, environment):
                run_client(tools["pg_dump"], [
                    "--dbname=" + connection_string, "--no-password", "--format=custom",
                    "--no-owner", "--no-privileges", "--snapshot=" + snapshot, "--file=" + str(dump),
                ], environment)
        manifest = {"format": FORMAT, "postgres_major": major,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": sha256(dump), "inventory": inventory}
        fd = os.open(bundle / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        shutil.rmtree(bundle)
        raise
    return bundle


def load_backup(bundle):
    bundle = Path(bundle).resolve()
    manifest_path, dump = bundle / "manifest.json", bundle / "database.dump"
    if not manifest_path.is_file() or not dump.is_file() or manifest_path.stat().st_size > 8 * 1024 * 1024:
        raise CommandError("Backup incompleto ou manifesto inválido.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        inventory = manifest["inventory"]
        valid = (isinstance(manifest, dict) and type(manifest["format"]) is int and manifest["format"] == FORMAT
                 and type(manifest["postgres_major"]) is int
                 and isinstance(inventory, dict)
                 and set(inventory) == {"tables", "triggers", "constraints"}
                 and isinstance(inventory["tables"], dict)
                 and all(isinstance(name, str) and type(count) is int and count >= 0
                         for name, count in inventory["tables"].items())
                 and isinstance(inventory["triggers"], list)
                 and isinstance(inventory["constraints"], list)
                 and re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"]))
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise CommandError("Manifesto de backup inválido ou de formato não suportado.")
    if sha256(dump) != manifest["sha256"]:
        raise CommandError("Checksum divergente: o backup foi alterado ou está incompleto.")
    return manifest, dump


def verify_backup(*, bundle, alias="default", pg_bin=None):
    manifest, dump = load_backup(bundle)
    parameters, tools = connection_parameters(alias), tools_directory(pg_bin)
    if client_major(tools["pg_restore"]) != manifest["postgres_major"]:
        raise CommandError("Use pg_restore da mesma versão principal registrada no backup.")
    name, created = "chags_restore_" + uuid.uuid4().hex, False
    # Always creates a new database. No --target, --clean or existing DB restore.
    with psycopg.connect(**{**parameters, "dbname": "postgres"}, autocommit=True) as admin:
        try:
            if admin.info.server_version // 10000 != manifest["postgres_major"]:
                raise CommandError("O servidor de verificação deve ter a mesma versão principal do backup.")
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
            created = True
            target = {**parameters, "dbname": name}
            with client_connection(target) as (connection_string, environment):
                run_client(tools["pg_restore"], [
                    "--dbname=" + connection_string, "--no-password", "--no-owner",
                    "--no-privileges", "--exit-on-error", "--single-transaction", str(dump),
                ], environment)
            with psycopg.connect(**target) as restored:
                restored.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                if database_inventory(restored) != manifest["inventory"]:
                    raise CommandError("A restauração divergiu nas contagens, triggers ou constraints.")
        finally:
            if created:
                try:
                    admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
                except psycopg.Error:
                    raise CommandError("Falha ao remover o banco temporário " + name + "; solicite limpeza ao DBA.") from None
    return len(manifest["inventory"]["tables"])
