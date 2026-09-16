from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.db import conn, now_iso


class PurchaseTransactionService:
    """Persist purchase orchestration state and prevent same-job double submit.

    The process-local lock protects the current single-container deployment.
    The durable transaction row makes the state explicit and can later be
    moved behind PostgreSQL row locks / Redis distributed locks without
    changing the document-import orchestration contract.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _lock_for(self, job_id: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(job_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[job_id] = lock
            return lock

    @asynccontextmanager
    async def lock(self, job_id: str) -> AsyncIterator[None]:
        lock = await self._lock_for(job_id)
        async with lock:
            yield

    @staticmethod
    def payload_hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with conn() as db:
            row = db.execute(
                "SELECT * FROM purchase_transactions WHERE job_id=?",
                (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def events(self, job_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with conn() as db:
            rows = db.execute(
                """
                SELECT id, transaction_id, job_id, stage, status, duration_ms,
                       details_json, created_at
                FROM purchase_events
                WHERE job_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (job_id, max(1, min(int(limit), 500))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except Exception:
                item["details"] = {}
                item.pop("details_json", None)
            result.append(item)
        return result

    def ensure(
        self,
        *,
        job_id: str,
        shop_code: str,
        company_code: str,
        supplier_code: str = "",
        doc_no: str = "",
    ) -> dict[str, Any]:
        existing = self.get(job_id)
        if existing:
            return existing
        now = now_iso()
        tx_id = uuid.uuid4().hex
        with conn() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO purchase_transactions(
                    id, job_id, shop_code, company_code, supplier_code, doc_no,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tx_id, job_id, shop_code, company_code, supplier_code, doc_no, "PREPARING", now, now),
            )
        return self.get(job_id) or {
            "id": tx_id,
            "job_id": job_id,
            "shop_code": shop_code,
            "company_code": company_code,
            "supplier_code": supplier_code,
            "doc_no": doc_no,
            "status": "PREPARING",
        }

    def update(self, job_id: str, status: str, **fields: Any) -> dict[str, Any]:
        fields = dict(fields)
        fields["status"] = status
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{name}=?" for name in fields)
        values = list(fields.values()) + [job_id]
        with conn() as db:
            db.execute(f"UPDATE purchase_transactions SET {assignments} WHERE job_id=?", values)
        return self.get(job_id) or {}

    def record_event(
        self,
        transaction_id: str,
        job_id: str,
        stage: str,
        status: str,
        *,
        duration_ms: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with conn() as db:
            db.execute(
                """
                INSERT INTO purchase_events(
                    transaction_id, job_id, stage, status, duration_ms, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    job_id,
                    stage,
                    status,
                    duration_ms,
                    json.dumps(details or {}, ensure_ascii=False, default=str),
                    now_iso(),
                ),
            )

    @staticmethod
    def response_trn_no(response: Any) -> str:
        if not isinstance(response, dict):
            return ""
        candidates = [response]
        for key in ("data", "result", "purchase"):
            nested = response.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
        for candidate in candidates:
            for key in ("trnNo", "TrnNo", "transactionNo", "transactionNumber"):
                value = candidate.get(key)
                if value not in (None, ""):
                    return str(value).strip()
        return ""


purchase_transaction_service = PurchaseTransactionService()
