from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from prometheus_client import Counter, Histogram


_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")

HTTP_REQUESTS_TOTAL = Counter(
    "madhushala_bridge_http_requests_total",
    "Total HTTP requests handled by the bridge.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "madhushala_bridge_http_request_duration_seconds",
    "Bridge HTTP request duration.",
    ("method", "route"),
)
MADHUSHALA_HTTP_REQUESTS_TOTAL = Counter(
    "madhushala_bridge_upstream_requests_total",
    "Total requests sent to the Madhushala API.",
    ("method", "path", "status"),
)
MADHUSHALA_HTTP_DURATION_SECONDS = Histogram(
    "madhushala_bridge_upstream_request_duration_seconds",
    "Madhushala upstream request duration.",
    ("method", "path"),
)
CACHE_EVENTS_TOTAL = Counter(
    "madhushala_bridge_cache_events_total",
    "Cache events by backend/result.",
    ("backend", "result"),
)
PURCHASE_SAVE_DURATION_SECONDS = Histogram(
    "madhushala_bridge_purchase_save_duration_seconds",
    "End-to-end Madhushala purchase-save request duration.",
    ("status",),
)


def get_correlation_id() -> str:
    return _correlation_id.get() or "-"


def set_correlation_id(value: str | None = None) -> contextvars.Token[str]:
    clean = str(value or "").strip()
    if not clean:
        clean = uuid.uuid4().hex
    return _correlation_id.set(clean[:128])


def reset_correlation_id(token: contextvars.Token[str]) -> None:
    _correlation_id.reset(token)


def log_extra(**values: Any) -> dict[str, Any]:
    return {"correlation_id": get_correlation_id(), **values}


def _safe_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


class JsonFormatter(logging.Formatter):
    """One JSON object per line for production-friendly log ingestion."""

    _reserved = set(logging.makeLogRecord({}).__dict__.keys()) | {
        "message",
        "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlationId": getattr(record, "correlation_id", None) or get_correlation_id(),
        }
        for key, value in record.__dict__.items():
            if key in self._reserved or key.startswith("_"):
                continue
            if key in {"args", "msg"}:
                continue
            payload[key] = _safe_json_value(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def route_label(request: Any) -> str:
    route = request.scope.get("route") if hasattr(request, "scope") else None
    path = getattr(route, "path", None)
    return str(path or "unmatched")


def observe_http_request(method: str, route: str, status: int, duration_seconds: float) -> None:
    method = str(method or "").upper() or "UNKNOWN"
    route = str(route or "unmatched")
    status_text = str(int(status))
    HTTP_REQUESTS_TOTAL.labels(method=method, route=route, status=status_text).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method=method, route=route).observe(max(0.0, duration_seconds))


def observe_madhushala_request(method: str, path: str, status: int | str, duration_seconds: float) -> None:
    MADHUSHALA_HTTP_REQUESTS_TOTAL.labels(
        method=str(method or "").upper() or "UNKNOWN",
        path=str(path or "unknown"),
        status=str(status),
    ).inc()
    MADHUSHALA_HTTP_DURATION_SECONDS.labels(
        method=str(method or "").upper() or "UNKNOWN",
        path=str(path or "unknown"),
    ).observe(max(0.0, duration_seconds))


def observe_cache(backend: str, result: str) -> None:
    CACHE_EVENTS_TOTAL.labels(
        backend=str(backend or "unknown"),
        result=str(result or "unknown"),
    ).inc()


def observe_purchase_save(status: str, duration_seconds: float) -> None:
    PURCHASE_SAVE_DURATION_SECONDS.labels(
        status=str(status or "unknown").lower(),
    ).observe(max(0.0, duration_seconds))


def monotonic_seconds() -> float:
    return time.perf_counter()
