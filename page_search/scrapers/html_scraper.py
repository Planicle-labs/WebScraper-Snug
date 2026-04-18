import asyncio
import random
from core.logger import logger

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
]


async def scrape_html_size_chart(brand_name, target_url):
    """
    Scrapes the raw html size chart data for a brand.
    """
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth.stealth import Stealth

        stealth = Stealth()
    except ImportError:
        logger.error(
            "playwright is not installed. Please install it with: pip install playwright && playwright install chromium"
        )
        return []

    logger.info(
        f"[{brand_name}] Starting async Playwright to scrape size chart at {target_url}"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Random user agent
        ua = random.choice(USER_AGENTS)
        context = await browser.new_context(user_agent=ua)
        page = await context.new_page()

        # Apply stealth mode
        await stealth.apply_stealth_async(page)

        try:
            # Wait until domcontentloaded to handle most tables efficiently
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

            # Simple fallback waiting for any JS generic load
            await asyncio.sleep(2)

            # NEW: Look for a sizing button and click it to open the modal
            logger.info(
                f"[{brand_name}] Searching for a 'Size Guide' or 'Size Chart' button to click..."
            )
            import re

            try:
                # Common elements that contain size guides ("Size Guide", "Size Chart", "Sizing")
                size_button = (
                    page.locator("button, a, span, div")
                    .filter(
                        has_text=re.compile(
                            r"(size\s*(guide|chart)|sizing)", re.IGNORECASE
                        )
                    )
                    .first
                )
                await size_button.wait_for(state="visible", timeout=5000)
                logger.info(
                    f"[{brand_name}] Found sizing button. Clicking to open chart modal..."
                )
                await size_button.click()
                await asyncio.sleep(
                    3
                )  # Give the modal time to open and render the table
            except Exception as e:
                logger.info(
                    f"[{brand_name}] Sizing button not found within 5s (the table may already be visible). Proceeding..."
                )

            # Detect page type
            tables = await page.query_selector_all("table")

            extracted_data = []

            if tables:
                logger.info(f"[{brand_name}] Type detected: HTML Table (Type A)")
                for index, table in enumerate(tables):
                    logger.info(f"[{brand_name}] Parsing Table {index + 1}...")
                    rows = await table.query_selector_all("tr")
                    for r_idx, row in enumerate(rows):
                        cells = await row.query_selector_all("td, th")
                        cell_texts = []
                        for cell in cells:
                            text = await cell.inner_text()
                            cell_texts.append(text.strip().replace("\n", " "))

                        if cell_texts:
                            extracted_data.append(cell_texts)
                            logger.info(f"[{brand_name}] Row {r_idx}: {cell_texts}")
            else:
                logger.info(
                    f"[{brand_name}] No standard <table> found. Looking for <div>-based charts (Type B)..."
                )
                chart_divs = await page.query_selector_all(
                    "div[class*='size'], div[class*='chart']"
                )
                if chart_divs:
                    logger.info(
                        f"[{brand_name}] Found generic size chart container <div>."
                    )
                    # Fallback text extraction for Phase 2 - will refine per-brand in Phase 4
                    for idx, div in enumerate(chart_divs):
                        text = await div.inner_text()
                        if text and len(text.strip()) > 20:
                            extracted_data.append([text.strip()])
                else:
                    logger.warning(
                        f"[{brand_name}] Could not find <table> or size-chart <div> elements."
                    )

            return extracted_data

        except Exception as e:
            logger.error(f"[{brand_name}] Error during page evaluation: {e}")
            return []
        finally:
            await browser.close()
