from __future__ import annotations

import contextvars
import uuid
from typing import Any


_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")


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
