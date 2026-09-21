"""CRM-session based Madhushala Excise Bridge."""
from __future__ import annotations

import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.automation.row_parser import normalize_raw_items
from app.config import settings
from app.db import close_db, conn, init_db
from app.errors import error_payload, is_retryable_status, normalize_http_detail
from app.excise_portals import resolve_excise_portal
from app.integrations.madhushala.client import MadhushalaApiError, MadhushalaClient, close_madhushala_http_clients
from app.modules.document_import.routes import create_router as create_document_import_router
from app.modules.document_import.service import DocumentImportService
from app.observability import (
    configure_json_logging,
    get_correlation_id,
    observe_http_request,
    reset_correlation_id,
    route_label,
    set_correlation_id,
)
from app.services.session_service import session_service
from app.services.cache_service import cache_service
from app.services.mapping_service import MappingService


configure_json_logging(logging.INFO)
logger = logging.getLogger("madhushala-excise-bridge")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate_production()
    init_db()
    session_service.cleanup_expired()
    await mapping_service.initialize()
    logger.info("Madhushala Automation Platform started env=%s", settings.APP_ENV)
    try:
        yield
    finally:
        await close_madhushala_http_clients()
        await cache_service.close()
        close_db()


PUBLIC_PREFIX = "/excise-import"
INDEX_HTML_PATH = Path("app/static/index.html")
STATIC_ASSET_VERSION = "20260921-mapping-footer-cleanup-v11"
DOCUMENT_IMPORT_SCRIPTS = (
    f'<script src="./static/qr-browser-fallback.js?v={STATIC_ASSET_VERSION}"></script>',
    f'<script src="./static/purchase-context.js?v={STATIC_ASSET_VERSION}"></script>',
)
PAGE_END_SCRIPTS = (
    f'<script src="./static/mapping-row-identity.js?v={STATIC_ASSET_VERSION}"></script>',
)

def _inject_missing_scripts(html: str, scripts: tuple[str, ...], marker: str) -> str:
    missing = [
        script
        for script in scripts
        if script.split('src="', 1)[1].split('"', 1)[0] not in html
    ]
    if not missing:
        return html
    return html.replace(marker, "    " + "\n    ".join(missing) + f"\n{marker}", 1)


def index_html() -> str:
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    return _inject_missing_scripts(html, PAGE_END_SCRIPTS, "</body>")


def document_import_html() -> str:
    html = index_html()
    return _inject_missing_scripts(html, DOCUMENT_IMPORT_SCRIPTS, "</head>")


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


@app.middleware("http")
async def request_observability(request: Request, call_next):
    correlation = request.headers.get("X-Correlation-ID")
    token = set_correlation_id(correlation)
    started = time.perf_counter()
    response = None
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration = time.perf_counter() - started
        route = route_label(request)
        observe_http_request(request.method, route, status_code, duration)
        duration_ms = int(duration * 1000)
        logger.info(
            "http_request method=%s route=%s status=%s durationMs=%s",
            request.method,
            route,
            status_code,
            duration_ms,
            extra={
                "event": "http_request",
                "httpMethod": request.method,
                "route": route,
                "statusCode": status_code,
                "durationMs": duration_ms,
            },
        )
        if response is not None:
            response.headers["X-Correlation-ID"] = get_correlation_id()
        reset_correlation_id(token)

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


def _document_job_owned(db: Any, session: dict[str, Any], job_id: str) -> bool:
    return bool(
        db.execute(
            "SELECT id FROM import_jobs WHERE id=? AND shop_code=? AND session_id=?",
            (job_id, session["shop_code"], session["session_id"]),
        ).fetchone()
    )


def _apply_document_row_mapping_state(
    workspace: dict[str, Any],
    session: dict[str, Any],
    job_id: str,
) -> dict[str, Any]:
    """For document jobs, local job-row state is authoritative over global Excise mappings."""
    with conn() as db:
        if not _document_job_owned(db, session, job_id):
            raise HTTPException(status_code=404, detail="Import job not found")
        states = {
            str(row["id"]): row
            for row in db.execute(
                "SELECT id, mapped_item_code, mapping_status FROM import_items WHERE job_id=?",
                (job_id,),
            ).fetchall()
        }

    dropdown = {
        str(item.get("itemCode")): item
        for item in workspace.get("madhushalaItems", [])
        if item.get("itemCode") is not None
    }
    for row in workspace.get("unmappedItems", []):
        state = states.get(str(row.get("jobItemId") or ""))
        if not state:
            continue
        mapped_code = str(state["mapped_item_code"] or "").strip()
        row["selectedItemCode"] = mapped_code or None
        row["selectedItem"] = dropdown.get(mapped_code) if mapped_code else None
        row["mappingStatus"] = "MAPPED" if mapped_code else (state["mapping_status"] or "PENDING")
    return workspace


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    code, message, details = normalize_http_detail(exc.status_code, exc.detail)
    payload = error_payload(
        code,
        message,
        retryable=is_retryable_status(exc.status_code),
        details=details,
    )
    # Compatibility field for existing browser/client integrations. New clients
    # should consume payload.error exclusively.
    payload["detail"] = exc.detail
    return JSONResponse(status_code=exc.status_code, content=payload, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = jsonable_encoder(exc.errors())
    payload = error_payload(
        "VALIDATION_ERROR",
        "Request validation failed.",
        retryable=False,
        details=details,
    )
    payload["detail"] = details
    return JSONResponse(status_code=422, content=payload)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception type=%s message=%s",
        type(exc).__name__,
        exc,
        exc_info=True,
        extra={
            "event": "unhandled_exception",
            "exceptionType": type(exc).__name__,
        },
    )
    payload = error_payload(
        "INTERNAL_SERVER_ERROR",
        "Internal server error.",
        retryable=True,
    )
    payload["detail"] = "Internal server error."
    return JSONResponse(status_code=500, content=payload)


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health/live", include_in_schema=False)
async def health_live():
    return {
        "status": "alive",
        "service": "madhushala-excise-bridge",
        "version": "2.1.1",
    }


