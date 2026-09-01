"""Madhushala Excise Bridge Phase 1 FastAPI app."""
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import logging
import os
from typing import Optional, Dict, Any

from app.config import settings
from app.automation.browser_manager import BrowserManager
from app.integrations.madhushala.client import MadhushalaApiError
from app.services.capture_service import CaptureService
from app.services.mapping_service import MappingService
from app.services.matching_service import score_dropdown_search

# Initialize logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("madhushala-excise-bridge")

# Create FastAPI app
app = FastAPI(
    title="Madhushala Excise Bridge - Phase 1",
    description="Simple local UI for manual excise portal interaction and data capture",
    version="1.0.0"
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# CORS middleware - bind only to localhost for security
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

capture_service = CaptureService()
mapping_service = MappingService()
madhushala_token = settings.MADHUSHALA_TOKEN

async def auto_process_latest_capture(batch_id: str) -> None:
    latest = capture_service.get_latest_capture()
    await mapping_service.auto_process_capture(latest, madhushala_token)

browser_manager = BrowserManager(capture_service, on_capture_saved=auto_process_latest_capture)

# Pydantic models
class Credentials(BaseModel):
    username: str = ""
    password: str = ""

class HealthResponse(BaseModel):
    status: str
    browserRunning: bool
    loginPageDetected: bool
    prepareIndentDetected: bool
    lastCapturedCount: int

class StatusResponse(BaseModel):
    browserRunning: bool
    currentUrl: Optional[str]
    loginPageDetected: bool
    prepareIndentDetected: bool
    lastCapturedBatch: Optional[Dict]
    lastError: Optional[str]
    mappingStatus: Optional[Dict] = None

class TokenRequest(BaseModel):
    token: str

class MappingSelection(BaseModel):
    exciseItemCode: int
    itemCode: str

class MappingSubmitRequest(BaseModel):
    mappings: list[MappingSelection]

class ExtensionCaptureRequest(BaseModel):
    pageUrl: str = ""
    capturedAt: Optional[str] = None
    items: list[dict[str, Any]]
    source: str = "chrome_extension"

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})

# Health check endpoint
@app.get("/health", response_model=HealthResponse)
async def health_check():
    return {
        "status": "ready",
        "browserRunning": browser_manager.is_running,
        "loginPageDetected": browser_manager.login_page_detected,
        "prepareIndentDetected": browser_manager.prepare_indent_detected,
        "lastCapturedCount": (capture_service.get_latest_capture() or {}).get("itemCount", 0)
    }

# Startup event
@app.on_event("startup")
async def startup_event():
    logger.info("Starting up application...")

    # Ensure data directories exist
    os.makedirs(settings.CAPTURES_DIR, exist_ok=True)
    os.makedirs(settings.BROWSER_PROFILE_DIR, exist_ok=True)

    # Initialize browser manager
    await browser_manager.initialize()
    logger.info("Browser manager initialized")

    # Initialize capture service
    await capture_service.initialize()
    logger.info("Capture service initialized")

    await mapping_service.initialize()
    logger.info("Mapping service initialized")

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down application...")

    # Stop browser manager
    await browser_manager.shutdown()
    logger.info("Browser manager stopped")

    # Stop capture service
    await capture_service.shutdown()
    logger.info("Capture service stopped")

# Automation endpoints
@app.post("/automation/start")
async def start_automation(credentials: Credentials):
    """Start a new automation session with credentials"""
    try:
        username = credentials.username.strip() or settings.EXCISE_USERNAME
        password = credentials.password or settings.EXCISE_PASSWORD
        if not username or not password:
            raise HTTPException(
                status_code=400,
                detail="Excise username/password are missing. Enter them once or set EXCISE_USERNAME and EXCISE_PASSWORD in .env.",
            )

        # Start browser with persistent context
        success = await browser_manager.start_browser()
        if not success:
            raise HTTPException(status_code=500, detail=browser_manager.last_error or "Failed to start browser")

        # Open excise portal and fill credentials
        await browser_manager.open_excise_portal(username, password)

        return {
            "status": "started",
            "browserRunning": True,
            "message": (
                "Browser opened. Username/password filled. Please enter CAPTCHA manually "
                "in the Excise browser and click Login."
            )
        }
    except Exception as e:
        logger.error(f"Failed to start automation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/automation/status", response_model=StatusResponse)
async def get_status():
    """Get current status of automation"""
    return {
        "browserRunning": browser_manager.is_running,
        "currentUrl": browser_manager.current_url,
        "loginPageDetected": browser_manager.login_page_detected,
        "prepareIndentDetected": browser_manager.prepare_indent_detected,
        "lastCapturedBatch": capture_service.get_latest_capture(),
        "lastError": browser_manager.last_error,
        "mappingStatus": mapping_service.get_auto_status(),
    }

