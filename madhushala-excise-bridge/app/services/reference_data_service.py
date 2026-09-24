from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable

from app.config import settings
from app.integrations.madhushala.client import MadhushalaApiError, MadhushalaClient
from app.services.cache_service import cache_service


logger = logging.getLogger("madhushala-excise-bridge.reference")


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _dict_value(row: dict[str, Any], *aliases: str) -> Any:
    normalized = {_key(name): value for name, value in row.items()}
    for alias in aliases:
        value = normalized.get(_key(alias))
        if value not in (None, ""):
            return value
    return None


def _unwrap_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    for key in ("data", "result", "item", "value"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return value


def _has_purchase_detail(item: dict[str, Any]) -> bool:
    normalized = {_key(name) for name in item}
    has_packing = any(_key(name) in normalized for name in ("packing", "bottlePerCase", "bottlesPerCase", "caseQty"))
    has_commercial = any(
        _key(name) in normalized
        for name in (
            "purchaseRate",
            "purchaseRateCase",
            "mrp",
            "t1Rate",
            "t2Rate",
            "t3Rate",
            "t4Rate",
            "etd",
        )
    )
    return has_packing and has_commercial


class MadhushalaReferenceDataService:
    """Cached/read-through view of the masters used by Madhushala Purchase."""

    def client_for_session(self, session: dict[str, Any]) -> MadhushalaClient:
        token = session.get("madhushala_token") or settings.MADHUSHALA_SERVICE_TOKEN
        return MadhushalaClient(
            settings.MADHUSHALA_BASE_URL,
            str(session.get("shop_code") or ""),
            str(token or ""),
        )

    @staticmethod
    def _scope(session: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(session.get("shop_code") or "").strip(),
            str(session.get("company_code") or settings.DEFAULT_COMPANY_CODE).strip(),
            str(session.get("bill_type") or settings.DEFAULT_BILL_TYPE).strip() or settings.DEFAULT_BILL_TYPE,
        )

    async def _cached_master(
        self,
        session: dict[str, Any],
        name: str,
        ttl: int,
        loader: Callable[[MadhushalaClient, str], Awaitable[list[Any]]],
    ) -> list[Any]:
        shop, company, _ = self._scope(session)
        cache_key = f"master:{name}:{shop}:{company}"
        client = self.client_for_session(session)

        async def load() -> list[Any]:
            rows = await loader(client, company)
            logger.info("reference_refresh name=%s shopCode=%s companyCode=%s rows=%s", name, shop, company, len(rows))
            return rows

        value = await cache_service.get_or_load(cache_key, ttl, load)
        return value if isinstance(value, list) else []

    async def companies(self, session: dict[str, Any]) -> list[Any]:
        shop, _, _ = self._scope(session)
        cache_key = f"master:companies:{shop}"
        client = self.client_for_session(session)

        async def load() -> list[Any]:
            rows = await client.get_purchase_company()
            logger.info("reference_refresh name=companies shopCode=%s rows=%s", shop, len(rows))
            return rows

        value = await cache_service.get_or_load(cache_key, settings.CACHE_MASTER_TTL_SECONDS, load)
        return value if isinstance(value, list) else []

    async def suppliers(self, session: dict[str, Any]) -> list[Any]:
        return await self._cached_master(
            session,
            "suppliers",
            settings.CACHE_MASTER_TTL_SECONDS,
            lambda client, company: client.get_purchase_suppliers(company),
        )

    async def storages(self, session: dict[str, Any]) -> list[Any]:
        return await self._cached_master(
            session,
            "storages",
            settings.CACHE_MASTER_TTL_SECONDS,
            lambda client, company: client.get_purchase_storages(company),
        )

    async def purchase_accounts(self, session: dict[str, Any]) -> list[Any]:
        return await self._cached_master(
            session,
            "purchase-accounts",
            settings.CACHE_MASTER_TTL_SECONDS,
            lambda client, company: client.get_purchase_accounts(company),
        )

    async def users(self, session: dict[str, Any]) -> list[Any]:
        return await self._cached_master(
            session,
            "users",
            settings.CACHE_USER_TTL_SECONDS,
            lambda client, company: client.get_purchase_users(company),
        )

    async def schemes(self, session: dict[str, Any]) -> list[Any]:
        return await self._cached_master(
            session,
            "schemes",
            settings.CACHE_SCHEME_TTL_SECONDS,
            lambda client, company: client.get_purchase_schemes(company),
        )

    async def purchase_context_rows(self, session: dict[str, Any]) -> dict[str, list[Any]]:
        results = await asyncio.gather(
            self.suppliers(session),
            self.storages(session),
            self.purchase_accounts(session),
            self.users(session),
            self.schemes(session),
            return_exceptions=True,
        )
        names = ("suppliers", "storages", "accounts", "users", "schemes")
        output: dict[str, list[Any]] = {}
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.warning("reference_load_failed name=%s error=%s", name, result)
                output[name] = []
            else:
                output[name] = result if isinstance(result, list) else []
        return output

    async def catalogue(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        shop, company, bill_type = self._scope(session)
        cache_key = f"catalogue:{shop}:{company}:{bill_type}"
        client = self.client_for_session(session)

        async def load() -> list[dict[str, Any]]:
            rows = await client.get_dropdown_items(company, bill_type)
            logger.info(
                "catalogue_refresh shopCode=%s companyCode=%s billType=%s rows=%s",
                shop,
                company,
                bill_type,
                len(rows),
            )
            return rows

        value = await cache_service.get_or_load(cache_key, settings.CACHE_ITEM_TTL_SECONDS, load)
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    async def search_catalogue(self, session: dict[str, Any], search: str) -> list[dict[str, Any]]:
        """Search Madhushala Item Master live, bypassing the long-lived catalogue cache."""
        query = str(search or "").strip()
        if not query:
            return await self.catalogue(session)

        shop, company, bill_type = self._scope(session)
        client = self.client_for_session(session)
        rows = await client.get_dropdown_items(company, bill_type, query)
        rows = [row for row in (rows or []) if isinstance(row, dict)]
        logger.info(
            "catalogue_live_search shopCode=%s companyCode=%s billType=%s query=%s rows=%s",
            shop,
            company,
            bill_type,
            query,
            len(rows),
        )
        return rows

    async def fresh_catalogue(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        """Bypass catalogue cache for company-scope validation.

        Mapping uses Madhushala's live purchase dropdown. Purchase validation
        must be able to re-check that same source before declaring a mapping
        invalid, otherwise a stale cache can create a false company mismatch.
        """
        shop, company, bill_type = self._scope(session)
        client = self.client_for_session(session)
        rows = await client.get_dropdown_items(company, bill_type)
        rows = [row for row in (rows or []) if isinstance(row, dict)]
        logger.info(
            "catalogue_fresh_validation shopCode=%s companyCode=%s billType=%s rows=%s",
            shop,
            company,
            bill_type,
            len(rows),
        )
        cache_key = f"catalogue:{shop}:{company}:{bill_type}"
        await cache_service.set_json(cache_key, rows, settings.CACHE_ITEM_TTL_SECONDS)
        return rows


    async def item(self, session: dict[str, Any], item_code: str, catalogue: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        shop, company, _ = self._scope(session)
        code = str(item_code or "").strip()
        if not code:
            return {}
        cache_key = f"item:{shop}:{company}:{code}"

        # Purchase Calculate/Save must use the same purchase-specific item row
        # that the Madhushala Purchase UI exposes through
        # /api/purchase/dropdown/items. Do not call /api/items/{itemCode} here:
        # that generic item endpoint can contain different commercial values and
        # must not overwrite the authoritative Purchase dropdown values.
        source_catalogue = catalogue if catalogue is not None else await self.catalogue(session)
        for row in source_catalogue:
            row_code = _dict_value(row, "itemCode", "code", "value", "id")
            if str(row_code or "").strip() == code:
                purchase_item = dict(row)
                await cache_service.set_json(cache_key, purchase_item, settings.CACHE_ITEM_TTL_SECONDS)
                logger.info(
                    "purchase_item_from_dropdown itemCode=%s shopCode=%s companyCode=%s",
                    code,
                    shop,
                    company,
                )
                return purchase_item

        return {}

    async def items(self, session: dict[str, Any], item_codes: list[str]) -> dict[str, dict[str, Any]]:
        catalogue = await self.catalogue(session)
        unique_codes = list(dict.fromkeys(str(code or "").strip() for code in item_codes if str(code or "").strip()))
        return {
            code: item
            for code in unique_codes
            if (item := await self.item(session, code, catalogue))
        }

    async def tax_mode(self, session: dict[str, Any]) -> str:
        shop, company, _ = self._scope(session)
        cache_key = f"purchase-tax-mode:{shop}:{company}"
        client = self.client_for_session(session)

        async def load() -> Any:
            return await client.get_purchase_tax_mode(company)

        raw = await cache_service.get_or_load(cache_key, settings.CACHE_TAX_TTL_SECONDS, load)
        if isinstance(raw, str):
            candidate = raw
        elif isinstance(raw, dict):
            candidate = str(_dict_value(raw, "purchaseTaxMode", "taxMode", "mode", "value") or "")
        else:
            candidate = ""
        mode = candidate.strip().upper()
        if mode not in {"ITEMWISE", "BILLWISE"}:
            logger.warning("unknown_purchase_tax_mode companyCode=%s raw=%s fallback=ITEMWISE", company, raw)
            return "ITEMWISE"
        return mode

    async def tax_tags(self, session: dict[str, Any]) -> list[Any]:
        shop, company, _ = self._scope(session)
        cache_key = f"tax-tags:{shop}:{company}"
        client = self.client_for_session(session)

        async def load() -> list[Any]:
            return await client.get_tax_tags(company)

        value = await cache_service.get_or_load(cache_key, settings.CACHE_TAX_TTL_SECONDS, load)
        return value if isinstance(value, list) else []

    async def purchase_bootstrap(self, session: dict[str, Any]) -> dict[str, Any]:
        """Warm the same Purchase-specific masters used by the manual screen.

        All independent reads run concurrently and are cached. We deliberately
        do not replay dashboard filters/session-data or account-maintenance
        endpoints because the CRM launch already establishes the session and
        those calls are not inputs to PurchaseCalculationRequest/PurchaseRequest.
        """
        names = (
            "companies",
            "suppliers",
            "storages",
            "accounts",
            "users",
            "schemes",
            "taxMode",
            "catalogue",
            "taxTags",
        )
        results = await asyncio.gather(
            self.companies(session),
            self.suppliers(session),
            self.storages(session),
            self.purchase_accounts(session),
            self.users(session),
            self.schemes(session),
            self.tax_mode(session),
            self.catalogue(session),
            self.tax_tags(session),
            return_exceptions=True,
        )
        output: dict[str, Any] = {}
        warnings: list[str] = []
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                warnings.append(f"{name}: {result}")
                output[name] = "ITEMWISE" if name == "taxMode" else []
                logger.warning("purchase_bootstrap_failed name=%s error=%s", name, result)
            else:
                output[name] = result
        output["warnings"] = warnings
        logger.info(
            "purchase_bootstrap_ready shopCode=%s companyCode=%s catalogue=%s warnings=%s",
            self._scope(session)[0],
            self._scope(session)[1],
            len(output.get("catalogue") or []),
            len(warnings),
        )
        return output


reference_data_service = MadhushalaReferenceDataService()
