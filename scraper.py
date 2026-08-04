"""
SKIDATA-Odoo scraper: download only the Parking Transactions report.
"""
import asyncio
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from playwright.async_api import (
    BrowserContext,
    Frame,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)


LOGIN_URL_DEFAULT = (
    "https://car.webhost.skidata.com/analytics/"
    "?proxyRestUri=https%3a%2f%2faut-qlikc01n01.sdnsa.net%3a4243%2fqps%2fswebanalyze4analytics%2f"
    "&targetId=5778d56a-9b37-4797-b185-736ebc571f4c"
)

EXPORTS_DIR = "exports"


def get_env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


async def find_frame_with_selector(
    page: Page,
    selector: str,
    per_frame_timeout_ms: int = 30_000,
    total_timeout_ms: Optional[int] = None,
) -> Optional[Frame]:
    """
    Search all frames for one that contains the given selector.

    Qlik Sense keeps adding iframes after the first paint, so we must refresh
    ``page.frames`` on each pass. A single ``for frame in page.frames`` snapshot
    can miss the frame that later receives the sheet list.
    """
    if total_timeout_ms is None:
        total_timeout_ms = max(180_000, per_frame_timeout_ms * 2)
    deadline = time.monotonic() + total_timeout_ms / 1000.0

    while time.monotonic() < deadline:
        frames = list(page.frames)
        for frame in frames:
            if time.monotonic() >= deadline:
                break
            remaining_ms = int(max(0, (deadline - time.monotonic()) * 1000))
            timeout_ms = min(per_frame_timeout_ms, max(250, remaining_ms))
            try:
                await frame.wait_for_selector(selector, timeout=timeout_ms)
                return frame
            except PlaywrightTimeoutError:
                continue
        await asyncio.sleep(0.35)
    return None


async def run_parking_transactions_flow(
    page: Page,
    download_dir: str,
) -> None:
    """
    Open Parking Transactions sheet, apply date filter (second row),
    right-click header, Download as → Data, click Export, then save with yesterday's date.
    """
    sheet_display_name = "Parking Transactions"
    file_base_name = "Parking-Transactions"
    header_selector = "header#VvpsUS_title"

    await page.evaluate("window.scrollTo(0, 0)")
    try:
        await page.wait_for_load_state("networkidle", timeout=60_000)
    except PlaywrightTimeoutError:
        await page.wait_for_load_state("load")
    await asyncio.sleep(3)

    sheet_selector = "div.qv-content-li-inner:has(span[title='Parking Transactions'])"
    print(f"Waiting for '{sheet_display_name}' sheet card...")
    sheet_frame = await find_frame_with_selector(page, sheet_selector)
    if sheet_frame is None:
        raise RuntimeError(
            f"Could not find '{sheet_display_name}' sheet card in any frame."
        )
    sheet_card = await sheet_frame.query_selector(sheet_selector)
    if sheet_card is None:
        raise RuntimeError("Sheet card element not found after frame detection.")
    await sheet_card.scroll_into_view_if_needed()
    await sheet_card.click()
    print(f"Opened '{sheet_display_name}' sheet.")

    print("Waiting for Date filter (filter pane)...")
    date_filter_selector = "div[data-testid='collapsed-title-Date']"
    date_frame = await find_frame_with_selector(page, date_filter_selector)
    if date_frame is None:
        date_filter_selector = "h6[aria-label='Date']"
        date_frame = await find_frame_with_selector(page, date_filter_selector)
    if date_frame is None:
        raise RuntimeError("Could not find Date filter in any frame.")
    await date_frame.click(date_filter_selector)
    print("Opened Date selection list.")
    await asyncio.sleep(0.8)

    listbox_base = "div.RowColumn-barContainer[data-testid='listbox.item']"
    second_row_selector = f"{listbox_base}:has(div[data-n='1'])"
    print("Waiting for date list (RowColumn-barContainer)...")
    list_frame = await find_frame_with_selector(page, listbox_base)
    if list_frame is None:
        raise RuntimeError("Date list (RowColumn-barContainer) not found in any frame.")
    second_date_item = await list_frame.query_selector(second_row_selector)
    if second_date_item is None:
        raise RuntimeError("Second date row (data-n=1) not found in Date selection list.")

    row_el = await second_date_item.query_selector('[role="row"]')
    is_date_already_selected = (
        row_el is not None
        and await row_el.get_attribute("aria-selected") == "true"
    )

    if is_date_already_selected:
        print("Date (second row) is already selected. Closing filter without changes.")
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
    else:
        await second_date_item.click()
        confirm_button_selector = (
            "button[data-testid='actions-toolbar-confirm'][title='Confirm selection']"
        )
        confirm_frame = await find_frame_with_selector(
            page, confirm_button_selector, per_frame_timeout_ms=5_000
        )
        if confirm_frame is not None:
            print("Clicking Confirm selection button...")
            await confirm_frame.click(confirm_button_selector)
        else:
            await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)

    await asyncio.sleep(4)
    await page.evaluate("window.scrollTo(0, 300)")

    print(f"Right-clicking header ({header_selector}) to open context menu...")
    pivot_frame = await find_frame_with_selector(
        page, header_selector, per_frame_timeout_ms=60_000
    )
    if pivot_frame is None:
        pivot_frame = await find_frame_with_selector(
            page,
            "article:not(.qv-object-filterpane) header.qv-object-header",
            per_frame_timeout_ms=30_000,
        )
    if pivot_frame is None:
        raise RuntimeError(
            "Could not find Parking Transactions header (header#VvpsUS_title)."
        )
    pivot_header = await pivot_frame.query_selector(header_selector)
    if pivot_header is None:
        pivot_header = await pivot_frame.query_selector(
            "article:not(.qv-object-filterpane) header.qv-object-header"
        )
    if pivot_header is None:
        raise RuntimeError("Header element not found after frame detection.")
    await pivot_header.scroll_into_view_if_needed()
    await pivot_header.click(button="right")
    await asyncio.sleep(0.5)

    export_group_selector = "li#export-group"
    export_selector = "li#export"
    menu_frame = await find_frame_with_selector(page, export_group_selector)
    if menu_frame is None:
        raise RuntimeError("Context menu 'Download as...' item not found in any frame.")
    await menu_frame.click(export_group_selector)
    await asyncio.sleep(0.5)
    export_item = await menu_frame.wait_for_selector(export_selector, timeout=60_000)
    await export_item.click()
    await asyncio.sleep(0.5)

    print("Clicking Export button...")
    export_button_selector = "button[tid='table-export']"
    export_btn_frame = await find_frame_with_selector(page, export_button_selector)
    if export_btn_frame is None:
        export_button_selector = "button:has-text('Export')"
        export_btn_frame = await find_frame_with_selector(page, export_button_selector)
    if export_btn_frame is None:
        export_button_selector = "button[name='confirm']:has-text('Export')"
        export_btn_frame = await find_frame_with_selector(page, export_button_selector)
    if export_btn_frame is None:
        raise RuntimeError("Export button not found.")
    await export_btn_frame.click(export_button_selector)

    print("Waiting for export to complete and download link to appear...")
    await asyncio.sleep(15)

    export_link_selector = "a.export-url"
    export_frame = await find_frame_with_selector(page, export_link_selector, 120_000)
    if export_frame is None:
        raise RuntimeError("Export link did not appear in any frame.")
    await export_frame.wait_for_selector(export_link_selector, timeout=300_000)

    print("Export link found, starting download...")
    os.makedirs(download_dir, exist_ok=True)
    async with page.expect_download() as download_info:
        await export_frame.click(export_link_selector)
    download = await download_info.value
    ext = os.path.splitext(download.suggested_filename)[1] or ".xlsx"
    yesterday = datetime.now() - timedelta(days=1)
    date_prefix = yesterday.strftime("%d-%b-%y")
    target_path = os.path.join(download_dir, f"{date_prefix}-{file_base_name}{ext}")
    await download.save_as(target_path)
    print(f"Download completed and saved to: {target_path}")
    print(f"Exports folder (for upload): {os.path.abspath(download_dir)}")