@app.post("/automation/capture-selected")
async def capture_selected_rows():
    try:
        return await browser_manager.capture_selected_rows()
    except Exception as e:
        logger.error(f"Failed to capture selected rows: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/extension/capture")
async def capture_from_extension(payload: ExtensionCaptureRequest):
    try:
        if not payload.items:
            raise HTTPException(status_code=400, detail="No typed case rows found")

        raw_batch = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        batch_id = await capture_service.save_capture(raw_batch)
        await auto_process_latest_capture(batch_id)
        latest = capture_service.get_latest_capture()
        return {
            "status": "captured",
            "batchId": batch_id,
            "itemCount": latest.get("itemCount", 0) if latest else 0,
            "mappingStatus": mapping_service.get_auto_status(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to capture extension rows: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/captures/latest")
async def get_latest_capture():
    """Get latest structured capture"""
    latest = capture_service.get_latest_capture()
    if not latest:
        raise HTTPException(status_code=404, detail="No captures available")
    return latest

@app.get("/captures")
async def get_captures():
    """Get capture-batch summaries"""
    return capture_service.get_all_captures()

@app.post("/automation/stop")
async def stop_automation():
    """Stop the current automation session"""
    try:
        await browser_manager.stop_browser()
        return {"status": "stopped"}
    except Exception as e:
        logger.error(f"Failed to stop automation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/madhushala/status")
async def madhushala_status():
    return {
        "tokenConfigured": bool(madhushala_token),
        "exciseCredentialsConfigured": bool(settings.EXCISE_USERNAME and settings.EXCISE_PASSWORD),
        "shopCode": settings.MADHUSHALA_SHOP_CODE,
        "companyCode": settings.MADHUSHALA_COMPANY_CODE,
        "billType": settings.MADHUSHALA_BILL_TYPE,
    }

@app.post("/madhushala/token")
async def set_madhushala_token(payload: TokenRequest):
    global madhushala_token
    token = payload.token.strip()
    if token.casefold().startswith("bearer "):
        token = token[7:].strip()
    madhushala_token = token
    if madhushala_token and capture_service.get_latest_capture():
        await mapping_service.auto_process_capture(capture_service.get_latest_capture(), madhushala_token)
    return {
        "status": "configured",
        "tokenConfigured": bool(madhushala_token),
        "mappingStatus": mapping_service.get_auto_status(),
    }

def require_madhushala_token() -> str:
    if not madhushala_token:
        raise HTTPException(status_code=400, detail="Madhushala API token is not configured")
    return madhushala_token

def handle_madhushala_error(exc: MadhushalaApiError):
    status_code = exc.status_code if exc.status_code and exc.status_code >= 400 else 502
    raise HTTPException(status_code=status_code, detail=str(exc))

@app.post("/mapping/prepare-latest-capture")
async def prepare_latest_capture_for_mapping():
    latest = capture_service.get_latest_capture()
    if not latest:
        raise HTTPException(status_code=404, detail="No capture available")

    try:
        return await mapping_service.prepare_latest_capture(latest, require_madhushala_token())
    except MadhushalaApiError as exc:
        handle_madhushala_error(exc)

@app.get("/mapping/status")
async def get_mapping_status():
    return mapping_service.get_auto_status()

@app.get("/mapping/workspace")
async def get_mapping_workspace(latestOnly: bool = True):
    try:
        token = require_madhushala_token()
        latest_capture = capture_service.get_latest_capture()
        if latestOnly and latest_capture:
            await mapping_service.prepare_latest_capture(latest_capture, token)
        return await mapping_service.workspace(
            token,
            capture=latest_capture,
            latest_only=latestOnly,
        )
    except MadhushalaApiError as exc:
        handle_madhushala_error(exc)

@app.get("/madhushala/items")
async def get_madhushala_items(q: str = ""):
    try:
        workspace = await mapping_service.workspace(require_madhushala_token(), latest_only=False)
    except MadhushalaApiError as exc:
        handle_madhushala_error(exc)

    query = q.casefold().strip()
    items = workspace["madhushalaItems"]
    if query:
        scored = [
            (score_dropdown_search(item, query), item)
            for item in items
        ]
        items = [
            item for score, item in sorted(scored, key=lambda entry: entry[0], reverse=True)
            if score > 0
        ]
    return {"items": items[:100], "count": len(items)}

@app.post("/mapping/submit")
async def submit_mappings(payload: MappingSubmitRequest):
    try:
        selections = [
            item.model_dump() if hasattr(item, "model_dump") else item.dict()
            for item in payload.mappings
        ]
        return await mapping_service.save_mappings(selections, require_madhushala_token())
    except MadhushalaApiError as exc:
        handle_madhushala_error(exc)

# Serve the main HTML page
@app.get("/")
async def serve_index():
    return FileResponse("app/static/index.html")
