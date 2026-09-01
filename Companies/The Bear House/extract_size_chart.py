"""Extract The Bear House size charts from KiwiSizing. No silent fallbacks."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.kiwi import chart_from_sizing, fetch_sizing_chart, parse_bootstrap
from core.parser import infer_category, infer_fit
from core.schema import product_error, product_success

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
SITE = "https://thebearhouse.com"
SHOP = "thebearhouseindia.myshopify.com"
BRAND = "The Bear House"
JSON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
HTML_HEADERS = {
    **JSON_HEADERS,
    "Accept": "text/html,application/xhtml+xml;q=0.9",
}


def _get(url: str, timeout: int = 20, headers: dict | None = None) -> str:
    last_exc: Exception | None = None
    for attempt in range(5):
        try:
            req = Request(url, headers=headers or JSON_HEADERS)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except HTTPError as exc:
            last_exc = exc
            if exc.code == 404:
                raise
            if exc.code in {429, 500, 502, 503} and attempt < 4:
                time.sleep(4.0 * (attempt + 1))
                continue
            raise
        except Exception as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise last_exc or RuntimeError(url)


def handle_from_url(url: str) -> str:
    return url.split("/products/")[-1].split("?")[0].strip("/")


def fetch_product_json(handle: str) -> Dict[str, Any]:
    raw = _get(f"{SITE}/products/{handle}.json")
    p = json.loads(raw).get("product") or {}
    tags = p.get("tags") or ""
    if isinstance(tags, list):
        tags = ", ".join(tags)
    return {
        "id": str(p.get("id") or ""),
        "title": p.get("title") or "",
        "product_type": p.get("product_type") or "",
        "tags": tags,
    }


def chart_for_group(sample_url: str, meta: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    html = _get(sample_url, headers=HTML_HEADERS)
    boot = parse_bootstrap(html) or {}
    sizing = fetch_sizing_chart(
        shop=boot.get("shop") or SHOP,
        product=boot.get("product") or meta.get("id") or "",
        tags=boot.get("tags") or meta.get("tags") or "",
        collections=boot.get("collections") or "",
        referer=SITE + "/",
    )
    if not sizing:
        return "", []
    return chart_from_sizing(sizing)


def main() -> None:
    labeled: List[Tuple[str, str]] = []
    if os.path.exists(TSHIRTS_FILE):
        with open(TSHIRTS_FILE, "r", encoding="utf-8") as f:
            labeled.extend((u, "t-shirt") for u in json.load(f))
    if os.path.exists(POLO_FILE):
        with open(POLO_FILE, "r", encoding="utf-8") as f:
            labeled.extend((u, "polo") for u in json.load(f))
    by_url = {u: cat for u, cat in labeled}
    urls = list(by_url.keys())
    logger.info("Bear House: %s URLs — loading product.json", len(urls))

    catalog: Dict[str, Dict[str, Any]] = {}
    for i, url in enumerate(urls, 1):
        handle = handle_from_url(url)
        try:
            catalog[handle] = fetch_product_json(handle)
        except HTTPError as exc:
            if exc.code == 404:
                logger.warning("404 %s", handle)
            else:
                logger.warning("json fail %s: %s", handle, exc)
        except Exception as exc:
            logger.warning("json fail %s: %s", handle, exc)
        if i % 50 == 0 or i == len(urls):
            logger.info("product.json %s/%s", i, len(urls))
        time.sleep(0.35)

    groups: Dict[str, List[str]] = defaultdict(list)
    for url in urls:
        meta = catalog.get(handle_from_url(url))
        if meta:
            groups[meta.get("tags") or ""].append(url)
    logger.info("%s tag groups from %s catalog hits", len(groups), len(catalog))

    chart_by_tags: Dict[str, Tuple[str, List[Dict[str, Any]]]] = {}
    for i, (tags, group_urls) in enumerate(groups.items(), 1):
        sample = group_urls[0]
        handle = handle_from_url(sample)
        try:
            title, rows = chart_for_group(sample, catalog[handle])
        except Exception as exc:
            logger.warning("kiwi group fail: %s", exc)
            title, rows = "", []
        chart_by_tags[tags] = (title, rows)
        logger.info("kiwi group %s/%s (%s skus) title=%s rows=%s",
                    i, len(groups), len(group_urls), title, len(rows))
        time.sleep(1.0)

    results: List[Dict[str, Any]] = []
    for url in urls:
        handle = handle_from_url(url)
        meta = catalog.get(handle)
        if not meta:
            results.append(product_error(
                brand=BRAND, product_url=url, handle=handle,
                error_type="HTTP_404", message="Product JSON not found",
            ))
            continue
        title, rows = chart_by_tags.get(meta.get("tags") or "", ("", []))
        if not rows:
            results.append(product_error(
                brand=BRAND, product_url=url, handle=handle,
                error_type="NO_KIWI_CHART", message="KiwiSizing returned no table",
            ))
            continue
        product_name = meta.get("title") or handle
        category = infer_category(
            product_name, handle, url, meta.get("product_type") or "", title, by_url.get(url, "")
        )
        fit = infer_fit(title, product_name, handle, default="regular")
        results.append(product_success(
            source="live",
            brand=BRAND,
            category=category,
            fit=fit,
            product_url=url,
            handle=handle,
            chart_title=title,
            product_name=product_name,
            product_id=meta.get("id") or "",
            size_chart=rows,
        ))

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, ensure_ascii=False)
    ok = sum(1 for r in results if r.get("status") == "success")
    logger.info("Saved %s Bear House charts (%s success, %s tag groups) → %s",
                len(results), ok, len(groups), OUTPUT_FILE)


if __name__ == "__main__":
    main()
