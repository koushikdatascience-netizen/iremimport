"""Login page helpers for the WB Excise portal."""
from playwright.async_api import Page


USERNAME_SELECTOR = "#txt_username"
PASSWORD_SELECTOR = "#txt_password"
CAPTCHA_SELECTOR = "#CodeNumberTextBox"


async def fill_credentials_and_focus_captcha(page: Page, username: str, password: str) -> None:
    await page.wait_for_selector(USERNAME_SELECTOR, timeout=15000)
    await page.wait_for_selector(PASSWORD_SELECTOR, timeout=15000)

    current_username = await page.locator(USERNAME_SELECTOR).input_value()
    if current_username != username:
        await page.fill(USERNAME_SELECTOR, username)

    current_password = await page.locator(PASSWORD_SELECTOR).input_value()
    if current_password != password:
        await page.fill(PASSWORD_SELECTOR, password)

    await page.focus(CAPTCHA_SELECTOR)
