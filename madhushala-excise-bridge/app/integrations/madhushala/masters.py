from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable

from app.config import settings
from app.integrations.madhushala.cache import madhushala_cache
from app.integrations.madhushala.client import MadhushalaClient


logger = logging.getLogger("madhushala-excise-bridge")


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _value(row: dict[str, Any] | None, *aliases: str) -> Any:
    if not isinstance(row, dict):
        return None
    normalized = {_norm_key(key): value for key, value in row.items()}
    for alias in aliases:
        value = normalized.get(_norm_key(alias))
        if value not in (None, ""):
            return value
    return None


def _item_code(row: dict[str, Any] | None) -> str:
    return str(_value(row, "itemCode", "code", "value", "id") or "").strip()


def _merge_dict(base: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in detail.items():
        if value not in (None, "") or key not in merged:
            merged[key] = value
    return merged


class MadhushalaMasterService:
    """Read-through cached access to Madhushala reference/master data."""

    def __init__(self, session: dict[str, Any]):
        self.session = session
        token = str(session.get("madhushala_token") or settings.MADHUSHALA_SERVICE_TOKEN or "")
        self.shop_code = str(session.get("shop_code") or "").strip()
        self.company_code = str(session.get("company_code") or settings.DEFAULT_COMPANY_CODE or "").strip()
        self.bill_type = str(session.get("bill_type") or settings.DEFAULT_BILL_TYPE or "AI").strip() or "AI"
        self.client = MadhushalaClient(settings.MADHUSHALA_BASE_URL, self.shop_code, token)

    async def _cached(
        self,
        key: str,
        ttl: int,
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        cached = await madhushala_cache.get(key)
        if cached is not None:
            logger.debug("event=master_cache_hit key=%s", key)
            return cached
        logger.info("event=master_cache_miss key=%s", key)
        value = await loader()
        await madhushala_cache.set(key, value, ttl)
        return value

    def _scope(self, name: str) -> str:
        return f"{name}:{self.shop_code}:{self.company_code}"

    async def suppliers(self) -> list[Any]:
        return await self._cached(
            self._scope("suppliers"),
            settings.MADHUSHALA_MASTER_CACHE_TTL_SECONDS,
            lambda: self.client.get_purchase_suppliers(self.company_code),
        )

    async def storages(self) -> list[Any]:
        return await self._cached(
            self._scope("storages"),
            settings.MADHUSHALA_MASTER_CACHE_TTL_SECONDS,
            lambda: self.client.get_purchase_storages(self.company_code),
        )

    async def accounts(self) -> list[Any]:
        return await self._cached(
            self._scope("purchase_accounts"),
            settings.MADHUSHALA_MASTER_CACHE_TTL_SECONDS,
            lambda: self.client.get_purchase_accounts(self.company_code),
        )

    async def users(self) -> list[Any]:
        return await self._cached(
            self._scope("users"),
            settings.MADHUSHALA_USER_CACHE_TTL_SECONDS,
            lambda: self.client.get_purchase_users(self.company_code),
        )

    async def schemes(self) -> list[Any]:
        return await self._cached(
            self._scope("schemes"),
            settings.MADHUSHALA_SCHEME_CACHE_TTL_SECONDS,
            lambda: self.client.get_purchase_schemes(self.company_code),
        )

    async def purchase_tax_mode(self) -> str:
        async def load() -> Any:
            return await self.client.get_purchase_tax_mode(self.company_code)

        data = await self._cached(
            self._scope("purchase_tax_mode"),
            settings.MADHUSHALA_CONFIG_CACHE_TTL_SECONDS,
            load,
        )
        candidates: list[Any] = [data]
        if isinstance(data, dict):
            candidates.extend(
                data.get(key)
                for key in ("purchaseTaxMode", "taxMode", "mode", "value", "data", "result")
            )
        for candidate in candidates:
            text = str(candidate or "").strip().upper()
            if text in {"ITEMWISE", "BILLWISE"}:
                return text
        logger.warning(
            "event=purchase_tax_mode_unknown companyCode=%s responseType=%s fallback=ITEMWISE",
            self.company_code,
            type(data).__name__,
        )
        return "ITEMWISE"

    async def tax_tags(self) -> list[dict[str, Any]]:
        data = await self._cached(
            self._scope("tax_tags"),
            settings.MADHUSHALA_CONFIG_CACHE_TTL_SECONDS,
            lambda: self.client.get_tax_tags(self.company_code),
        )
        rows = self.client._list_payload(data, "taxTags", "tags")
        return [row for row in rows if isinstance(row, dict)]

    async def item_catalogue(self) -> list[dict[str, Any]]:
        key = f"items:{self.shop_code}:{self.company_code}:{self.bill_type}"
        data = await self._cached(
            key,
            settings.MADHUSHALA_ITEM_CACHE_TTL_SECONDS,
            lambda: self.client.get_dropdown_items(self.company_code, self.bill_type),
        )
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    @staticmethod
    def _needs_item_detail(item: dict[str, Any] | None) -> bool:
        if not item:
            return True
        # Dropdown endpoints sometimes return only code/name. Purchase calculation
        # needs commercial/tax master fields, so fetch detail only when they are absent.
        important = (
            "packing",
            "purchaseRate",
            "purchaseRateCase",
            "mrp",
            "t1Rate",
            "t2Rate",
            "t3Rate",
            "t4Rate",
            "etd",
            "cgst",
            "sgst",
            "igst",
        )
        return not any(_value(item, field) not in (None, "") for field in important)

    async def item_detail(self, item_code: str) -> dict[str, Any]:
        code = str(item_code or "").strip()
        if not code:
            return {}
        key = f"item:{self.company_code}:{code}"
        data = await self._cached(
            key,
            settings.MADHUSHALA_ITEM_CACHE_TTL_SECONDS,
            lambda: self.client.get_item(code, self.company_code),
        )
        if isinstance(data, dict):
            for nested_key in ("data", "result", "item"):
                nested = data.get(nested_key)
                if isinstance(nested, dict):
                    return nested
            return data
        rows = self.client._list_payload(data, "items")
        return rows[0] if rows and isinstance(rows[0], dict) else {}

    async def purchase_items_by_codes(self, item_codes: list[str]) -> list[dict[str, Any]]:
        wanted = [str(code or "").strip() for code in item_codes if str(code or "").strip()]
        catalogue = await self.item_catalogue()
        index = {_item_code(row): row for row in catalogue if _item_code(row)}
        result: dict[str, dict[str, Any]] = {
            code: dict(index.get(code) or {}) for code in wanted
        }

        needs_detail = [code for code in wanted if self._needs_item_detail(result.get(code))]
        if needs_detail:
            semaphore = asyncio.Semaphore(max(1, settings.MADHUSHALA_ITEM_FETCH_CONCURRENCY))

            async def fetch(code: str) -> tuple[str, dict[str, Any]]:
                async with semaphore:
                    try:
                        return code, await self.item_detail(code)
                    except Exception as exc:
                        logger.warning("event=item_detail_failed itemCode=%s reason=%s", code, exc)
                        return code, {}

            details = await asyncio.gather(*(fetch(code) for code in dict.fromkeys(needs_detail)))
            for code, detail in details:
                if detail:
                    result[code] = _merge_dict(result.get(code) or {}, detail)

        return [result.get(code) or {"itemCode": code} for code in wanted]

    async def warm_purchase_context(self) -> dict[str, Any]:
        """Fetch independent reference data concurrently; each call is cache-backed."""
        suppliers, storages, accounts, users, schemes, tax_mode = await asyncio.gather(
            self.suppliers(),
            self.storages(),
            self.accounts(),
            self.users(),
            self.schemes(),
            self.purchase_tax_mode(),
        )
        return {
            "suppliers": suppliers,
            "storages": storages,
            "accounts": accounts,
            "users": users,
            "schemes": schemes,
            "taxMode": tax_mode,
        }
