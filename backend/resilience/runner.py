"""Bounded real-HTTP checks against a runner-owned disposable environment."""
import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import dataclass
import hashlib
from http.cookiejar import CookieJar
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from threading import Barrier
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request
import uuid


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

SETTINGS = '''from config.settings.development import *
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DATABASES["default"]["CONN_MAX_AGE"] = 60
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
'''

# Uses the existing label-checked helper, with a unique private state per run.
POSTGRES = '''import importlib.util, json, os, re, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("ci_postgres", "backend/scripts/ci-postgres.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
helper.STATE = Path(os.environ["CHAGS_RESILIENCE_DIR"]) / "postgres.json"
action = sys.argv[1]
if action in ("start", "stop"):
    getattr(helper, action)()
else:
    state = json.loads(helper.STATE.read_text())
    name, owner = state["container"], state["owner"]
    assert re.fullmatch(r"chags-backend-ci-[a-f0-9]{32}", name)
    actual = helper.docker(["inspect", "--format", '{{index .Config.Labels "' + helper.OWNER_LABEL + '"}}', name], operation="verificar propriedade")
    assert actual == owner
    commands = {
        "deny": "ALTER ROLE chags_ci NOLOGIN; SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'chags_ci';",
        "allow": "ALTER ROLE chags_ci LOGIN;",
    }
    helper.docker(["exec", "--interactive", name, "psql", "-X", "-U", "postgres", "-d", "postgres", "--set", "ON_ERROR_STOP=1", "--quiet"], operation="simular/restaurar acesso", input_text=commands[action])
'''


def require(condition):
    if not condition:
        raise RuntimeError("Verificação de resistência não atendida.")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


@dataclass
class Sample:
    status: int
    data: dict | None
    milliseconds: float
    retry_after: str | None


class HTTPClient:
    def __init__(self, origin):
        parsed = urlsplit(origin)
        require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port is not None)
        require(not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment))
        self.origin = origin
        self.cookies = CookieJar()
        # Bypass inherited HTTP proxies and never follow redirects to other sites.
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies), NoRedirect())
        self.csrf = ""

    def clone(self):
        client = HTTPClient(self.origin)
        for cookie in self.cookies:
            client.cookies.set_cookie(copy.copy(cookie))
        client.csrf = self.csrf
        return client

    def request(self, path, data=None):
        require(path.startswith("/") and not path.startswith("//") and "://" not in path)
        headers = {"Accept": "application/json"}
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers.update({"Content-Type": "application/json", "X-CSRFToken": self.csrf})
        request = Request(self.origin + path, data=body, headers=headers)
        started = time.perf_counter()
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(256 * 1024 + 1)
            require(len(raw) <= 256 * 1024)
            elapsed = (time.perf_counter() - started) * 1000
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                payload = None
            return Sample(response.status, payload, elapsed, response.headers.get("Retry-After"))

    def bootstrap(self):
        response = self.request("/api/v1/auth/session/")
        require(response.status == 200 and response.data["data"]["authenticated"] is False)
        self.csrf = response.data["data"]["csrf_token"]

    def login(self, email, password):
        self.bootstrap()
        response = self.request("/api/v1/auth/login/", {"email": email, "password": password})
        if response.status == 200:
            self.csrf = response.data["data"]["csrf_token"]
        return response


def summarize(samples, seconds, concurrency):
    require(bool(samples) and seconds > 0)
    values = sorted(sample.milliseconds for sample in samples)
    return {
        "requests": len(samples), "concurrency": concurrency,
        "statuses": dict(sorted(Counter(str(sample.status) for sample in samples).items())),
        "elapsed_seconds": round(seconds, 3), "requests_per_second": round(len(samples) / seconds, 2),
        "p50_ms": round(values[math.ceil(len(values) * .50)-1], 3),
        "p95_ms": round(values[math.ceil(len(values) * .95)-1], 3), "max_ms": round(values[-1], 3),
    }


def child_environment(directory):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CHAGS_", "PG")) and key not in (
               "DJANGO_SETTINGS_MODULE", "PYTHONPATH", "PYTHONOPTIMIZE", "GUNICORN_CMD_ARGS",
               "GITHUB_ACTIONS", "GITHUB_ENV",
           )}
    env.update(
        CHAGS_RESILIENCE_DIR=str(directory), DJANGO_SETTINGS_MODULE="resilience_settings",
        PYTHONPATH=os.pathsep.join((str(directory), str(BACKEND))),
        CHAGS_SECRET_KEY=secrets.token_urlsafe(64),
        CHAGS_MFA_ENCRYPTION_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    )
    return env


