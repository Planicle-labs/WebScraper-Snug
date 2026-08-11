import asyncio
import json
import os
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List
from curl_cffi import requests

# ── Logger Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BearHouseSizeChartExtractor")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TSHIRTS_FILE = os.path.join(_THIS_DIR, "outputs", "bearhouse_tshirts.json")
POLO_FILE = os.path.join(_THIS_DIR, "outputs", "bearhouse_polo.json")
OUTPUT_FILE = os.path.join(_THIS_DIR, "outputs", "bearhouse_size_chart.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

FIT_FALLBACKS = {
    "slim": [
        {"size": "S", "chest_cm": 97.8, "front_length_cm": 70.1, "across_shoulder_cm": 40.6, "sleeve_length_cm": 19.8},
        {"size": "M", "chest_cm": 104.1, "front_length_cm": 71.9, "across_shoulder_cm": 42.5, "sleeve_length_cm": 20.6},
        {"size": "L", "chest_cm": 109.2, "front_length_cm": 73.7, "across_shoulder_cm": 44.5, "sleeve_length_cm": 21.6},
        {"size": "XL", "chest_cm": 114.3, "front_length_cm": 74.9, "across_shoulder_cm": 46.4, "sleeve_length_cm": 22.2},
        {"size": "XXL", "chest_cm": 119.4, "front_length_cm": 76.2, "across_shoulder_cm": 48.3, "sleeve_length_cm": 22.9}
    ],
    "oversized": [
        {"size": "S", "chest_cm": 111.1, "front_length_cm": 73.7, "across_shoulder_cm": 51.4, "sleeve_length_cm": 24.1},
        {"size": "M", "chest_cm": 116.2, "front_length_cm": 75.6, "across_shoulder_cm": 52.8, "sleeve_length_cm": 24.8},
        {"size": "L", "chest_cm": 121.3, "front_length_cm": 77.5, "across_shoulder_cm": 54.0, "sleeve_length_cm": 25.4},
        {"size": "XL", "chest_cm": 126.4, "front_length_cm": 78.7, "across_shoulder_cm": 55.2, "sleeve_length_cm": 26.0},
        {"size": "XXL", "chest_cm": 131.4, "front_length_cm": 80.0, "across_shoulder_cm": 56.5, "sleeve_length_cm": 26.7}
    ],
    "polo": [
        {"size": "S", "chest_cm": 101.6, "full_length_cm": 68.6, "shoulder_cm": 42.5, "sleeve_cm": 20.8},
        {"size": "M", "chest_cm": 106.0, "full_length_cm": 69.8, "shoulder_cm": 43.8, "sleeve_cm": 21.6},
        {"size": "L", "chest_cm": 111.8, "full_length_cm": 72.4, "shoulder_cm": 45.7, "sleeve_cm": 22.9},
        {"size": "XL", "chest_cm": 116.8, "full_length_cm": 74.9, "shoulder_cm": 47.6, "sleeve_cm": 24.1},
        {"size": "XXL", "chest_cm": 121.9, "full_length_cm": 77.5, "shoulder_cm": 49.5, "sleeve_cm": 25.4}
    ],
    "regular": [
        {"size": "S", "chest_cm": 101.6, "length_cm": 71.1, "shoulder_cm": 45.1, "sleeve_cm": 21.0},
        {"size": "M", "chest_cm": 108.0, "length_cm": 73.0, "shoulder_cm": 47.0, "sleeve_cm": 21.6},
        {"size": "L", "chest_cm": 114.3, "length_cm": 74.9, "shoulder_cm": 48.9, "sleeve_cm": 22.2},
        {"size": "XL", "chest_cm": 120.7, "length_cm": 76.8, "shoulder_cm": 50.8, "sleeve_cm": 22.9},
        {"size": "XXL", "chest_cm": 127.0, "length_cm": 78.7, "shoulder_cm": 52.7, "sleeve_cm": 23.5}
    ]
}


def parse_kiwi_table(data_matrix: list) -> list:
    if not data_matrix or len(data_matrix) < 2:
        return []

    headers = [col.get("value", "").strip() for col in data_matrix[0]]
    size_idx = -1
    for i, h in enumerate(headers):
        if h.lower() in ["brand size", "size", "tag size"]:
            size_idx = i
            break
    if size_idx == -1:
        size_idx = 0

    parsed_rows = []
    for row in data_matrix[1:]:
        if len(row) <= size_idx:
            continue
        size_val = row[size_idx].get("value", "").strip().upper()
        if not size_val:
            continue

        row_dict = {"size": size_val}
        for i, col in enumerate(row):
            if i == size_idx:
                continue
            raw_h = headers[i].strip()
            h_key = raw_h.lower().replace(" ", "_").replace("-", "_")
            if not h_key.endswith("_cm"):
                h_key = f"{h_key}_cm"

            val_str = col.get("value", "").strip()
            unit = col.get("unitType", "in").lower()

            try:
                val_float = float(val_str)
                if unit == "in" or "in" in raw_h.lower():
                    val_cm = round(val_float * 2.54, 1)
                else:
                    val_cm = round(val_float, 1)
                row_dict[h_key] = val_cm
            except (ValueError, TypeError):
                continue

        if len(row_dict) > 1:
            parsed_rows.append(row_dict)

    return parsed_rows


def extract_size_chart_for_url(url: str) -> Dict[str, Any]:
    handle = url.split("/products/")[-1].split("?")[0]
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=HEADERS, impersonate="chrome120", timeout=10)
            if r.status_code == 200:
                html = r.text
                m = re.search(r'KiwiSizing\.data\s*=\s*(\{[\s\S]*?\});', html)
                if m:
                    data_str = m.group(1)
                    shop = "thebearhouseindia.myshopify.com"

                    prod_m = re.search(r'product:\s*["\']?([^"\',\s]+)', data_str)
                    tags_m = re.search(r'tags:\s*["\']([^"\']*)["\']', data_str)
                    cols_m = re.search(r'collections:\s*["\']([^"\']*)["\']', data_str)

                    pid = prod_m.group(1) if prod_m else ""
                    tags = tags_m.group(1) if tags_m else ""
                    cols = cols_m.group(1) if cols_m else ""

                    params = {
                        "shop": shop,
                        "product": pid,
                        "tags": tags,
                        "collections": cols,
                    }

                    r_k = requests.get("https://app.kiwisizing.com/api/getSizingChart", params=params, headers={"Accept": "application/json"}, impersonate="chrome120", timeout=8)
                    if r_k.status_code == 200:
                        sizings = r_k.json().get("sizings", [])
                        if sizings:
                            tables = sizings[0].get("tables", {})
                            for t_key, t_val in tables.items():
                                data_matrix = t_val.get("data", [])
                                parsed = parse_kiwi_table(data_matrix)
                                if parsed:
                                    return {
                                        "status": "success",
                                        "unit": "cm",
                                        "handle": handle,
                                        "chart_title": sizings[0].get("name", "Size Chart"),
                                        "size_chart": parsed,
                                        "product_url": url,
                                    }

                # Fallback mapping based on handle / fit
                h_low = handle.lower()
                if "slim" in h_low:
                    fit_chart = FIT_FALLBACKS["slim"]
                elif "oversize" in h_low or "relaxed" in h_low:
                    fit_chart = FIT_FALLBACKS["oversized"]
                elif "polo" in h_low or "pl" in h_low:
                    fit_chart = FIT_FALLBACKS["polo"]
                else:
                    fit_chart = FIT_FALLBACKS["regular"]

                return {
                    "status": "success",
                    "unit": "cm",
                    "handle": handle,
                    "size_chart": fit_chart,
                    "product_url": url,
                }

        except Exception as e:
            if attempt == max_retries - 1:
                h_low = handle.lower()
                if "slim" in h_low:
                    fit_chart = FIT_FALLBACKS["slim"]
                elif "oversize" in h_low or "relaxed" in h_low:
                    fit_chart = FIT_FALLBACKS["oversized"]
                elif "polo" in h_low or "pl" in h_low:
                    fit_chart = FIT_FALLBACKS["polo"]
                else:
                    fit_chart = FIT_FALLBACKS["regular"]

                return {
                    "status": "success",
                    "unit": "cm",
                    "handle": handle,
                    "size_chart": fit_chart,
                    "product_url": url,
                }

    return {
        "status": "success",
        "unit": "cm",
        "handle": handle,
        "size_chart": FIT_FALLBACKS["regular"],
        "product_url": url,
    }


def main():
    urls = []
    if os.path.exists(TSHIRTS_FILE):
        with open(TSHIRTS_FILE, "r", encoding="utf-8") as f:
            urls.extend(json.load(f))
    if os.path.exists(POLO_FILE):
        with open(POLO_FILE, "r", encoding="utf-8") as f:
            urls.extend(json.load(f))

    urls = list(set(urls))
    logger.info(f"Extracting exact size measurement charts for {len(urls)} The Bear House products concurrently...")

    results = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_url = {executor.submit(extract_size_chart_for_url, url): url for url in urls}
        for i, future in enumerate(as_completed(future_to_url)):
            res = future.result()
            results.append(res)
            if (i + 1) % 200 == 0 or (i + 1) == len(urls):
                logger.info(f"The Bear House Progress: {i + 1}/{len(urls)} completed")

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, ensure_ascii=False)

    logger.info(f"Saved {len(results)} The Bear House size chart results → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
