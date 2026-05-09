import asyncio
import json
import os
import logging
import httpx

# ── Logger ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("SnitchScraper")

# ── Config ───────────────────────────────────────────────────────────────────
API_BASE    = "https://mxemjhp3rt.ap-south-1.awsapprunner.com/products/plp/v2"
SITE_BASE   = "https://www.snitch.com"
PRODUCT_TYPE = "T-Shirts"
LIMIT       = 100          # products per page (max the API returns)
MAX_PAGES   = 50           # hard safety cap  (~5 000 products max)
CONCURRENCY = 3            # parallel requests — keep low to avoid rate limits

OUTPUT_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "product_pages.json")

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "DNT": "1",
    "Origin": SITE_BASE,
    "Referer": f"{SITE_BASE}/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Mobile Safari/537.36"
    ),
    "client-id": "snitch_secret",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def save_products(products: list[str]):
    ensure_dirs()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(products)} product URLs → {OUTPUT_FILE}")


def build_url(product: dict) -> str | None:
    handle = product.get("handle")
    pid = product.get("shopify_product_id")
    if handle and pid:
        return f"{SITE_BASE}/men-t-shirts/{handle}/{pid}/buy"
    return None


# ── Core fetch ────────────────────────────────────────────────────────────────

async def fetch_page(client: httpx.AsyncClient, page: int) -> dict | None:
    params = {
        "page": page,
        "limit": LIMIT,
        "0": "[object Object]",   # reproduced exactly from the captured request
        "product_type": PRODUCT_TYPE,
    }
    try:
        resp = await client.get(API_BASE, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Page {page} HTTP error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Page {page} error: {e}")
    return None


def extract_products(data: dict) -> list[dict]:
    return data.get("data", {}).get("products", [])


def get_total_pages(data: dict, fetched_count: int) -> int | None:
    """
    Try to read total page / product count from the response envelope.
    Returns None if not found — we'll just paginate until empty.
    """
    for key in ("total_pages", "totalPages", "last_page", "pages"):
        val = data.get(key)
        if isinstance(val, int):
            return val
    for key in ("total", "total_count", "totalCount", "count"):
        val = data.get(key)
        if isinstance(val, int) and val > 0:
            import math
            return math.ceil(val / LIMIT)
    return None


# ── Main scraper ──────────────────────────────────────────────────────────────

async def scrape_all() -> list[str]:
    ensure_dirs()
    all_urls: list[str] = []
    seen: set[str] = set()
    total_pages: int | None = None

    async with httpx.AsyncClient() as client:
        # ── Step 1: fetch page 1 to discover total pages ──────────────────
        logger.info(f"Fetching page 1 to probe API structure …")
        data = await fetch_page(client, page=1)
        if data is None:
            logger.error("Failed to fetch page 1 — aborting")
            return []

        # Log raw keys so you can verify field names in your terminal
        if isinstance(data, dict):
            logger.info(f"Top-level response keys: {list(data.keys())}")

        products = extract_products(data)
        logger.info(f"Page 1: {len(products)} products in response")

        if not products:
            logger.error("No products found in page 1 response — check extract_products() field names")
            return []

        # Collect URLs from page 1
        for p in products:
            url = build_url(p)
            if url and url not in seen:
                seen.add(url)
                all_urls.append(url)

        total_pages = get_total_pages(data, len(products))
        if total_pages:
            logger.info(f"API reports {total_pages} total pages")
        else:
            logger.info("Could not determine total pages — will paginate until empty")
            total_pages = MAX_PAGES

        total_pages = min(total_pages, MAX_PAGES)

        # ── Step 2: fetch remaining pages with bounded concurrency ────────
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def fetch_and_collect(page_num: int):
            async with semaphore:
                logger.info(f"Fetching page {page_num}/{total_pages} …")
                d = await fetch_page(client, page=page_num)
                return page_num, d

        tasks = [fetch_and_collect(p) for p in range(2, total_pages + 1)]
        results = await asyncio.gather(*tasks)

        for page_num, d in sorted(results, key=lambda x: x[0]):
            if d is None:
                continue
            prods = extract_products(d)
            if not prods:
                logger.info(f"Page {page_num}: empty — stopping early")
                break
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