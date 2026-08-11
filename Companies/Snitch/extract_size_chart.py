import asyncio
import json
import os
import re
import logging
import httpx
from typing import Dict, Any, Optional, Tuple, List

# ── Logger Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("SnitchSizeChartExtractor")

# ── Configuration ─────────────────────────────────────────────────────────────
API_BASE = "https://mxemjhp3rt.ap-south-1.awsapprunner.com"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.snitch.com",
    "Referer": "https://www.snitch.com/",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Mobile Safari/537.36"
    ),
    "client-id": "snitch_secret",
}

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(_THIS_DIR, "outputs", "products.json")
OUTPUT_FILE = os.path.join(_THIS_DIR, "outputs", "snitch_size_chart.json")
SAMPLE_FILE = os.path.join(_THIS_DIR, "outputs", "snitch_size_chart_sample.json")
CONCURRENCY = 15


def parse_url_details(url: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    Parses product_id, handle, and product_type from Snitch product URL.
    """
    if not url or not isinstance(url, str):
        return None, None, "T-Shirts"

    product_id = None
    match_id = re.search(r"/(\d{10,15})(?:/|$)", url)
    if match_id:
        product_id = match_id.group(1)

    handle = None
    match_handle = re.search(r"/[a-z0-9\-]+(?:/([a-z0-9\-]+))/\d{10,15}", url)
    if match_handle:
        handle = match_handle.group(1)
    else:
        parts = [p for p in url.split("/") if p and p != "buy" and not p.isdigit() and "snitch.com" not in p]
        if parts:
            handle = parts[-1]

    product_type = "T-Shirts"
    url_lower = url.lower()
    if "shirt" in url_lower and "t-shirt" not in url_lower:
        product_type = "Shirts"
    elif "jean" in url_lower:
        product_type = "Jeans"
    elif "trouser" in url_lower:
        product_type = "Trousers"
    elif "shorts" in url_lower:
        product_type = "Shorts"
    elif "boxer" in url_lower:
        product_type = "Boxers"

    return product_id, handle, product_type


def infer_fit_from_text(text: str) -> str:
    """
    Infer product fit from title, handle, or URL text.
    """
    txt = text.lower().replace("-", " ")
    if "oversized" in txt:
        return "Oversized Fit"
    elif "slim" in txt or "polo" in txt:
        return "Slim Fit"
    elif "relaxed" in txt:
        return "Relaxed Fit"
    elif "box" in txt:
        return "Box Fit"
    else:
        return "Slim Fit"


def infer_sleeve_type(text: str) -> str:
    """
    Infer sleeve type from text.
    """
    txt = text.lower()
    if "full sleeve" in txt or "long sleeve" in txt:
        return "Full Sleeve"
    elif "sleeveless" in txt or "tank" in txt:
        return "Sleeveless"
    elif "elbow" in txt:
        return "Elbow Sleeve"
    else:
        return "Half Sleeve"


def normalize_measurement_key(raw_key: str) -> str:
    """
    Normalizes arbitrary API column names to clean snake_case keys ending with _cm.
    """
    key = raw_key.lower().strip()
    key = re.sub(r"\s*\(cm\)", "", key)
    key = re.sub(r"\s*\(inch\)", "", key)
    key = key.replace("front length", "length")
    key = re.sub(r"[^a-z0-9_]+", "_", key).strip("_")
    
    if not key.endswith("_cm"):
        key = f"{key}_cm"
    return key


def parse_dynamic_row_to_cm(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dynamically parses and converts measurement attributes in a row to CM.
    """
    parsed_item = {}
    size_label = row.get("SIZE") or row.get("size")
    parsed_item["size"] = str(size_label).upper() if size_label else "UNKNOWN"

    metadata_keys = {"size", "size_label", "fit", "sleeves", "status", "product_id", "branch_code"}

    for raw_k, val in row.items():
        k_lower = raw_k.lower()
        if k_lower in metadata_keys or val is None:
            continue

        try:
            val_float = float(val)
        except (ValueError, TypeError):
            continue

        norm_key = normalize_measurement_key(raw_k)

        if "(cm)" in raw_k.lower():
            parsed_item[norm_key] = round(val_float, 1)
        else:
            parsed_item[norm_key] = round(val_float * 2.54, 1)

    return parsed_item


def sort_and_deduplicate_sizes(sizes_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicates and sorts size entries standardly (XS -> 6XL).
    """
    standard_order = ["XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL", "5XL", "6XL"]
    order_map = {sz: i for i, sz in enumerate(standard_order)}

    seen = set()
    deduped = []
    for entry in sizes_list:
        sz = entry.get("size")
        if sz and sz not in seen:
            seen.add(sz)
            deduped.append(entry)

    deduped.sort(key=lambda x: order_map.get(x.get("size", ""), 99))
    return deduped


async def fetch_size_chart_by_product_url(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    """
    Fetches and formats size chart in CM for a Snitch product URL.
    """
    product_id, handle, product_type = parse_url_details(url)

    if not product_id:
        return {
            "status": "error",
            "error_type": "INVALID_URL",
            "message": f"Could not parse a valid 13-digit product ID from URL: {url}",
            "product_url": url,
        }

    detected_fit = None
    target_sleeve = infer_sleeve_type(f"{handle or ''} {url}")
    parsed_sizes = []

    # Step 1: Call Snitch API /products/size-info/v4
    endpoint_v4 = f"{API_BASE}/products/size-info/v4"
    try:
        resp_v4 = await client.get(endpoint_v4, params={"product_id": product_id}, headers=HEADERS, timeout=10.0)
        
        if resp_v4.status_code == 404:
            return {
                "status": "error",
                "error_type": "PRODUCT_NOT_FOUND",
                "message": f"Product ID {product_id} was not found on Snitch servers.",
                "product_url": url,
            }
        
        resp_v4.raise_for_status()
        res_json = resp_v4.json()
        data = res_json.get("data", {})
        
        product_info = data.get("product_info", {})
        chart_data_dict = data.get("chart_data", {})
        
        if product_info.get("fit"):
            detected_fit = product_info.get("fit")
        if product_info.get("product_type"):
            product_type = product_info.get("product_type")

        if chart_data_dict:
            for cat, rows in chart_data_dict.items():
                if isinstance(rows, list):
                    for row in rows:
                        parsed_item = parse_dynamic_row_to_cm(row)
                        parsed_sizes.append(parsed_item)

    except Exception:
        pass

    # Step 2: Fallback to /products/size-chart/v2
    if not parsed_sizes:
        if not detected_fit:
            text_to_check = f"{handle or ''} {url}"
            detected_fit = infer_fit_from_text(text_to_check)

        endpoint_v2 = f"{API_BASE}/products/size-chart/v2"
        params_v2 = {"shopify_product_type": product_type, "fit": detected_fit}

        try:
            resp_v2 = await client.get(endpoint_v2, params=params_v2, headers=HEADERS, timeout=10.0)
            if resp_v2.status_code == 200:
                v2_res = resp_v2.json()
                v2_data = v2_res.get("data", []) if isinstance(v2_res, dict) else v2_res
                
                if isinstance(v2_data, list):
                    matching_rows = [
                        row for row in v2_data
                        if row.get("fit", "").lower() == detected_fit.lower()
                    ]
                    if not matching_rows:
                        matching_rows = v2_data

                    sleeve_matched = [
                        row for row in matching_rows
                        if row.get("sleeves", "").lower() == target_sleeve.lower()
                    ]
                    if sleeve_matched:
                        matching_rows = sleeve_matched

                    for row in matching_rows:
                        parsed_item = parse_dynamic_row_to_cm(row)
                        parsed_sizes.append(parsed_item)
        except Exception:
            pass

    if not parsed_sizes:
        return {
            "status": "error",
            "error_type": "NO_SIZE_CHART_AVAILABLE",
            "message": f"Could not find size chart for product ID {product_id}.",
            "product_id": product_id,
            "product_url": url,
        }

    final_chart = sort_and_deduplicate_sizes(parsed_sizes)

    return {
        "status": "success",
        "unit": "cm",
        "product_id": product_id,
        "product_type": product_type,
        "fit": detected_fit,
        "size_chart": final_chart,
        "product_url": url,
    }


async def main():
    if not os.path.exists(PRODUCTS_FILE):
        logger.error(f"Products file not found: {PRODUCTS_FILE}")
        return

    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)

    if not products:
        logger.error("No product URLs found in products.json.")
        return

    logger.info(f"Extracting size charts for {len(products)} products in products.json...")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    results = []
    completed = 0

    async with httpx.AsyncClient() as client:
        async def worker(url: str):
            nonlocal completed
            async with semaphore:
                res = await fetch_size_chart_by_product_url(client, url)
                completed += 1
                if completed % 100 == 0 or completed == len(products):
                    logger.info(f"Progress: {completed}/{len(products)} completed")
                return res

        results = await asyncio.gather(*[worker(url) for url in products])

    # Clean up sample file if present
    if os.path.exists(SAMPLE_FILE):
        try:
            os.remove(SAMPLE_FILE)
            logger.info(f"Removed old sample file: {SAMPLE_FILE}")
        except Exception as e:
            logger.warning(f"Could not remove sample file: {e}")

    # Save output to JSON file
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, ensure_ascii=False)

    successful = [r for r in results if r.get("status") == "success"]
    logger.info(f"Saved {len(results)} size chart results ({len(successful)} successful) → {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
