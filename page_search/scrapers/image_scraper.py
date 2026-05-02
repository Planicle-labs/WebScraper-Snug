"""
page_search/scrapers/image_scraper.py
--------------------------------------
Image-based size chart scraper.

Designed for brands (e.g. Overlaysnow) that display their size chart
as one or more <img> elements inside a modal triggered by a "Size chart" button.

Flow:
  1. Navigate to product URL
  2. Click the "Size chart" button  (selector: button.size_chart_text)
  3. Wait for the modal to load
  4. Scrape the src of every <img> inside the modal — including all tabs (Inches/CM)
  5. Download each image and save to output_dir/{product_slug}_{label}.png

Returns a list of dicts:
  [{"url": ..., "image_path": ..., "label": ..., "status": "ok"|"failed"}, ...]
"""

import asyncio
import os
import random
import re
import urllib.parse
import httpx

from core.logger import logger

try:
    from core.metrics import MetricsTracker
except ImportError:
    MetricsTracker = None


# ── Constants ─────────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# Selector for the button that opens the size chart modal
SIZE_CHART_BUTTON_SELECTORS = [
    "button.size_chart_text",
    "button:has-text('Size chart')",
    "button:has-text('Size Chart')",
    "a:has-text('Size chart')",
    "a:has-text('Size Chart')",
    "span:has-text('Size chart')",
]

# Tab buttons inside the modal (Inches / CM)
TAB_BUTTON_SELECTOR = ".tab-btn"

# Close button inside the modal
CLOSE_BUTTON_SELECTOR = ".close-btn"

# ── Helpers ───────────────────────────────────────────────────────────────────

def slug_from_url(url: str) -> str:
    """Extract the last path segment to use as filename slug."""
    path = urllib.parse.urlparse(url).path.rstrip("/")
    return path.split("/")[-1] or "product"


def sanitize_label(text: str) -> str:
    """Turn a tab label like 'Inches' → 'inches' safe for filenames."""
    return re.sub(r"[^a-z0-9]", "_", text.strip().lower())


