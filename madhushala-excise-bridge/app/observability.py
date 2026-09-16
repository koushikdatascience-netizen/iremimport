from __future__ import annotations

import contextvars
import logging
import secrets
from typing import Any


_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def current_request_id() -> str:
    return _request_id_var.get()


def bind_request_id(value: str | None = None) -> contextvars.Token[str]:
    request_id = str(value or "").strip() or f"imp-{secrets.token_hex(8)}"
    return _request_id_var.set(request_id[:128])


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id_var.reset(token)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Configure compact structured-enough logging without adding a log vendor dependency."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s",
    )
    request_filter = RequestIdFilter()
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(request_filter)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    suffix = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    logger.info("event=%s%s", event, f" {suffix}" if suffix else "")
