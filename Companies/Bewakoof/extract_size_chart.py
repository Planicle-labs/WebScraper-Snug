"""Extract Bewakoof size charts from __NEXT_DATA__ garment_details."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List

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
logger = logging.getLogger("BewakoofSizeChartExtractor")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(_THIS_DIR, "outputs", "products.json")
OUTPUT_FILE = os.path.join(_THIS_DIR, "outputs", "bewakoof_size_chart.json")
BRAND = "Bewakoof"
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


def _is_customizable(url: str, name: str = "") -> bool:
    blob = f"{url} {name}".lower()
    return "custom" in blob


def enrich_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    url = raw.get("product_url") or ""
    name = str(raw.get("product_name") or "")
    if _is_customizable(url, name) or (
        raw.get("status") != "success"
        and "productDetails missing" in str(raw.get("message") or "")
    ):
        return product_error(
            brand=BRAND,
            product_url=url,
            error_type="CUSTOMIZABLE",
            message="Customizable PDP skipped",
        )
    if raw.get("status") != "success":
        return product_error(
            brand=BRAND,
            product_url=url,
            error_type=str(raw.get("error_type") or "EXTRACT_ERROR"),
            message=str(raw.get("message") or "error"),
        )
    rows = normalize_chart_rows(raw.get("size_chart") or [])
    if not rows:
        return product_error(
            brand=BRAND,
            product_url=url,
            error_type="NO_SIZE_CHART_DATA",
            message="No size measurements",
        )
    fit = infer_fit(name, url, default="regular")
    category = infer_category(name, url)
    sleeve = infer_sleeve(name, url, sleeve_cm=next(
        (r.get("garment_sleeve_cm") for r in rows if r.get("garment_sleeve_cm") is not None),
        None,
    ))
    return product_success(
        source="live",
        brand=BRAND,
        category=category,
        fit=fit,
        sleeve=sleeve,
        product_url=url,
        product_name=name,
        product_id=str(raw.get("product_id") or ""),
        size_chart=rows,
    )


def parse_garment_details(sz_item: Dict[str, Any]) -> Dict[str, Any]:
    parsed: Dict[str, Any] = {"size": str(sz_item.get("name", "UNKNOWN"))}
    details = sz_item.get("garment_details") or {}
    if not details and "units" in sz_item:
        units = sz_item.get("units") or []
        if units:
            m_list = units[0].get("measurements") or []
            details = {m.get("key", ""): m.get("value") for m in m_list if m.get("key")}
    for raw_k, val in details.items():
        if val is None:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if "(cm)" in str(raw_k).lower():
            parsed[raw_k] = round(num, 1)
        else:
            parsed[raw_k] = round(num * 2.54, 1)
    return parsed


async def fetch_one(client: Any, url: str) -> Dict[str, Any]:
    if _is_customizable(url):
        return product_error(
            brand=BRAND,
            product_url=url,
            error_type="CUSTOMIZABLE",
            message="Customizable PDP skipped",
        )
    try:
        resp = await client.get(url, headers=HEADERS, timeout=12.0)
        if resp.status_code != 200:
            return product_error(
                brand=BRAND,
                product_url=url,
                error_type=f"HTTP_{resp.status_code}",
                message=f"HTTP {resp.status_code}",
            )
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">([\s\S]*?)</script>',
            resp.text,
        )
        if not m:
            return product_error(
                brand=BRAND,
                product_url=url,
                error_type="NO_NEXT_DATA",
                message="__NEXT_DATA__ missing",
            )
        data = json.loads(m.group(1))
        pd = data.get("props", {}).get("pageProps", {}).get("productDetails") or {}
        if not pd:
            return product_error(
                brand=BRAND,
                product_url=url,
                error_type="CUSTOMIZABLE",
                message="productDetails missing (likely customizable)",
            )
        name = str(pd.get("name") or "")
        parsed_chart: List[Dict[str, Any]] = []
        for sz_item in pd.get("sizes") or []:
            row = parse_garment_details(sz_item)
            if len(row) > 1:
                parsed_chart.append(row)
        if not parsed_chart:
            size_guide = (pd.get("size_guide") or {}).get("size_guide_entries") or {}
            for sz_data in size_guide.values():
                if isinstance(sz_data, dict):
                    row = parse_garment_details(sz_data)
                    if len(row) > 1:
                        parsed_chart.append(row)
        rows = normalize_chart_rows(parsed_chart)
        if not rows:
            return product_error(
                brand=BRAND,
                product_url=url,
                error_type="NO_SIZE_CHART_DATA",
                message="No size measurements found",
            )
        fit = infer_fit(name, url, default="regular")
        category = infer_category(name, url)
        sleeve = infer_sleeve(name, url, sleeve_cm=next(
            (r.get("garment_sleeve_cm") for r in rows if r.get("garment_sleeve_cm") is not None),
            None,
        ))
        return product_success(
            source="live",
            brand=BRAND,
            category=category,
            fit=fit,
            sleeve=sleeve,
            product_url=url,
            product_name=name,
            product_id=str(pd.get("id") or ""),
            size_chart=rows,
        )
    except Exception as exc:
        return product_error(
            brand=BRAND,
            product_url=url,
            error_type="EXCEPTION",
            message=str(exc),
        )


def rewrite_existing() -> None:
    if not os.path.exists(OUTPUT_FILE):
        logger.error("No existing %s", OUTPUT_FILE)
        return
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        raw_rows = json.load(f)
    results = [
        enrich_record(r) if isinstance(r, dict) else product_error(
            brand=BRAND, product_url="", error_type="NULL_RECORD", message="null"
        )
        for r in raw_rows
    ]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, ensure_ascii=False)
    ok = sum(1 for r in results if r.get("status") == "success")
    logger.info("Rewrote %s Bewakoof rows (%s success) → %s", len(results), ok, OUTPUT_FILE)


async def main_async() -> None:
    try:
        import httpx
    except ImportError:
        logger.error("httpx is required: uv pip install httpx")
        return
    if not os.path.exists(PRODUCTS_FILE):
        logger.error("Products file not found")
        return
    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)
    logger.info("Extracting Bewakoof charts for %s products...", len(products))
    sem = asyncio.Semaphore(CONCURRENCY)
    completed = 0
    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def worker(url: str) -> Dict[str, Any]:
            nonlocal completed
            async with sem:
                res = await fetch_one(client, url)
                completed += 1
                if completed % 100 == 0 or completed == len(products):
                    logger.info("Bewakoof %s/%s", completed, len(products))
                return res
        results = await asyncio.gather(*[worker(u) for u in products])
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(list(results), out, indent=2, ensure_ascii=False)
    ok = sum(1 for r in results if r.get("status") == "success")
    logger.info("Saved %s Bewakoof charts (%s success) → %s", len(results), ok, OUTPUT_FILE)


def main() -> None:
    if "--from-existing" in sys.argv:
        rewrite_existing()
        return
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
