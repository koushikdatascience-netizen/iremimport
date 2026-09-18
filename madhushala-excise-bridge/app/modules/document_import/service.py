from __future__ import annotations

import json
import mimetypes
import re
import tempfile
import uuid
from datetime import date, datetime
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
from app.modules.document_import.pdf_extractor import extract_pdf_locally
from app.modules.document_import.purchase_context import build_purchase_context
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct, NormalizedImportItem
from app.services.mapping_service import MappingService
from app.services.normalizer import normalize_brand
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



def _cell_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _clean_cell(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _first_present(row: dict[str, Any], *aliases: str) -> Any:
    normalized = {_cell_key(key): value for key, value in row.items()}
    for alias in aliases:
        value = normalized.get(_cell_key(alias))
        if value not in (None, ""):
            return value
    return None



def _normalize_html_date(value: Any) -> str:
    text = _clean_cell(value)
    if not text:
        return ""
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if iso:
        return text
    named = re.match(r"^(\d{1,2})[-/ ]([A-Za-z]{3,})[-/ ](\d{4})$", text)
    if named:
        month = MONTHS.get(named.group(2)[:3].casefold())
        if month:
            return f"{named.group(3)}-{month}-{int(named.group(1)):02d}"
    numeric = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", text)
    if numeric:
        return f"{numeric.group(3)}-{int(numeric.group(2)):02d}-{int(numeric.group(1)):02d}"
    return text

def _parse_ml_value(*values: Any) -> int | None:
    for value in values:
        text = str(value or "")
        match = re.search(r"(\d{2,5})\s*m\.?l\.?", text, re.IGNORECASE)
        if match:
            return _int_value(match.group(1)) or None
        parsed = _int_value(text)
        if 30 <= parsed <= 5000:
            return parsed
    return None


def _qr_meta_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    text = str(payload.get("text") or "")
    meta: dict[str, str] = {}
    patterns = {
        "indentNo": r"Indent\s*No\s*[:\-]?\s*([A-Z0-9\-/]+)",
        "indentDate": r"Indent\s*Date\s*[:\-]?\s*(\d{1,2}[-/]\w{3}[-/]\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}-\d{2}-\d{2})",
        "invoiceNo": r"Invoice\s*No\.?\s*[:\-]?\s*([A-Z0-9\-/]+)",
        "invoiceDate": r"Dated\s*[:\-]?\s*(\d{1,2}[-/]\w{3}[-/]\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}-\d{2}-\d{2})",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            meta[key] = _normalize_html_date(match.group(1)) if key.lower().endswith("date") else _clean_cell(match.group(1))
    for row in payload.get("tables") or []:
        cells = [_clean_cell(cell) for cell in row]
        for index, cell in enumerate(cells[:-1]):
            key = _cell_key(cell)
            if key in {"indentno", "indentnumber"} and cells[index + 1]:
                meta.setdefault("indentNo", cells[index + 1])
            if key in {"indentdate", "date"} and cells[index + 1]:
                meta.setdefault("indentDate", _normalize_html_date(cells[index + 1]))
    return meta


def _table_dict_rows(tables: list[list[list[str]]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for table in tables or []:
        header: list[str] | None = None
        for raw_row in table:
            cells = [_clean_cell(cell) for cell in raw_row]
            keys = [_cell_key(cell) for cell in cells]
            has_name = any(key in {"brand", "description", "descriptionofgoods", "itemname", "productname"} for key in keys)
            has_item_shape = has_name and any("pack" in key or "quantity" in key or "bottle" in key or "amount" in key or "duty" in key or "rate" in key for key in keys)
            if has_item_shape:
                header = cells
                continue
            if not header or len(cells) < 3:
                continue
            values = cells[:len(header)]
            if len(values) < len(header):
                values.extend([""] * (len(header) - len(values)))
            row = {header[index] or f"column{index + 1}": values[index] for index in range(len(header))}
            name = _first_present(row, "Description", "Description of Goods", "Brand", "Item Name", "Product Name")
            if name and not _cell_key(str(name)).startswith(("total", "tcspayable", "roundoff")):
                rows.append(row)
    return rows
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
                        packing, quantity, box, loose, rate, mrp, amount, barcode, confidence,
                        mapping_status, raw_data_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        item.box,
                        item.loose,
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

    @staticmethod
    def _review_row(row: Any) -> dict[str, Any]:
        name = str(row["raw_name"] or row["normalized_name"] or "").strip()
        brand = str(row["brand"] or name).strip()
        ml = _int_value(row["ml"])

        raw: dict[str, Any] = {}
        try:
            loaded = json.loads(row["raw_data_json"] or "{}")
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            raw = {}

        keys = set(row.keys()) if hasattr(row, "keys") else set()
        box = _int_value(row["box"]) if "box" in keys else 0
        loose = _int_value(row["loose"]) if "loose" in keys else 0
        if box <= 0:
            box = _int_value(raw.get("canonicalBox", raw.get("box")))
        if loose <= 0:
            loose = _int_value(raw.get("canonicalLoose", raw.get("loose")))
        if box <= 0 and loose <= 0:
            # Legacy review compatibility only.
            loose = _int_value(row["quantity"])

        issues: list[str] = []
        if not name:
            issues.append("Name is required")
        if not brand:
            issues.append("Brand is required")
        if ml <= 0:
            issues.append("ML must be a positive whole number")
        if box <= 0 and loose <= 0:
            issues.append("Enter at least one case/box or loose bottle")

        return {
            "id": str(row["id"]),
            "name": name,
            "brand": brand,
            "ml": ml or None,
            "box": box,
            "loose": loose,
            "sourceFile": str(raw.get("sourceFilename") or "").strip(),
            "sourcePart": _int_value(raw.get("sourceFileIndex")) or None,
            "issues": issues,
            "valid": not issues,
        }

    def get_review_items(self, session: dict[str, Any], job_id: str) -> list[dict[str, Any]]:
        self.get_job(session, job_id)
        with conn() as db:
            rows = db.execute(
                "SELECT * FROM import_items WHERE job_id=? ORDER BY created_at, id",
                (job_id,),
            ).fetchall()
        return [self._review_row(row) for row in rows]

    async def confirm_review(
        self,
        session: dict[str, Any],
        job_id: str,
        edits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        job = self.get_job(session, job_id)
        if str(job.get("source_type") or "") not in {"DOCUMENT_PDF", "DOCUMENT_IMAGE", "QR_HTML"}:
            raise HTTPException(status_code=409, detail="This import source does not support document review")

        with conn() as db:
            rows = db.execute(
                "SELECT * FROM import_items WHERE job_id=? ORDER BY created_at, id",
                (job_id,),
            ).fetchall()
            existing = {str(row["id"]): row for row in rows}

        if not rows:
            raise HTTPException(status_code=409, detail="No extracted products are available for review")

        supplied_ids = [str(item.get("id") or "").strip() for item in edits]
        if len(supplied_ids) != len(set(supplied_ids)):
            raise HTTPException(status_code=400, detail="Duplicate review rows are not allowed")
        if set(supplied_ids) != set(existing):
            raise HTTPException(
                status_code=409,
                detail="The extracted product list changed. Refresh the review before continuing.",
            )

        cleaned: list[dict[str, Any]] = []
        validation_errors: list[str] = []
        for position, item in enumerate(edits, start=1):
            row_id = str(item.get("id") or "").strip()
            name = " ".join(str(item.get("name") or "").split()).strip()
            brand = " ".join(str(item.get("brand") or "").split()).strip()
            try:
                ml_decimal = Decimal(str(item.get("ml") or "0"))
                box_decimal = Decimal(str(item.get("box") or "0"))
                loose_decimal = Decimal(str(item.get("loose") or "0"))
            except Exception:
                validation_errors.append(f"Row {position}: ML, Box and Loose must be numbers")
                continue

            ml = int(ml_decimal) if ml_decimal == ml_decimal.to_integral_value() else 0
            box = int(box_decimal) if box_decimal == box_decimal.to_integral_value() else -1
            loose = int(loose_decimal) if loose_decimal == loose_decimal.to_integral_value() else -1

            row_errors: list[str] = []
            if not name:
                row_errors.append("Name is required")
            if not brand:
                row_errors.append("Brand is required")
            if ml <= 0 or ml > 10000:
                row_errors.append("ML must be a positive whole number")
            if box < 0:
                row_errors.append("Box/Cases must be a non-negative whole number")
            if loose < 0:
                row_errors.append("Loose/Bottles must be a non-negative whole number")
            if box == 0 and loose == 0:
                row_errors.append("Enter at least one case/box or loose bottle")
            if len(name) > 300 or len(brand) > 300:
                row_errors.append("Name and Brand must be 300 characters or fewer")
            if row_errors:
                validation_errors.append(f"Row {position}: " + "; ".join(row_errors))
                continue

            raw: dict[str, Any] = {}
            try:
                loaded = json.loads(existing[row_id]["raw_data_json"] or "{}")
                if isinstance(loaded, dict):
                    raw = loaded
            except Exception:
                raw = {}

            raw.update(
                {
                    "itemName": name,
                    "brand": brand,
                    "ml": ml,
                    "box": box,
                    "loose": loose,
                    "canonicalBox": box,
                    "canonicalLoose": loose,
                    "canonicalQuantityVersion": 2,
                    "quantity": box + loose,
                    "reviewConfirmed": True,
                }
            )
            cleaned.append(
                {
                    "id": row_id,
                    "name": name,
                    "brand": brand,
                    "ml": ml,
                    "box": box,
                    "loose": loose,
                    "quantity": box + loose,
                    "raw": raw,
                }
            )

        if validation_errors:
            raise HTTPException(status_code=422, detail=" | ".join(validation_errors[:8]))

        reviewed_at = now_iso()
        with conn() as db:
            for item in cleaned:
                db.execute(
                    """
                    UPDATE import_items
                    SET raw_name=?, normalized_name=?, brand=?, ml=?,
                        packing=NULL, quantity=?, box=?, loose=?,
                        mapping_status='PENDING', excise_item_code=NULL,
                        mapped_item_code=NULL, raw_data_json=?, updated_at=?
                    WHERE id=? AND job_id=?
                    """,
                    (
                        item["name"],
                        normalize_brand(item["name"]),
                        item["brand"],
                        item["ml"],
                        float(item["quantity"]),
                        item["box"],
                        item["loose"],
                        json.dumps(item["raw"], ensure_ascii=False),
                        reviewed_at,
                        item["id"],
                        job_id,
                    ),
                )

        self._update_job(job_id, status="CHECKING_MAPPING", error=None)
        try:
            await self.mapping_service.prepare_document_job(session, job_id)
            workspace = await self.mapping_service.workspace_for_session(session, job_id=job_id)
        except MadhushalaApiError as exc:
            self._update_job(job_id, status="FAILED", error=str(exc))
            raise

        mapping_rows = workspace.get("unmappedItems", [])
        unmapped_count = sum(1 for row in mapping_rows if not row.get("selectedItemCode"))
        status = "MAPPING_REQUIRED" if unmapped_count else "READY"
        self._update_job(
            job_id,
            status=status,
            mapped_count=len(cleaned) - unmapped_count,
            error=None,
        )
        return {
            "job": self.get_job(session, job_id),
            "reviewItems": self.get_review_items(session, job_id),
            "summary": {
                "detected": len(cleaned),
                "recognized": len(cleaned) - unmapped_count,
                "needMapping": unmapped_count,
            },
        }

    @staticmethod
    def _document_number_key(value: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())

    @staticmethod
    def _document_date_key(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        candidates = [text, text.split()[0]]
        for candidate in dict.fromkeys(candidates):
            for fmt in (
                "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
                "%d-%b-%Y", "%d/%b/%Y", "%d %b %Y", "%d %B %Y",
            ):
                try:
                    return datetime.strptime(candidate, fmt).date().isoformat()
                except ValueError:
                    continue
        return re.sub(r"\s+", "", text.casefold())

    @staticmethod
    def _merge_extracted(primary: ExtractedDocument, secondary: ExtractedDocument) -> ExtractedDocument:
        """Merge a secondary parser without duplicating rows already found by the primary parser."""
        primary_items = list(primary.items or [])
        secondary_items = list(secondary.items or [])

        def key(item: ExtractedProduct) -> tuple[str, int]:
            name = normalize_brand(str(item.itemName or item.brand or ""))
            ml = _int_value(item.ml)
            return name, ml

        primary_counts: dict[tuple[str, int], int] = {}
        for item in primary_items:
            item_key = key(item)
            primary_counts[item_key] = primary_counts.get(item_key, 0) + 1

        secondary_seen: dict[tuple[str, int], int] = {}
        merged_items = list(primary_items)
        for item in secondary_items:
            item_key = key(item)
            occurrence = secondary_seen.get(item_key, 0)
            secondary_seen[item_key] = occurrence + 1
            if occurrence < primary_counts.get(item_key, 0):
                continue
            merged_items.append(item)

        return ExtractedDocument(
            documentType=primary.documentType or secondary.documentType,
            supplierName=primary.supplierName or secondary.supplierName,
            invoiceNumber=primary.invoiceNumber or secondary.invoiceNumber,
            invoiceDate=primary.invoiceDate or secondary.invoiceDate,
            items=merged_items,
            extractionEngine="pymupdf+llamaparse",
            extractionProfile=getattr(primary, "extractionProfile", None),
            sourceState=getattr(primary, "sourceState", None),
            extractionDiagnostics={
                "primaryItems": len(primary_items),
                "secondaryItems": len(secondary_items),
                "mergedItems": len(merged_items),
            },
        )

    async def _extract_upload_part(
        self,
        data: bytes,
        ext: str,
        filename: str,
        source_index: int,
    ) -> tuple[ExtractedDocument, dict[str, Any]]:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as handle:
                handle.write(data)
                temp_path = Path(handle.name)

            if ext == "pdf":
                extracted, meta = extract_pdf_locally(temp_path)
                if extracted is None:
                    extracted = await LlamaCloudClient().extract_scanned_pdf_pages(temp_path, filename)
                    meta = {
                        **meta,
                        "engine": "llamaparse-page-by-page",
                        "fallbackFrom": "pymupdf",
                        "fallbackReason": meta.get("reason") or "no_usable_local_extraction",
                        "pageItemCounts": getattr(extracted, "pageItemCounts", None),
                    }
                elif meta.get("needsFallback"):
                    secondary = await LlamaCloudClient().extract_scanned_pdf_pages(temp_path, filename)
                    primary_count = len(extracted.items or [])
                    secondary_count = len(secondary.items or [])
                    extracted = self._merge_extracted(extracted, secondary)
                    meta = {
                        **meta,
                        "engine": "pymupdf+llamaparse",
                        "fallbackFrom": "pymupdf-state-adapter",
                        "fallbackReason": "deterministic_extraction_incomplete",
                        "primaryProductCount": primary_count,
                        "secondaryProductCount": secondary_count,
                        "mergedProductCount": len(extracted.items or []),
                    }
                else:
                    meta = {
                        **meta,
                        "engine": meta.get("engine") or "pymupdf-state-adapter",
                    }
            else:
                extracted = await LlamaCloudClient().extract_products(temp_path, filename)
                meta = {"engine": "llamaparse"}

            tagged: list[ExtractedProduct] = []
            for item in extracted.items or []:
                payload = item.model_dump()
                payload["sourceFilename"] = filename
                payload["sourceFileIndex"] = source_index
                tagged.append(ExtractedProduct.model_validate(payload))
            payload = extracted.model_dump()
            payload["items"] = [item.model_dump() for item in tagged]
            extracted = ExtractedDocument.model_validate(payload)
            return extracted, {**meta, "filename": filename, "sourceFileIndex": source_index}
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)

    async def process_uploads(
        self,
        session: dict[str, Any],
        uploads: list[UploadFile],
    ) -> dict[str, Any]:
        if not uploads:
            raise HTTPException(status_code=400, detail="Select at least one PDF or image")
        if len(uploads) > settings.DOCUMENT_IMPORT_MAX_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"A purchase can contain at most {settings.DOCUMENT_IMPORT_MAX_FILES} source files",
            )

        parts: list[tuple[bytes, str, str]] = []
        source_types: set[str] = set()
        for upload in uploads:
            data = await upload.read()
            ext = self._validate_file(upload, data)
            filename = Path(upload.filename or f"document.{ext}").name
            source_type = self._source_type(ext)
            source_types.add(source_type)
            parts.append((data, ext, filename))

        if len(source_types) != 1:
            raise HTTPException(
                status_code=400,
                detail="Upload PDFs together or images together for one purchase; do not mix file types in the same batch.",
            )

        source_type = next(iter(source_types))
        filenames = [filename for _data, _ext, filename in parts]
        job_id = self._create_job(session, source_type, " | ".join(filenames)[:1000])

        try:
            self._update_job(job_id, status="UPLOADING")
            extracted_parts: list[ExtractedDocument] = []
            metas: list[dict[str, Any]] = []
            self._update_job(job_id, status="EXTRACTING")

            for index, (data, ext, filename) in enumerate(parts, start=1):
                extracted, meta = await self._extract_upload_part(data, ext, filename, index)
                extracted_parts.append(extracted)
                metas.append(meta)

            doc_numbers = [
                str(doc.invoiceNumber or "").strip()
                for doc in extracted_parts
                if str(doc.invoiceNumber or "").strip()
            ]
            doc_dates = [
                str(doc.invoiceDate or "").strip()
                for doc in extracted_parts
                if str(doc.invoiceDate or "").strip()
            ]
            number_keys = {self._document_number_key(value) for value in doc_numbers if self._document_number_key(value)}
            date_keys = {self._document_date_key(value) for value in doc_dates if self._document_date_key(value)}
            if len(number_keys) > 1:
                raise HTTPException(
                    status_code=422,
                    detail="Selected files appear to belong to different document numbers. Upload only files/pages from the same purchase.",
                )
            if len(date_keys) > 1:
                raise HTTPException(
                    status_code=422,
                    detail="Selected files appear to have different document dates. Upload only files/pages from the same purchase.",
                )

            merged_items: list[ExtractedProduct] = []
            for doc in extracted_parts:
                merged_items.extend(doc.items or [])

            if not merged_items:
                raise HTTPException(status_code=422, detail="No valid product rows were extracted")

            merged = ExtractedDocument(
                documentType=next((doc.documentType for doc in extracted_parts if doc.documentType), "invoice"),
                supplierName=next((doc.supplierName for doc in extracted_parts if doc.supplierName), None),
                invoiceNumber=doc_numbers[0] if doc_numbers else None,
                invoiceDate=doc_dates[0] if doc_dates else None,
                items=merged_items,
                extractionEngine="batch",
                extractionDiagnostics={
                    "sourceFiles": filenames,
                    "fileCount": len(filenames),
                    "fileExtractions": metas,
                    "mergedProductCount": len(merged_items),
                },
            )

            self._update_job(
                job_id,
                status="NORMALIZING",
                document_type=merged.documentType,
                supplier_name=merged.supplierName,
                invoice_number=merged.invoiceNumber,
                invoice_date=merged.invoiceDate,
            )
            normalized = normalize_extracted_document(merged, source_type)
            if not normalized:
                raise HTTPException(status_code=422, detail="No valid product rows were extracted")

            self._persist_items(job_id, normalized)
            self._update_job(
                job_id,
                status="REVIEW_REQUIRED",
                extracted_count=len(normalized),
                mapped_count=0,
                error=None,
            )
            review_items = self.get_review_items(session, job_id)
            attention = sum(1 for item in review_items if not item["valid"])
            return {
                "job": self.get_job(session, job_id),
                "summary": {
                    "detected": len(review_items),
                    "recognized": 0,
                    "needMapping": len(review_items),
                    "valid": len(review_items) - attention,
                    "needsAttention": attention,
                    "reviewRequired": True,
                    "sourceFiles": len(filenames),
                },
                "extraction": (
                    {
                        "engine": "batch",
                        "files": metas,
                    }
                    if len(parts) > 1
                    else {
                        **metas[0],
                        "files": metas,
                    }
                ),
                "sourceFiles": filenames,
                "extractedDocument": merged.model_dump(),
                "normalizedItems": [item.model_dump() for item in normalized],
                "reviewItems": review_items,
            }
        except HTTPException as exc:
            self._update_job(job_id, status="FAILED", error=str(exc.detail))
            raise
        except MadhushalaApiError as exc:
            self._update_job(job_id, status="FAILED", error=str(exc))
            status_code = 401 if exc.status_code == 401 else 403 if exc.status_code == 403 else 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except (LlamaCloudError, ValueError) as exc:
            self._update_job(job_id, status="FAILED", error=str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def process_upload(self, session: dict[str, Any], upload: UploadFile) -> dict[str, Any]:
        return await self.process_uploads(session, [upload])

    def _qr_document_from_html(self, payload: dict[str, Any]) -> ExtractedDocument:
        meta = _qr_meta_from_payload(payload)
        items: list[ExtractedProduct] = []
        for index, row in enumerate(_table_dict_rows(payload.get("tables") or []), start=1):
            name = _clean_cell(_first_present(row, "Description", "Description of Goods", "Brand", "Item Name", "Product Name"))
            if not name:
                continue
            brand = _clean_cell(_first_present(row, "Brand") or name)
            package_type = _clean_cell(_first_present(row, "Packaging Type", "Package Type", "Packing Type"))
            ml = _parse_ml_value(
                _first_present(row, "Packaging Size", "Packing Size", "Size", "ML", "Measure ML"),
                name,
            )
            boxes = _first_present(
                row,
                "No of Cases / Mono Cartons Dispatched",
                "No of Cases Dispatched",
                "Cases Dispatched",
                "No of Cases / Mono Cartons Requested",
                "No of Cases Requested",
                "Quantity",
                "Qty",
            )
            bottles = _first_present(row, "No of Bottles Dispatched", "Bottles Dispatched", "No of Bottles Requested", "Bottles Requested")
            amount = _first_present(row, "Amount", "Duty Fee", "DutyFee", "Total Amount")
            rate = _first_present(row, "Rate", "Box Rate", "Case Rate")
            mrp = _first_present(row, "MRP", "MRP Per Unit", "MrpPerUnit")
            packing = None
            bottle_count = _int_value(bottles)
            box_count = _int_value(boxes)
            extracted_box = box_count
            extracted_loose = bottle_count
            extracted_quantity = (box_count + bottle_count) or None
            raw = {
                **meta,
                **row,
                "packageType": package_type,
                "measureMl": ml,
                "box": extracted_box,
                "loose": extracted_loose,
                "quantity": extracted_quantity,
                "qnty": bottle_count or bottles,
                "itemAmount": amount,
                "sourceRow": index,
            }
            extras = {
                key: value for key, value in raw.items()
                if key not in {
                    "itemName", "brand", "ml", "packing", "quantity", "box", "rate", "mrp",
                    "amount", "itemAmount", "confidence"
                }
            }
            items.append(
                ExtractedProduct(
                    itemName=name,
                    brand=brand,
                    ml=ml,
                    packing=packing,
                    quantity=extracted_quantity,
                    box=extracted_box,
                    loose=extracted_loose,
                    rate=rate,
                    mrp=mrp,
                    amount=amount,
                    itemAmount=amount,
                    confidence=1,
                    **extras,
                )
            )
        return ExtractedDocument(
            documentType="stock_list",
            supplierName=None,
            invoiceNumber=meta.get("indentNo") or meta.get("invoiceNo"),
            invoiceDate=meta.get("indentDate") or meta.get("invoiceDate"),
            items=items,
        )

    async def extract_qr_link(self, session: dict[str, Any], url: str) -> dict[str, Any]:
        clean_url = str(url or "").strip()
        parsed = urlparse(clean_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="QR did not contain a valid HTTP link")
        host = (parsed.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.endswith(".local"):
            raise HTTPException(status_code=400, detail="QR link host is not allowed")

        job_id = self._create_job(session, "QR_HTML", clean_url[:240])
        try:
            self._update_job(job_id, status="FETCHING_QR_LINK")
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(clean_url, headers={"accept": "text/html,application/json,*/*"})
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._update_job(job_id, status="FAILED", error=exc.response.text[:500] or "QR link request failed")
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text[:500] or "QR link request failed") from exc
        except httpx.HTTPError as exc:
            self._update_job(job_id, status="FAILED", error=str(exc))
            raise HTTPException(status_code=502, detail=f"QR link could not be opened: {exc}") from exc

        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                body: dict[str, Any] = response.json()
            except ValueError:
                body = {"text": response.text[:12000], "tables": []}
        else:
            parser = _ReadableHtmlParser()
            parser.feed(response.text[:500000])
            body = parser.payload()

        extracted = self._qr_document_from_html(body)
        self._update_job(
            job_id,
            status="NORMALIZING",
            document_type=extracted.documentType,
            invoice_number=extracted.invoiceNumber,
            invoice_date=extracted.invoiceDate,
        )
        normalized = normalize_extracted_document(extracted, "QR_HTML")
        if not normalized:
            self._update_job(job_id, status="FAILED", error="No item rows were found in the QR linked page")
            raise HTTPException(status_code=422, detail="No item rows were found in the QR linked page")

        self._persist_items(job_id, normalized)
        self._update_job(
            job_id,
            status="REVIEW_REQUIRED",
            extracted_count=len(normalized),
            mapped_count=0,
            error=None,
        )
        review_items = self.get_review_items(session, job_id)
        attention = sum(1 for item in review_items if not item["valid"])
        return {
            "source": "QR_HTML",
            "url": clean_url,
            "finalUrl": str(response.url),
            "statusCode": response.status_code,
            "contentType": content_type,
            "job": self.get_job(session, job_id),
            "summary": {
                "detected": len(review_items),
                "recognized": 0,
                "needMapping": len(review_items),
                "valid": len(review_items) - attention,
                "needsAttention": attention,
                "reviewRequired": True,
            },
            "extractedDocument": extracted.model_dump(),
            "normalizedItems": [item.model_dump() for item in normalized],
            "reviewItems": review_items,
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
        header = dict(header or {})
        context_defaults: dict[str, Any] = {}
        master_fields = ("supplierCode", "storeCode", "purchaseAccCode", "userCode")
        if any(not str(header.get(name) or "").strip() for name in master_fields):
            try:
                context = await build_purchase_context(
                    session,
                    supplier_name=str(job.get("supplier_name") or ""),
                )
                defaults = context.get("defaults") if isinstance(context, dict) else {}
                if isinstance(defaults, dict):
                    context_defaults = defaults
            except Exception:
                # Master-data lookup is only a convenience. The Madhushala
                # purchase/save API remains the authority on required fields.
                context_defaults = {}

        def header_text(name: str, fallback: Any = "") -> str:
            return str(header.get(name) or fallback or "").strip()

        doc_date = header_text("docDate", job.get("invoice_date"))
        trn_date = header_text("trnDate", date.today().isoformat())
        year_code = header_text("yearCode")
        if not year_code:
            try:
                effective_date = date.fromisoformat((doc_date or trn_date)[:10])
                start_year = effective_date.year if effective_date.month >= 4 else effective_date.year - 1
                year_code = f"{start_year}-{str((start_year + 1) % 100).zfill(2)}"
            except ValueError:
                year_code = ""

        clean_header = {
            "yearCode": year_code,
            "trnDate": trn_date,
            "docDate": doc_date,
            "docNo": header_text("docNo", job.get("invoice_number")),
            "supplierCode": header_text("supplierCode", context_defaults.get("supplierCode")),
            "storeCode": header_text("storeCode", context_defaults.get("storeCode")),
            "purchaseAccCode": header_text("purchaseAccCode", context_defaults.get("purchaseAccCode")),
            "userCode": header_text("userCode", context_defaults.get("userCode")),
        }
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
        # The live Swagger contract does not declare these header fields
        # as required. Omit empty values and let Madhushala validate them.
        for optional_key in (
            "yearCode",
            "docDate",
            "docNo",
            "tpPassNo",
            "supplierCode",
            "storeCode",
            "schemeCode",
            "purchaseAccCode",
            "userCode",
        ):
            if payload.get(optional_key) in (None, ""):
                payload.pop(optional_key, None)
        client = self._client_for_session(session)
        if str(job.get("source_type") or "") != "QR_HTML":
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
