from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any

from app.config import settings
from app.integrations.madhushala.masters import MadhushalaMasterService


_OPTION_ALIASES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "supplier": (
        ("supplierCode", "ledgerCode", "accountCode", "accCode", "code", "value", "id"),
        ("supplierName", "ledgerName", "accountName", "accName", "name", "text", "label", "description"),
    ),
    "storage": (
        ("storeCode", "storageCode", "godownCode", "warehouseCode", "code", "value", "id"),
        ("storeName", "storageName", "godownName", "warehouseName", "name", "text", "label", "description"),
    ),
    "scheme": (
        ("schemeCode", "schemecode", "schemeNo", "code", "value", "id"),
        ("schemeName", "schemename", "name", "text", "label", "description"),
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


def up_supplier_hint(payload: Any) -> str:
    """Read the consignor's unit/licensee name from UP Excise's paired party table."""
    if not isinstance(payload, dict):
        return ""
    tables = payload.get("tables")
    if not isinstance(tables, list):
        return ""
    fallback = ""
    for table in tables:
        if not isinstance(table, list):
            continue
        for raw_row in table:
            if not isinstance(raw_row, list):
                continue
            row = [_text(cell) for cell in raw_row]
            keys = [_key(cell) for cell in row]
            for field in ("unitname", "licenseename"):
                indexes = [index for index, key in enumerate(keys) if key == field]
                if indexes:
                    first = indexes[0]
                    if first + 1 < len(row) and row[first + 1]:
                        if field == "unitname":
                            return row[first + 1]
                        fallback = fallback or row[first + 1]
    return fallback


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
    normalized_claim_names = {_key(item) for item in preferred_claims}
    for key, value in claims.items():
        if _key(key) in normalized_claim_names and isinstance(value, (str, int)):
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
    """Return cached Purchase reference data, loading independent APIs concurrently on cache miss."""
    token = str(session.get("madhushala_token") or settings.MADHUSHALA_SERVICE_TOKEN or "")
    master = MadhushalaMasterService(session)
    calls = (
        ("suppliers", "supplier", master.suppliers()),
        ("storages", "storage", master.storages()),
        ("accounts", "account", master.accounts()),
        ("users", "user", master.users()),
        ("schemes", "scheme", master.schemes()),
    )
    results = await asyncio.gather(*(call[2] for call in calls), return_exceptions=True)
    options: dict[str, list[dict[str, str]]] = {}
    warnings: list[str] = []
    for (name, kind, _), result in zip(calls, results):
        if isinstance(result, Exception):
            options[name] = []
            warnings.append(f"{name}: {result}")
        else:
            options[name] = normalize_options(result, kind)

    try:
        tax_mode = await master.purchase_tax_mode()
    except Exception as exc:
        tax_mode = "ITEMWISE"
        warnings.append(f"taxMode: {exc}")

    defaults = {
        "supplierCode": _match_option(options["suppliers"], supplier_name) or _single_option(options["suppliers"]),
        "storeCode": _single_option(options["storages"]),
        "schemeCode": _single_option(options["schemes"]),
        "purchaseAccCode": _single_option(options["accounts"]),
        "userCode": _current_user_default(options["users"], token),
        "taxMode": tax_mode,
    }

    return {
        "shopCode": master.shop_code,
        "companyCode": master.company_code,
        "billType": master.bill_type,
        "supplierHint": _text(supplier_name),
        "options": options,
        "defaults": defaults,
        "taxMode": tax_mode,
        "warnings": warnings,
    }
