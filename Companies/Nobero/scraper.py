import asyncio
import json
import math
import os
import logging

import httpx

# ── Logger ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("NoberoScraper")

# ── Config ───────────────────────────────────────────────────────────────────
SITE_BASE    = "https://nobero.com"
API_BASE     = "https://api-prod.nobero.com/v2/collection-handle/t-shirts"
PAGE_SIZE    = 100         # original was 12, bumping to reduce requests
CONCURRENCY  = 3
MAX_PRODUCTS = 10000

OUTPUT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
OUTPUT_FILE  = os.path.join(OUTPUT_DIR, "nobero_tshirts.json")

BASE_PARAMS = {
    "sorting_score": "rpti_brkns_v1",
    "page_size": PAGE_SIZE,
}

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": SITE_BASE,
    "Referer": f"{SITE_BASE}/",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Mobile Safari/537.36"
    ),
    "x-tmrw-tenant-id": "nobero",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def save_products(urls: list[str]):
    ensure_dirs()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(urls, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(urls)} product URLs → {OUTPUT_FILE}")


def build_url(product: dict) -> str | None:
    handle = product.get("handle", "").strip()
    if handle:
        return f"{SITE_BASE}/products/{handle}"
    pid = product.get("product_id", "").strip()
    if pid:
        return f"{SITE_BASE}/products/{pid}"
    return None


# ── Core fetch ────────────────────────────────────────────────────────────────

async def fetch_page(client: httpx.AsyncClient, page: int) -> dict | None:
    params = {**BASE_PARAMS, "page": page}
    try:
        resp = await client.get(API_BASE, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Page {page} HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        logger.error(f"Page {page} error: {e}")
    return None


def extract_products(data: dict) -> list[dict]:
    return data.get("products", [])


def get_total_pages(data: dict) -> int:
    total = data.get("total_count", 0)
    page_size = data.get("page_count", PAGE_SIZE)  # they call it page_count but it's page_size
    if total and page_size:
        return math.ceil(total / PAGE_SIZE)
    return 1


# ── Main ──────────────────────────────────────────────────────────────────────

async def scrape_all() -> list[str]:
    ensure_dirs()
    all_urls: list[str] = []
    seen: set[str] = set()

    async with httpx.AsyncClient() as client:
        # Step 1: fetch page 1 to get total count
        logger.info("Fetching page 1 to get total count …")
        data = await fetch_page(client, page=1)
        if data is None:
            logger.error("Failed to fetch page 1 — aborting")
            return []

        total_count = data.get("total_count", "?")
        total_pages = get_total_pages(data)
        logger.info(f"Total products: {total_count} | Total pages: {total_pages} (page_size={PAGE_SIZE})")

        for p in extract_products(data):
            url = build_url(p)
            if url and url not in seen:
                seen.add(url)
                all_urls.append(url)
        logger.info(f"Page 1: {len(all_urls)} URLs collected")

        # Step 2: remaining pages concurrently
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def fetch_and_collect(page_num: int):
            async with semaphore:
                logger.info(f"Fetching page {page_num}/{total_pages} …")
                d = await fetch_page(client, page=page_num)
                return page_num, d

        results = await asyncio.gather(
            *[fetch_and_collect(p) for p in range(2, total_pages + 1)]
        )

        for page_num, d in sorted(results, key=lambda x: x[0]):
            if d is None:
                continue
            prods = extract_products(d)
            if not prods:
                logger.info(f"Page {page_num}: empty — skipping")
                continue
            new = 0
            for p in prods:
                url = build_url(p)
                if url and url not in seen:
                    seen.add(url)
                    all_urls.append(url)
                    new += 1
            logger.info(f"Page {page_num}: +{new} new URLs (total: {len(all_urls)})")

    logger.info(f"Done. Total unique product URLs: {len(all_urls)}")
    save_products(all_urls)
    return all_urls


if __name__ == "__main__":
    asyncio.run(scrape_all())