async def _readiness_state() -> tuple[bool, dict[str, str]]:
    dependencies: dict[str, str] = {}

    try:
        with conn() as db:
            db.execute("SELECT 1").fetchone()
        dependencies["database"] = "ok"
    except Exception as exc:
        dependencies["database"] = "error"
        logger.error(
            "readiness_dependency_failed dependency=database error=%s",
            exc,
            extra={"event": "readiness_dependency_failed", "dependency": "database"},
        )

    if settings.REDIS_URL:
        redis_ok = await cache_service.ping()
        dependencies["redis"] = "ok" if redis_ok else "error"
    else:
        dependencies["redis"] = "disabled"

    ready = dependencies.get("database") == "ok" and dependencies.get("redis") != "error"
    return ready, dependencies


@app.get("/health/ready", include_in_schema=False)
async def health_ready():
    ready, dependencies = await _readiness_state()
    content = {
        "status": "ready" if ready else "not_ready",
        "service": "madhushala-excise-bridge",
        "version": "2.1.1",
        "dependencies": dependencies,
    }
    return JSONResponse(status_code=200 if ready else 503, content=content)


@app.get("/health", include_in_schema=False)
async def health_check():
    # Backward-compatible deployment health endpoint. New infrastructure should
    # use /health/live for liveness and /health/ready for traffic readiness.
    return await health_ready()


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

    # Persist the exact token that was validated for this upstream session.
    # In server-authenticated mode madhushala_token can be empty while
    # MADHUSHALA_SERVICE_TOKEN was the token actually validated.
    response = session_service.create(
        shop_code,
        normalize_company_code(payload.companyCode),
        normalize_bill_type(payload.billType),
        upstream_token,
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


@app.get("/portal/bootstrap")
async def portal_bootstrap(request: Request):
    """Resolve company credentials plus a server-configured state login profile."""
    session = session_service.from_request(request)
    client = MadhushalaClient(
        settings.MADHUSHALA_BASE_URL,
        session["shop_code"],
        session.get("madhushala_token") or settings.MADHUSHALA_SERVICE_TOKEN,
    )
    try:
        company = await client.get_company_master(session["company_code"])
    except MadhushalaApiError as exc:
        handle_madhushala_error(exc)

    raw_state = str(company.get("state") or "").strip()
    try:
        portal = resolve_excise_portal(raw_state)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        logger.exception("excise_portal_registry_error state=%s", raw_state)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "EXCISE_PORTAL_REGISTRY_ERROR",
                "message": "Excise portal configuration is invalid.",
            },
        ) from exc

    user_id = str(company.get("exciseUserId") or "").strip()
    password = str(company.get("excisePassword") or "")

    response = {
        "companyCode": str(company.get("companyCode") or session["company_code"]),
        "companyName": str(company.get("companyName") or ""),
        "state": portal["state"] if portal else raw_state,
        "exciseLoginUrl": portal["loginUrl"] if portal else "",
        "loginProfile": portal["loginProfile"] if portal else None,
        "exciseUserId": user_id,
        "excisePassword": password,
        "ready": False,
        "error": None,
    }

    if not portal:
        response["error"] = {
            "code": "EXCISE_STATE_NOT_SUPPORTED",
            "message": f"Excise login automation is not configured for state '{raw_state or 'UNKNOWN'}'.",
            "state": raw_state,
        }
        return response

    if not user_id or not password:
        response["error"] = {
            "code": "EXCISE_CREDENTIALS_MISSING",
            "message": "Excise User ID or password is missing in Company Master.",
            "state": portal["state"],
        }
        return response

    response["ready"] = True
    return response


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
async def get_mapping_workspace(
    request: Request,
    latestOnly: bool = True,
    jobId: str | None = None,
    includeMapped: bool = False,
):
    session = session_service.from_request(request)
    try:
        workspace = await mapping_service.workspace_for_session(
            session,
            latest_capture_for_session(session["session_id"]),
            latest_only=latestOnly,
            job_id=jobId,
            include_mapped=includeMapped,
        )
    except MadhushalaApiError as exc:
        if exc.status_code in (401, 403):
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "MADHUSHALA_AUTH_EXPIRED",
                    "message": "Madhushala login/JWT is expired or invalid. Relaunch Excise Import from Madhushala CRM to create a fresh authenticated session.",
                },
            ) from exc
        handle_madhushala_error(exc)
    if jobId:
        workspace = _apply_document_row_mapping_state(workspace, session, jobId)
    return workspace


