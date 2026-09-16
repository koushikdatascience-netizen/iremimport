"""Resilient Madhushala API client for excise import and purchase orchestration."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import time
from typing import Any, ClassVar

import httpx

from app.config import settings


logger = logging.getLogger("madhushala-excise-bridge")


class MadhushalaApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class _CircuitBreaker:
    def __init__(self) -> None:
        self.failures = 0
        self.opened_until = 0.0

    def ensure_available(self) -> None:
        if self.opened_until > time.monotonic():
            raise MadhushalaApiError("Madhushala API circuit is temporarily open", 503)

    def success(self) -> None:
        self.failures = 0
        self.opened_until = 0.0

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= max(1, settings.MADHUSHALA_CIRCUIT_FAILURE_THRESHOLD):
            self.opened_until = time.monotonic() + max(1, settings.MADHUSHALA_CIRCUIT_OPEN_SECONDS)
            logger.error(
                "event=madhushala_circuit_open failures=%s openSeconds=%s",
                self.failures,
                settings.MADHUSHALA_CIRCUIT_OPEN_SECONDS,
            )


class MadhushalaClient:
    _shared_client: ClassVar[httpx.AsyncClient | None] = None
    _shared_client_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _request_semaphore: ClassVar[asyncio.Semaphore | None] = None
    _circuit: ClassVar[_CircuitBreaker] = _CircuitBreaker()

    def __init__(self, base_url: str, shop_code: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.shop_code = shop_code
        self.token = self._normalize_token(token)

    @classmethod
    async def _http_client(cls) -> httpx.AsyncClient:
        if cls._shared_client is not None:
            return cls._shared_client
        async with cls._shared_client_lock:
            if cls._shared_client is None:
                timeout = httpx.Timeout(
                    connect=settings.MADHUSHALA_CONNECT_TIMEOUT_SECONDS,
                    read=settings.MADHUSHALA_READ_TIMEOUT_SECONDS,
                    write=settings.MADHUSHALA_WRITE_TIMEOUT_SECONDS,
                    pool=settings.MADHUSHALA_POOL_TIMEOUT_SECONDS,
                )
                limits = httpx.Limits(
                    max_connections=settings.MADHUSHALA_MAX_CONNECTIONS,
                    max_keepalive_connections=settings.MADHUSHALA_MAX_KEEPALIVE_CONNECTIONS,
                    keepalive_expiry=30.0,
                )
                cls._shared_client = httpx.AsyncClient(timeout=timeout, limits=limits)
                cls._request_semaphore = asyncio.Semaphore(max(1, settings.MADHUSHALA_MAX_CONCURRENCY))
                logger.info(
                    "event=madhushala_http_pool_ready maxConnections=%s keepalive=%s concurrency=%s",
                    settings.MADHUSHALA_MAX_CONNECTIONS,
                    settings.MADHUSHALA_MAX_KEEPALIVE_CONNECTIONS,
                    settings.MADHUSHALA_MAX_CONCURRENCY,
                )
        return cls._shared_client

    @classmethod
    async def close_shared_client(cls) -> None:
        async with cls._shared_client_lock:
            if cls._shared_client is not None:
                await cls._shared_client.aclose()
            cls._shared_client = None
            cls._request_semaphore = None

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
        method_upper = method.upper()
        safe_retry = method_upper in {"GET", "HEAD", "OPTIONS"}
        attempts = max(1, settings.MADHUSHALA_READ_RETRY_ATTEMPTS if safe_retry else 1)
        url = f"{self.base_url}{path}"
        self._circuit.ensure_available()
        started = time.perf_counter()

        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await self._send(method_upper, url, params=params, json_body=json_body, headers=headers)
                status = response.status_code
                if status >= 500:
                    self._circuit.failure()
                    if safe_retry and attempt < attempts:
                        await asyncio.sleep(settings.MADHUSHALA_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
                        continue
                else:
                    self._circuit.success()
                response.raise_for_status()
                elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
                logger.info(
                    "event=madhushala_http method=%s path=%s status=%s elapsedMs=%s attempt=%s",
                    method_upper,
                    path,
                    status,
                    elapsed_ms,
                    attempt,
                )
                if not response.content:
                    return None
                try:
                    return response.json()
                except ValueError:
                    return response.text
            except httpx.HTTPStatusError as exc:
                last_error = exc
                message = exc.response.text or exc.response.reason_phrase
                if exc.response.status_code < 500 or not safe_retry or attempt >= attempts:
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
                    logger.warning(
                        "event=madhushala_http_error method=%s path=%s status=%s elapsedMs=%s attempt=%s",
                        method_upper,
                        path,
                        exc.response.status_code,
                        elapsed_ms,
                        attempt,
                    )
                    raise MadhushalaApiError(message, exc.response.status_code) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                self._circuit.failure()
                if safe_retry and attempt < attempts:
                    await asyncio.sleep(settings.MADHUSHALA_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
                    continue
                elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
                logger.warning(
                    "event=madhushala_transport_error method=%s path=%s elapsedMs=%s attempt=%s reason=%s",
                    method_upper,
                    path,
                    elapsed_ms,
                    attempt,
                    exc,
                )
                raise MadhushalaApiError(str(exc)) from exc

        raise MadhushalaApiError(str(last_error or "Madhushala API request failed"))

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        json_body: Any | None,
        headers: dict[str, str] | None,
    ) -> httpx.Response:
        client = await self._http_client()
        semaphore = self.__class__._request_semaphore or asyncio.Semaphore(1)
        try:
            async with semaphore:
                return await client.request(method, url, params=params, json=json_body, headers=headers)
        except httpx.ConnectError as exc:
            if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                raise
            # Compatibility fallback for legacy server certificates only. It is not
            # used on the normal healthy HTTPS path.
            async with httpx.AsyncClient(timeout=settings.MADHUSHALA_READ_TIMEOUT_SECONDS, verify=False) as fallback:
                return await fallback.request(method, url, params=params, json=json_body, headers=headers)

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

    async def get_dropdown_items(self, company_code: str, bill_type: str, search: str = "") -> list[dict[str, Any]]:
        safe_company_code = str(company_code or "").strip()
        safe_bill_type = str(bill_type or "").strip()
        params: dict[str, Any] = {
            "shopCode": self.shop_code,
            "companyCode": safe_company_code,
            "billType": safe_bill_type,
        }
        if str(search or "").strip():
            params["search"] = str(search).strip()
        data = await self._request(
            "GET",
            "/api/purchase/dropdown/items",
            params=params,
            headers=self._auth_headers("*/*"),
        )
        items = self._list_payload(data, "products")
        logger.info(
            "event=purchase_items_loaded shopCode=%s companyCode=%s billType=%s itemCount=%s",
            self.shop_code,
            safe_company_code,
            safe_bill_type,
            len(items),
        )
        return [item for item in items if isinstance(item, dict)]

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
            "event=purchase_master_loaded path=%s shopCode=%s companyCode=%s rowCount=%s",
            path,
            self.shop_code,
            safe_company_code,
            len(rows),
        )
        return rows

    async def get_purchase_suppliers(self, company_code: str) -> list[Any]:
        return await self._get_purchase_master(
            "/api/purchase/dropdown/suppliers", company_code, "suppliers", "supplierList"
        )

    async def get_purchase_storages(self, company_code: str) -> list[Any]:
        return await self._get_purchase_master(
            "/api/purchase/dropdown/storages", company_code, "storages", "stores", "storageList"
        )

    async def get_purchase_accounts(self, company_code: str) -> list[Any]:
        return await self._get_purchase_master(
            "/api/purchase/dropdown/purchase-accounts", company_code, "accounts", "purchaseAccounts", "accountList"
        )

    async def get_purchase_users(self, company_code: str) -> list[Any]:
        return await self._get_purchase_master(
            "/api/counter-sales/users", company_code, "users", "userList"
        )

    async def get_purchase_schemes(self, company_code: str) -> list[Any]:
        return await self._get_purchase_master(
            "/api/purchase/dropdown/schemes", company_code, "schemes", "schemeList"
        )

    async def get_purchase_tax_mode(self, company_code: str) -> Any:
        return await self._request(
            "GET",
            "/api/companies/purchase-tax-mode",
            params={"companyCode": company_code, "targetCompanyCode": company_code},
            headers=self._auth_headers("*/*"),
        )

    async def get_item(self, item_code: str, company_code: str) -> Any:
        return await self._request(
            "GET",
            f"/api/items/{item_code}",
            params={"companyCode": company_code, "targetCompanyCode": company_code},
            headers=self._auth_headers("*/*"),
        )

    async def get_tax_tags(self, company_code: str) -> Any:
        return await self._request(
            "GET",
            "/api/TaxTag/ShowTaxTag",
            params={"page": 1, "targetCompanyCode": company_code},
            headers=self._auth_headers("*/*"),
        )

    async def check_duplicate_bill(self, company_code: str, supplier_code: str, doc_no: str) -> Any:
        return await self._request(
            "GET",
            "/api/purchase/check-duplicate-billno",
            params={
                "shopCode": self.shop_code,
                "companyCode": company_code,
                "supplierCode": supplier_code,
                "docNo": doc_no,
                "excludeTrnNo": "",
            },
            headers=self._auth_headers("*/*"),
        )

    async def calculate_purchase(self, payload: dict[str, Any]) -> Any:
        headers = self._auth_headers("application/json")
        headers["Content-Type"] = "application/json"
        return await self._request("POST", "/api/purchase/calculate", json_body=payload, headers=headers)

    async def save_purchase(self, payload: dict[str, Any]) -> Any:
        headers = self._auth_headers("application/json")
        headers["Content-Type"] = "application/json"
        request_payload = dict(payload)
        for field in ("trnDate", "docDate"):
            if field in request_payload:
                request_payload[field] = self._purchase_datetime(request_payload[field])
        logger.info(
            "event=purchase_save_request shopCode=%s companyCode=%s docNo=%s itemCount=%s",
            request_payload.get("shopCode"),
            request_payload.get("companyCode"),
            request_payload.get("docNo"),
            len(request_payload.get("items") or []),
        )
        # POST /purchase/save is deliberately never auto-retried. A lost response
        # can represent a committed purchase and retrying could create a duplicate.
        return await self._request("POST", "/api/purchase/save", json_body=request_payload, headers=headers)
