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
logger = logging.getLogger("SouledStoreScraper")

# ── Config ───────────────────────────────────────────────────────────────────
SITE_BASE    = "https://www.thesouledstore.com"
API_URL      = "https://api.thesouledstore.com/api/v2/graphql"
PAGE_SIZE    = 48          # original was 24, doubling to reduce requests
CONCURRENCY  = 3
MAX_PAGES    = 200         # hard safety cap

OUTPUT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
OUTPUT_FILE  = os.path.join(OUTPUT_DIR, "products.json")

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Authorization": "null",
    "Origin": SITE_BASE,
    "Referer": f"{SITE_BASE}/",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Mobile Safari/537.36"
    ),
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
}

# GraphQL query — only requesting fields we need for URL building
GQL_QUERY = """
{
  listing(
    page: %d,
    size: %d,
    gender: 1,
    isKids: false,
    isWeb: true,
    sort: DEFAULT,
    category: [],
    artist: [],
    tags: ["men-tshirts"]
    filters: {
      category: [], artist: [], tags: [], size: [], price: [],
      gender: [], fabric: [], color: [], discount: [], pattern: [],
      sleeve: [], neck: [], fittype: [], sneaker: [], agegroup: []
    },
    includefilter: ["Age Group", "Sneakers"],
    tiptile: 1
  ) {
    products {
      id
      product_slug: productSlug
    }
    pagination {
      currentPage
      totalPages
      totalProduct
    }
  }
}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def save_products(urls: list[str]):
    ensure_dirs()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(urls, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(urls)} product URLs → {OUTPUT_FILE}")


def build_url(product: dict) -> str | None:
    slug = (product.get("product_slug") or product.get("productSlug") or "").strip()
    if slug:
        return f"{SITE_BASE}/product/{slug}?gte=1"
    pid = product.get("id")
    if pid:
        return f"{SITE_BASE}/product/{pid}?gte=1"
    return None


def build_payload(page: int) -> dict:
    return {
        "query": GQL_QUERY % (page, PAGE_SIZE),
        "localcart": None,
        "is_ab_visible": True,
        "page_url": "/men-tshirts",
    }


# ── Core fetch ────────────────────────────────────────────────────────────────

async def fetch_page(client: httpx.AsyncClient, page: int) -> dict | None:
    try:
        resp = await client.post(
            API_URL,
            json=build_payload(page),
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Page {page} HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        logger.error(f"Page {page} error: {e}")
    return None


def extract_products(data: dict) -> list[dict]:
    return (
        data.get("data", {})
            .get("listing", {})
            .get("products", [])
        or []
    )


def get_pagination(data: dict) -> dict:
    return (
        data.get("data", {})
            .get("listing", {})
            .get("pagination", {})
        or {}
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def scrape_all() -> list[str]:
    ensure_dirs()
    all_urls: list[str] = []
    seen: set[str] = set()

    async with httpx.AsyncClient() as client:
        # Step 1: fetch page 1 to get totalPages
        logger.info("Fetching page 1 to get total pages …")
        data = await fetch_page(client, page=1)
        if data is None:
            logger.error("Failed to fetch page 1 — aborting")
            return []

        pagination = get_pagination(data)
        total_pages = min(pagination.get("totalPages", MAX_PAGES), MAX_PAGES)
        total_products = pagination.get("totalProduct", "?")
        logger.info(f"Total products: {total_products} | Total pages: {total_pages}")

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