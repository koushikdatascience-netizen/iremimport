"""Prepare Indent detection helpers."""
from playwright.async_api import Page


STOCK_GRID_SELECTOR = "#ctl00_ContentPlaceHolder1_TabContainer1_tab_Consignment_GridStock"
PREPARE_INDENT_BUTTON_SELECTOR = "#ctl00_ContentPlaceHolder1_TabContainer1_tab_Consignment_btnshwsel"


async def prepare_indent_detected(page: Page) -> bool:
    try:
        grid_count = await page.locator(STOCK_GRID_SELECTOR).count()
        button_count = await page.locator(PREPARE_INDENT_BUTTON_SELECTOR).count()
        return grid_count > 0 or button_count > 0
    except Exception:
        return False
