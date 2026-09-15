from __future__ import annotations

import base64
import json
import re
from typing import Any

from app.config import settings
from app.integrations.madhushala.client import MadhushalaApiError, MadhushalaClient


_OPTION_ALIASES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "supplier": (
        ("supplierCode", "ledgerCode", "accountCode", "accCode", "code", "value", "id"),
        ("supplierName", "ledgerName", "accountName", "accName", "name", "text", "label", "description"),
    ),
    "storage": (
        ("storeCode", "storageCode", "godownCode", "warehouseCode", "code", "value", "id"),
        ("storeName", "storageName", "godownName", "warehouseName", "name", "text", "label", "description"),
    ),
    "account": (
        ("purchaseAccCode", "accountCode", "accCode", "ledgerCode", "code", "value", "id"),
        ("purchaseAccName", "accountName", "accName", "ledgerName", "name", "text", "label", "description"),
    ),
    "user": (
        ("userCode", "loginCode", "employeeCode", "code", "value", "id"),
        ("userName", "loginName", "displayName", "employeeName", "name", "text", "label", "description"),
    ),
}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dict_value(row: dict[str, Any], aliases: tuple[str, ...]) -> str:
    normalized = {_key(key): value for key, value in row.items()}
    for alias in aliases:
        value = normalized.get(_key(alias))
        if value not in (None, ""):
            return _text(value)
    return ""


def _fallback_dict_value(row: dict[str, Any], *, want_name: bool) -> str:
    scalar = [(str(key), value) for key, value in row.items() if isinstance(value, (str, int, float)) and value not in (None, "")]
    if want_name:
        preferred = [value for key, value in scalar if any(token in _key(key) for token in ("name", "text", "label", "description"))]
    else:
        preferred = [value for key, value in scalar if any(token in _key(key) for token in ("code", "id", "value"))]
    if preferred:
        return _text(preferred[0])
    return ""


def normalize_options(rows: Any, kind: str) -> list[dict[str, str]]:
    aliases = _OPTION_ALIASES[kind]
    if not isinstance(rows, list):
        return []

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, (str, int, float)):
            code = name = _text(row)
        elif isinstance(row, dict):
            code = _dict_value(row, aliases[0]) or _fallback_dict_value(row, want_name=False)
            name = _dict_value(row, aliases[1]) or _fallback_dict_value(row, want_name=True) or code
        else:
            continue
        if not code or code in seen:
            continue
        seen.add(code)
        result.append({"code": code, "name": name or code})
    return result


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _text(value).casefold()).strip()


def _match_option(options: list[dict[str, str]], hint: str) -> str:
    target = _normalized_name(hint)
    if not target:
        return ""

    exact = [option for option in options if _normalized_name(option.get("name", "")) == target]
    if len(exact) == 1:
        return exact[0]["code"]

    fuzzy = [
        option
        for option in options
        if target in _normalized_name(option.get("name", ""))
        or _normalized_name(option.get("name", "")) in target
    ]
    return fuzzy[0]["code"] if len(fuzzy) == 1 else ""


def _single_option(options: list[dict[str, str]]) -> str:
    return options[0]["code"] if len(options) == 1 else ""


def _jwt_claims(token: str) -> dict[str, Any]:
    value = str(token or "").strip()
    if value.casefold().startswith("bearer "):
        value = value[7:].strip()
    parts = value.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode()).decode("utf-8")
        data = json.loads(decoded)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _current_user_default(options: list[dict[str, str]], token: str) -> str:
    claims = _jwt_claims(token)
    if not claims:
        return _single_option(options)

    candidates: list[str] = []
    preferred_claims = {
        "usercode",
        "username",
        "preferredusername",
        "unique_name",
        "name",
        "email",
        "sub",
    }
    for key, value in claims.items():
        if _key(key) in {_key(item) for item in preferred_claims} and isinstance(value, (str, int)):
            candidates.append(_text(value))

    for candidate in candidates:
        by_code = [option for option in options if option.get("code", "").casefold() == candidate.casefold()]
        if len(by_code) == 1:
            return by_code[0]["code"]
        by_name = _match_option(options, candidate)
        if by_name:
            return by_name
    return _single_option(options)


async def build_purchase_context(
    session: dict[str, Any],
    supplier_name: str = "",
) -> dict[str, Any]:
    token = str(session.get("madhushala_token") or settings.MADHUSHALA_SERVICE_TOKEN or "")
    client = MadhushalaClient(
        settings.MADHUSHALA_BASE_URL,
        str(session.get("shop_code") or ""),
        token,
    )
    company_code = str(session.get("company_code") or settings.DEFAULT_COMPANY_CODE)

    calls = {
        "suppliers": (client.get_purchase_suppliers, "supplier"),
        "storages": (client.get_purchase_storages, "storage"),
        "accounts": (client.get_purchase_accounts, "account"),
        "users": (client.get_purchase_users, "user"),
    }
    options: dict[str, list[dict[str, str]]] = {}
    warnings: list[str] = []

    for key, (method, kind) in calls.items():
        try:
            rows = await method(company_code)
            options[key] = normalize_options(rows, kind)
        except MadhushalaApiError as exc:
            options[key] = []
            warnings.append(f"{key}: {exc}")

    defaults = {
        "supplierCode": _match_option(options["suppliers"], supplier_name) or _single_option(options["suppliers"]),
        "storeCode": _single_option(options["storages"]),
        "purchaseAccCode": _single_option(options["accounts"]),
        "userCode": _current_user_default(options["users"], token),
    }

    return {
        "shopCode": str(session.get("shop_code") or ""),
        "companyCode": company_code,
        "billType": str(session.get("bill_type") or settings.DEFAULT_BILL_TYPE),
        "supplierHint": _text(supplier_name),
        "options": options,
        "defaults": defaults,
        "warnings": warnings,
    }
