"""
page_search/run.py
------------------
Stage 2 of the Snug pipeline.

Reads product page URLs from outputs/product_pages.json (written by Stage 1),
checks robots.txt for each brand, then routes each page to the appropriate
scraper (html, pdf, vision) to extract the size chart data.

Run from project root:
    python -m page_search.run
"""

import json
import os
import asyncio
from urllib.parse import urlparse

from core.logger import logger
from core.robots import check_robots
from core.utils import read_json_file
from page_search.scrapers.html_scraper import scrape_html_size_chart
from Companies.Overlays.image_scraper import scrape_image_size_chart

# ── Paths (resolved from this file's location so they work from any cwd) ────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)

CONFIG_FILE = os.path.join(_THIS_DIR, "config", "brands.json")
BLOCKED_FILE = os.path.join(_ROOT, "outputs", "blocked.json")
PRODUCT_PAGES_FILE = os.path.join(_ROOT, "outputs", "product_pages.json")


def ensure_dirs():
    os.makedirs(os.path.join(_ROOT, "outputs"), exist_ok=True)


def load_brands():
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"Config file not found: {CONFIG_FILE}. Creating an empty one.")
        with open(CONFIG_FILE, "w") as f:
            json.dump([], f)
        return []

    with open(CONFIG_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON in {CONFIG_FILE}: {e}")
            return []


def load_product_pages() -> list[str]:
    data = read_json_file(PRODUCT_PAGES_FILE)
    if not data:
        return []
    return [url for url in data if isinstance(url, str) and url.strip()]


def url_matches_brand(url: str, brand: dict) -> bool:
    base_url = brand.get("base_url", "").strip()
    if not base_url:
        return False
    return urlparse(url).netloc == urlparse(base_url).netloc


def product_urls_for_brand(brand: dict, discovered_urls: list[str]) -> list[str]:
    matched = [url for url in discovered_urls if url_matches_brand(url, brand)]
    if matched:
        return matched

    fallback = brand.get("product_url")
    return [fallback] if fallback else []


def save_blocked(blocked_brand):
    blocked_list = []
    if os.path.exists(BLOCKED_FILE):
        with open(BLOCKED_FILE, "r") as f:
            try:
                blocked_list = json.load(f)
            except json.JSONDecodeError:
                blocked_list = []

    # Avoid duplicates
    if not any(b.get("brand_name") == blocked_brand.get("brand_name") for b in blocked_list):
        blocked_list.append(blocked_brand)
        with open(BLOCKED_FILE, "w") as f:
            json.dump(blocked_list, f, indent=2)
        logger.info(f"Saved {blocked_brand.get('brand_name')} to {BLOCKED_FILE}")


async def async_main():
    logger.info("Starting page_search (Stage 2)...")
    ensure_dirs()
    brands = load_brands()
    discovered_urls = load_product_pages()

    if not brands:
        logger.info(f"No brands found in {CONFIG_FILE}. Please add a brand and try again.")
        return

    logger.info(f"Loaded {len(brands)} brand(s) for processing.")
    logger.info(f"Loaded {len(discovered_urls)} discovered product URL(s) from Stage 1.")

    for brand in brands:
        brand_name = brand.get("brand_name", "Unknown")
        base_url = brand.get("base_url")
        chart_type = brand.get("chart_type", "html")
        target_urls = product_urls_for_brand(brand, discovered_urls)

        logger.info(f"--- Processing brand: {brand_name} ---")

        if not base_url:
            logger.warning(f"Missing base_url for {brand_name}. Skipping.")
            continue

        if not target_urls:
            logger.warning(f"No matching product URLs found for {brand_name}. Skipping.")
            continue

        if chart_type == "html":
            logger.info(f"[{brand_name}] Routing {len(target_urls)} product page(s) to HTML Scraper...")
            for target_url in target_urls:
                is_allowed, delay = check_robots(base_url, target_url)
                if is_allowed:
                    logger.info(f"✅ ALLOWED: {brand_name} (Crawl Delay: {delay}s)")
                else:
                    logger.warning(f"🚫 BLOCKED by robots.txt: {brand_name}. Proceeding anyway (override).")

                raw_data = await scrape_html_size_chart(brand_name, target_url)
                if raw_data:
                    logger.info(f"[{brand_name}] Successfully extracted {len(raw_data)} rows/entries of data.")
                else:
                    logger.warning(f"[{brand_name}] Extraction returned empty for {target_url}.")

        elif chart_type == "image":
            # Build the output folder: outputs/{brand_name_lower}_output_img/
            folder_name = brand_name.lower().replace(" ", "_") + "_output_img"
            output_dir = os.path.join(_ROOT, "outputs", folder_name)
            logger.info(
                f"[{brand_name}] Routing {len(target_urls)} product page(s) to Image Scraper → saving to: {output_dir}"
            )

            image_results = []
            for target_url in target_urls:
                is_allowed, delay = check_robots(base_url, target_url)
                if is_allowed:
                    logger.info(f"✅ ALLOWED: {brand_name} (Crawl Delay: {delay}s)")
                else:
                    logger.warning(f"🚫 BLOCKED by robots.txt: {brand_name}. Proceeding anyway (override).")
                image_results.extend(await scrape_image_size_chart(brand_name, target_url, output_dir))

            ok_count = sum(1 for r in image_results if r.get("status") == "ok")
            logger.info(f"[{brand_name}] Image scrape complete: {ok_count}/{len(target_urls)} product page(s) saved.")

            # Save a JSON manifest of all results
            manifest_path = os.path.join(_ROOT, "outputs", f"{brand_name.lower()}_results.json")
            existing_manifest = []
            if os.path.exists(manifest_path):
                with open(manifest_path, "r", encoding="utf-8") as mf:
                    try:
                        existing_manifest = json.load(mf)
                    except json.JSONDecodeError:
                        existing_manifest = []
            existing_manifest.extend(image_results)
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(existing_manifest, mf, indent=2, ensure_ascii=False)
            logger.info(f"[{brand_name}] Results manifest saved → {manifest_path}")

        else:
            logger.info(f"[{brand_name}] Chart type '{chart_type}' not yet supported.")


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
