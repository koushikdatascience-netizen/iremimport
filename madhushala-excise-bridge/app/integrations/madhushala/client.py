"""Production Madhushala API client for excise import and purchase orchestration."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import random
import time
from typing import Any

import httpx

from app.config import settings
from app.observability import get_correlation_id, observe_madhushala_request, redact_for_logging
from app.services.excise_tax_tags import apply_excise_tax_tags


logger = logging.getLogger("madhushala-excise-bridge.madhushala")

_VERIFIED_CLIENT: httpx.AsyncClient | None = None
_INSECURE_CLIENT: httpx.AsyncClient | None = None
_CLIENT_LOCK = asyncio.Lock()
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class MadhushalaApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _http_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=settings.MADHUSHALA_CONNECT_TIMEOUT_SECONDS,
        read=settings.MADHUSHALA_READ_TIMEOUT_SECONDS,
        write=settings.MADHUSHALA_WRITE_TIMEOUT_SECONDS,
        pool=settings.MADHUSHALA_POOL_TIMEOUT_SECONDS,
    )


def _http_limits() -> httpx.Limits:
    return httpx.Limits(
        max_connections=max(1, settings.MADHUSHALA_MAX_CONNECTIONS),
        max_keepalive_connections=max(1, settings.MADHUSHALA_MAX_KEEPALIVE_CONNECTIONS),
    )


async def _shared_client(*, verify: bool) -> httpx.AsyncClient:
    global _VERIFIED_CLIENT, _INSECURE_CLIENT
    existing = _VERIFIED_CLIENT if verify else _INSECURE_CLIENT
    if existing is not None and not existing.is_closed:
        return existing

    async with _CLIENT_LOCK:
        existing = _VERIFIED_CLIENT if verify else _INSECURE_CLIENT
        if existing is not None and not existing.is_closed:
            return existing
        client = httpx.AsyncClient(
            timeout=_http_timeout(),
            limits=_http_limits(),
            verify=verify,
            follow_redirects=True,
        )
        if verify:
            _VERIFIED_CLIENT = client
        else:
            _INSECURE_CLIENT = client
        return client


async def close_madhushala_http_clients() -> None:
    global _VERIFIED_CLIENT, _INSECURE_CLIENT
    for client in (_VERIFIED_CLIENT, _INSECURE_CLIENT):
        if client is not None and not client.is_closed:
            await client.aclose()
    _VERIFIED_CLIENT = None
    _INSECURE_CLIENT = None


class MadhushalaClient:
    def __init__(self, base_url: str, shop_code: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.shop_code = shop_code
        self.token = self._normalize_token(token)
        self._excise_tax_tags_cache: list[dict[str, Any]] | None = None

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
        return {
            "accept": accept,
            "Authorization": f"Bearer {self.token}",
            "X-Correlation-ID": get_correlation_id(),
        }

    async def _send_once(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        json_body: Any | None,
        headers: dict[str, str] | None,
    ) -> httpx.Response:
        try:
            client = await _shared_client(verify=True)
            return await client.request(method, url, params=params, json=json_body, headers=headers)
        except httpx.ConnectError as exc:
            if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                raise
            logger.warning("madhushala_tls_fallback host=%s", self.base_url)
            client = await _shared_client(verify=False)
            return await client.request(method, url, params=params, json=json_body, headers=headers)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        method = method.upper()
        url = f"{self.base_url}{path}"
        max_attempts = 1 + (max(0, settings.MADHUSHALA_GET_RETRIES) if method == "GET" else 0)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            started = time.perf_counter()
            logger.info(
                "madhushala_flow_request method=%s path=%s attempt=%s",
                method,
                path,
                attempt,
                extra={
                    "event": "madhushala_flow_request",
                    "httpMethod": method,
                    "upstreamPath": path,
                    "attempt": attempt,
                    "shopCode": self.shop_code,
                    "query": redact_for_logging(params or {}),
                    "requestPayload": redact_for_logging(json_body),
                },
            )
            try:
                response = await self._send_once(
                    method,
                    url,
                    params=params,
                    json_body=json_body,
                    headers=headers,
                )
                duration_seconds = time.perf_counter() - started
                duration_ms = int(duration_seconds * 1000)
                observe_madhushala_request(method, path, response.status_code, duration_seconds)
                logger.info(
                    "madhushala_http method=%s path=%s status=%s durationMs=%s attempt=%s",
                    method,
                    path,
                    response.status_code,
                    duration_ms,
                    attempt,
                    extra={
                        "event": "madhushala_http",
                        "httpMethod": method,
                        "upstreamPath": path,
                        "statusCode": response.status_code,
                        "durationMs": duration_ms,
                        "attempt": attempt,
                    },
                )
                if method == "GET" and response.status_code in _RETRYABLE_STATUS and attempt < max_attempts:
                    logger.warning(
                        "madhushala_flow_response method=%s path=%s status=%s retrying=true",
                        method,
                        path,
                        response.status_code,
                        extra={
                            "event": "madhushala_flow_response",
                            "httpMethod": method,
                            "upstreamPath": path,
                            "statusCode": response.status_code,
                            "durationMs": duration_ms,
                            "attempt": attempt,
                            "retrying": True,
                            "responsePayload": redact_for_logging(
                                response.json() if response.content and "json" in response.headers.get("content-type", "").casefold()
                                else response.text
                            ),
                        },
                    )
                    await asyncio.sleep((0.12 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.08))
                    continue
                response.raise_for_status()
                if not response.content:
                    logger.info(
                        "madhushala_flow_response method=%s path=%s status=%s",
                        method,
                        path,
                        response.status_code,
                        extra={
                            "event": "madhushala_flow_response",
                            "httpMethod": method,
                            "upstreamPath": path,
                            "statusCode": response.status_code,
                            "durationMs": duration_ms,
                            "attempt": attempt,
                            "responsePayload": None,
                        },
                    )
                    return None
                try:
                    response_payload = response.json()
                except ValueError:
                    response_payload = response.text
                logger.info(
                    "madhushala_flow_response method=%s path=%s status=%s",
                    method,
                    path,
                    response.status_code,
                    extra={
                        "event": "madhushala_flow_response",
                        "httpMethod": method,
                        "upstreamPath": path,
                        "statusCode": response.status_code,
                        "durationMs": duration_ms,
                        "attempt": attempt,
                        "responsePayload": redact_for_logging(response_payload),
                    },
                )
                return response_payload
            except httpx.HTTPStatusError as exc:
                message = exc.response.text or exc.response.reason_phrase
                try:
                    error_payload = exc.response.json()
                except ValueError:
                    error_payload = message
                logger.error(
                    "madhushala_flow_error method=%s path=%s status=%s",
                    method,
                    path,
                    exc.response.status_code,
                    extra={
                        "event": "madhushala_flow_error",
                        "httpMethod": method,
                        "upstreamPath": path,
                        "statusCode": exc.response.status_code,
                        "attempt": attempt,
                        "query": redact_for_logging(params or {}),
                        "requestPayload": redact_for_logging(json_body),
                        "responsePayload": redact_for_logging(error_payload),
                    },
                )
                raise MadhushalaApiError(message, exc.response.status_code) from exc
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                last_error = exc
                if method == "GET" and attempt < max_attempts:
                    await asyncio.sleep((0.12 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.08))
                    continue
                raise MadhushalaApiError(str(exc)) from exc
            except httpx.HTTPError as exc:
                raise MadhushalaApiError(str(exc)) from exc

        raise MadhushalaApiError(str(last_error or "Madhushala request failed"))

    async def _get_excise_tax_tags(self) -> list[dict[str, Any]]:
        """Load TaxTag once per client/batch using the same endpoint as Madhushala UI."""
        if self._excise_tax_tags_cache is not None:
            return self._excise_tax_tags_cache
        data = await self._request(
            "GET",
            "/api/TaxTag/ShowTaxTag",
            headers=self._auth_headers("application/json"),
        )
        rows = [row for row in self._list_payload(data, "taxTags", "taxTagList") if isinstance(row, dict)]
        self._excise_tax_tags_cache = rows
        logger.info("excise_tax_tags_loaded shopCode=%s count=%s", self.shop_code, len(rows))
        return rows

    async def save_excise_item(self, payload: dict[str, str]) -> dict[str, Any]:
        headers = self._auth_headers("application/json")
        headers["Content-Type"] = "application/json"
        tax_tags = await self._get_excise_tax_tags()
        request_payload = apply_excise_tax_tags(payload, tax_tags)
        logger.info(
            "excise_item_tax_values shopCode=%s item=%s t1=%s t2=%s t3=%s t4=%s",
            self.shop_code,
            request_payload.get("itemName"),
            request_payload.get("t1"),
            request_payload.get("t2"),
            request_payload.get("t3"),
            request_payload.get("t4"),
        )
        return await self._request(
            "POST",
            "/api/excise-import/ExciseItemMasterSave",
            params={"shopCode": self.shop_code},
            json_body=request_payload,
            headers=headers,
        )

    async def get_company_master(self, company_code: str) -> dict[str, Any]:
        """Load company/state-specific Excise credentials from Madhushala."""
        safe_company_code = str(company_code or "").strip()
        if not safe_company_code:
            raise MadhushalaApiError("companyCode is required")
        data = await self._request(
            "GET",
            f"/api/company-mast/{safe_company_code}",
            params={"shopCode": self.shop_code},
            headers=self._auth_headers("application/json"),
        )
        if isinstance(data, dict):
            for key in ("data", "result", "company"):
                nested = data.get(key)
                if isinstance(nested, dict):
                    return nested
            return data
        raise MadhushalaApiError("Company master returned an invalid response")

    async def get_excise_items(self, search: str = "") -> list[dict[str, Any]]:
        """Return the authoritative Excise↔software mapping master for this shop."""
        params: dict[str, Any] = {"shopCode": self.shop_code}
        safe_search = str(search or "").strip()
        if safe_search:
            params["search"] = safe_search
        data = await self._request(
            "GET",
            "/api/excise-import/excise-items",
            params=params,
            headers=self._auth_headers("*/*"),
        )
        return [row for row in self._list_payload(data, "items", "exciseItems") if isinstance(row, dict)]

    async def get_unmapped_items(self) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/excise-import/unmapped-items",
            params={"shopCode": self.shop_code},
            headers=self._auth_headers("*/*"),
        )

    async def save_mapping(self, mappings: list[dict[str, str]]) -> Any:
        headers = {"accept": "*/*", "Content-Type": "application/json", "X-Correlation-ID": get_correlation_id()}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return await self._request(
            "POST",
            "/api/excise-import/save-mapping",
            params={"shopCode": self.shop_code},
            json_body=mappings,
            headers=headers,
        )

    async def get_dropdown_items(
        self,
        company_code: str,
        bill_type: str,
        search: str = "",
    ) -> list[dict[str, Any]]:
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
        return [row for row in self._list_payload(data, "products") if isinstance(row, dict)]

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
            "madhushala_master path=%s shopCode=%s companyCode=%s rowCount=%s",
            path,
            self.shop_code,
            safe_company_code,
            len(rows),
        )
        return rows

    async def get_purchase_company(self) -> list[Any]:
        data = await self._request(
            "GET",
            "/api/purchase/dropdown/company",
            params={"shopCode": self.shop_code},
            headers=self._auth_headers("*/*"),
        )
        return self._list_payload(data, "companies", "companyList")

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
        safe = str(company_code or "").strip()
        return await self._request(
            "GET",
            "/api/companies/purchase-tax-mode",
            params={"companyCode": safe, "targetCompanyCode": safe},
            headers=self._auth_headers("application/json"),
        )

    async def get_item(self, item_code: str, company_code: str) -> Any:
        safe_company = str(company_code or "").strip()
        # Purchase item detail must stay in the same shop/company scope as the
        # Purchase dropdown. targetCompanyCode is an admin/generic item scope
        # and can reject an otherwise valid purchase company.
        return await self._request(
            "GET",
            f"/api/items/{item_code}",
            params={"shopCode": self.shop_code, "companyCode": safe_company},
            headers=self._auth_headers("application/json"),
        )

    async def get_tax_tags(self, company_code: str, *, search: str = "", page: int = 1) -> list[Any]:
        params: dict[str, Any] = {
            "page": max(1, int(page)),
            "targetCompanyCode": str(company_code or "").strip(),
        }
        if str(search or "").strip():
            params["search"] = str(search).strip()
        data = await self._request(
            "GET",
            "/api/TaxTag/ShowTaxTag",
            params=params,
            headers=self._auth_headers("application/json"),
        )
        return self._list_payload(data, "taxTags", "taxTagList")

    async def get_tax_tag_by_code(self, code: str, company_code: str, item_type: str = "") -> Any:
        params: dict[str, Any] = {"targetCompanyCode": str(company_code or "").strip()}
        if str(item_type or "").strip():
            params["itemType"] = str(item_type).strip()
        return await self._request(
            "GET",
            f"/api/TaxTag/by-TaxCode/{code}",
            params=params,
            headers=self._auth_headers("application/json"),
        )

    async def check_duplicate_bill(
        self,
        company_code: str,
        supplier_code: str,
        doc_no: str,
        *,
        exclude_trn_no: str = "",
    ) -> Any:
        params: dict[str, Any] = {
            "shopCode": self.shop_code,
            "companyCode": str(company_code or "").strip(),
            "supplierCode": str(supplier_code or "").strip(),
            "docNo": str(doc_no or "").strip(),
        }
        if str(exclude_trn_no or "").strip():
            params["excludeTrnNo"] = str(exclude_trn_no).strip()
        return await self._request(
            "GET",
            "/api/purchase/check-duplicate-billno",
            params=params,
            headers=self._auth_headers("application/json"),
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

    async def get_purchase_for_edit(
        self,
        company_code: str,
        year_code: str,
        trn_no: str,
    ) -> Any:
        return await self._request(
            "GET",
            "/api/purchase/edit",
            params={
                "shopCode": self.shop_code,
                "companyCode": str(company_code or "").strip(),
                "yearCode": str(year_code or "").strip(),
                "trnNo": str(trn_no or "").strip(),
            },
            headers=self._auth_headers("application/json"),
        )

    async def save_purchase(self, payload: dict[str, Any]) -> Any:
        headers = self._auth_headers("application/json")
        headers["Content-Type"] = "application/json"
        request_payload = dict(payload)
        for field in ("trnDate", "docDate"):
            if field in request_payload:
                request_payload[field] = self._purchase_datetime(request_payload[field])
        logger.info(
            "purchase_save_outbound shopCode=%s companyCode=%s docNo=%s itemCount=%s batches=%s",
            request_payload.get("shopCode"),
            request_payload.get("companyCode"),
            request_payload.get("docNo"),
            len(request_payload.get("items") or []),
            [
                {
                    "itemCode": item.get("itemCode"),
                    "batchNo": item.get("batchNo"),
                }
                for item in (request_payload.get("items") or [])
                if str(item.get("batchNo") or "").strip()
            ],
        )
        return await self._request(
            "POST",
            "/api/purchase/save",
            json_body=request_payload,
            headers=headers,
        )
