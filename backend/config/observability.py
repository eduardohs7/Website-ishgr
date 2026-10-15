"""Request correlation and production logs with an explicit field allowlist."""
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import re
from time import perf_counter
from uuid import uuid4


current_request_id = ContextVar("chags_request_id", default=None)
http_logger = logging.getLogger("chags.http")
METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}


def valid_request_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def route_name(request):
    match = getattr(request, "resolver_match", None)
    name = match.view_name if match is not None else "unresolved"
    # The resolver name is static; never record path, query or captured arguments.
    return name if re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", name) else "unresolved"


class RequestDiagnosticsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Client/proxy supplied IDs are intentionally not trusted or echoed.
        request.request_id = uuid4().hex
        context = current_request_id.set(request.request_id)
        started = perf_counter()
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request.request_id
            status = response.status_code
            level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
            http_logger.log(level, "http_response", extra={
                "request_id": request.request_id,
                "http_method": request.method if request.method in METHODS else "OTHER",
                "http_route": route_name(request),
                "status_code": status,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            })
            return response
        finally:
            current_request_id.reset(context)


class SafeJSONFormatter(logging.Formatter):
    """Do not format messages, exception values, request data or arbitrary extras."""
    def format(self, record):
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": logging.getLevelName(record.levelno),
            "event": "server_event",
        }
        request = getattr(record, "request", None)
        request_id = getattr(record, "request_id", None)
        if not valid_request_id(request_id):
            request_id = getattr(request, "request_id", None) or current_request_id.get()
        if valid_request_id(request_id):
            data["request_id"] = request_id
        status = getattr(record, "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            data["status"] = status
        if record.name == "chags.http" and record.msg == "http_response":
            data["event"] = "http_response"
            data["method"] = record.http_method
            data["route"] = record.http_route
            data["duration_ms"] = record.duration_ms
        if record.exc_info and record.exc_info[1] is not None:
            data["exception_type"] = type(record.exc_info[1]).__name__
            frames = []
            traceback = record.exc_info[2]
            while traceback is not None:
                frame = traceback.tb_frame
                frames.append({
                    "module": frame.f_globals.get("__name__", "unknown"),
                    "function": frame.f_code.co_name,
                    "line": traceback.tb_lineno,
                })
                traceback = traceback.tb_next
            data["stack"] = frames[-8:]
        return json.dumps(data, ensure_ascii=True, separators=(",", ":"))


def production_logging():
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"safe_json": {"()": "config.observability.SafeJSONFormatter"}},
        "handlers": {"safe_console": {
            "class": "logging.StreamHandler", "stream": "ext://sys.stdout", "formatter": "safe_json",
        }},
        "root": {"handlers": ["safe_console"], "level": "WARNING"},
        "loggers": {
            "django": {"handlers": ["safe_console"], "level": "WARNING", "propagate": False},
            "django.server": {"handlers": ["safe_console"], "level": "INFO", "propagate": False},
            "chags.http": {"handlers": ["safe_console"], "level": "INFO", "propagate": False},
        },
    }
