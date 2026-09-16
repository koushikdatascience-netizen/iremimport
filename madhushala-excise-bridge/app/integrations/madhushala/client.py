"""Madhushala API client for excise import mapping."""
from __future__ import annotations

from datetime import datetime
from typing import Any
import logging

import httpx


logger = logging.getLogger("madhushala-excise-bridge")


class MadhushalaApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class MadhushalaClient:
    def __init__(self, base_url: str, shop_code: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.shop_code = shop_code
        self.token = self._normalize_token(token)

    @staticmethod
    def _normalize_token(token: str) -> str:
        value = (token or "").strip()
        if value.casefold().startswith("bearer "):
            value = value[7:].strip()
        return value

    @staticmethod
    def _list_payload(data: Any, *keys: str) -> list[Any]:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in (*keys, "items", "data", "result", "results", "values", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        # Some APIs wrap the real list one level deeper, e.g. {data: {items: [...]}}.
        for value in data.values():
            if isinstance(value, dict):
                nested = MadhushalaClient._list_payload(value, *keys)
                if nested:
                    return nested
        return []

    @staticmethod
    def _purchase_datetime(value: Any) -> str:
        """Convert purchase dates to the ISO date-time shape expected by ASP.NET."""
        text = str(value or "").strip()
        if not text:
            return ""

        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = None
            for fmt in (
                "%d-%b-%Y %H:%M:%S",
                "%d-%b-%Y",
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y",
            ):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise MadhushalaApiError(
                    f"Invalid purchase date '{text}'. Expected an ISO date or date-time."
                )

        # Madhushala models these as System.DateTime rather than DateTimeOffset.
        # Send a timezone-free ISO local date-time; date-only inputs become midnight.
        return parsed.replace(tzinfo=None).isoformat(timespec="seconds")

    def _auth_headers(self, accept: str = "application/json") -> dict[str, str]:
        if not self.token:
            raise MadhushalaApiError("Madhushala token is not configured")
        return {"accept": accept, "Authorization": f"Bearer {self.token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = await self._send(method, url, params=params, json_body=json_body, headers=headers)
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()
        except httpx.HTTPStatusError as exc:
            message = exc.response.text or exc.response.reason_phrase
            raise MadhushalaApiError(message, exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise MadhushalaApiError(str(exc)) from exc

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        json_body: Any | None,
        headers: dict[str, str] | None,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                return await client.request(method, url, params=params, json=json_body, headers=headers)
        except httpx.ConnectError as exc:
            if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                raise
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                return await client.request(method, url, params=params, json=json_body, headers=headers)

    async def save_excise_item(self, payload: dict[str, str]) -> dict[str, Any]:
        headers = self._auth_headers("application/json")
        headers["Content-Type"] = "application/json"
        return await self._request(
            "POST",
            "/api/excise-import/ExciseItemMasterSave",
            params={"shopCode": self.shop_code},
            json_body=payload,
            headers=headers,
        )

    async def get_unmapped_items(self) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/excise-import/unmapped-items",
            params={"shopCode": self.shop_code},
            headers=self._auth_headers("*/*"),
        )

    async def save_mapping(self, mappings: list[dict[str, str]]) -> Any:
        headers = {"accept": "*/*", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return await self._request(
            "POST",
            "/api/excise-import/save-mapping",
            params={"shopCode": self.shop_code},
            json_body=mappings,
            headers=headers,
        )

    async def get_dropdown_items(self, company_code: str, bill_type: str) -> list[dict[str, Any]]:
        safe_company_code = str(company_code or "").strip()
        safe_bill_type = str(bill_type or "").strip()
        logger.info(
            "Madhushala dropdown request shopCode=%s companyCode=%s billType=%s",
            self.shop_code,
            safe_company_code,
            safe_bill_type,
        )
        data = await self._request(
            "GET",
            "/api/purchase/dropdown/items",
            params={"shopCode": self.shop_code, "companyCode": safe_company_code, "billType": safe_bill_type},
            headers=self._auth_headers("*/*"),
        )
        items = self._list_payload(data, "products")
        if not isinstance(items, list):
            raise MadhushalaApiError("Madhushala dropdown response was not a list")
        logger.info(
            "Madhushala dropdown response shopCode=%s companyCode=%s billType=%s itemCount=%s",
            self.shop_code,
            safe_company_code,
            safe_bill_type,
            len(items),
        )
        return items

    async def _get_purchase_master(
        self,
        path: str,
        company_code: str,
        *response_keys: str,
    ) -> list[Any]:
        safe_company_code = str(company_code or "").strip()
        data = await self._request(
            "GET",
            path,
            params={"shopCode": self.shop_code, "companyCode": safe_company_code},
            headers=self._auth_headers("*/*"),
        )
        rows = self._list_payload(data, *response_keys)
        logger.info(
            "Madhushala purchase master response path=%s shopCode=%s companyCode=%s rowCount=%s",
            path,
            self.shop_code,
            safe_company_code,
            len(rows),
        )
        return rows

    async def get_purchase_suppliers(self, company_code: str) -> list[Any]:
        return await self._get_purchase_master(
            "/api/purchase/dropdown/suppliers",
            company_code,
            "suppliers",
            "supplierList",
        )

    async def get_purchase_storages(self, company_code: str) -> list[Any]:
        return await self._get_purchase_master(
            "/api/purchase/dropdown/storages",
            company_code,
            "storages",
            "stores",
            "storageList",
        )

    async def get_purchase_accounts(self, company_code: str) -> list[Any]:
        return await self._get_purchase_master(
            "/api/purchase/dropdown/purchase-accounts",
            company_code,
            "accounts",
            "purchaseAccounts",
            "accountList",
        )

    async def get_purchase_users(self, company_code: str) -> list[Any]:
        return await self._get_purchase_master(
            "/api/counter-sales/users",
            company_code,
            "users",
            "userList",
        )

    async def calculate_purchase(self, payload: dict[str, Any]) -> Any:
        headers = self._auth_headers("application/json")
        headers["Content-Type"] = "application/json"
        return await self._request(
            "POST",
            "/api/purchase/calculate",
            json_body=payload,
            headers=headers,
        )

    async def save_purchase(self, payload: dict[str, Any]) -> Any:
        headers = self._auth_headers("application/json")
        headers["Content-Type"] = "application/json"
        request_payload = dict(payload)
        for field in ("trnDate", "docDate"):
            if field in request_payload:
                request_payload[field] = self._purchase_datetime(request_payload[field])
        logger.info(
            "Madhushala purchase save request shopCode=%s companyCode=%s docNo=%s itemCount=%s",
            request_payload.get("shopCode"),
            request_payload.get("companyCode"),
            request_payload.get("docNo"),
            len(request_payload.get("items") or []),
        )
        return await self._request(
            "POST",
            "/api/purchase/save",
            json_body=request_payload,
            headers=headers,
        )
