"""Phase 2 mapping workflow service."""
from __future__ import annotations

import json
import os
import re
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.db import conn
from app.integrations.madhushala.client import MadhushalaApiError, MadhushalaClient
from app.services.matching_service import suggest_matches

logger = logging.getLogger("madhushala-excise-bridge")


class MappingService:
    def __init__(self):
        self.state_path = os.path.join(settings.MAPPINGS_DIR, "mapping_state.json")
        self.state: dict[str, Any] = {"imports": {}, "mappings": {}}
        self.last_auto_status: dict[str, Any] = {
            "state": "idle",
            "message": "Waiting for capture.",
            "mappingRequired": False,
            "unmappedCount": 0,
            "preparedCount": 0,
            "lastError": None,
            "updatedAt": None,
        }

    async def initialize(self) -> None:
        os.makedirs(settings.MAPPINGS_DIR, exist_ok=True)
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as handle:
                self.state = json.load(handle)

    def _save_state(self) -> None:
        os.makedirs(settings.MAPPINGS_DIR, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=2, ensure_ascii=False)

    def _set_auto_status(self, **updates: Any) -> None:
        self.last_auto_status.update(updates)
        self.last_auto_status["updatedAt"] = datetime.now(timezone.utc).isoformat()

    def get_auto_status(self) -> dict[str, Any]:
        return dict(self.last_auto_status)

    def _client(self, token: str) -> MadhushalaClient:
        return MadhushalaClient(settings.MADHUSHALA_BASE_URL, settings.MADHUSHALA_SHOP_CODE, token)

    def _client_for_session(self, session: dict[str, Any]) -> MadhushalaClient:
        token = session.get("madhushala_token") or settings.MADHUSHALA_SERVICE_TOKEN
        return MadhushalaClient(settings.MADHUSHALA_BASE_URL, session["shop_code"], token)

    @staticmethod
    def _normalize_excise_name(value: str) -> str:
        text = str(value or "").casefold().strip()
        text = re.sub(r"\bml\b", " ml ", text, flags=re.IGNORECASE)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _unmapped_indexes(cls, items: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        exact: dict[str, dict[str, Any]] = {}
        normalized: dict[str, dict[str, Any]] = {}
        for item in items:
            name = str(item.get("itemName") or "").strip()
            if not name:
                continue
            exact.setdefault(name, item)
            key = cls._normalize_excise_name(name)
            if key:
                normalized.setdefault(key, item)
        return exact, normalized

    @classmethod
    def _find_existing_excise(cls, payload: dict[str, str], exact: dict[str, dict[str, Any]], normalized: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        name = str(payload.get("itemName") or "").strip()
        return exact.get(name) or normalized.get(cls._normalize_excise_name(name))

    @staticmethod
    def _is_duplicate_error(error: Exception) -> bool:
        if not isinstance(error, MadhushalaApiError) or error.status_code != 400:
            return False
        message = str(error).casefold()
        return any(marker in message for marker in ("already saved", "already exists", "duplicate"))

    async def _create_or_reuse_excise_item(
        self,
        client: MadhushalaClient,
        payload: dict[str, str],
        unmapped: list[dict[str, Any]],
    ) -> tuple[str | int | None, str, list[dict[str, Any]]]:
        exact, normalized = self._unmapped_indexes(unmapped)
        existing = self._find_existing_excise(payload, exact, normalized)
        if existing:
            code = existing.get("exciseItemCode")
            logger.info("Excise item reused existing code=%s item=%s", code, payload.get("itemName"))
            return code, "reused_existing", unmapped

        try:
            response = await client.save_excise_item(payload)
            code = response.get("exciseItemCode") or response.get("itemCode")
            logger.info("Excise item created code=%s item=%s", code, payload.get("itemName"))
            if code is not None:
                unmapped = [*unmapped, {"exciseItemCode": code, "itemName": response.get("itemName") or payload.get("itemName")} ]
            return code, "created", unmapped
        except MadhushalaApiError as exc:
            if not self._is_duplicate_error(exc):
                raise
            refreshed = await client.get_unmapped_items()
            exact, normalized = self._unmapped_indexes(refreshed)
            existing = self._find_existing_excise(payload, exact, normalized)
            if existing:
                code = existing.get("exciseItemCode")
                logger.info("Excise duplicate recovered code=%s item=%s", code, payload.get("itemName"))
                return code, "duplicate_recovered", refreshed
            logger.warning("Excise duplicate unresolved item=%s", payload.get("itemName"))
            return None, "review_required", refreshed

    @staticmethod
    def _payload_text(item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    @classmethod
    def build_excise_payload(cls, item: dict[str, Any]) -> dict[str, str]:
        if item.get("rawName"):
            item_name = cls._payload_text(item, "rawName", "brand")
            measure_ml = cls._payload_text(item, "measureMl", "ml")
            mrp_per_unit = cls._payload_text(item, "mrpPerUnit", "mrp", "rate")
            bottles_per_case = cls._payload_text(item, "bottlesPerCase", "packing")
            package_type = cls._payload_text(item, "packageType")
        else:
            item_name = cls._payload_text(item, "itemName") or f"{item['brand']}, {item['measureMl']} Ml. ({item['packageType']})"
            measure_ml = cls._payload_text(item, "measureMl", "ml")
            mrp_per_unit = cls._payload_text(item, "mrpPerUnit", "mrp", "rate")
            bottles_per_case = cls._payload_text(item, "bottlesPerCase", "packing")
            package_type = cls._payload_text(item, "packageType")

        return {
            "itemName": item_name,
            "t1": "",
            "t2": "",
            "t3": "",
            "t4": "",
            "strengthRaw": cls._payload_text(item, "strengthRaw"),
            "measureMl": measure_ml,
            "packageType": package_type,
            "retailerMargin": cls._payload_text(item, "retailerMargin"),
            "roundOffGovt": cls._payload_text(item, "roundOffGovt"),
            "specialPurposeFee": cls._payload_text(item, "specialPurposeFee"),
            "mrpPerUnit": mrp_per_unit,
            "bottlesPerCase": bottles_per_case,
            "mrpPerCase": cls._payload_text(item, "mrpPerCase"),
        }

    def find_import_by_excise_code(self, excise_item_code: int | str) -> dict[str, Any] | None:
        code = str(excise_item_code)
        for imported in self.state.get("imports", {}).values():
            if str(imported.get("exciseItemCode")) == code:
                return imported
        return None

    async def prepare_latest_capture(self, capture: dict[str, Any], token: str) -> dict[str, Any]:
        client = self._client(token)
        unmapped = await client.get_unmapped_items()

        prepared = []
        latest_capture_keys = []
        latest_unmapped_codes = []
        latest_unmapped_names = []
        latest_created_codes = []
        batch_id = capture.get("batchId")
        for item in capture.get("items", []):
            canonical_key = item["canonicalKey"]
            latest_capture_keys.append(canonical_key)
            payload = self.build_excise_payload(item)
            latest_unmapped_names.append(payload["itemName"])
            imported = self.state["imports"].get(canonical_key)
            prepare_action = "known"

            if not imported:
                excise_item_code, prepare_action, unmapped = await self._create_or_reuse_excise_item(client, payload, unmapped)
                response = {
                    "itemCode": excise_item_code,
                    "itemName": payload["itemName"],
                    "t1": payload["t1"],
                    "t2": payload["t2"],
                    "t3": payload["t3"],
                    "t4": payload["t4"],
                }

                imported = {
                    "canonicalKey": canonical_key,
                    "exciseItemCode": response.get("itemCode"),
                    "itemName": response.get("itemName") or payload["itemName"],
                    "payload": payload,
                    "capturedItem": item,
                    "savedAt": datetime.now(timezone.utc).isoformat(),
                }
                self.state["imports"][canonical_key] = imported
            else:
                imported["capturedItem"] = item

            imported["lastSeenBatchId"] = batch_id
            imported["lastSeenAt"] = datetime.now(timezone.utc).isoformat()
            imported["lastPrepareAction"] = prepare_action
            if imported.get("exciseItemCode") is not None:
                latest_unmapped_codes.append(str(imported["exciseItemCode"]))
            if prepare_action == "created" and imported.get("exciseItemCode") is not None:
                latest_created_codes.append(str(imported["exciseItemCode"]))

            prepared.append(imported)

        self.state["latestBatchId"] = batch_id
        self.state["latestCaptureKeys"] = latest_capture_keys
        self.state["latestUnmappedExciseCodes"] = latest_unmapped_codes
        self.state["latestUnmappedItemNames"] = latest_unmapped_names
        self.state["latestCreatedExciseCodes"] = latest_created_codes
        self._save_state()
        return {"preparedCount": len(prepared), "createdCount": len(latest_created_codes), "items": prepared}

    def _latest_unmapped_scope(self, capture: dict[str, Any] | None = None) -> tuple[set[str], set[str], bool]:
        codes = {str(code) for code in self.state.get("latestUnmappedExciseCodes", [])}
        names = {str(name) for name in self.state.get("latestUnmappedItemNames", [])}

        if capture:
            for item in capture.get("items", []):
                key = item.get("canonicalKey")
                imported = self.state.get("imports", {}).get(str(key))
                if imported and imported.get("exciseItemCode") is not None:
                    codes.add(str(imported["exciseItemCode"]))
                try:
                    names.add(self.build_excise_payload(item)["itemName"])
                except KeyError:
                    continue

        return codes, names, bool(codes or names or capture)

    async def workspace(self, token: str, capture: dict[str, Any] | None = None, latest_only: bool = True) -> dict[str, Any]:
        client = self._client(token)
        unmapped = await client.get_unmapped_items()
        madhushala_items = await client.get_dropdown_items(settings.DEFAULT_COMPANY_CODE, settings.DEFAULT_BILL_TYPE)
        rows = []
        latest_codes, latest_names, latest_scope_active = self._latest_unmapped_scope(capture) if latest_only else (set(), set(), False)

        for unmapped_item in unmapped:
            excise_code = str(unmapped_item.get("exciseItemCode"))
            item_name = str(unmapped_item.get("itemName", ""))
            if latest_scope_active and excise_code not in latest_codes and item_name not in latest_names:
                continue

            imported = self.find_import_by_excise_code(unmapped_item.get("exciseItemCode"))
            excise_context = dict(unmapped_item)
            if imported:
                excise_context.update(imported.get("capturedItem") or {})
                excise_context["canonicalKey"] = imported.get("canonicalKey")

            suggestions = suggest_matches(excise_context, madhushala_items)
            rows.append(
                {
                    "exciseItemCode": unmapped_item.get("exciseItemCode"),
                    "itemName": unmapped_item.get("itemName"),
                    "canonicalKey": excise_context.get("canonicalKey"),
                    "capturedItem": imported.get("capturedItem") if imported else None,
                    "suggestions": suggestions,
                    "selectedItemCode": self.state.get("mappings", {}).get(str(unmapped_item.get("exciseItemCode")), {}).get("itemCode"),
                }
            )

        return {
            "unmappedItems": rows,
            "madhushalaItems": madhushala_items,
            "dropdownCount": len(madhushala_items),
            "latestOnly": latest_only,
            "latestCaptureCount": len(latest_codes or latest_names),
        }

    async def auto_process_capture(self, capture: dict[str, Any] | None, token: str | None) -> dict[str, Any]:
        if not capture:
            self._set_auto_status(
                state="idle",
                message="Waiting for captured items.",
                mappingRequired=False,
                unmappedCount=0,
                preparedCount=0,
                lastError=None,
            )
            return self.get_auto_status()

        if not token:
            self._set_auto_status(
                state="needs_token",
                message="Capture saved. Paste Madhushala token to check mapping.",
                mappingRequired=False,
                preparedCount=capture.get("itemCount", 0),
                lastError=None,
            )
            return self.get_auto_status()

        try:
            self._set_auto_status(
                state="processing",
                message="Capture saved. Checking Madhushala mapping.",
                mappingRequired=False,
                preparedCount=capture.get("itemCount", 0),
                lastError=None,
            )
            prepared = await self.prepare_latest_capture(capture, token)
            workspace = await self.workspace(token, capture=capture)
            unmapped_count = len(workspace.get("unmappedItems", []))
            mapping_required = unmapped_count > 0
            self._set_auto_status(
                state="mapping_required" if mapping_required else "complete",
                message=(
                    f"{unmapped_count} item needs matching."
                    if mapping_required
                    else "All captured items are mapped."
                ),
                mappingRequired=mapping_required,
                unmappedCount=unmapped_count,
                preparedCount=prepared.get("preparedCount", 0),
                createdCount=prepared.get("createdCount", 0),
                lastError=None,
            )
        except Exception as exc:
            self._set_auto_status(
                state="error",
                message="Could not check Madhushala mapping.",
                mappingRequired=False,
                lastError=str(exc),
            )
        return self.get_auto_status()

    async def save_mappings(self, selections: list[dict[str, str]], token: str) -> dict[str, Any]:
        clean = [
            {"exciseItemCode": int(item["exciseItemCode"]), "itemCode": str(item["itemCode"]).strip()}
            for item in selections
            if str(item.get("exciseItemCode", "")).strip() and str(item.get("itemCode", "")).strip()
        ]
        if not clean:
            return {"mappedCount": 0, "response": None}

        response = await self._client(token).save_mapping(clean)
        mapped_at = datetime.now(timezone.utc).isoformat()
        for item in clean:
            self.state["mappings"][str(item["exciseItemCode"])] = {
                "exciseItemCode": item["exciseItemCode"],
                "itemCode": item["itemCode"],
                "mappedAt": mapped_at,
            }
        self._save_state()
        return {"mappedCount": len(clean), "response": response}

    async def prepare_session_capture(self, session: dict[str, Any], capture: dict[str, Any]) -> dict[str, Any]:
        shop_code = session["shop_code"]
        client = self._client_for_session(session)
        unmapped = await client.get_unmapped_items()
        prepared: list[dict[str, Any]] = []
        latest_codes: list[str] = []

        with conn() as db:
            for item in capture.get("items", []):
                canonical_key = item["canonicalKey"]
                payload = self.build_excise_payload(item)
                row = db.execute(
                    "SELECT * FROM imports WHERE shop_code=? AND canonical_key=?",
                    (shop_code, canonical_key),
                ).fetchone()

                if row:
                    excise_item_code = row["excise_item_code"]
                else:
                    excise_item_code, prepare_action, unmapped = await self._create_or_reuse_excise_item(
                        client, payload, unmapped
                    )

                if excise_item_code is not None:
                    latest_codes.append(str(excise_item_code))

                db.execute(
                    """
                    INSERT INTO imports(
                        shop_code, canonical_key, excise_item_code, item_name,
                        captured_item_json, last_seen_batch_id, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(shop_code, canonical_key) DO UPDATE SET
                        excise_item_code=excluded.excise_item_code,
                        item_name=excluded.item_name,
                        captured_item_json=excluded.captured_item_json,
                        last_seen_batch_id=excluded.last_seen_batch_id,
                        updated_at=excluded.updated_at
                    """,
                    (
                        shop_code,
                        canonical_key,
                        str(excise_item_code or ""),
                        payload["itemName"],
                        json.dumps(item, ensure_ascii=False),
                        capture["batchId"],
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                prepared.append(
                    {
                        "canonicalKey": canonical_key,
                        "exciseItemCode": excise_item_code,
                        "itemName": payload["itemName"],
                        "capturedItem": item,
                    }
                )

        return {"preparedCount": len(prepared), "latestUnmappedExciseCodes": latest_codes, "items": prepared}

    async def workspace_for_session(
        self,
        session: dict[str, Any],
        capture: dict[str, Any] | None = None,
        latest_only: bool = True,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        shop_code = session["shop_code"]
        client = self._client_for_session(session)
        unmapped = await client.get_unmapped_items()
        company_code = str(session.get("company_code") or settings.DEFAULT_COMPANY_CODE).strip()
        bill_type = str(session.get("bill_type") or settings.DEFAULT_BILL_TYPE).strip()
        madhushala_items = await client.get_dropdown_items(company_code, bill_type)
        latest_codes: set[str] = set()

        if job_id:
            with conn() as db:
                job = db.execute(
                    "SELECT id FROM import_jobs WHERE id=? AND shop_code=? AND session_id=?",
                    (job_id, shop_code, session["session_id"]),
                ).fetchone()
                if not job:
                    from fastapi import HTTPException
                    raise HTTPException(status_code=404, detail="Import job not found")
                job_rows = db.execute(
                    "SELECT excise_item_code FROM import_items WHERE job_id=? AND excise_item_code IS NOT NULL",
                    (job_id,),
                ).fetchall()
                latest_codes = {str(row["excise_item_code"]) for row in job_rows if row["excise_item_code"]}
        elif latest_only and capture:
            with conn() as db:
                for item in capture.get("items", []):
                    row = db.execute(
                        "SELECT excise_item_code FROM imports WHERE shop_code=? AND canonical_key=?",
                        (shop_code, item["canonicalKey"]),
                    ).fetchone()
                    if row and row["excise_item_code"]:
                        latest_codes.add(str(row["excise_item_code"]))

        rows: list[dict[str, Any]] = []
        with conn() as db:
            for unmapped_item in unmapped:
                excise_code = str(unmapped_item.get("exciseItemCode", ""))
                if latest_codes and excise_code not in latest_codes:
                    continue

                imported = db.execute(
                    "SELECT * FROM imports WHERE shop_code=? AND excise_item_code=?",
                    (shop_code, excise_code),
                ).fetchone()
                captured = json.loads(imported["captured_item_json"]) if imported else None
                if job_id:
                    job_item = db.execute(
                        "SELECT * FROM import_items WHERE job_id=? AND excise_item_code=?",
                        (job_id, excise_code),
                    ).fetchone()
                    if job_item:
                        captured = {
                            "rawName": job_item["raw_name"],
                            "brand": job_item["brand"],
                            "measureMl": job_item["ml"],
                            "ml": job_item["ml"],
                            "bottlesPerCase": job_item["packing"],
                            "packing": job_item["packing"],
                            "mrpPerUnit": job_item["mrp"],
                            "barcode": job_item["barcode"],
                            "confidence": job_item["confidence"],
                        }
                context = dict(unmapped_item)
                if captured:
                    context.update(captured)

                mapped = db.execute(
                    "SELECT madhushala_item_code FROM mappings WHERE shop_code=? AND excise_item_code=?",
                    (shop_code, excise_code),
                ).fetchone()
                rows.append(
                    {
                        "exciseItemCode": unmapped_item.get("exciseItemCode"),
                        "itemName": unmapped_item.get("itemName"),
                        "capturedItem": captured,
                        "suggestions": suggest_matches(context, madhushala_items),
                        "selectedItemCode": mapped["madhushala_item_code"] if mapped else None,
                    }
                )

        return {
            "shopCode": shop_code,
            "jobId": job_id,
            "unmappedItems": rows,
            "madhushalaItems": madhushala_items,
            "dropdownCount": len(madhushala_items),
            "latestOnly": latest_only,
        }

    async def prepare_document_job(self, session: dict[str, Any], job_id: str) -> dict[str, Any]:
        shop_code = session["shop_code"]
        client = self._client_for_session(session)
        unmapped = await client.get_unmapped_items()
        prepared = 0
        now = datetime.now(timezone.utc).isoformat()

        with conn() as db:
            job = db.execute(
                "SELECT id FROM import_jobs WHERE id=? AND shop_code=? AND session_id=?",
                (job_id, shop_code, session["session_id"]),
            ).fetchone()
            if not job:
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail="Import job not found")

            rows = db.execute("SELECT * FROM import_items WHERE job_id=?", (job_id,)).fetchall()
            for row in rows:
                item = {
                    "rawName": row["raw_name"],
                    "brand": row["brand"],
                    "ml": row["ml"],
                    "packing": row["packing"],
                    "mrp": row["mrp"],
                    "rate": row["rate"],
                    "barcode": row["barcode"],
                }
                payload = self.build_excise_payload(item)
                excise_item_code, prepare_action, unmapped = await self._create_or_reuse_excise_item(
                    client, payload, unmapped
                )
                mapping_status = "UNMAPPED" if excise_item_code else ("REVIEW_REQUIRED" if prepare_action == "review_required" else "PENDING")
                db.execute(
                    """
                    UPDATE import_items
                    SET excise_item_code=?, mapping_status=?, updated_at=?
                    WHERE id=?
                    """,
                    (str(excise_item_code or ""), mapping_status, now, row["id"]),
                )
                prepared += 1
        return {"preparedCount": prepared}

    async def save_session_mappings(
        self,
        session: dict[str, Any],
        selections: list[dict[str, Any]],
        job_id: str | None = None,
    ) -> dict[str, Any]:
        clean = [
            {"exciseItemCode": int(item["exciseItemCode"]), "itemCode": str(item["itemCode"]).strip()}
            for item in selections
            if str(item.get("exciseItemCode", "")).strip() and str(item.get("itemCode", "")).strip()
        ]
        if not clean:
            return {"mappedCount": 0, "response": None}

        response = await self._client_for_session(session).save_mapping(clean)
        mapped_at = datetime.now(timezone.utc).isoformat()
        with conn() as db:
            for item in clean:
                db.execute(
                    """
                    INSERT INTO mappings(shop_code, excise_item_code, madhushala_item_code, mapped_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(shop_code, excise_item_code) DO UPDATE SET
                        madhushala_item_code=excluded.madhushala_item_code,
                        mapped_at=excluded.mapped_at
                    """,
                    (session["shop_code"], str(item["exciseItemCode"]), item["itemCode"], mapped_at),
                )
                if job_id:
                    db.execute(
                        """
                        UPDATE import_items
                        SET mapping_status='MAPPED', mapped_item_code=?, updated_at=?
                        WHERE job_id=? AND excise_item_code=?
                        """,
                        (item["itemCode"], mapped_at, job_id, str(item["exciseItemCode"])),
                    )
            if job_id:
                counts = db.execute(
                    """
                    SELECT
                      COUNT(*) AS total,
                      SUM(CASE WHEN mapping_status='MAPPED' THEN 1 ELSE 0 END) AS mapped
                    FROM import_items WHERE job_id=?
                    """,
                    (job_id,),
                ).fetchone()
                total = counts["total"] or 0
                mapped = counts["mapped"] or 0
                status = "COMPLETED" if total and mapped >= total else "MAPPING_REQUIRED"
                db.execute(
                    """
                    UPDATE import_jobs
                    SET mapped_count=?, status=?, completed_at=CASE WHEN ?='COMPLETED' THEN ? ELSE completed_at END, updated_at=?
                    WHERE id=? AND shop_code=? AND session_id=?
                    """,
                    (mapped, status, status, mapped_at, mapped_at, job_id, session["shop_code"], session["session_id"]),
                )
        return {"mappedCount": len(clean), "response": response}




