"""CRM-session based Madhushala Excise Bridge."""
from __future__ import annotations

import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.automation.row_parser import normalize_raw_items
from app.config import settings
from app.db import conn, init_db
from app.integrations.madhushala.client import MadhushalaApiError, MadhushalaClient
from app.modules.document_import.routes import create_router as create_document_import_router
from app.modules.document_import.service import DocumentImportService
from app.services.session_service import session_service
from app.services.mapping_service import MappingService


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("madhushala-excise-bridge")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate_production()
    init_db()
    session_service.cleanup_expired()
    await mapping_service.initialize()
    logger.info("Madhushala Automation Platform started env=%s", settings.APP_ENV)
    yield


PUBLIC_PREFIX = "/excise-import"
INDEX_HTML_PATH = Path("app/static/index.html")
DOCUMENT_IMPORT_SCRIPTS = (
    '<script src="./static/qr-browser-fallback.js"></script>',
    '<script src="./static/purchase-context.js"></script>',
)


def document_import_html() -> str:
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    missing_scripts = [script for script in DOCUMENT_IMPORT_SCRIPTS if script.split('src="', 1)[1].split('"', 1)[0] not in html]
    if missing_scripts:
        html = html.replace("</head>", "    " + "\n    ".join(missing_scripts) + "\n</head>", 1)
    return html


app = FastAPI(
    title="Madhushala Automation Platform",
    description="CRM-launched automation, document import, and shared Madhushala mapping",
    version="2.1.1",
    docs_url=None,
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
    servers=[{"url": PUBLIC_PREFIX}],
    lifespan=lifespan,
)

if not settings.is_production:
    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_docs():
        return get_swagger_ui_html(
            openapi_url=f"{PUBLIC_PREFIX}/openapi.json",
            title=f"{app.title} - Swagger UI",
        )

    @app.get("/redoc", include_in_schema=False)
    async def custom_redoc():
        return get_redoc_html(
            openapi_url=f"{PUBLIC_PREFIX}/openapi.json",
            title=f"{app.title} - ReDoc",
        )

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CRM-Integration-Key"],
)

mapping_service = MappingService()
document_import_service = DocumentImportService(mapping_service)
app.include_router(create_document_import_router(document_import_service))


class CrmSessionRequest(BaseModel):
    shopCode: str
    companyCode: str | None = None
    billType: str | None = None
    accessToken: str | None = None


class CaptureRequest(BaseModel):
    pageUrl: str = ""
    capturedAt: str | None = None
    items: list[dict[str, Any]]


class MappingRequest(BaseModel):
    mappings: list[dict[str, Any]]
    jobId: str | None = None


def normalize_token(token: str | None) -> str:
    value = (token or "").strip()
    if value.casefold().startswith("bearer "):
        value = value[7:].strip()
    return value


def normalize_company_code(value: str | None) -> str:
    company_code = str(value or "").strip()
    if company_code and company_code.isdigit():
        return company_code
    if company_code:
        logger.warning(
            "Ignoring invalid CRM companyCode value; using default companyCode=%s",
            settings.DEFAULT_COMPANY_CODE,
        )
    return settings.DEFAULT_COMPANY_CODE


def normalize_bill_type(value: str | None) -> str:
    return str(value or settings.DEFAULT_BILL_TYPE).strip() or settings.DEFAULT_BILL_TYPE


def has_valid_crm_key(request: Request) -> bool:
    expected = settings.CRM_INTEGRATION_KEY
    supplied = request.headers.get("X-CRM-Integration-Key", "").strip()
    if not expected or not supplied:
        return False
    return secrets.compare_digest(supplied, expected)


def handle_madhushala_error(exc: MadhushalaApiError) -> None:
    status_code = exc.status_code if exc.status_code and exc.status_code >= 400 else 502
    raise HTTPException(status_code=status_code, detail=str(exc))


def latest_capture_for_session(session_id: str) -> dict[str, Any] | None:
    with conn() as db:
        row = db.execute(
            "SELECT payload_json FROM captures WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def capture_signature(items: list[dict[str, Any]]) -> str:
    parts = []
    for item in items:
        parts.append(
            "|".join(
                [
                    str(item.get("canonicalKey") or "").casefold(),
                    str(item.get("measureMl") or ""),
                    str(item.get("packageType") or "").casefold(),
                    str(item.get("requestedCases") or ""),
                    str(item.get("requestedBottles") or ""),
                ]
            )
        )
    return "||".join(sorted(parts))


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.get("/health")
async def health_check():
    return {"status": "ready", "version": "2.1.1"}


async def validate_madhushala_context(shop_code: str, token: str, *, force: bool = False) -> None:
    if not force and not settings.VALIDATE_MADHUSHALA_TOKEN_ON_SESSION:
        return
    client = MadhushalaClient(settings.MADHUSHALA_BASE_URL, shop_code, token)
    try:
        await client.get_unmapped_items()
    except MadhushalaApiError as exc:
        if exc.status_code in (401, 403):
            raise HTTPException(status_code=exc.status_code, detail="Madhushala login is invalid, expired, or not authorized for this shop") from exc
        raise HTTPException(status_code=502, detail="Could not validate Madhushala session") from exc


@app.post("/crm/session")
async def create_crm_session(payload: CrmSessionRequest, request: Request):
    shop_code = payload.shopCode.strip()
    if not shop_code:
        raise HTTPException(status_code=400, detail="shopCode is required")

    # Two supported production integration modes:
    # 1) CRM backend -> bridge, authenticated with X-CRM-Integration-Key.
    # 2) Trusted Angular origin -> bridge directly, authenticated with the current
    #    Madhushala JWT in Authorization: Bearer ... (or accessToken in the body).
    # The server-side integration key must never be embedded in frontend JavaScript.
    header_token = normalize_token(request.headers.get("Authorization"))
    payload_token = normalize_token(payload.accessToken)
    madhushala_token = payload_token or header_token
    server_authenticated = has_valid_crm_key(request)

    supplied_crm_key = request.headers.get("X-CRM-Integration-Key", "").strip()
    if supplied_crm_key and not server_authenticated:
        raise HTTPException(status_code=401, detail="Invalid CRM integration key")

    if settings.is_production and not server_authenticated and not madhushala_token:
        raise HTTPException(
            status_code=401,
            detail="Provide the current Madhushala Bearer token for browser session creation",
        )

    if not madhushala_token and not settings.MADHUSHALA_SERVICE_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="Provide accessToken/Authorization Bearer token or configure MADHUSHALA_SERVICE_TOKEN",
        )

    # Browser-direct mode always validates the user's JWT against the requested shop.
    # Server-to-server mode follows VALIDATE_MADHUSHALA_TOKEN_ON_SESSION.
    upstream_token = madhushala_token or settings.MADHUSHALA_SERVICE_TOKEN
    await validate_madhushala_context(
        shop_code, upstream_token, force=settings.is_production and not server_authenticated
    )

    response = session_service.create(
        shop_code,
        normalize_company_code(payload.companyCode),
        normalize_bill_type(payload.billType),
        madhushala_token,
    )
    return response


