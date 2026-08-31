"""Playwright browser lifecycle for Madhushala Excise Bridge Phase 1."""
import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional, Dict, Any, List
import logging
from playwright.async_api import async_playwright, Page, BrowserContext
import os

from app.automation.indent_monitor import prepare_indent_detected
from app.automation.login_helper import fill_credentials_and_focus_captcha
from app.config import settings
from app.services.capture_service import CaptureService

logger = logging.getLogger("madhushala-excise-bridge")

class BrowserManager:
    """Manages Playwright browser instances for the automation."""

    def __init__(
        self,
        capture_service: CaptureService,
        on_capture_saved: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.playwright: Optional[Any] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.capture_service = capture_service
        self.on_capture_saved = on_capture_saved
        self.is_running = False
        self.current_url: Optional[str] = None
        self.login_page_detected = False
        self.prepare_indent_detected = False
        self.last_error: Optional[str] = None
        self.captured_items: List[Dict] = []
        self._monitor_task: Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        """Initialize the browser manager"""
        # Ensure browser profile directory exists
        os.makedirs(settings.BROWSER_PROFILE_DIR, exist_ok=True)

    async def start_browser(self) -> bool:
        """Start the browser with persistent context"""
        if self.is_running:
            logger.warning("Browser is already running")
            return True

        try:
            if self.context or self.playwright:
                await self.stop_browser()

            # Start Playwright
            self.playwright = await async_playwright().start()

            # Launch persistent browser context
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=settings.BROWSER_PROFILE_DIR,
                headless=settings.HEADLESS,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-sandbox",
                ],
            )

            await self._setup_context_hooks()

            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.is_running = True
            self.current_url = None
            self.login_page_detected = False
            self.prepare_indent_detected = False
            self.last_error = None

            self._attach_page(self.page)
            self._monitor_task = asyncio.create_task(self._monitor_page_state())

            logger.info("Browser started successfully with persistent context")
            return True

        except Exception as e:
            logger.error(f"Failed to start browser: {e}")
            self.last_error = str(e)
            return False

    async def _setup_context_hooks(self):
        if not self.context:
            return

        async def capture_binding(source, payload):
            if not isinstance(payload, dict):
                return
            items = payload.get("items") or []
            if not isinstance(items, list):
                return
            batch = {
                "capturedAt": payload.get("capturedAt"),
                "pageUrl": payload.get("pageUrl") or source.get("page").url,
                "items": items,
            }
            await self._process_captured_items(batch)

        await self.context.expose_binding("__madhushalaCaptureRows", capture_binding)
        script_path = Path(__file__).with_name("capture_bridge.js")
        await self.context.add_init_script(path=str(script_path))

        self.context.on("page", self._attach_page)

    def _attach_page(self, page: Page):
        self.page = page

        def update_url():
            self.current_url = page.url
            self.login_page_detected = "Login.aspx" in self.current_url

        def on_load(page: Page):
            update_url()

        def on_frame_navigated(frame):
            if frame == page.main_frame:
                update_url()

        def on_close(page: Page):
            self.is_running = False
            self.current_url = None
            self.login_page_detected = False
            self.prepare_indent_detected = False
            self.last_error = "Browser disconnected"

        page.on("load", on_load)
        page.on("framenavigated", on_frame_navigated)
        page.on("close", on_close)

    async def _monitor_page_state(self):
        while self.is_running:
            try:
                if self.page and not self.page.is_closed():
                    self.current_url = self.page.url
                    self.login_page_detected = await self.page.locator("#txt_username").count() > 0
                    self.prepare_indent_detected = await prepare_indent_detected(self.page)
                else:
                    self.is_running = False
                    self.last_error = "Browser disconnected"
            except Exception as e:
                self.last_error = str(e)
            await asyncio.sleep(1)

    async def _process_captured_items(self, raw_batch: Dict[str, Any]):
        """Process items captured from the browser"""
        self.captured_items = raw_batch.get("items", [])
        batch_id = await self.capture_service.save_capture(raw_batch)
        logger.info("Saved capture batch %s with %s raw rows", batch_id, len(self.captured_items))
        if self.on_capture_saved:
            await self.on_capture_saved(batch_id)

    async def capture_selected_rows(self) -> dict[str, Any]:
        """Capture Prepare Indent rows with typed case quantity without clicking Add to Bucket."""
        if not self.page or self.page.is_closed():
            self.last_error = "Excise browser is not open"
            raise RuntimeError(self.last_error)

        try:
            payload = await self.page.evaluate(
                """() => {
                    function text(row, selector) {
                        return (row.querySelector(selector)?.textContent || "").trim();
                    }
                    function value(row, selector) {
                        return (row.querySelector(selector)?.value || "").trim();
                    }
                    function typedCaseQuantity(row) {
                        const raw = value(row, 'input[id$="_Qty"]');
                        const quantity = Number.parseInt(raw, 10);
                        return Number.isFinite(quantity) ? quantity : 0;
                    }
                    const snapshot = window.__madhushalaSnapshotCaseTypedRows || function () {
                        return Array.from(document.querySelectorAll('input[id$="_Qty"]'))
                            .map((input) => input.closest("tr"))
                            .filter(Boolean)
                            .filter((row) => typedCaseQuantity(row) > 0)
                            .map((row) => ({
                                brand: text(row, '[id$="_glbl_brandvt"]'),
                                strengthRaw: text(row, '[id$="_mlbllegStr"]'),
                                measureMl: text(row, '[id$="_lblmsr"]'),
                                packageType: text(row, '[id$="_lblbottle"]'),
                                retailerMargin: text(row, '[id$="_lblrm"]'),
                                roundOffGovt: text(row, '[id$="_lbl_Round_Off_Govt3"]'),
                                specialPurposeFee: text(row, '[id$="_lbl_Special_Levy3"]'),
                                mrpPerUnit: text(row, '[id$="_Label55"]'),
                                bottlesPerCase: text(row, '[id$="_lblnobotpercase"]'),
                                mrpPerCase: text(row, '[id$="_lblmrppercase"]'),
                                supplier: text(row, '[id$="_lblsupplier"]'),
                                warehouseCasesRaw: text(row, '[id$="_lblclblcase"]'),
                                warehouseBottles: text(row, '[id$="_lblclosbal"]'),
                                requestedCases: value(row, 'input[id$="_Qty"]'),
                                requestedBottles: value(row, 'input[id$="_txt_bot"]'),
                            }));
                    };
                    return {
                        pageUrl: window.location.href,
                        capturedAt: new Date().toISOString(),
                        items: snapshot(),
                    };
                }"""
            )
            if not payload.get("items"):
                raise RuntimeError("No rows selected")
            await self._process_captured_items(payload)
            latest = self.capture_service.get_latest_capture() or {}
            return {
                "status": "captured",
                "itemCount": latest.get("itemCount", 0),
                "batchId": latest.get("batchId"),
            }
        except Exception as e:
            logger.error(f"Failed to capture selected rows: {e}")
            self.last_error = str(e)
            raise

    async def open_excise_portal(self, username: str, password: str) -> bool:
        """Open the Excise portal and fill credentials"""
        if not self.page:
            self.last_error = "Browser not initialized"
            return False

        try:
            # Navigate to the login page
            await self.page.goto(settings.EXCISE_LOGIN_URL)
            logger.info("Navigated to Excise login page")

            await fill_credentials_and_focus_captcha(self.page, username, password)
            self.login_page_detected = True

            logger.info("Credentials filled, captcha field focused")
            return True

        except Exception as e:
            logger.error(f"Failed to open Excise portal: {e}")
            self.last_error = str(e)
            return False

    async def stop_browser(self) -> None:
        """Stop the browser and cleanup resources"""
        if not self.is_running:
            return

        try:
            if self._monitor_task:
                self._monitor_task.cancel()
                self._monitor_task = None
            if self.context:
                await self.context.close()
            if self.playwright:
                await self.playwright.stop()

            self.playwright = None
            self.context = None
            self.page = None
            self.is_running = False
            self.current_url = None
            self.login_page_detected = False
            self.prepare_indent_detected = False
            self.last_error = None

            logger.info("Browser stopped successfully")

        except Exception as e:
            logger.error(f"Error stopping browser: {e}")
            self.last_error = str(e)

    async def shutdown(self) -> None:
        """Shutdown the browser manager"""
        await self.stop_browser()
