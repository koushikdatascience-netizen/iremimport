"""
Browser Manager for Madhushala Excise Bridge
Handles Playwright browser lifecycle and automation logic.
"""
import asyncio
from typing import Optional, Dict, Any, List
import logging
from playwright.async_api import async_playwright, Page

logger = logging.getLogger("madhushala-excise-bridge")

class BrowserManager:
    """Manages Playwright browser instances for the automation."""
    
    def __init__(self):
        self.browser: Optional[Any] = None
        self.context: Optional[Any] = None
        self.page: Optional[Page] = None
        self.is_running = False
        self.selected_items: Dict[str, Dict] = {}
    
    async def start(self) -> bool:
        """Start the browser and initialize Playwright context."""
        if self.is_running:
            logger.warning("Browser is already running")
            return True
        
        try:
            # Launch browser
            async with async_playwright() as p:
                self.browser = await p.chromium.launch(headless=False, args=['-disable-gpu'])
                self.context = await self.browser.new_context()
                self.page = await self.context.new_page()
                self.is_running = True
                logger.info("Browser started successfully")
                
                # Set up event listeners
                await self._setup_event_listeners()
                
                return True
        except Exception as e:
            logger.error(f"Failed to start browser: {e}")
            return False
    
    async def _setup_event_listeners(self):
        """Set up DOM event listeners for manual consent automation."""
        # Checkbox change handler
        await self.page.add_event_listener("change", lambda event: self._handle_checkbox_change(event.target))
        
        # Input change handler (for quantities)
        await self.page.add_event_listener("input", lambda event: self._handle_quantity_change(event.target))
        
        # Add to Bucket button click handler
        await self.page.add_event_listener("click", lambda event: self._handle_add_to_bucket_click(event.target))
    
    async def _handle_checkbox_change(self, element):
        """Handle checkbox state changes."""
        if element.get_attribute("id") and element.get_attribute("id").endswith("_chkselect"):
            row = await element.locator("xpath=..").first()
            item_data = await self._extract_item_data(row)
            if item_data:
                self.selected_items[item_data['canonical_key']] = item_data
                logger.info(f"Item selected: {item_data['canonical_key']}")
    
    async def _handle_quantity_change(self, element):
        """Handle quantity changes in selected items."""
        if element.get_attribute("id") and element.get_attribute("id").endswith("_Qty"):
            row = await element.locator("xpath=..").first()
            item_data = await self._extract_item_data(row)
            if item_data:
                item_data['requested_cases'] = int(element.input_value)
                self.selected_items[item_data['canonical_key']] = item_data
                logger.info(f"Quantity updated for {item_data['canonical_key']}: {item_data['requested_cases']}")
    
    async def _handle_add_to_bucket_click(self, element):
        """Handle Add to Bucket button click."""
        if element.get_attribute("id") == "ctl00_ContentPlaceHolder1_TabContainer1_tab_Consignment_btnshwsel":
            logger.info("Add to Bucket clicked - capturing selected items")
            # Freeze current selection state
            frozen_items = {k: v.copy() for k, v in self.selected_items.items()}
            # Store in session manager or queue for background processing
            # (Implementation would go here)
            logger.info(f"Captured {len(frozen_items)} items for processing")
    
    async def _extract_item_data(self, row):
        """Extract standardized item data from a table row."""
        try:
            # Extract brand
            brand = await row.locator("id$=_glbl_brandvt").first().text_content()
            # Extract measure ML
            measure = await row.locator("id$=_lblmsr").first().text_content()
            # Extract package type
            package = await row.locator("id$=_lblbottle").first().text_content()
            
            # Create canonical key
            canonical_key = f"{brand.strip()}|{measure.strip()}|{package.strip()}"
            
            # Build full item data
            return {
                'canonical_key': canonical_key,
                'brand': brand.strip(),
                'measure_ml': int(measure.strip()),
                'package_type': package.strip(),
                'requested_cases': 0,  # Will be updated on quantity change
                'source': "WB_EXCISE_PREPARE_INDENT"
            }
        except Exception as e:
            logger.warning(f"Failed to extract item data: {e}")
            return None
    
    async def stop(self) -> None:
        """Stop the browser and cleanup resources."""
        if self.browser:
            await self.browser.close()
            self.browser = None
            self.context = None
            self.page = None
            self.is_running = False
            logger.info("Browser stopped")
    
    async def open_excise_portal(self) -> bool:
        """Open the Excise portal in the browser."""
        if not self.page:
            raise RuntimeError("Browser not initialized")
        
        try:
            await self.page.goto("http://localhost:55481")
            logger.info("Opened Excise portal")
            return True
        except Exception as e:
            logger.error(f"Failed to open Excise portal: {e}")
            return False
    
    async def start_browser(self) -> bool:
        """Start the browser if not already running."""
        return await self.start()
    
    async def stop_browser(self) -> None:
        """Stop the browser."""
        await self.stop()