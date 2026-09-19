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
from app.modules.document_import.quantity import extract_physical_quantity
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

    @staticmethod
    def _measure_key(item: dict[str, Any]) -> str:
        value = (
            item.get("measureMl")
            or item.get("ml")
            or item.get("measure")
            or item.get("size")
            or ""
        )
        text = str(value or "").strip()
        match = re.search(r"\d{2,5}", text)
        return match.group(0) if match else ""

    @classmethod
    def _excise_identity_key(cls, item: dict[str, Any]) -> tuple[str, str]:
        name = cls._normalize_excise_name(str(item.get("itemName") or ""))
        return name, cls._measure_key(item)

    @classmethod
    def _unmapped_indexes(
        cls,
        items: list[dict[str, Any]],
    ) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        by_identity: dict[tuple[str, str], dict[str, Any]] = {}
        by_name: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            name = str(item.get("itemName") or "").strip()
            if not name:
                continue
            normalized_name = cls._normalize_excise_name(name)
            identity = cls._excise_identity_key(item)
            by_identity.setdefault(identity, item)
            by_name.setdefault(normalized_name, []).append(item)
        return by_identity, by_name

    @classmethod
    def _find_existing_excise(
        cls,
        payload: dict[str, str],
        by_identity: dict[tuple[str, str], dict[str, Any]],
        by_name: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any] | None:
        identity = cls._excise_identity_key(payload)
        match = by_identity.get(identity)
        if match:
            return match

        # Never collapse same-name products with different ML. Name-only
        # fallback is safe only when neither side exposes a measure and there
        # is exactly one candidate.
        name_key, measure_key = identity
        candidates = by_name.get(name_key, [])
        if not measure_key and len(candidates) == 1 and not cls._measure_key(candidates[0]):
            return candidates[0]
        return None

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
        unmapped: list[dict[str, Any]] | None = None,
    ) -> tuple[str | int | None, str, list[dict[str, Any]]]:
        """Send the item to Madhushala first; let Madhushala own deduplication.

        The old bridge queried ``unmapped-items`` before saving and skipped
        ``ExciseItemMasterSave`` when a similar item was found. The current Madhushala
        contract requires every captured item to be posted first. We only consult the
        unmapped list afterwards as a recovery mechanism when the save response does
        not contain an Excise item code or an older backend reports a duplicate error.
        """
        current_unmapped = list(unmapped or [])
        try:
            response = await client.save_excise_item(payload)
            response = response if isinstance(response, dict) else {}
            code = response.get("exciseItemCode") or response.get("itemCode")
            logger.info("Excise item submitted code=%s item=%s", code, payload.get("itemName"))
            if code is not None:
                return code, "submitted", current_unmapped

            refreshed = await client.get_unmapped_items()
            by_identity, by_name = self._unmapped_indexes(refreshed)
            existing = self._find_existing_excise(payload, by_identity, by_name)
            if existing:
                code = existing.get("exciseItemCode")
                logger.info("Excise item resolved after submit code=%s item=%s", code, payload.get("itemName"))
                return code, "submitted_resolved", refreshed
            logger.warning("Excise submit returned no code and item was not in unmapped list item=%s", payload.get("itemName"))
            return None, "review_required", refreshed
        except MadhushalaApiError as exc:
            if not self._is_duplicate_error(exc):
                raise
            # Compatibility fallback for older Madhushala deployments. The important
            # ordering remains Save first -> Unmapped second.
            refreshed = await client.get_unmapped_items()
            by_identity, by_name = self._unmapped_indexes(refreshed)
            existing = self._find_existing_excise(payload, by_identity, by_name)
            if existing:
                code = existing.get("exciseItemCode")
                logger.info("Excise duplicate resolved after save code=%s item=%s", code, payload.get("itemName"))
                return code, "duplicate_resolved", refreshed
            logger.warning("Excise duplicate unresolved after save item=%s", payload.get("itemName"))
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
        unmapped: list[dict[str, Any]] = []

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
                # Legacy flow only. The authenticated session path below always posts
                # every captured row as required by the current Madhushala contract.
                imported["capturedItem"] = item

            imported["lastSeenBatchId"] = batch_id
            imported["lastSeenAt"] = datetime.now(timezone.utc).isoformat()
            imported["lastPrepareAction"] = prepare_action
            if imported.get("exciseItemCode") is not None:
                latest_unmapped_codes.append(str(imported["exciseItemCode"]))
            if prepare_action in {"submitted", "submitted_resolved"} and imported.get("exciseItemCode") is not None:
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
        """Post every captured WB Excise row first, then use mapping APIs.

        Madhushala owns item-master deduplication. A local import row is only useful as
        recovery metadata; it must never suppress ``ExciseItemMasterSave``.
        """
        shop_code = session["shop_code"]
        client = self._client_for_session(session)
        prepared: list[dict[str, Any]] = []
        latest_codes: list[str] = []
        unmapped_cache: list[dict[str, Any]] = []

        for item in capture.get("items", []):
            canonical_key = item["canonicalKey"]
            payload = self.build_excise_payload(item)
            existing_code = ""
            with conn() as db:
                row = db.execute(
                    "SELECT * FROM imports WHERE shop_code=? AND canonical_key=?",
                    (shop_code, canonical_key),
                ).fetchone()
                if row:
                    existing_code = str(row["excise_item_code"] or "").strip()

            # Required ordering: ExciseItemMasterSave first for EVERY captured item.
            excise_item_code, prepare_action, refreshed = await self._create_or_reuse_excise_item(
                client, payload, unmapped_cache
            )
            if refreshed:
                unmapped_cache = refreshed
            if excise_item_code is None and existing_code:
                # If an older backend reports a duplicate that is already mapped and
                # therefore absent from unmapped-items, retain our previously confirmed code.
                excise_item_code = existing_code
                prepare_action = "submitted_existing_code"

            if excise_item_code is not None:
                latest_codes.append(str(excise_item_code))

            with conn() as db:
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

            logger.info(
                "excise_capture_prepared shopCode=%s item=%s action=%s exciseItemCode=%s",
                shop_code,
                payload["itemName"],
                prepare_action,
                excise_item_code,
            )
            prepared.append(
                {
                    "canonicalKey": canonical_key,
                    "exciseItemCode": excise_item_code,
                    "itemName": payload["itemName"],
                    "capturedItem": item,
                    "prepareAction": prepare_action,
                }
            )

        return {"preparedCount": len(prepared), "latestUnmappedExciseCodes": latest_codes, "items": prepared}

    async def company_item_codes(self, session: dict[str, Any]) -> set[str]:
        """Return valid Madhushala item codes for the active company scope."""
        company_code = str(session.get("company_code") or settings.DEFAULT_COMPANY_CODE).strip()
        bill_type = str(session.get("bill_type") or settings.DEFAULT_BILL_TYPE).strip()
        rows = await self._client_for_session(session).get_dropdown_items(company_code, bill_type)
        return {
            str(item.get("itemCode") or "").strip()
            for item in (rows or [])
            if isinstance(item, dict) and str(item.get("itemCode") or "").strip()
        }

    async def workspace_for_session(
        self,
        session: dict[str, Any],
        capture: dict[str, Any] | None = None,
        latest_only: bool = True,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        shop_code = session["shop_code"]
        client = self._client_for_session(session)
        company_code = str(session.get("company_code") or settings.DEFAULT_COMPANY_CODE).strip()
        bill_type = str(session.get("bill_type") or settings.DEFAULT_BILL_TYPE).strip()
        madhushala_items = await client.get_dropdown_items(company_code, bill_type)
        valid_item_codes = {
            str(item.get("itemCode") or "").strip()
            for item in madhushala_items
            if str(item.get("itemCode") or "").strip()
        }

        if job_id:
            rows: list[dict[str, Any]] = []
            with conn() as db:
                job = db.execute(
                    "SELECT id FROM import_jobs WHERE id=? AND shop_code=? AND session_id=?",
                    (job_id, shop_code, session["session_id"]),
                ).fetchone()
                if not job:
                    from fastapi import HTTPException
                    raise HTTPException(status_code=404, detail="Import job not found")

                job_rows = db.execute(
                    "SELECT * FROM import_items WHERE job_id=? ORDER BY created_at, id",
                    (job_id,),
                ).fetchall()
                for job_item in job_rows:
                    excise_code = str(job_item["excise_item_code"] or "").strip()
                    raw_data: dict[str, Any] = {}
                    try:
                        loaded = json.loads(job_item["raw_data_json"] or "{}")
                        if isinstance(loaded, dict):
                            raw_data = loaded
                    except Exception:
                        raw_data = {}

                    keys = set(job_item.keys())
                    try:
                        canonical_box = max(0, int(float(job_item["box"] or 0))) if "box" in keys else 0
                    except Exception:
                        canonical_box = 0
                    try:
                        canonical_loose = max(0, int(float(job_item["loose"] or 0))) if "loose" in keys else 0
                    except Exception:
                        canonical_loose = 0
                    if canonical_box <= 0:
                        try:
                            canonical_box = max(0, int(float(raw_data.get("canonicalBox", raw_data.get("box")) or 0)))
                        except Exception:
                            canonical_box = 0
                    if canonical_loose <= 0:
                        try:
                            canonical_loose = max(0, int(float(raw_data.get("canonicalLoose", raw_data.get("loose")) or 0)))
                        except Exception:
                            canonical_loose = 0
                    if canonical_box <= 0 and canonical_loose <= 0:
                        canonical_loose = (
                            int(job_item["quantity"])
                            if job_item["quantity"] not in (None, "") and float(job_item["quantity"]) > 0
                            else extract_physical_quantity(raw_data)
                        )

                    captured = {
                        **raw_data,
                        "rawName": job_item["raw_name"],
                        "brand": job_item["brand"],
                        "measureMl": job_item["ml"],
                        "ml": job_item["ml"],
                        "bottlesPerCase": job_item["packing"],
                        "packing": job_item["packing"],
                        "box": canonical_box,
                        "loose": canonical_loose,
                        "quantity": (canonical_box + canonical_loose) or None,
                        "rate": job_item["rate"],
                        "mrpPerUnit": job_item["mrp"],
                        "mrp": job_item["mrp"],
                        "amount": job_item["amount"],
                        "barcode": job_item["barcode"],
                        "confidence": job_item["confidence"],
                    }
                    context = {
                        "exciseItemCode": excise_code,
                        "itemName": job_item["raw_name"] or job_item["normalized_name"] or excise_code,
                        **captured,
                    }

                    mapped_code = str(job_item["mapped_item_code"] or "").strip()
                    if not mapped_code and excise_code:
                        mapped = db.execute(
                            "SELECT madhushala_item_code FROM mappings_v2 WHERE shop_code=? AND company_code=? AND excise_item_code=?",
                            (shop_code, company_code, excise_code),
                        ).fetchone()
                        mapped_code = str(mapped["madhushala_item_code"] if mapped else "").strip()
                    if mapped_code and mapped_code not in valid_item_codes:
                        logger.warning(
                            "stale_company_mapping_cleared shopCode=%s companyCode=%s itemCode=%s jobItemId=%s",
                            shop_code,
                            company_code,
                            mapped_code,
                            job_item["id"],
                        )
                        db.execute(
                            """
                            UPDATE import_items
                            SET mapped_item_code=NULL, mapping_status='UNMAPPED', updated_at=?
                            WHERE id=?
                            """,
                            (datetime.now(timezone.utc).isoformat(), job_item["id"]),
                        )
                        mapped_code = ""
                    mapped_item = next((item for item in madhushala_items if str(item.get("itemCode")) == mapped_code), None)
                    rows.append(
                        {
                            "jobItemId": job_item["id"],
                            "exciseItemCode": excise_code,
                            "itemName": job_item["raw_name"] or job_item["normalized_name"] or excise_code,
                            "capturedItem": captured,
                            "suggestions": suggest_matches(context, madhushala_items),
                            "selectedItemCode": mapped_code or None,
                            "selectedItem": mapped_item,
                            "mappingStatus": "MAPPED" if mapped_code else (job_item["mapping_status"] or "PENDING"),
                            "documentRow": True,
                        }
                    )

            return {
                "shopCode": shop_code,
                "jobId": job_id,
                "documentMapping": True,
                "unmappedItems": rows,
                "madhushalaItems": madhushala_items,
                "dropdownCount": len(madhushala_items),
                "latestOnly": latest_only,
            }

        # For portal import this is intentionally AFTER ExciseItemMasterSave. Madhushala
        # is the source of truth for which submitted rows still require mapping.
        unmapped = await client.get_unmapped_items()
        latest_codes: set[str] = set()

        if latest_only and capture:
            with conn() as db:
                for item in capture.get("items", []):
                    row = db.execute(
                        "SELECT excise_item_code FROM imports WHERE shop_code=? AND canonical_key=?",
                        (shop_code, item["canonicalKey"]),
                    ).fetchone()
                    if row and row["excise_item_code"]:
                        latest_codes.add(str(row["excise_item_code"]))
        elif latest_only:
            # A browser refresh, a new CRM session, or a service restart may not
            # have a capture attached to the new session. In that case keep
            # latestOnly semantics by falling back to the most recently imported
            # batch for this shop rather than returning every historical unmapped item.
            with conn() as db:
                latest_batch = db.execute(
                    """
                    SELECT last_seen_batch_id
                    FROM imports
                    WHERE shop_code=?
                      AND last_seen_batch_id IS NOT NULL
                      AND TRIM(last_seen_batch_id) <> ''
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (shop_code,),
                ).fetchone()

                if latest_batch and latest_batch["last_seen_batch_id"]:
                    batch_rows = db.execute(
                        """
                        SELECT excise_item_code
                        FROM imports
                        WHERE shop_code=?
                          AND last_seen_batch_id=?
                          AND excise_item_code IS NOT NULL
                          AND TRIM(excise_item_code) <> ''
                        """,
                        (shop_code, latest_batch["last_seen_batch_id"]),
                    ).fetchall()
                    latest_codes = {
                        str(row["excise_item_code"])
                        for row in batch_rows
                        if row["excise_item_code"]
                    }

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
                context = dict(unmapped_item)
                if captured:
                    context.update(captured)

                mapped = db.execute(
                    "SELECT madhushala_item_code FROM mappings_v2 WHERE shop_code=? AND company_code=? AND excise_item_code=?",
                    (shop_code, company_code, excise_code),
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
            "documentMapping": False,
            "unmappedItems": rows,
            "madhushalaItems": madhushala_items,
            "dropdownCount": len(madhushala_items),
            "latestOnly": latest_only,
        }

    async def prepare_document_job(self, session: dict[str, Any], job_id: str) -> dict[str, Any]:
        shop_code = session["shop_code"]
        company_code = str(session.get("company_code") or settings.DEFAULT_COMPANY_CODE).strip()
        client = self._client_for_session(session)
        unmapped: list[dict[str, Any]] = []
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
            existing_mapped = str(row["mapped_item_code"] or "").strip()
            existing_excise_code = str(row["excise_item_code"] or "").strip()
            if excise_item_code is None and existing_excise_code:
                excise_item_code = existing_excise_code

            mapped_item_code = existing_mapped
            with conn() as db:
                if not mapped_item_code and excise_item_code is not None:
                    mapped = db.execute(
                        "SELECT madhushala_item_code FROM mappings_v2 WHERE shop_code=? AND company_code=? AND excise_item_code=?",
                        (shop_code, company_code, str(excise_item_code)),
                    ).fetchone()
                    mapped_item_code = str(mapped["madhushala_item_code"] if mapped else "").strip()
                mapping_status = "MAPPED" if mapped_item_code else ("UNMAPPED" if excise_item_code else ("REVIEW_REQUIRED" if prepare_action == "review_required" else "PENDING"))
                db.execute(
                    """
                    UPDATE import_items
                    SET excise_item_code=?, mapping_status=?, mapped_item_code=?, updated_at=?
                    WHERE id=?
                    """,
                    (str(excise_item_code or ""), mapping_status, mapped_item_code, now, row["id"]),
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
                    INSERT INTO mappings_v2(
                        shop_code, company_code, excise_item_code, madhushala_item_code, mapped_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(shop_code, company_code, excise_item_code) DO UPDATE SET
                        madhushala_item_code=excluded.madhushala_item_code,
                        mapped_at=excluded.mapped_at
                    """,
                    (
                        session["shop_code"],
                        str(session.get("company_code") or settings.DEFAULT_COMPANY_CODE).strip(),
                        str(item["exciseItemCode"]),
                        item["itemCode"],
                        mapped_at,
                    ),
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
