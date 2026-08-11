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
logger = logging.getLogger("NoberoSizeChartExtractor")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(_THIS_DIR, "outputs", "nobero_tshirts.json")
OUTPUT_FILE = os.path.join(_THIS_DIR, "outputs", "nobero_size_chart.json")
CONCURRENCY = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def normalize_key(raw_key: str) -> str:
    """
    Normalizes measurement header to clean snake_case ending with _cm.
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


async def fetch_nobero_size_chart(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    """
    Fetches Nobero size chart HTML via ?view=size-guide with 429 retry and parses measurements into CM.
    """
    target_url = f"{url}?view=size-guide"
    
    max_retries = 5
    for attempt in range(max_retries):
        try:
            resp = await client.get(target_url, headers=HEADERS, timeout=12.0)
            if resp.status_code == 429:
                await asyncio.sleep(2.0 * (attempt + 1))
                continue

            if resp.status_code != 200:
                return {
                    "status": "error",
                    "error_type": f"HTTP_{resp.status_code}",
                    "message": f"HTTP status {resp.status_code} returned for URL: {target_url}",
                    "product_url": url,
                }

            html = resp.text
            if "sizeChartTable" not in html:
                return {
                    "status": "error",
                    "error_type": "NO_SIZE_TABLE",
                    "message": "sizeChartTable not found in view=size-guide response.",
                    "product_url": url,
                }

            # Parse th headers
            headers_list = []
            th_matches = re.findall(r'<th[^>]*>([\s\S]*?)</th>', html, re.I)
            for th in th_matches:
                clean_th = re.sub(r'<[^>]+>', '', th).strip()
                if clean_th:
                    headers_list.append(clean_th)

            if not headers_list or "Size" not in headers_list:
                return {
                    "status": "error",
                    "error_type": "INVALID_HEADERS",
                    "message": "Size header missing in sizeChartTable.",
                    "product_url": url,
                }

            size_idx = headers_list.index("Size")
            measurement_cols = [
                (i, normalize_key(h))
                for i, h in enumerate(headers_list)
                if i != size_idx
            ]

            tr_matches = re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', html, re.I)
            parsed_sizes = []

            for tr in tr_matches[1:]: # skip header row
                td_matches = re.findall(r'<td[^>]*>([\s\S]*?)</td>', tr, re.I)
                clean_tds = [re.sub(r'<[^>]+>', '', td).strip() for td in td_matches if re.sub(r'<[^>]+>', '', td).strip()]
                
                if len(clean_tds) >= len(headers_list):
                    size_val = clean_tds[size_idx].upper()
                    row_dict = {"size": size_val}

                    for col_idx, norm_k in measurement_cols:
                        val_str = clean_tds[col_idx]
                        try:
                            val_float = float(val_str)
                            row_dict[norm_k] = round(val_float * 2.54, 1)
                        except (ValueError, TypeError):
                            continue
                    
                    if len(row_dict) > 1:
                        parsed_sizes.append(row_dict)

            if not parsed_sizes:
                return {
                    "status": "error",
                    "error_type": "NO_PARSED_SIZES",
                    "message": "Could not parse size rows from sizeChartTable.",
                    "product_url": url,
                }

            handle = url.split("/products/")[-1].split("?")[0]
            return {
                "status": "success",
                "unit": "cm",
                "handle": handle,
                "size_chart": parsed_sizes,
                "product_url": url,
            }

        except Exception as e:
            if attempt == max_retries - 1:
                return {
                    "status": "error",
                    "error_type": "EXCEPTION",
                    "message": str(e),
                    "product_url": url,
                }
            await asyncio.sleep(1.0)

    return {
        "status": "error",
        "error_type": "MAX_RETRIES_EXCEEDED",
        "message": "Failed after max retries.",
        "product_url": url,
    }


async def main():
    if not os.path.exists(PRODUCTS_FILE):
        logger.error(f"Products file not found: {PRODUCTS_FILE}")
        return

    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)

    logger.info(f"Extracting size charts for {len(products)} Nobero products with retry logic...")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    results = []
    completed = 0

    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def worker(url: str):
            nonlocal completed
            async with semaphore:
                res = await fetch_nobero_size_chart(client, url)
                completed += 1
                if completed % 50 == 0 or completed == len(products):
                    logger.info(f"Nobero Progress: {completed}/{len(products)} completed")
                return res if res else {"status": "error", "message": "None returned", "product_url": url}

        results = await asyncio.gather(*[worker(url) for url in products])

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, ensure_ascii=False)

    successful = [r for r in results if isinstance(r, dict) and r.get("status") == "success"]
    logger.info(f"Saved {len(results)} Nobero size chart results ({len(successful)} successful) → {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