def source_fingerprint():
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(BACKEND):
        dirs[:] = sorted(name for name in dirs if name not in (".local", "__pycache__"))
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix == ".py" or name in ("requirements.in", "requirements.txt", "requirements-windows.txt"):
                digest.update(str(path.relative_to(BACKEND)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


class ResilienceRun:
    def __init__(self, directory, concurrency, duration):
        self.directory = directory
        self.env = child_environment(directory)
        self.concurrency, self.duration = concurrency, duration
        self.server = None
        self.stage = "preparation"
        self.report = {
            "schema": 1, "status": "failed", "source_sha256": source_fingerprint(),
            "environment": {"python": platform.python_version(), "django": version("Django"),
                            "gunicorn": version("gunicorn"),
                            "gunicorn_workers": 2, "gunicorn_threads_per_worker": 4,
                            "persistent_connections_seconds": 60, "connection_health_checks": True,
                            "cpu_count_visible": os.cpu_count(), "read_seconds_per_level": duration},
            "phases": {}, "cleanup": False,
        }

    def command(self, arguments, *, code=None, timeout=180):
        result = subprocess.run([sys.executable, *arguments], input=code, env=self.env, cwd=ROOT,
                                text=True, capture_output=True, timeout=timeout)
        require(result.returncode == 0)
        return result.stdout

    def postgres(self, action):
        return self.command(["-", action], code=POSTGRES)

    def fixture(self, function, count=None):
        expression = f"{function}({count})" if count is not None else f"{function}()"
        return self.command(["-c", "import django; django.setup(); from resilience.fixtures import "
                             + function + "; import json; value=" + expression + "; print(json.dumps(value))"])

    def phase(self, name, count, workers, callback):
        self.stage = name
        workers = min(count, workers)
        barrier = Barrier(workers, timeout=15)

        def run(index):
            if index < workers:
                barrier.wait()
            return callback(index)
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            batches = list(executor.map(run, range(count)))
        samples = [sample for batch in batches for sample in batch]
        self.report["phases"][name] = summarize(samples, time.perf_counter()-started, workers)
        return samples

    def setup(self):
        (self.directory / "resilience_settings.py").write_text(SETTINGS)
        self.stage = "database_start"
        self.postgres("start")
        state = json.loads((self.directory / "postgres.json").read_text())
        self.env.update(state["environment"])
        self.env["DJANGO_SETTINGS_MODULE"] = "resilience_settings"
        self.stage = "migrations"
        self.command(["backend/manage.py", "migrate", "--noinput"])
        self.stage = "seed"
        self.report["environment"].update(json.loads(self.fixture("seed")))
        self.credentials = json.loads((self.directory / "fixtures.json").read_text())
        self.stage = "http_server_start"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            self.origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
            self.server = subprocess.Popen(
                [sys.executable, "-m", "gunicorn", "config.wsgi:application", "--bind", f"fd://{listener.fileno()}",
                 "--workers", "2", "--worker-class", "gthread", "--threads", "4", "--timeout", "30"],
                cwd=BACKEND, env=self.env, pass_fds=(listener.fileno(),), start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            require(self.server.poll() is None)
            try:
                if HTTPClient(self.origin).request("/health/ready/").status == 200:
                    return
            except OSError:
                pass
            time.sleep(.1)
        require(False)

    def exercise(self):
        credentials = self.credentials
        clients = []
        self.stage = "initial_login"
        for email in credentials["participants"]:
            client = HTTPClient(self.origin)
            require(client.login(email, credentials["password"]).status == 200)
            clients.append(client)

        def signup(_):
            client = HTTPClient(self.origin)
            client.bootstrap()
            sample = client.request("/api/v1/auth/signup/", {
                "first_name": "Carga", "last_name": "Sintética", "email": "duplicate@example.invalid",
                "password1": credentials["password"], "password2": credentials["password"], "language": "pt-br",
            })
            require(sample.status in (202, 429))
            if sample.status == 429:
                require(int(sample.retry_after) > 0 and sample.data["error"]["code"] == "rate_limited")
            return [sample]
        samples = self.phase("duplicate_signup", 4, self.concurrency, signup)
        require(Counter(sample.status for sample in samples) == {202: 3, 429: 1})

        def login(_):
            sample = HTTPClient(self.origin).login(credentials["participants"][0], credentials["password"])
            require(sample.status in (200, 429))
            if sample.status == 429:
                require(int(sample.retry_after) > 0 and sample.data["error"]["code"] == "rate_limited")
            return [sample]
        samples = self.phase("login_rate_limit", 12, self.concurrency, login)
        require(Counter(sample.status for sample in samples) == {200: 9, 429: 3})

        payload = {"price_id": credentials["price_id"]}

        def register(index):
            sample = clients[index % 4].clone().request("/api/v1/participant/registration/", payload)
            require(sample.status in (200, 201))
            return [sample]
        samples = self.phase("duplicate_registration", 16, self.concurrency, register)
        require(Counter(sample.status for sample in samples) == {201: 4, 200: 12})
        require(len({sample.data["data"]["registration"]["id"] for sample in samples}) == 4)

        def reads(index):
            client = clients[index % 4].clone()
            samples = []
            deadline = time.monotonic() + self.duration
            while time.monotonic() < deadline:
                for path in ("event/", "prices/", "registration/"):
                    sample = client.request("/api/v1/participant/" + path)
                    require(sample.status == 200 and sample.data["ok"] is True)
                    samples.append(sample)
            return samples
        for concurrency in (1, self.concurrency):
            self.phase(f"participant_reads_{concurrency}", concurrency, concurrency, reads)
        self.stage = "integrity_before_outage"
        self.report["integrity_before_outage"] = json.loads(self.fixture("verify", 4))

        self.stage = "database_access_loss"
        self.postgres("deny")
        try:
            for client in (HTTPClient(self.origin), clients[0].clone()):
                require(client.request("/health/live/").status == 200)
                require(client.request("/health/ready/").status == 503)
            blocked = clients[4].clone().request("/api/v1/participant/registration/", payload)
            require(blocked.status in (500, 503))
            self.report["outage"] = {"anonymous_liveness": 200, "authenticated_liveness": 200,
                                     "anonymous_readiness": 503, "authenticated_readiness": 503,
                                     "blocked_registration_status": blocked.status}
        finally:
            self.postgres("allow")

        self.stage = "database_recovery"
        started = time.perf_counter()
        deadline = time.monotonic() + 15
        while HTTPClient(self.origin).request("/health/ready/").status != 200:
            require(time.monotonic() < deadline)
            time.sleep(.1)
        require(clients[4].request("/api/v1/participant/registration/").data["data"]["registration"] is None)
        self.report["recovery_ready_and_session_ms"] = round((time.perf_counter()-started)*1000, 3)
        for _ in range(2):
            for client in clients:
                require(client.clone().request("/api/v1/participant/profile/").status == 200)
        sample = clients[4].request("/api/v1/participant/registration/", payload)
        require(sample.status == 201)
        again = clients[4].request("/api/v1/participant/registration/", payload)
        require(again.status == 200 and again.data["data"]["registration"]["id"] == sample.data["data"]["registration"]["id"])
        self.report["integrity_after_recovery"] = json.loads(self.fixture("verify", 5))
        self.report["status"] = "passed"

    def cleanup(self):
        if self.server is not None:
            try:
                os.killpg(self.server.pid, signal.SIGTERM)
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.server.pid, signal.SIGKILL)
                self.server.wait(timeout=5)
            except ProcessLookupError:
                self.server.wait(timeout=5)
        self.postgres("stop")
        self.report["cleanup"] = True


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, choices=(2, 4, 8), default=4)
    parser.add_argument("--duration", type=int, choices=range(5, 31), default=10,
                        help="segundos de leitura por nível de concorrência (5 a 30)")
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    if sys.platform != "linux" or not __debug__:
        print("O ensaio exige Linux com Docker e Python sem -O; consulte o guia de resistência.", file=sys.stderr)
        return 2
    root = BACKEND / ".local" / "resilience"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    reports = BACKEND / ".local" / "resilience-reports"
    reports.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_path = args.report or reports / f"{uuid.uuid4().hex}.json"
    try:
        descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        print("Escolha um relatório novo em um diretório existente; arquivos não são sobrescritos.", file=sys.stderr)
        return 2
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    with os.fdopen(descriptor, "w") as output:
        run = ResilienceRun(directory, args.concurrency, args.duration)
        try:
            print("Executando HTTP real e recuperação em ambiente descartável...", flush=True)
            run.setup()
            run.exercise()
        except (Exception, KeyboardInterrupt) as error:
            run.report.update(status="failed", failed_stage=run.stage, error_type=type(error).__name__)
        finally:
            try:
                run.cleanup()
            except Exception:
                run.report.update(status="failed", cleanup=False)
            if run.report["cleanup"]:
                shutil.rmtree(directory)
            else:
                # Retain ownership metadata for safe manual cleanup after a Docker failure.
                (directory / "fixtures.json").unlink(missing_ok=True)
                run.report["cleanup_state_directory"] = directory.name
        json.dump(run.report, output, indent=2, sort_keys=True)
        print(json.dumps(run.report, indent=2, sort_keys=True))
        signal.signal(signal.SIGTERM, previous_handler)
        return 0 if run.report["status"] == "passed" and run.report["cleanup"] else 1
