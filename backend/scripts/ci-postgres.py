"""Private, disposable PostgreSQL for CI; never uses production credentials."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "backend" / ".local" / "ci-postgres" / "state.json"
POSTGRES_IMAGE = "postgres:17.11@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3"
OWNER_LABEL = "org.chags14.backend-ci"


class CIError(Exception):
    pass


def docker(arguments, *, operation, input_text=None, environment=None):
    try:
        result = subprocess.run(
            ["docker", *arguments], input=input_text, env=environment,
            text=True, capture_output=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CIError(f"Docker indisponível ou sem resposta ao {operation}.") from None
    if result.returncode:
        # Never dump SQL, docker inspect, environment or credential-bearing logs.
        raise CIError(f"Falha no Docker ao {operation}.")
    return result.stdout.strip()


def stop():
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text())
    name, owner = state["container"], state["owner"]
    if not re.fullmatch(r"chags-backend-ci-[a-f0-9]{32}", name):
        raise CIError("Identificador do banco temporário inválido.")
    container = docker(
        ["container", "ls", "--all", "--filter", f"name=^/{name}$", "--format", "{{.ID}}"],
        operation="localizar o banco temporário",
    )
    if container:
        actual_owner = docker(
            ["inspect", "--format", '{{index .Config.Labels "' + OWNER_LABEL + '"}}', name],
            operation="verificar a identificação do banco temporário",
        )
        if actual_owner != owner:
            raise CIError("O contêiner não pertence a esta execução; limpeza recusada.")
        docker(["rm", "--force", "--volumes", name], operation="remover o banco temporário")
    STATE.unlink()


def start():
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    owner = uuid.uuid4().hex
    name = f"chags-backend-ci-{owner}"
    admin_password, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    is_github = os.environ.get("GITHUB_ACTIONS") == "true"
    if is_github:
        # GitHub consumes these control commands and masks both values in logs.
        print(f"::add-mask::{admin_password}", flush=True)
        print(f"::add-mask::{password}", flush=True)
    state = {"container": name, "owner": owner}
    try:
        fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise CIError("Já existe um banco de CI nesta pasta; execute stop antes de iniciar outro.") from None
    with os.fdopen(fd, "w") as stream:
        json.dump(state, stream)
    print("Preparando PostgreSQL temporário...", flush=True)
    try:
        docker(
            ["run", "--detach", "--name", name, "--label", f"{OWNER_LABEL}={owner}",
             "--publish", "127.0.0.1::5432", "--env", "POSTGRES_PASSWORD", POSTGRES_IMAGE],
            operation="iniciar o PostgreSQL temporário",
            environment={**os.environ, "POSTGRES_PASSWORD": admin_password},
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "postgres"],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise CIError("PostgreSQL temporário não ficou disponível dentro do prazo.")
        # The container's local admin creates a non-superuser database owner.
        # Generated passwords use only URL-safe characters; SQL isn't logged.
        docker(
            ["exec", "--interactive", name, "psql", "-X", "-U", "postgres", "-d", "postgres",
             "--set", "ON_ERROR_STOP=1", "--quiet"],
            operation="preparar o usuário e o banco de testes",
            input_text=(f"CREATE ROLE chags_ci LOGIN CREATEDB NOSUPERUSER NOCREATEROLE PASSWORD '{password}';\n"
                        "CREATE DATABASE chags_ci OWNER chags_ci;\n"),
        )
        published = docker(["port", name, "5432/tcp"], operation="consultar a porta do banco temporário")
        if not re.fullmatch(r"127\.0\.0\.1:\d+", published):
            raise CIError("O banco temporário não está restrito ao endereço local.")
        connection = {
            "DJANGO_SETTINGS_MODULE": "config.settings.development",
            "CHAGS_DB_HOST": "127.0.0.1", "CHAGS_DB_PORT": published.rsplit(":", 1)[1],
            "CHAGS_DB_NAME": "chags_ci", "CHAGS_DB_USER": "chags_ci", "CHAGS_DB_PASSWORD": password,
        }
        state["environment"] = connection
        with STATE.open("w") as stream:
            json.dump(state, stream)
        if is_github:
            with open(os.environ["GITHUB_ENV"], "a") as stream:
                for key, value in connection.items():
                    stream.write(f"{key}={value}\n")
    except BaseException:
        try:
            stop()
        except CIError:
            pass  # The workflow's always() cleanup makes another attempt.
        raise
    print("PostgreSQL pronto; usuário de testes sem privilégios de superusuário.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    args = parser.parse_args()
    try:
        start() if args.action == "start" else stop()
    except (CIError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        # Unanticipated environment errors are reported without their values.
        message = str(error) if isinstance(error, CIError) else "Falha na configuração do banco temporário."
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
