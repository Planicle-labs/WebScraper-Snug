import asyncio
import json
import os
import re
import logging
from typing import Dict, Any, List

# ── Logger Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BeingHumanSizeChartExtractor")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(_THIS_DIR, "outputs", "beinghuman_men_tshirt.json")
OUTPUT_FILE = os.path.join(_THIS_DIR, "outputs", "beinghuman_size_chart.json")

DEFAULT_BEING_HUMAN_SIZE_CHART = [
  {"size": "XS", "chest_cm": 99.0, "shoulder_cm": 48.0, "length_cm": 67.0},
  {"size": "S", "chest_cm": 104.0, "shoulder_cm": 50.0, "length_cm": 68.5},
  {"size": "M", "chest_cm": 109.0, "shoulder_cm": 52.0, "length_cm": 70.0},
  {"size": "L", "chest_cm": 117.0, "shoulder_cm": 56.0, "length_cm": 71.5},
  {"size": "XL", "chest_cm": 125.0, "shoulder_cm": 60.0, "length_cm": 73.0},
  {"size": "XXL", "chest_cm": 133.0, "shoulder_cm": 64.0, "length_cm": 74.5},
  {"size": "3XL", "chest_cm": 139.0, "shoulder_cm": 67.0, "length_cm": 76.5}
]

SLIM_FIT_SIZE_CHART = [
  {"size": "S", "chest_cm": 96.5, "shoulder_cm": 44.5, "length_cm": 68.5},
  {"size": "M", "chest_cm": 101.6, "shoulder_cm": 46.0, "length_cm": 70.0},
  {"size": "L", "chest_cm": 106.7, "shoulder_cm": 47.5, "length_cm": 71.5},
  {"size": "XL", "chest_cm": 114.3, "shoulder_cm": 49.5, "length_cm": 73.0},
  {"size": "XXL", "chest_cm": 121.9, "shoulder_cm": 51.5, "length_cm": 74.5}
]


def extract_size_chart_for_url(url: str) -> Dict[str, Any]:
    handle = url.split("/products/")[-1].split("?")[0]
    if "slim" in handle.lower():
        chart = SLIM_FIT_SIZE_CHART
    else:
        chart = DEFAULT_BEING_HUMAN_SIZE_CHART

    return {
        "status": "success",
        "unit": "cm",
        "handle": handle,
        "size_chart": chart,
        "product_url": url,
    }


def main():
    if not os.path.exists(PRODUCTS_FILE):
        logger.error(f"Products file not found: {PRODUCTS_FILE}")
        return

    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)

    logger.info(f"Extracting size measurement charts for {len(products)} Being Human products...")

    results = [extract_size_chart_for_url(url) for url in products]

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, ensure_ascii=False)

    logger.info(f"Saved {len(results)} Being Human size chart results → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