async def download_image(src: str, dest_path: str) -> bool:
    """Download a single image from src URL and save to dest_path."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(src)
            response.raise_for_status()
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(response.content)
        logger.info(f"  [OK] Saved: {dest_path}")
        return True
    except Exception as e:
        logger.error(f"  [FAIL] Failed to download {src}: {e}")
        return False


# ── Core scrape function ──────────────────────────────────────────────────────

async def scrape_image_size_chart(
    brand_name: str,
    target_url: str,
    output_dir: str,
) -> list[dict]:
    """
    Navigate to target_url, click the size chart button, extract all chart
    images (across tabs), download them, and return a results list.

    Args:
        brand_name:  Human-readable brand name for logging.
        target_url:  Product page URL.
        output_dir:  Folder to save downloaded images into.

    Returns:
        List of dicts with keys: url, image_path, label, status.
    """
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth.stealth import Stealth
        stealth = Stealth()
    except ImportError:
        logger.error(
            "playwright or playwright-stealth is not installed. "
            "Run: pip install playwright playwright-stealth && playwright install chromium"
        )
        return []

    results: list[dict] = []
    product_slug = slug_from_url(target_url)
    os.makedirs(output_dir, exist_ok=True)

    logger.info(f"[{brand_name}] -- Scraping size chart images for: {target_url}")

    tracker = MetricsTracker(stage="PageSearch", url=target_url) if MetricsTracker else None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ua = random.choice(USER_AGENTS)
        context = await browser.new_context(
            user_agent=ua,
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        if tracker:
            tracker.attach_to_context(context)

        await stealth.apply_stealth_async(page)

        try:
            # ── 1. Navigate ───────────────────────────────────────────────
            logger.info(f"[{brand_name}] Navigating to {target_url}")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # ── 2. Dismiss any interstitial popups ────────────────────────
            # The site sometimes shows an app install banner or overlay — try
            # pressing Escape which closes most overlay patterns.
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
            except Exception:
                pass

            # ── 3. Click the Size Chart button ────────────────────────────
            button_clicked = False
            for selector in SIZE_CHART_BUTTON_SELECTORS:
                try:
                    btn = page.locator(selector).first
                    await btn.wait_for(state="visible", timeout=4000)
                    await btn.scroll_into_view_if_needed()
                    await btn.click()
                    logger.info(f"[{brand_name}] Clicked size chart button via: {selector}")
                    button_clicked = True
                    break
                except Exception:
                    continue

            if not button_clicked:
                logger.warning(
                    f"[{brand_name}] Could not find any Size Chart button on {target_url}. Skipping."
                )
                results.append({"url": target_url, "image_path": None, "label": None, "status": "no_button"})
                return results

            # ── 4. Wait for modal to render ───────────────────────────────
            try:
                await page.wait_for_selector(CLOSE_BUTTON_SELECTOR, state="visible", timeout=8000)
            except Exception:
                logger.warning(f"[{brand_name}] Modal close-btn not found — modal may not have opened.")

            await asyncio.sleep(1.5)  # allow images to fully load

            # ── 5. Collect all image srcs while modal is open ─────────────
            # Strategy: click each tab (if present) while the modal is still
            # open, accumulating unique image srcs. Download everything after.
            collected: dict[str, str] = {}  # src → label

            tab_buttons = await page.query_selector_all(TAB_BUTTON_SELECTOR)

            if tab_buttons:
                logger.info(f"[{brand_name}] Found {len(tab_buttons)} tab(s) in modal — iterating...")
                for tab in tab_buttons:
                    tab_label_raw = await tab.inner_text()
                    tab_label = sanitize_label(tab_label_raw)

                    try:
                        await tab.click(timeout=5000)
                        await asyncio.sleep(1.0)
                    except Exception as e:
                        logger.warning(f"[{brand_name}] Could not click tab '{tab_label_raw}': {e}")

                    # Collect visible images after tab switch
                    img_srcs: list[str] = await page.evaluate("""
                        () => {
                            const imgs = document.querySelectorAll('img');
                            const srcs = [];
                            for (const img of imgs) {
                                const src = img.src || img.getAttribute('data-src') || '';
                                if (src && src.includes('cdn.shopify.com') && src.length > 50) {
                                    if (!srcs.includes(src)) srcs.push(src);
                                }
                            }
                            return srcs;
                        }
                    """)
                    logger.info(f"[{brand_name}] Tab '{tab_label_raw}': {len(img_srcs)} image(s) found")
                    for src in img_srcs:
                        if src not in collected:
                            collected[src] = tab_label

            else:
                # No tabs — collect all CDN images currently visible in the modal
                logger.info(f"[{brand_name}] No tabs — scraping modal images directly.")
                img_srcs: list[str] = await page.evaluate("""
                    () => {
                        const imgs = document.querySelectorAll('img');
                        const srcs = [];
                        for (const img of imgs) {
                            const src = img.src || img.getAttribute('data-src') || '';
                            if (src && src.includes('cdn.shopify.com') && src.length > 50) {
                                if (!srcs.includes(src)) srcs.push(src);
                            }
                        }
                        return srcs;
                    }
                """)
                logger.info(f"[{brand_name}] Found {len(img_srcs)} image(s).")
                for idx, src in enumerate(img_srcs):
                    collected[src] = f"chart_{idx}"

            # ── 6. Download all collected images ──────────────────────────
            logger.info(f"[{brand_name}] Downloading {len(collected)} unique image(s)...")
            label_counts: dict[str, int] = {}
            for src, label in collected.items():
                count = label_counts.get(label, 0)
                suffix = f"_{count}" if count > 0 else ""
                label_counts[label] = count + 1
                filename = f"{product_slug}_{label}{suffix}.png"
                dest = os.path.join(output_dir, filename)
                ok = await download_image(src, dest)
                results.append({
                    "url": target_url,
                    "image_path": dest if ok else None,
                    "label": f"{label}{suffix}",
                    "status": "ok" if ok else "download_failed",
                })

            if not results:
                logger.warning(f"[{brand_name}] No images were extracted from {target_url}")
                results.append({"url": target_url, "image_path": None, "label": None, "status": "no_images"})


        except Exception as e:
            logger.error(f"[{brand_name}] Unexpected error on {target_url}: {e}", exc_info=True)
            results.append({"url": target_url, "image_path": None, "label": None, "status": f"error: {e}"})

        finally:
            if tracker:
                tracker.save()
            await browser.close()

    return results