async def login_and_open_portal() -> None:
    """
    Login to SKIDATA, open Parking_Dashboards_SA_Makan_Analyze,
    and download only Parking Transactions.
    """
    load_dotenv()

    login_url = get_env("SKIDATA_URL", LOGIN_URL_DEFAULT)
    tenant = get_env("SKIDATA_TENANT")
    login_name = get_env("SKIDATA_LOGIN")
    password = get_env("SKIDATA_PASSWORD")

    headless = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context: BrowserContext = await browser.new_context(
            accept_downloads=True,
            viewport={"width": 1920, "height": 1080},
            screen={"width": 1920, "height": 1080},
        )

        project_root = os.path.dirname(os.path.abspath(__file__))
        download_dir = os.path.join(project_root, EXPORTS_DIR)

        page: Page = await context.new_page()
        print(f"Opening login page: {login_url}")
        await page.goto(login_url, wait_until="domcontentloaded")

        await page.fill("#tenantName", tenant)
        await page.fill("#loginName", login_name)
        await page.fill("#password", password)

        print("Submitting login form...")
        await page.click("form.login_form button[type='submit']")

        card_container_selector = (
            "div.qv-content-li-inner:has-text('Parking_Dashboards_SA_Makan_Analyze')"
        )

        print("Waiting for portal card 'Parking_Dashboards_SA_Makan_Analyze'...")
        await page.wait_for_load_state("networkidle")

        target_frame = None
        for frame in page.frames:
            try:
                print(f"Checking frame for portal card: {frame.url}")
                await frame.wait_for_selector(card_container_selector, timeout=30_000)
                target_frame = frame
                break
            except PlaywrightTimeoutError:
                continue

        if target_frame is None:
            print("Could not find portal card in any frame. Debugging information:")
            for frame in page.frames:
                try:
                    titles = await frame.eval_on_selector_all(
                        "div.qv-content-li-inner",
                        "els => els.map(el => el.textContent && el.textContent.trim())",
                    )
                    if titles:
                        print(
                            f"Frame {frame.url} has qv-content-li-inner text blocks:",
                            titles,
                        )
                except Exception:
                    continue

            print(
                "Leaving browser open for manual inspection. "
                "Check what appears after login and share details so we can refine selectors."
            )
            try:
                while True:
                    await asyncio.sleep(3600)
            except KeyboardInterrupt:
                print("Closing browser...")
            finally:
                await browser.close()
            return

        card_element = await target_frame.query_selector(card_container_selector)
        if card_element is None:
            raise RuntimeError("Portal card element not found after it appeared.")

        await card_element.scroll_into_view_if_needed()
        await card_element.click()

        print("Clicked portal card, waiting for new tab with reports...")
        new_page_promise = context.wait_for_event("page", timeout=60_000)
        try:
            new_page = await new_page_promise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("No new tab opened after clicking portal card.") from exc

        await new_page.wait_for_load_state("domcontentloaded")
        print("New reports tab detected. URL:", new_page.url)
        try:
            await new_page.wait_for_load_state("networkidle", timeout=60_000)
        except PlaywrightTimeoutError:
            await new_page.wait_for_load_state("load")
        await asyncio.sleep(5)

        await run_parking_transactions_flow(new_page, download_dir)

        await asyncio.sleep(3)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(login_and_open_portal())
