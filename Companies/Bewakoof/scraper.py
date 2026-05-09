import asyncio
import json
import math
import os
import logging
from datetime import datetime

import httpx

# ── Logger ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BewakoofScraper")

# ── Config ───────────────────────────────────────────────────────────────────
SITE_BASE    = "https://www.bewakoof.com"
API_BASE     = "https://api-prod.bewakoof.com/v1/collections/collection-handle/men-t-shirts"
LIMIT        = 50         # max per page (original was 20, bump to 100)
CONCURRENCY  = 3
MAX_PRODUCTS = 10000       # hard safety cap

OUTPUT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
OUTPUT_FILE  = os.path.join(OUTPUT_DIR, "products.json")

# Static token from their frontend JS — not user-specific
API_TOKEN = "NGNlNTUwYTc0MjBjYzQzZTdiZTNhMmY1NjNhMThhOGU6OGI1NThkZDgtOGQ5ZS00OWYxLTk4MDAtNzYxMGEzOGNjYzNk"

def _dt_param() -> str:
    """Reproduces their dt param format: day:month:year:hour"""
    now = datetime.now()
    return f"{now.day}:{now.month}:{now.year}:{now.hour}"

BASE_PARAMS = {
    "qf": "true",
    "cover_type": "",
    "offer_type": "",
    "designer": "",
    "preview": "",
    "sort": "popular",
    "limit": LIMIT,
    "fields": "results",
    "compression": "false",
    "product_fields": "id,name,url,mrp,price,flip_image,display_image,in_stock,status,product_type,limited_edition,color_name,group_count,category_info,sp,cat_designer,offer,gender",
    "custom_filters": "",
    "plp_score": "plp_score1",
    "user_type": "new_user_score",
}

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": SITE_BASE,
    "Referer": f"{SITE_BASE}/",
    "api-token": API_TOKEN,
    "client-device-token": API_TOKEN,
    "x-client-device-token": API_TOKEN,
    "ab-id": "70",
    "x-ab-id": "70",
    "preferred-location": "IN",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Mobile Safari/537.36"
    ),
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
    slug = product.get("url", "").strip()
    if slug:
        return f"{SITE_BASE}/p/{slug}"
    pid = product.get("id")
    if pid:
        return f"{SITE_BASE}/p/{pid}"
    return None


# ── Core fetch ────────────────────────────────────────────────────────────────

async def fetch_page(client: httpx.AsyncClient, page: int) -> dict | None:
    params = {**BASE_PARAMS, "page": page, "dt": _dt_param()}
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
    # Response shape: { "data": { "products": [...], "total": N } }
    return (
        data.get("data", {}).get("products", [])
        or data.get("products", [])
        or []
    )


def get_total(data: dict) -> int:
    return (
        data.get("data", {}).get("total", 0)
        or data.get("total", 0)
        or 0
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def scrape_all() -> list[str]:
    ensure_dirs()
    all_urls: list[str] = []
    seen: set[str] = set()

    async with httpx.AsyncClient() as client:
        # Step 1: fetch page 1 to get total count
        logger.info("Fetching page 1 to probe total count …")
        data = await fetch_page(client, page=1)
        if data is None:
            logger.error("Failed to fetch page 1 — aborting")
            return []

        if isinstance(data, dict):
            logger.info(f"Top-level keys: {list(data.keys())}")

        total = get_total(data)
        logger.info(f"Total products reported: {total}")

        products = extract_products(data)
        logger.info(f"Page 1: {len(products)} products")

        if not products:
            logger.error("No products on page 1 — check extract_products() keys")
            return []

        for p in products:
            url = build_url(p)
            if url and url not in seen:
                seen.add(url)
                all_urls.append(url)

        total = min(total or MAX_PRODUCTS, MAX_PRODUCTS)
        total_pages = math.ceil(total / LIMIT)
        logger.info(f"Total pages to fetch: {total_pages}")

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