"""Extract Snitch size charts from their size-info API (fit-level templates)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.parser import infer_category, infer_fit, infer_sleeve, normalize_chart_rows
from core.schema import product_error, product_success

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("SnitchSizeChartExtractor")

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
BRAND = "Snitch"
CONCURRENCY = 15


def parse_url_details(url: str) -> Tuple[Optional[str], Optional[str]]:
    if not url:
        return None, None
    product_id = None
    match_id = re.search(r"/(\d{10,15})(?:/|$)", url)
    if match_id:
        product_id = match_id.group(1)
    parts = [p for p in url.split("/") if p and p != "buy" and not p.isdigit() and "snitch.com" not in p]
    handle = parts[-1] if parts else None
    return product_id, handle


def _fit(api_fit: Optional[str], *texts: str) -> str:
    url_fit = infer_fit(*texts)
    api = infer_fit(api_fit or "") if api_fit else None
    if url_fit == "oversized":
        return "oversized"
    return api or url_fit or "regular"


def enrich_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Rewrite an existing Snitch row into the Snug product schema (no network)."""
    url = raw.get("product_url") or ""
    product_id, handle = parse_url_details(url)
    if raw.get("status") != "success":
        return product_error(
            brand=BRAND,
            product_url=url,
            handle=handle or "",
            error_type=str(raw.get("error_type") or "EXTRACT_ERROR"),
            message=str(raw.get("message") or "error"),
        )
    rows = normalize_chart_rows(raw.get("size_chart") or [])
    if not rows:
        return product_error(
            brand=BRAND,
            product_url=url,
            handle=handle or "",
            error_type="NO_SIZE_CHART",
            message="empty chart",
        )
    url_fit = infer_fit(url, handle or "")
    api_fit = infer_fit(str(raw.get("fit") or ""))
    if url_fit == "oversized" and api_fit != "oversized":
        return product_error(
            brand=BRAND,
            product_url=url,
            handle=handle or "",
            error_type="FIT_MISMATCH",
            message="URL says oversized but stored fit is not; needs live re-fetch",
        )
    fit = _fit(str(raw.get("fit") or ""), url, handle or "")
    category = infer_category(url, handle or "", str(raw.get("product_type") or ""))
    sleeve = infer_sleeve(url, handle or "", sleeve_cm=next(
        (r.get("garment_sleeve_cm") for r in rows if r.get("garment_sleeve_cm") is not None),
        None,
    ))
    return product_success(
        source="brand_api",
        brand=BRAND,
        category=category,
        fit=fit,
        sleeve=sleeve,
        product_url=url,
        handle=handle or "",
        product_id=str(raw.get("product_id") or product_id or ""),
        size_chart=rows,
    )


def parse_dynamic_row(row: Dict[str, Any]) -> Dict[str, Any]:
    parsed: Dict[str, Any] = {"size": str(row.get("SIZE") or row.get("size") or "UNKNOWN")}
    skip = {"size", "size_label", "fit", "sleeves", "status", "product_id", "branch_code"}
    for raw_k, val in row.items():
        if raw_k.lower() in skip or val is None:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        key = raw_k
        if "(cm)" in raw_k.lower():
            parsed[key] = round(num, 1)
        else:
            parsed[key] = round(num * 2.54, 1)
    return parsed


async def fetch_one(client: Any, url: str) -> Dict[str, Any]:
    product_id, handle = parse_url_details(url)
    if not product_id:
        return product_error(
            brand=BRAND,
            product_url=url,
            handle=handle or "",
            error_type="INVALID_URL",
            message="Could not parse product id",
        )
    parsed_sizes: List[Dict[str, Any]] = []
    detected_fit = None
    product_type = ""
    try:
        resp = await client.get(
            f"{API_BASE}/products/size-info/v4",
            params={"product_id": product_id},
            headers=HEADERS,
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json().get("data") or {}
            info = data.get("product_info") or {}
            detected_fit = info.get("fit")
            product_type = info.get("product_type") or ""
            chart_data = data.get("chart_data") or {}
            for rows in chart_data.values():
                if isinstance(rows, list):
                    parsed_sizes.extend(parse_dynamic_row(r) for r in rows if isinstance(r, dict))
    except Exception:
        parsed_sizes = []

    if not parsed_sizes:
        detected_fit = detected_fit or infer_fit(handle or "", url, default="regular")
        try:
            resp = await client.get(
                f"{API_BASE}/products/size-chart/v2",
                params={"shopify_product_type": product_type or "T-Shirts", "fit": detected_fit},
                headers=HEADERS,
                timeout=10.0,
            )
            if resp.status_code == 200:
                payload = resp.json()
                v2 = payload.get("data", []) if isinstance(payload, dict) else payload
                if isinstance(v2, list):
                    parsed_sizes = [parse_dynamic_row(r) for r in v2 if isinstance(r, dict)]
        except Exception:
            parsed_sizes = []

    rows = normalize_chart_rows(parsed_sizes)
    if not rows:
        return product_error(
            brand=BRAND,
            product_url=url,
            handle=handle or "",
            error_type="NO_SIZE_CHART_AVAILABLE",
            message=f"No size chart for product {product_id}",
        )
    fit = _fit(detected_fit, url, handle or "")
    category = infer_category(url, handle or "", product_type)
    sleeve = infer_sleeve(url, handle or "", sleeve_cm=next(
        (r.get("garment_sleeve_cm") for r in rows if r.get("garment_sleeve_cm") is not None),
        None,
    ))
    return product_success(
        source="brand_api",
        brand=BRAND,
        category=category,
        fit=fit,
        sleeve=sleeve,
        product_url=url,
        handle=handle or "",
        product_id=product_id,
        size_chart=rows,
    )


def rewrite_existing() -> None:
    if not os.path.exists(OUTPUT_FILE):
        logger.error("No existing %s to rewrite", OUTPUT_FILE)
        return
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        raw_rows = json.load(f)
    results = [enrich_record(r) if isinstance(r, dict) else product_error(
        brand=BRAND, product_url="", error_type="NULL_RECORD", message="null"
    ) for r in raw_rows]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, ensure_ascii=False)
    ok = sum(1 for r in results if r.get("status") == "success")
    logger.info("Rewrote %s Snitch rows (%s success) → %s", len(results), ok, OUTPUT_FILE)


async def main_async() -> None:
    try:
        import httpx
    except ImportError:
        logger.error("httpx is required: uv pip install httpx")
        return
    if not os.path.exists(PRODUCTS_FILE):
        logger.error("Products file not found: %s", PRODUCTS_FILE)
        return
    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)
    logger.info("Extracting Snitch size charts for %s products...", len(products))
    sem = asyncio.Semaphore(CONCURRENCY)
    completed = 0
    async with httpx.AsyncClient() as client:
        async def worker(url: str) -> Dict[str, Any]:
            nonlocal completed
            async with sem:
                res = await fetch_one(client, url)
                completed += 1
                if completed % 100 == 0 or completed == len(products):
                    logger.info("Snitch %s/%s", completed, len(products))
                return res
        results = await asyncio.gather(*[worker(u) for u in products])
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(list(results), out, indent=2, ensure_ascii=False)
    ok = sum(1 for r in results if r.get("status") == "success")
    logger.info("Saved %s Snitch charts (%s success) → %s", len(results), ok, OUTPUT_FILE)


def main() -> None:
    if "--from-existing" in sys.argv:
        rewrite_existing()
        return
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
