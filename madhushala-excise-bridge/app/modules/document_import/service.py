from __future__ import annotations

import json
import mimetypes
import re
import tempfile
import uuid
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException, UploadFile

from app.config import settings
from app.db import conn, now_iso
from app.modules.document_import.llama_client import LlamaCloudClient, LlamaCloudError
from app.modules.document_import.normalizer import normalize_extracted_document
from app.modules.document_import.schemas import ExtractedDocument, NormalizedImportItem
from app.services.mapping_service import MappingService
from app.integrations.madhushala.client import MadhushalaApiError, MadhushalaClient


IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}
MIME_BY_EXT = {
    "pdf": {"application/pdf"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "png": {"image/png"},
}


class _ReadableHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self._current_cell: list[str] | None = None
        self._current_row: list[str] | None = None
        self.rows: list[list[str]] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag == "tr":
            self._current_row = []
        if tag in {"td", "th"}:
            self._current_cell = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in {"td", "th"} and self._current_cell is not None:
            value = " ".join("".join(self._current_cell).split())
            if self._current_row is not None:
                self._current_row.append(value)
            self._current_cell = None
        if tag == "tr" and self._current_row is not None:
            if any(self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data):
        clean = " ".join(data.split())
        if not clean:
            return
        if self._in_title:
            self.title = f"{self.title} {clean}".strip()
        if self._current_cell is not None:
            self._current_cell.append(data)
        self.text_parts.append(clean)

    def payload(self) -> dict[str, Any]:
        text = " ".join(self.text_parts)
        return {
            "title": self.title,
            "text": text[:12000],
            "tables": self.rows[:50],
        }


def _money(value: Any) -> float:
    try:
        return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


def _int_value(value: Any) -> int:
    try:
        return max(0, int(Decimal(str(value or 0))))
    except Exception:
        return 0


def _today() -> str:
    return date.today().isoformat()


class DocumentImportService:
    def __init__(self, mapping_service: MappingService):
        self.mapping_service = mapping_service

    def _client_for_session(self, session: dict[str, Any]) -> MadhushalaClient:
        token = session.get("madhushala_token") or settings.MADHUSHALA_SERVICE_TOKEN
        return MadhushalaClient(settings.MADHUSHALA_BASE_URL, session["shop_code"], token)

    def _source_type(self, ext: str) -> str:
        return "DOCUMENT_IMAGE" if ext in IMAGE_EXTENSIONS else "DOCUMENT_PDF"

    def _validate_file(self, upload: UploadFile, data: bytes) -> str:
        if not data:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        max_bytes = settings.DOCUMENT_IMPORT_MAX_MB * 1024 * 1024
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail=f"File is larger than {settings.DOCUMENT_IMPORT_MAX_MB} MB")

        filename = Path(upload.filename or "").name
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in {item.lower() for item in settings.DOCUMENT_IMPORT_ALLOWED_TYPES}:
            raise HTTPException(status_code=400, detail="Unsupported file type")

        content_type = (upload.content_type or mimetypes.guess_type(filename)[0] or "").lower()
        allowed_mimes = MIME_BY_EXT.get(ext, set())
        if allowed_mimes and content_type and content_type not in allowed_mimes:
            raise HTTPException(status_code=400, detail="File content type does not match the extension")

        if ext == "pdf" and not data.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="Invalid PDF file")
        if ext in {"jpg", "jpeg"} and not data.startswith(b"\xff\xd8"):
            raise HTTPException(status_code=400, detail="Invalid JPEG file")
        if ext == "png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(status_code=400, detail="Invalid PNG file")
        return ext

    def _create_job(self, session: dict[str, Any], source_type: str, filename: str) -> str:
        job_id = uuid.uuid4().hex
        now = now_iso()
        with conn() as db:
            db.execute(
                """
                INSERT INTO import_jobs(
                    id, shop_code, session_id, source_type, status, source_filename,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, session["shop_code"], session["session_id"], source_type, "CREATED", filename, now, now),
            )
        return job_id

    def _update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{name}=?" for name in fields)
        values = list(fields.values()) + [job_id]
        with conn() as db:
            db.execute(f"UPDATE import_jobs SET {assignments} WHERE id=?", values)

    def _persist_items(self, job_id: str, items: list[NormalizedImportItem]) -> None:
        now = now_iso()
        with conn() as db:
            for item in items:
                db.execute(
                    """
                    INSERT INTO import_items(
                        id, job_id, source_item_id, raw_name, normalized_name, brand, ml,
                        packing, quantity, rate, mrp, amount, barcode, confidence,
                        mapping_status, raw_data_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        job_id,
                        item.sourceItemId,
                        item.rawName,
                        item.normalizedName,
                        item.brand,
                        item.ml,
                        item.packing,
                        item.quantity,
                        item.rate,
                        item.mrp,
                        item.amount,
                        item.barcode,
                        item.confidence,
                        "PENDING",
                        json.dumps(item.rawData, ensure_ascii=False),
                        now,
                        now,
                    ),
                )

    def get_job(self, session: dict[str, Any], job_id: str) -> dict[str, Any]:
        with conn() as db:
            row = db.execute(
                "SELECT * FROM import_jobs WHERE id=? AND shop_code=? AND session_id=?",
                (job_id, session["shop_code"], session["session_id"]),
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Import job not found")
        return dict(row)

    def get_items(self, session: dict[str, Any], job_id: str) -> list[dict[str, Any]]:
        self.get_job(session, job_id)
        with conn() as db:
            rows = db.execute("SELECT * FROM import_items WHERE job_id=? ORDER BY created_at, id", (job_id,)).fetchall()
        return [dict(row) for row in rows]

    async def process_upload(self, session: dict[str, Any], upload: UploadFile) -> dict[str, Any]:
        data = await upload.read()
        ext = self._validate_file(upload, data)
        filename = Path(upload.filename or f"document.{ext}").name
        source_type = self._source_type(ext)
        job_id = self._create_job(session, source_type, filename)
        temp_path: Path | None = None

        try:
            self._update_job(job_id, status="UPLOADING")
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as handle:
                handle.write(data)
                temp_path = Path(handle.name)

            self._update_job(job_id, status="EXTRACTING")
            extracted = await LlamaCloudClient().extract_products(temp_path, filename)
            self._update_job(
                job_id,
                status="NORMALIZING",
                document_type=extracted.documentType,
                supplier_name=extracted.supplierName,
                invoice_number=extracted.invoiceNumber,
                invoice_date=extracted.invoiceDate,
            )
            normalized = normalize_extracted_document(extracted, source_type)
            if not normalized:
                self._update_job(job_id, status="FAILED", error="No valid product rows were extracted")
                raise HTTPException(status_code=422, detail="No valid product rows were extracted")

            self._persist_items(job_id, normalized)
            self._update_job(job_id, status="CHECKING_MAPPING", extracted_count=len(normalized))
            await self.mapping_service.prepare_document_job(session, job_id)
            workspace = await self.mapping_service.workspace_for_session(session, job_id=job_id)
            unmapped_count = len(workspace.get("unmappedItems", []))
            status = "MAPPING_REQUIRED" if unmapped_count else "READY"
            self._update_job(job_id, status=status, mapped_count=len(normalized) - unmapped_count)
            return {
                "job": self.get_job(session, job_id),
                "summary": {
                    "detected": len(normalized),
                    "recognized": len(normalized) - unmapped_count,
                    "needMapping": unmapped_count,
                },
                "extractedDocument": extracted.model_dump(),
                "normalizedItems": [item.model_dump() for item in normalized],
            }
        except HTTPException:
            raise
        except MadhushalaApiError as exc:
            self._update_job(job_id, status="FAILED", error=str(exc))
            status_code = 401 if exc.status_code == 401 else 403 if exc.status_code == 403 else 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except (LlamaCloudError, ValueError) as exc:
            self._update_job(job_id, status="FAILED", error=str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)

    async def extract_qr_link(self, session: dict[str, Any], url: str) -> dict[str, Any]:
        clean_url = str(url or "").strip()
        parsed = urlparse(clean_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="QR did not contain a valid HTTP link")
        host = (parsed.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.endswith(".local"):
            raise HTTPException(status_code=400, detail="QR link host is not allowed")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(clean_url, headers={"accept": "text/html,application/json,*/*"})
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text[:500] or "QR link request failed") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"QR link could not be opened: {exc}") from exc

        content_type = response.headers.get("content-type", "")
        body: Any
        if "json" in content_type:
            try:
                body = response.json()
            except ValueError:
                body = {"text": response.text[:12000]}
        else:
            parser = _ReadableHtmlParser()
            parser.feed(response.text[:500000])
            body = parser.payload()
        return {
            "source": "QR_LINK",
            "url": clean_url,
            "finalUrl": str(response.url),
            "statusCode": response.status_code,
            "contentType": content_type,
            "extracted": body,
        }

    @staticmethod
    def _catalogue_key(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(name or "").casefold())

    def _catalogue_value(self, item: dict[str, Any] | None, *aliases: str) -> Any:
        if not item:
            return None
        normalized = {self._catalogue_key(key): value for key, value in item.items()}
        for alias in aliases:
            key = self._catalogue_key(alias)
            if key in normalized and normalized[key] not in (None, ""):
                return normalized[key]
        return None

    def _catalogue_item(self, item_code: str, catalogue: list[dict[str, Any]]) -> dict[str, Any] | None:
        target = str(item_code or "").strip()
        for item in catalogue:
            code = self._catalogue_value(item, "itemCode", "code", "value", "id")
            if str(code or "").strip() == target:
                return item
        return None

    def _purchase_item_name(self, item_code: str, catalogue: list[dict[str, Any]], fallback: str) -> str:
        item = self._catalogue_item(item_code, catalogue)
        name = self._catalogue_value(item, "itemName", "name", "label", "text")
        return str(name or fallback).strip() or fallback

    def _catalogue_money(self, item: dict[str, Any] | None, *aliases: str) -> float:
        return _money(self._catalogue_value(item, *aliases))

    def _catalogue_int(self, item: dict[str, Any] | None, *aliases: str) -> int:
        return _int_value(self._catalogue_value(item, *aliases))

    def _build_purchase_calculation_request(self, payload: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
        calc_items = []
        for item in payload.get("items") or []:
            calc_items.append({
                "itemCode": item.get("itemCode", ""),
                "box": _int_value(item.get("box")),
                "loose": _int_value(item.get("loose")),
                "free": _int_value(item.get("freeQnty")),
                "boxRate": _money(item.get("boxRate") if item.get("boxRate") not in (None, "") else item.get("rate")),
                "looseRate": _money(item.get("looseRate")),
                "mrp": _money(item.get("mrp")),
                "discount": _money(item.get("discount")),
                "cgst": _money(item.get("cgst")),
                "sgst": _money(item.get("sgst")),
                "cess": _money(item.get("cess")),
                "addCess": _money(item.get("addCess")),
                "igst": _money(item.get("igst")),
                "t1Amt": _money(item.get("t1Amt")),
                "t2Amt": _money(item.get("t2Amt")),
                "t3Amt": _money(item.get("t3Amt")),
                "t4Amt": _money(item.get("t4Amt")),
                "etd": _money(item.get("etd")),
                "packing": _int_value(item.get("packing") or item.get("qnty")),
                "t1Rate": _money(item.get("t1Rate")),
                "t2Rate": _money(item.get("t2Rate")),
                "t3Rate": _money(item.get("t3Rate")),
                "t4Rate": _money(item.get("t4Rate")),
            })
        return {
            "shopCode": payload["shopCode"],
            "companyCode": payload["companyCode"],
            "schemeCode": payload.get("schemeCode", ""),
            "salesTaxRate": _money(header.get("salesTaxRate") or payload.get("salesTaxOnMRP") or 0),
            "salesTaxIncludingFree": bool(payload.get("saletaxIncludingFree")),
            "items": calc_items,
        }

    def _calculated_items(self, response: Any) -> list[dict[str, Any]]:
        if isinstance(response, list):
            return [item for item in response if isinstance(item, dict)]
        if not isinstance(response, dict):
            return []
        candidates = [response]
        for key in ("data", "result", "calculation", "payload"):
            nested = response.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
        for candidate in candidates:
            items = candidate.get("items") or candidate.get("Items") or candidate.get("itemDetails")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    def _merge_purchase_calculation(self, payload: dict[str, Any], response: Any) -> None:
        calculated = self._calculated_items(response)
        by_code = {str(item.get("itemCode") or item.get("code") or "").strip(): item for item in calculated}
        merge_fields = (
            "qnty", "rate", "mrp", "itemAmount", "discount", "cgst", "sgst", "cess", "addCess", "igst",
            "t1Amt", "t2Amt", "t3Amt", "t4Amt", "etd", "cgstInptLdgr", "sgstInptLdgr", "cessInptLdgr",
            "adCessInptLdgr", "igstInptLdgr",
        )
        for index, item in enumerate(payload.get("items") or []):
            calc = by_code.get(str(item.get("itemCode") or "").strip()) or (calculated[index] if index < len(calculated) else None)
            if not calc:
                continue
            for field in merge_fields:
                value = calc.get(field)
                if value is None and field == "addCess":
                    value = calc.get("addcess") or calc.get("adCess")
                if value is not None:
                    item[field] = value if field.endswith("Ldgr") else _money(value)
        if isinstance(response, dict):
            for field in ("grossAmount", "taxAmount", "netAmount", "discount", "salesTaxOnMRP", "roundOff"):
                value = response.get(field) or response.get(field[:1].upper() + field[1:])
                if value is not None:
                    payload[field] = _money(value)
            taxes = response.get("taxes") or response.get("Taxes")
            if isinstance(taxes, list):
                payload["taxes"] = taxes

    async def _purchase_items_for_job(self, session: dict[str, Any], job_id: str) -> list[dict[str, Any]]:
        self.get_job(session, job_id)
        company_code = str(session.get("company_code") or "").strip()
        bill_type = str(session.get("bill_type") or "AI").strip() or "AI"
        catalogue = await self._client_for_session(session).get_dropdown_items(company_code, bill_type)
        with conn() as db:
            rows = db.execute("SELECT * FROM import_items WHERE job_id=? ORDER BY created_at, id", (job_id,)).fetchall()
            items = []
            missing = []
            for row in rows:
                try:
                    raw_data = json.loads(row["raw_data_json"] or "{}")
                except Exception:
                    raw_data = {}
                if not isinstance(raw_data, dict):
                    raw_data = {}

                raw_normalized = {self._catalogue_key(key): value for key, value in raw_data.items()}

                def raw_value(*aliases: str) -> Any:
                    for alias in aliases:
                        value = raw_normalized.get(self._catalogue_key(alias))
                        if value not in (None, ""):
                            return value
                    return None

                def raw_money(*aliases: str, fallback: Any = None) -> float:
                    value = raw_value(*aliases)
                    return _money(fallback if value in (None, "") else value)

                def raw_int(*aliases: str, fallback: Any = None) -> int:
                    value = raw_value(*aliases)
                    return _int_value(fallback if value in (None, "") else value)

                mapped = (row["mapped_item_code"] or "").strip()
                if not mapped and row["excise_item_code"]:
                    map_row = db.execute(
                        "SELECT madhushala_item_code FROM mappings WHERE shop_code=? AND excise_item_code=?",
                        (session["shop_code"], str(row["excise_item_code"])),
                    ).fetchone()
                    mapped = (map_row["madhushala_item_code"] if map_row else "").strip()
                if not mapped:
                    missing.append(row["raw_name"] or row["normalized_name"] or row["id"])
                    continue

                fallback_name = str(row["normalized_name"] or row["raw_name"] or mapped)
                catalogue_item = self._catalogue_item(mapped, catalogue)
                catalogue_packing = self._catalogue_int(catalogue_item, "packing", "bottlePerCase", "bottlesPerCase", "caseQty")

                packing = raw_int("packing", "bottlePerCase", "bottlesPerCase", "caseQty", fallback=row["packing"] or catalogue_packing)
                box = raw_int("box", "boxes", "case", "cases", fallback=row["quantity"] or 1)
                loose = raw_int("loose", "looseQty", "bottle", "bottles", fallback=0)
                qnty = raw_int("qnty", "qty", "totalQty", "totalQuantity", fallback=0)
                if not qnty:
                    qnty = (box * packing + loose) if packing else (box + loose)

                catalogue_mrp = self._catalogue_money(catalogue_item, "mrp", "itemMrp", "mrpPerUnit")
                catalogue_rate = self._catalogue_money(catalogue_item, "rate", "boxRate", "purchaseRate", "itemRate")
                amount = raw_money("itemAmount", "amount", "lineAmount", "totalAmount", fallback=row["amount"])
                rate = raw_money("rate", "purchaseRate", fallback=row["rate"] or catalogue_rate)
                box_rate = raw_money("boxRate", "caseRate", fallback=rate or catalogue_rate)
                loose_rate = raw_money("looseRate", "bottleRate", fallback=self._catalogue_money(catalogue_item, "looseRate", "bottleRate"))
                if not amount:
                    if box_rate and box:
                        amount = _money((box_rate * box) + (loose_rate * loose))
                    elif rate and qnty:
                        amount = _money(rate * qnty)

                item = {
                    "itemCode": mapped,
                    "itemName": self._purchase_item_name(mapped, catalogue, fallback_name),
                    "batchNo": str(raw_value("batchNo", "batch") or self._catalogue_value(catalogue_item, "batchNo", "batch") or ""),
                    "box": box,
                    "loose": loose,
                    "qnty": qnty,
                    "freeQnty": raw_int("freeQnty", "freeQty", "free", fallback=self._catalogue_int(catalogue_item, "freeQnty", "freeQty", "free") or 0),
                    "rate": rate or box_rate,
                    "boxRate": box_rate or rate,
                    "mrp": raw_money("mrp", fallback=row["mrp"] or catalogue_mrp),
                    "itemAmount": amount,
                    "discount": raw_money("discount", "disc", fallback=0),
                    "cgst": raw_money("cgst", fallback=0),
                    "sgst": raw_money("sgst", fallback=0),
                    "cess": raw_money("cess", fallback=0),
                    "addCess": raw_money("addCess", "adCess", "additionalCess", fallback=0),
                    "igst": raw_money("igst", fallback=0),
                    "t1Amt": raw_money("t1Amt", "t1", fallback=0),
                    "t2Amt": raw_money("t2Amt", "t2", fallback=0),
                    "t3Amt": raw_money("t3Amt", "t3", fallback=0),
                    "t4Amt": raw_money("t4Amt", "t4", fallback=0),
                    "etd": raw_money("etd", fallback=0),
                    "cgstInptLdgr": str(raw_value("cgstInptLdgr") or ""),
                    "sgstInptLdgr": str(raw_value("sgstInptLdgr") or ""),
                    "cessInptLdgr": str(raw_value("cessInptLdgr") or ""),
                    "adCessInptLdgr": str(raw_value("adCessInptLdgr", "addCessInptLdgr") or ""),
                    "igstInptLdgr": str(raw_value("igstInptLdgr") or ""),
                }
                item["packing"] = packing
                item["looseRate"] = loose_rate
                item["t1Rate"] = raw_money("t1Rate", fallback=self._catalogue_money(catalogue_item, "t1Rate"))
                item["t2Rate"] = raw_money("t2Rate", fallback=self._catalogue_money(catalogue_item, "t2Rate"))
                item["t3Rate"] = raw_money("t3Rate", fallback=self._catalogue_money(catalogue_item, "t3Rate"))
                item["t4Rate"] = raw_money("t4Rate", fallback=self._catalogue_money(catalogue_item, "t4Rate"))
                items.append(item)
        if missing:
            raise HTTPException(status_code=409, detail=f"Map all items before saving purchase: {', '.join(missing[:5])}")
        if not items:
            raise HTTPException(status_code=400, detail="No items available for purchase save")
        return items

    async def save_purchase(self, session: dict[str, Any], job_id: str, header: dict[str, Any]) -> dict[str, Any]:
        job = self.get_job(session, job_id)
        required = ["yearCode", "trnDate", "docDate", "docNo", "supplierCode", "storeCode", "purchaseAccCode", "userCode"]
        clean_header = {name: str(header.get(name) or "").strip() for name in required}
        missing = [name for name, value in clean_header.items() if not value]
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing purchase fields: {', '.join(missing)}")
        items = await self._purchase_items_for_job(session, job_id)
        gross = _money(sum(_money(item["itemAmount"]) for item in items))
        payload = {
            "shopCode": session["shop_code"],
            "companyCode": session["company_code"],
            "yearCode": clean_header["yearCode"],
            "trnDate": clean_header["trnDate"],
            "docDate": clean_header["docDate"],
            "docNo": clean_header["docNo"],
            "tpPassNo": str(header.get("tpPassNo") or "").strip(),
            "supplierCode": clean_header["supplierCode"],
            "storeCode": clean_header["storeCode"],
            "schemeCode": str(header.get("schemeCode") or "").strip(),
            "purchaseAccCode": clean_header["purchaseAccCode"],
            "narration": str(header.get("narration") or "PDF import").strip(),
            "userCode": clean_header["userCode"],
            "billType": "AI",
            "pType": "PURCHASE",
            "taxMode": str(header.get("taxMode") or "ITEMWISE").strip().upper() or "ITEMWISE",
            "grossAmount": _money(header.get("grossAmount") or gross),
            "taxAmount": _money(header.get("taxAmount") or 0),
            "netAmount": _money(header.get("netAmount") or header.get("grossAmount") or gross),
            "discount": _money(header.get("discount") or 0),
            "salesTaxOnMRP": _money(header.get("salesTaxOnMRP") or 0),
            "roundOff": _money(header.get("roundOff") or 0),
            "saletaxIncludingFree": bool(header.get("saletaxIncludingFree") or False),
            "items": items,
            "taxes": header.get("taxes") if isinstance(header.get("taxes"), list) else [],
        }
        client = self._client_for_session(session)
        try:
            calculation = await client.calculate_purchase(self._build_purchase_calculation_request(payload, header))
            self._merge_purchase_calculation(payload, calculation)
        except MadhushalaApiError:
            raise
        except Exception:
            # Calculation response shape is not documented; keep the extracted/mapped payload if calculation is unavailable.
            pass
        if payload["taxMode"] == "BILLWISE":
            for item in payload["items"]:
                for key in ("cgst", "sgst", "cess", "addCess", "igst", "t1Amt", "t2Amt", "t3Amt", "t4Amt", "etd"):
                    item[key] = 0
        else:
            payload["taxes"] = []
        for item in payload["items"]:
            for helper_key in ("packing", "boxRate", "looseRate", "t1Rate", "t2Rate", "t3Rate", "t4Rate"):
                item.pop(helper_key, None)
        response = await client.save_purchase(payload)
        with conn() as db:
            db.execute(
                "UPDATE import_jobs SET status=?, completed_at=?, updated_at=? WHERE id=?",
                ("PURCHASE_SAVED", now_iso(), now_iso(), job_id),
            )
        return {"success": True, "jobId": job_id, "purchasePayload": payload, "madhushalaResponse": response, "job": job}