@app.post("/mapping/submit")
async def submit_mappings(payload: MappingRequest, request: Request):
    session = session_service.from_request(request)
    job_id = str(payload.jobId or "").strip()
    uses_job_rows = bool(
        job_id
        and payload.mappings
        and all(str(item.get("jobItemId") or "").strip() for item in payload.mappings)
    )
    session_state = "complete"

    if uses_job_rows:
        clean: list[dict[str, Any]] = []
        seen_job_items: set[str] = set()
        with conn() as db:
            if not _document_job_owned(db, session, job_id):
                raise HTTPException(status_code=404, detail="Import job not found")
            for item in payload.mappings:
                job_item_id = str(item.get("jobItemId") or "").strip()
                item_code = str(item.get("itemCode") or "").strip()
                if not job_item_id or not item_code:
                    raise HTTPException(status_code=400, detail="jobItemId and itemCode are required")
                if job_item_id in seen_job_items:
                    raise HTTPException(status_code=400, detail="Duplicate document row in mapping request")
                seen_job_items.add(job_item_id)

                row = db.execute(
                    "SELECT excise_item_code FROM import_items WHERE id=? AND job_id=?",
                    (job_item_id, job_id),
                ).fetchone()
                if not row:
                    raise HTTPException(status_code=400, detail="Document mapping row was not found")
                actual_excise_code = str(row["excise_item_code"] or "").strip()
                requested_excise_code = str(item.get("exciseItemCode") or "").strip()
                if not actual_excise_code or not actual_excise_code.isdigit():
                    raise HTTPException(status_code=409, detail="Excise item code is not ready for this document row")
                if requested_excise_code and requested_excise_code != actual_excise_code:
                    raise HTTPException(status_code=409, detail="Document mapping row changed; refresh and try again")
                clean.append(
                    {
                        "jobItemId": job_item_id,
                        "exciseItemCode": int(actual_excise_code),
                        "itemCode": item_code,
                    }
                )

        try:
            # Save the global Excise -> Madhushala mapping without the legacy broad
            # document-row UPDATE; local row state is updated by its unique jobItemId below.
            result = await mapping_service.save_session_mappings(session, clean, job_id=None)
        except MadhushalaApiError as exc:
            handle_madhushala_error(exc)

        mapped_at = datetime.now(timezone.utc).isoformat()
        with conn() as db:
            for item in clean:
                db.execute(
                    """
                    UPDATE import_items
                    SET mapping_status='MAPPED', mapped_item_code=?, updated_at=?
                    WHERE id=? AND job_id=?
                    """,
                    (item["itemCode"], mapped_at, item["jobItemId"], job_id),
                )
            counts = db.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN mapping_status='MAPPED' THEN 1 ELSE 0 END) AS mapped
                FROM import_items WHERE job_id=?
                """,
                (job_id,),
            ).fetchone()
            total = counts["total"] or 0
            mapped = counts["mapped"] or 0
            status = "COMPLETED" if total and mapped >= total else "MAPPING_REQUIRED"
            session_state = "complete" if status == "COMPLETED" else "mapping_required"
            db.execute(
                """
                UPDATE import_jobs
                SET mapped_count=?, status=?,
                    completed_at=CASE WHEN ?='COMPLETED' THEN ? ELSE completed_at END,
                    updated_at=?
                WHERE id=? AND shop_code=? AND session_id=?
                """,
                (mapped, status, status, mapped_at, mapped_at, job_id, session["shop_code"], session["session_id"]),
            )
        result["mappedCount"] = len(clean)
    else:
        try:
            result = await mapping_service.save_session_mappings(session, payload.mappings, job_id=payload.jobId)
        except MadhushalaApiError as exc:
            handle_madhushala_error(exc)

    with conn() as db:
        db.execute(
            "UPDATE integration_sessions SET state=? WHERE session_id=?",
            (session_state, session["session_id"]),
        )
    return result


@app.get("/")
async def serve_index():
    return HTMLResponse(
        content=index_html(),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/document-import")
async def serve_document_import():
    return HTMLResponse(
        content=document_import_html(),
        headers={"Cache-Control": "no-store"},
    )