@app.get("/session/status")
async def session_status(request: Request):
    session = session_service.from_request(request)
    capture = latest_capture_for_session(session["session_id"])
    return {
        "sessionId": session["session_id"],
        "shopCode": session["shop_code"],
        "companyCode": session["company_code"],
        "billType": session["bill_type"],
        "state": session["state"],
        "hasCapture": bool(capture),
        "expiresAt": session["expires_at"],
    }


@app.post("/extension/capture")
async def capture_from_extension(payload: CaptureRequest, request: Request):
    session = session_service.from_request(request)
    if not payload.items:
        raise HTTPException(status_code=400, detail="No positive case rows found")

    captured_at = payload.capturedAt or datetime.now(timezone.utc).isoformat()
    items = normalize_raw_items(payload.items, captured_at=captured_at)
    if not items:
        raise HTTPException(status_code=400, detail="No valid excise rows found")

    signature = capture_signature(items)
    with conn() as db:
        existing = db.execute(
            """
            SELECT response_json FROM captures
            WHERE session_id=? AND capture_signature=? AND response_json IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (session["session_id"], signature),
        ).fetchone()
    if existing:
        response = json.loads(existing["response_json"])
        response["status"] = "duplicate_ignored"
        return response

    batch_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
    batch = {
        "batchId": batch_id,
        "capturedAt": captured_at,
        "pageUrl": payload.pageUrl,
        "itemCount": len(items),
        "items": items,
    }

    with conn() as db:
        db.execute(
            """
            INSERT INTO captures(session_id, shop_code, batch_id, captured_at, capture_signature, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session["session_id"], session["shop_code"], batch_id, captured_at, signature, json.dumps(batch)),
        )
        db.execute(
            "UPDATE integration_sessions SET state='captured' WHERE session_id=?",
            (session["session_id"],),
        )

    try:
        await mapping_service.prepare_session_capture(session, batch)
        workspace = await mapping_service.workspace_for_session(session, batch, latest_only=True)
    except MadhushalaApiError as exc:
        handle_madhushala_error(exc)

    unmapped_count = len(workspace.get("unmappedItems", []))
    mapping_required = unmapped_count > 0
    with conn() as db:
        db.execute(
            "UPDATE integration_sessions SET state=? WHERE session_id=?",
            ("mapping_required" if mapping_required else "complete", session["session_id"]),
        )

    response = {
        "status": "captured",
        "batchId": batch_id,
        "itemCount": len(items),
        "shopCode": session["shop_code"],
        "mappingStatus": {
            "mappingRequired": mapping_required,
            "unmappedCount": unmapped_count,
            "state": "mapping_required" if mapping_required else "complete",
        },
    }
    with conn() as db:
        db.execute(
            "UPDATE captures SET response_json=? WHERE session_id=? AND batch_id=?",
            (json.dumps(response), session["session_id"], batch_id),
        )
    return response


@app.get("/mapping/workspace")
async def get_mapping_workspace(request: Request, latestOnly: bool = True, jobId: str | None = None):
    session = session_service.from_request(request)
    try:
        return await mapping_service.workspace_for_session(
            session,
            latest_capture_for_session(session["session_id"]),
            latest_only=latestOnly,
            job_id=jobId,
        )
    except MadhushalaApiError as exc:
        handle_madhushala_error(exc)


@app.post("/mapping/submit")
async def submit_mappings(payload: MappingRequest, request: Request):
    session = session_service.from_request(request)
    try:
        result = await mapping_service.save_session_mappings(session, payload.mappings, job_id=payload.jobId)
    except MadhushalaApiError as exc:
        handle_madhushala_error(exc)
    with conn() as db:
        db.execute(
            "UPDATE integration_sessions SET state='complete' WHERE session_id=?",
            (session["session_id"],),
        )
    return result


@app.get("/")
async def serve_index():
    return FileResponse(INDEX_HTML_PATH)


@app.get("/document-import")
async def serve_document_import():
    return HTMLResponse(
        content=document_import_html(),
        headers={"Cache-Control": "no-store"},
    )
