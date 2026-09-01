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
logger = logging.getLogger("beinghumanscraper")

# ── Config ───────────────────────────────────────────────────────────────────
SITE_BASE    = "https://www.beinghumanclothing.com"
API_BASE     = f"{SITE_BASE}/collections/men-t-shirt/products.json"
LIMIT        = 250         # Shopify's hard max per page
CONCURRENCY  = 3

OUTPUT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
OUTPUT_FILE  = os.path.join(OUTPUT_DIR, "beinghuman_men_tshirt.json")

HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Mobile Safari/537.36"
    ),
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
    return None


# ── Core fetch ────────────────────────────────────────────────────────────────

async def fetch_page(client: httpx.AsyncClient, page: int) -> list[dict]:
    params = {"limit": LIMIT, "page": page}
    try:
        resp = await client.get(API_BASE, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.json().get("products", [])
    except httpx.HTTPStatusError as e:
        logger.error(f"Page {page} HTTP {e.response.status_code}")
    except Exception as e:
        logger.error(f"Page {page} error: {e}")
    return []


# ── Main ──────────────────────────────────────────────────────────────────────

async def scrape_all() -> list[str]:
    ensure_dirs()
    all_urls: list[str] = []
    seen: set[str] = set()

    async with httpx.AsyncClient() as client:
        page = 1
        semaphore = asyncio.Semaphore(CONCURRENCY)

        # Shopify doesn't expose total count on products.json
        # so we paginate until we get an empty page
        while True:
            logger.info(f"Fetching page {page} …")
            products = await fetch_page(client, page)

            if not products:
                logger.info(f"Page {page}: empty — done")
                break

            new = 0
            for p in products:
                url = build_url(p)
                if url and url not in seen:
                    seen.add(url)
                    all_urls.append(url)
                    new += 1

            logger.info(f"Page {page}: +{new} new URLs (total: {len(all_urls)})")

            if len(products) < LIMIT:
                # Last page — fewer results than limit means no more pages
                logger.info("Last page reached")
                break

            page += 1

    logger.info(f"Done. Total unique product URLs: {len(all_urls)}")
    save_products(all_urls)
    return all_urls


if __name__ == "__main__":
    asyncio.run(scrape_all())