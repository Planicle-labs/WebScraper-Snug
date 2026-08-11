import asyncio
import json
import os
import re
import logging
import httpx
from typing import Dict, Any, List

# ── Logger Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BewakoofSizeChartExtractor")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(_THIS_DIR, "outputs", "products.json")
OUTPUT_FILE = os.path.join(_THIS_DIR, "outputs", "bewakoof_size_chart.json")
CONCURRENCY = 15

API_TOKEN = "NGNlNTUwYTc0MjBjYzQzZTdiZTNhMmY1NjNhMThhOGU6OGI1NThkZDgtOGQ5ZS00OWYxLTk4MDAtNzYxMGEzOGNjYzNk"
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Origin": "https://www.bewakoof.com",
    "Referer": "https://www.bewakoof.com/",
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
}


def normalize_key(raw_key: str) -> str:
    """
    Normalizes measurement key to clean snake_case ending with _cm.
    """
    key = raw_key.lower().strip()
    key = re.sub(r"\s*\(in inch\)", "", key)
    key = re.sub(r"\s*\(inch\)", "", key)
    key = re.sub(r"\s*\(cm\)", "", key)
    key = key.replace("front length", "length")
    key = re.sub(r"[^a-z0-9_]+", "_", key).strip("_")
    if not key.endswith("_cm"):
        key = f"{key}_cm"
    return key


def parse_garment_details_to_cm(sz_item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts sizes entry with garment_details or size_guide to CM.
    """
    parsed = {"size": str(sz_item.get("name", "UNKNOWN")).upper()}
    details = sz_item.get("garment_details", {})
    if not details and "units" in sz_item:
        units = sz_item.get("units", [])
        if units:
            m_list = units[0].get("measurements", [])
            details = {m.get("key", ""): m.get("value") for m in m_list if m.get("key")}

    for raw_k, val in details.items():
        if val is None:
            continue
        try:
            val_float = float(val)
        except (ValueError, TypeError):
            continue
        
        norm_k = normalize_key(raw_k)
        if "(cm)" in raw_k.lower():
            parsed[norm_k] = round(val_float, 1)
        else:
            parsed[norm_k] = round(val_float * 2.54, 1)

    return parsed


async def fetch_bewakoof_size_chart(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    """
    Fetches product page HTML and parses size_chart from __NEXT_DATA__.
    """
    try:
        resp = await client.get(url, headers=HEADERS, timeout=12.0)
        if resp.status_code != 200:
            return {
                "status": "error",
                "error_type": f"HTTP_{resp.status_code}",
                "message": f"HTTP status {resp.status_code} returned for URL: {url}",
                "product_url": url,
            }

        html = resp.text
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">([\s\S]*?)</script>', html)
        if not m:
            return {
                "status": "error",
                "error_type": "NO_NEXT_DATA",
                "message": "Could not find __NEXT_DATA__ in page HTML.",
                "product_url": url,
            }

        data = json.loads(m.group(1))
        pd = data.get("props", {}).get("pageProps", {}).get("productDetails", {})
        if not pd:
            return {
                "status": "error",
                "error_type": "NO_PRODUCT_DETAILS",
                "message": "productDetails missing in __NEXT_DATA__ pageProps.",
                "product_url": url,
            }

        product_id = str(pd.get("id", ""))
        product_name = pd.get("name", "")
        product_type = pd.get("product_type") or pd.get("ptype") or "T-Shirt"
        
        raw_sizes = pd.get("sizes", [])
        parsed_chart = []

        if raw_sizes:
            for sz_item in raw_sizes:
                parsed_row = parse_garment_details_to_cm(sz_item)
                if len(parsed_row) > 1:
                    parsed_chart.append(parsed_row)

        if not parsed_chart:
            size_guide = pd.get("size_guide", {}).get("size_guide_entries", {})
            for sz_name, sz_data in size_guide.items():
                parsed_row = parse_garment_details_to_cm(sz_data)
                if len(parsed_row) > 1:
                    parsed_chart.append(parsed_row)

        if not parsed_chart:
            return {
                "status": "error",
                "error_type": "NO_SIZE_CHART_DATA",
                "message": "No size measurements found for product.",
                "product_id": product_id,
                "product_name": product_name,
                "product_url": url,
            }

        return {
            "status": "success",
            "unit": "cm",
            "product_id": product_id,
            "product_name": product_name,
            "product_type": product_type,
            "size_chart": parsed_chart,
            "product_url": url,
        }

    except Exception as e:
        return {
            "status": "error",
            "error_type": "EXCEPTION",
            "message": str(e),
            "product_url": url,
        }


async def main():
    if not os.path.exists(PRODUCTS_FILE):
        logger.error(f"Products file not found: {PRODUCTS_FILE}")
        return

    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)

    logger.info(f"Extracting size charts for {len(products)} Bewakoof products...")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    results = []
    completed = 0

    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def worker(url: str):
            nonlocal completed
            async with semaphore:
                res = await fetch_bewakoof_size_chart(client, url)
                completed += 1
                if completed % 100 == 0 or completed == len(products):
                    logger.info(f"Bewakoof Progress: {completed}/{len(products)} completed")
                return res

        results = await asyncio.gather(*[worker(url) for url in products])

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, ensure_ascii=False)

    successful = [r for r in results if r.get("status") == "success"]
    logger.info(f"Saved {len(results)} Bewakoof size chart results ({len(successful)} successful) → {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
