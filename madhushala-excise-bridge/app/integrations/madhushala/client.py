"""Madhushala API client for excise import mapping."""
from __future__ import annotations

from typing import Any

import httpx


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
        return await self._request(
            "GET",
            "/api/purchase/dropdown/items",
            params={"shopCode": self.shop_code, "companyCode": company_code, "billType": bill_type},
            headers=self._auth_headers("*/*"),
        )
