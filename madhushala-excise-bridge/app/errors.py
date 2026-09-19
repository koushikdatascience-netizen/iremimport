from __future__ import annotations

from typing import Any

from app.observability import get_correlation_id


def error_payload(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: Any | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": str(code or "INTERNAL_SERVER_ERROR"),
        "message": str(message or "Unexpected error"),
        "correlationId": get_correlation_id(),
        "retryable": bool(retryable),
    }
    if details is not None:
        error["details"] = details
    return {"error": error}


def normalize_http_detail(status_code: int, detail: Any) -> tuple[str, str, Any | None]:
    if isinstance(detail, dict):
        code = str(detail.get("code") or _default_code(status_code))
        message = str(detail.get("message") or detail.get("error") or "Request failed")
        details = detail.get("details")
        return code, message, details
    return _default_code(status_code), str(detail or "Request failed"), None


def _default_code(status_code: int) -> str:
    if status_code == 400:
        return "BAD_REQUEST"
    if status_code == 401:
        return "UNAUTHORIZED"
    if status_code == 403:
        return "FORBIDDEN"
    if status_code == 404:
        return "NOT_FOUND"
    if status_code == 409:
        return "CONFLICT"
    if status_code == 422:
        return "VALIDATION_ERROR"
    if status_code == 429:
        return "RATE_LIMITED"
    if 500 <= status_code < 600:
        return "UPSTREAM_OR_SERVER_ERROR"
    return f"HTTP_{status_code}"


def is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}
