"""Extract Nobero size charts from ?view=size-guide. Never writes null rows."""

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

from core.parser import infer_category, infer_fit, normalize_chart_rows, normalize_measurement_key
from core.schema import product_error, product_success

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("NoberoSizeChartExtractor")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(_THIS_DIR, "outputs", "nobero_tshirts.json")
OUTPUT_FILE = os.path.join(_THIS_DIR, "outputs", "nobero_size_chart.json")
BRAND = "Nobero"
CONCURRENCY = 5
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _strip(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


def parse_size_guide_html(html: str) -> List[Dict[str, Any]]:
    if "sizeChartTable" not in html:
        return []
    headers: List[str] = []
    for th in re.findall(r"<th[^>]*>([\s\S]*?)</th>", html, re.I):
        clean = _strip(th)
        if clean:
            headers.append(clean)
    if not headers:
        return []
    size_idx = 0
    for i, h in enumerate(headers):
        if h.lower() in {"size", "brand size", "tag size"}:
            size_idx = i
            break
    rows: List[Dict[str, Any]] = []
    trs = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", html, re.I)
    for tr in trs[1:]:
        tds = [_strip(td) for td in re.findall(r"<td[^>]*>([\s\S]*?)</td>", tr, re.I)]
        tds = [td for td in tds if td]
        if len(tds) < len(headers):
            continue
        size_val = tds[size_idx]
        row: Dict[str, Any] = {"size": size_val}
        for i, header in enumerate(headers):
            if i == size_idx:
                continue
            try:
                row[normalize_measurement_key(header)] = float(tds[i])
            except (TypeError, ValueError):
                continue
        if len(row) > 1:
            rows.append(row)
    return normalize_chart_rows(rows)


async def fetch_one(client: Any, url: str) -> Dict[str, Any]:
    handle = url.split("/products/")[-1].split("?")[0]
    target = f"{url}?view=size-guide"
    last_err = "unknown"
    for attempt in range(5):
        try:
            resp = await client.get(target, headers=HEADERS, timeout=12.0)
            if resp.status_code == 429:
                await asyncio.sleep(2.0 * (attempt + 1))
                last_err = "HTTP_429"
                continue
            if resp.status_code != 200:
                return product_error(
                    brand=BRAND,
                    product_url=url,
                    handle=handle,
                    error_type=f"HTTP_{resp.status_code}",
                    message=f"HTTP {resp.status_code} for {target}",
                )
            rows = parse_size_guide_html(resp.text)
            if not rows:
                return product_error(
                    brand=BRAND,
                    product_url=url,
                    handle=handle,
                    error_type="NO_PARSED_SIZES",
                    message="Could not parse sizeChartTable",
                )
            category = infer_category(handle, url)
            fit = infer_fit(handle, url, default="regular")
            return product_success(
                source="live",
                brand=BRAND,
                category=category,
                fit=fit,
                product_url=url,
                handle=handle,
                size_chart=rows,
            )
        except Exception as exc:
            last_err = str(exc)
            if attempt == 4:
                return product_error(
                    brand=BRAND,
                    product_url=url,
                    handle=handle,
                    error_type="EXCEPTION",
                    message=last_err,
                )
            await asyncio.sleep(1.0)
    return product_error(
        brand=BRAND,
        product_url=url,
        handle=handle,
        error_type="MAX_RETRIES_EXCEEDED",
        message=last_err,
    )


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
    logger.info("Extracting Nobero size charts for %s products...", len(products))
    sem = asyncio.Semaphore(CONCURRENCY)
    completed = 0

    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def worker(url: str) -> Dict[str, Any]:
            nonlocal completed
            async with sem:
                res = await fetch_one(client, url)
                completed += 1
                if completed % 50 == 0 or completed == len(products):
                    logger.info("Nobero %s/%s", completed, len(products))
                return res if isinstance(res, dict) else product_error(
                    brand=BRAND,
                    product_url=url,
                    handle=url.split("/products/")[-1].split("?")[0],
                    error_type="NULL_RECORD",
                    message="worker returned empty",
                )

        results = await asyncio.gather(*[worker(u) for u in products])

    results = [r if isinstance(r, dict) else product_error(
        brand=BRAND, product_url="", error_type="NULL_RECORD", message="null row"
    ) for r in results]

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, ensure_ascii=False)
    ok = sum(1 for r in results if r.get("status") == "success")
    logger.info("Saved %s Nobero charts (%s success) → %s", len(results), ok, OUTPUT_FILE)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
