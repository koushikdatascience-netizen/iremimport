from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import logging
from datetime import datetime
import os

# Import local modules
from app.config import settings
from app.db.database import database
from app.services.session_manager import SessionManager
from app.services.import_processor import ImportProcessor
from app.integrations.madhushala.client import MadhushalaClient
from app.automation.browser_manager import BrowserManager
from app.automation.prepare_indent_monitor import PrepareIndentMonitor
from app.integrations.madhushala.payload_mapper import get_payload_mapper

# Initialize logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("madhushala-excise-bridge")

# Create FastAPI app
app = FastAPI(
    title="Madhushala Excise Bridge",
    description="Production foundation for Madhushala CRM Excise integration",
    version="0.1.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return {"error": "Internal server error"}

# Health check endpoint
@app.get("/health")
async def health_check():
    return {
        "status": "ready",
        "browserRunning": settings.HEADLESS is False,  # Will be set by browser manager
        "activeSession": SessionManager.session_id if SessionManager.session_id else None
    }

# Startup event
@app.on_event("startup")
async def startup_event():
    logger.info("Starting up application...")
    
    # Initialize database
    await database.connect()
    logger.info("Database connected")
    
    # Initialize session manager
    await SessionManager.start()
    logger.info("Session manager started")
    
    # Initialize Madhushala client
    await MadhushalaClient.start()
    logger.info("Madhushala client initialized")
    
    # Initialize browser manager
    await BrowserManager.start()
    logger.info("Browser manager started")
    
    # Initialize import processor
    await ImportProcessor.start()
    logger.info("Import processor started")

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down application...")
    
    # Stop import processor
    await ImportProcessor.stop()
    logger.info("Import processor stopped")
    
    # Stop browser manager
    await BrowserManager.stop()
    logger.info("Browser manager stopped")
    
    # Stop Madhushala client
    await MadhushalaClient.stop()
    logger.info("Madhushala client stopped")
    
    # Close database connection
    await database.disconnect()
    logger.info("Database disconnected")

# WebSocket endpoint for events
@app.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket client connected")
    
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")

# Automation endpoints
@app.post("/automation/start")
async def start_automation(session_data: dict = None):
    """Start a new automation session"""
    try:
        session_id = await SessionManager.create_session()
        logger.info(f"New session started: {session_id}")
        
        # Start browser if not already running
        await BrowserManager.start_browser()
        
        return {
            "sessionId": session_id,
            "status": "started"
        }
    except Exception as e:
        logger.error(f"Failed to start automation: {e}")
        return {"error": str(e)}

@app.post("/automation/open-excise")
async def open_excise():
    """Open the Excise portal in the browser"""
    try:
        await BrowserManager.open_excise_portal()
        return {"status": "browser_opened"}
    except Exception as e:
        logger.error(f"Failed to open Excise portal: {e}")
        return {"error": str(e)}

@app.get("/automation/status")
async def get_status():
    """Get current status of automation"""
    return {
        "browserRunning": BrowserManager.is_running,
        "pageUrl": BrowserManager.current_url,
        "selectedItems": SessionManager.selected_items,
        "activeSession": SessionManager.session_id,
        "lastError": SessionManager.last_error,
        "browserStatus": BrowserManager.browser_status
    }

@app.get("/automation/selected-items")
async def get_selected_items():
    """Get currently selected items"""
    return SessionManager.selected_items

@app.get("/automation/pending-mappings")
async def get_pending_mappings():
    """Get mapping-required records"""
    # This would query the database for unmapped items
    # For now, return empty list
    return []

@app.post("/automation/map")
async def map_excise_item(code_data: dict):
    """Map Excise item to Madhushala item code"""
    try:
        result = await ImportProcessor.handle_mapping(code_data)
        return result
    except Exception as e:
        logger.error(f"Mapping failed: {e}")
        return {"error": str(e)}

@app.post("/automation/stop")
async def stop_automation():
    """Stop the current automation session"""
    try:
        await BrowserManager.stop_browser()
        await SessionManager.end_session()
        return {"status": "stopped"}
    except Exception as e:
        logger.error(f"Failed to stop automation: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8091, reload=True)
