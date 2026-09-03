"""Phase 2 mapping workflow service."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.integrations.madhushala.client import MadhushalaClient
from app.services.matching_service import suggest_matches


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

    @staticmethod
    def build_excise_payload(item: dict[str, Any]) -> dict[str, str]:
        """Build the catalogue payload expected by ExciseItemMasterSave.

        The API stores every catalogue value as text.  Keep the legacy tag
        fields during the API transition because existing shops may still use
        them, while also sending the new named fields.
        """
        return {
            "itemName": f"{item['brand']}, {item['measureMl']} Ml. ({item['packageType']})",
            "strengthRaw": str(item.get("strengthRaw", "")),
            "measureMl": str(item.get("measureMl", "")),
            "packageType": str(item.get("packageType", "")),
            "retailerMargin": str(item.get("retailerMargin", "")),
            "roundOffGovt": str(item.get("roundOffGovt", "")),
            "specialPurposeFee": str(item.get("specialPurposeFee", "")),
            "mrpPerUnit": str(item.get("mrpPerUnit", "")),
            "bottlesPerCase": str(item.get("bottlesPerCase", "")),
            "mrpPerCase": str(item.get("mrpPerCase", "")),
            "t1": str(item.get("measureMl", "")),
            "t2": str(item.get("mrpPerUnit", "")),
            "t3": str(item.get("packageType", "")),
            "t4": str(item.get("supplier", "")),
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
        unmapped_by_name = {item.get("itemName", ""): item for item in unmapped}

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
                existing_unmapped = unmapped_by_name.get(payload["itemName"])
                if existing_unmapped:
                    prepare_action = "already_unmapped"
                    response = {
                        **payload,
                        "itemCode": existing_unmapped["exciseItemCode"],
                        "itemName": existing_unmapped["itemName"],
                    }
                else:
                    prepare_action = "created"
                    response = await client.save_excise_item(payload)

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
        madhushala_items = await client.get_dropdown_items(settings.MADHUSHALA_COMPANY_CODE, settings.MADHUSHALA_BILL_TYPE)
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
