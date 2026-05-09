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
logger = logging.getLogger("RareRabbitScraper")

# ── Config ───────────────────────────────────────────────────────────────────
API_BASE    = (
    "https://search.unbxd.io"
    "/e94cac92f0f2da84ae5ca93f42a57658"
    "/ss-unbxd-aapac-prod-shopify-houseofrare58591725608684"
    "/category"
)

ROWS        = 100          # max per request (original was 20, 100 works fine)
CONCURRENCY = 3            # parallel requests
MAX_PRODUCTS = 5000        # hard safety cap

OUTPUT_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "rarerrabbit_polo.json")

# Fixed params that don't change across pages
SITE_BASE = "https://thehouseofrare.com"

BASE_PARAMS = {
    "p": 'category_handle_uFilter:"rr-men-polo-t-shirts"',  # changed
    "facet.multiselect": "true",
    "variants": "true",
    "variants.fields": "variantId,v_Size,v_availableForSale,v_sku",
    "variants.count": "20",
    "fields": (
        "title,uniqueId,price,imageUrl,productUrl,meta_my_fields_main_title,"
        "handle,images,variants,meta_my_fields_sub_title,compareAtPrice,"
        "computed_discount,grouped_products,meta_custom_variant_color_image,"
        "meta_my_fields_COLOR,swatch_image_url,meta_custom_gender,"
        "meta_custom_best_price,meta_custom_black_friday_sale_price,"
        "best_price,url,v_sku,gst_saving_amount"
    ),
    "spellcheck": "true",
    "pagetype": "boolean",
    "rows": ROWS,
}

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://thehouseofrare.com",
    "Referer": "https://thehouseofrare.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "unbxd-device-type": '{ "type":"desktop" , "os": "Windows" , "source": "browser" }',
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
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
    """
    productUrl from API: /products/rabo-mens-t-shirt-off-white
    Final URL: https://thehouseofrare.com/products/rabo-mens-t-shirt-off-white
    """
    product_url = product.get("productUrl", "").strip()
    if product_url:
        return f"{SITE_BASE}{product_url}"
    # fallback via handle
    handle = product.get("handle", "").strip()
    if handle:
        return f"{SITE_BASE}/products/{handle}"
    return None


# ── Core fetch ────────────────────────────────────────────────────────────────

async def fetch_page(client: httpx.AsyncClient, start: int) -> dict | None:
    params = {**BASE_PARAMS, "start": start}
    try:
        resp = await client.get(API_BASE, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"start={start} HTTP error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"start={start} error: {e}")
    return None


def extract_products(data: dict) -> list[dict]:
    return data.get("response", {}).get("products", [])


def get_total(data: dict) -> int:
    return data.get("response", {}).get("numberOfProducts", 0)


# ── Main ──────────────────────────────────────────────────────────────────────

async def scrape_all() -> list[str]:
    ensure_dirs()
    all_urls: list[str] = []
    seen: set[str] = set()

    async with httpx.AsyncClient() as client:
        # Step 1: probe page 0 to get total count
        logger.info("Fetching start=0 to get total product count …")
        data = await fetch_page(client, start=0)
        if data is None:
            logger.error("Failed to fetch start=0 — aborting")
            return []

        total = get_total(data)
        logger.info(f"Total products reported by API: {total}")
        total = min(total, MAX_PRODUCTS)

        # Collect from first page
        for p in extract_products(data):
            url = build_url(p)
            if url and url not in seen:
                seen.add(url)
                all_urls.append(url)
        logger.info(f"start=0: {len(all_urls)} URLs collected")

        # Step 2: remaining pages concurrently
        offsets = list(range(ROWS, total, ROWS))
        total_pages = math.ceil(total / ROWS)
        logger.info(f"Fetching {len(offsets)} more pages ({total_pages} total) …")

        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def fetch_and_collect(start: int):
            async with semaphore:
                logger.info(f"Fetching start={start}/{total} …")
                d = await fetch_page(client, start=start)
                return start, d

        results = await asyncio.gather(*[fetch_and_collect(s) for s in offsets])

        for start, d in sorted(results, key=lambda x: x[0]):
            if d is None:
                continue
            prods = extract_products(d)
            if not prods:
                logger.info(f"start={start}: empty response, skipping")
                continue
            new = 0
            for p in prods:
                url = build_url(p)
                if url and url not in seen:
                    seen.add(url)
                    all_urls.append(url)
                    new += 1
            logger.info(f"start={start}: +{new} new URLs (total: {len(all_urls)})")

    logger.info(f"Done. Total unique product URLs: {len(all_urls)}")
    save_products(all_urls)
    return all_urls


if __name__ == "__main__":
    asyncio.run(scrape_all())