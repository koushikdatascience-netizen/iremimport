import asyncio
import json
import logging
from contextlib import contextmanager

from app.errors import error_payload, normalize_http_detail
from app.observability import JsonFormatter, reset_correlation_id, set_correlation_id


def test_standard_error_envelope_contains_correlation_id():
    token = set_correlation_id("corr-123")
    try:
        payload = error_payload(
            "MADHUSHALA_AUTH_EXPIRED",
            "Login expired.",
            retryable=False,
        )
    finally:
        reset_correlation_id(token)

    assert payload == {
        "error": {
            "code": "MADHUSHALA_AUTH_EXPIRED",
            "message": "Login expired.",
            "correlationId": "corr-123",
            "retryable": False,
        }
    }


def test_normalize_http_detail_preserves_structured_code():
    code, message, details = normalize_http_detail(
        401,
        {
            "code": "MADHUSHALA_AUTH_EXPIRED",
            "message": "Login expired.",
            "details": {"action": "relaunch"},
        },
    )

    assert code == "MADHUSHALA_AUTH_EXPIRED"
    assert message == "Login expired."
    assert details == {"action": "relaunch"}


def test_json_formatter_emits_machine_readable_fields():
    token = set_correlation_id("corr-log")
    try:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="request completed",
            args=(),
            exc_info=None,
        )
        record.event = "http_request"
        record.durationMs = 42
        payload = json.loads(JsonFormatter().format(record))
    finally:
        reset_correlation_id(token)

    assert payload["level"] == "INFO"
    assert payload["message"] == "request completed"
    assert payload["correlationId"] == "corr-log"
    assert payload["event"] == "http_request"
    assert payload["durationMs"] == 42


def test_readiness_requires_database_and_configured_redis(monkeypatch):
    from app import main as main_module

    class FakeDb:
        def execute(self, _query):
            return self

        def fetchone(self):
            return {"ok": 1}

    @contextmanager
    def fake_conn():
        yield FakeDb()

    async def redis_ok():
        return True

    monkeypatch.setattr(main_module, "conn", fake_conn)
    monkeypatch.setattr(main_module.settings, "REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setattr(main_module.cache_service, "ping", redis_ok)

    ready, dependencies = asyncio.run(main_module._readiness_state())

    assert ready is True
    assert dependencies == {"database": "ok", "redis": "ok"}


def test_readiness_fails_when_redis_is_unavailable(monkeypatch):
    from app import main as main_module

    class FakeDb:
        def execute(self, _query):
            return self

        def fetchone(self):
            return {"ok": 1}

    @contextmanager
    def fake_conn():
        yield FakeDb()

    async def redis_failed():
        return False

    monkeypatch.setattr(main_module, "conn", fake_conn)
    monkeypatch.setattr(main_module.settings, "REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setattr(main_module.cache_service, "ping", redis_failed)

    ready, dependencies = asyncio.run(main_module._readiness_state())

    assert ready is False
    assert dependencies["database"] == "ok"
    assert dependencies["redis"] == "error"
