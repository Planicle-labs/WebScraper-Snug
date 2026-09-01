"""Extract Being Human size charts from KiwiSizing.

Hits Shopify products.json + one PDP per unique tag set so we do not
request 4,880 HTML pages (that 429s the store).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
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
logger = logging.getLogger("BeingHumanSizeChartExtractor")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(_THIS_DIR, "outputs", "beinghuman_men_tshirt.json")
OUTPUT_FILE = os.path.join(_THIS_DIR, "outputs", "beinghuman_size_chart.json")
SITE = "https://www.beinghumanclothing.com"
SHOP = "beinghuman-clothing.myshopify.com"
BRAND = "Being Human"
COLLECTIONS = [
    "men-t-shirt",
    "men-polo-t-shirt",
    "men-topwear",
    "t-shirt",
    "tops-t-shirts",
]
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
            if exc.code in {429, 500, 502, 503} and attempt < 4:
                time.sleep(3.0 * (attempt + 1))
                continue
            raise
        except Exception as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise last_exc or RuntimeError(url)


def normalize_url(url: str) -> str:
    return url.replace("beinghumanclothing.com//", "beinghumanclothing.com/")


def handle_from_url(url: str) -> str:
    return url.split("/products/")[-1].split("?")[0].strip("/")


def load_collection_catalog() -> Dict[str, Dict[str, Any]]:
    catalog: Dict[str, Dict[str, Any]] = {}
    for col in COLLECTIONS:
        page = 1
        while page <= 50:
            url = f"{SITE}/collections/{col}/products.json?limit=250&page={page}"
            try:
                raw = _get(url)
                data = json.loads(raw)
            except Exception as e:
                logger.warning("collection %s page %s failed: %s", col, page, e)
                break
            products = data.get("products") or []
            if not products:
                break
            for p in products:
                handle = p.get("handle") or ""
                if not handle or handle in catalog:
                    continue
                tags = p.get("tags") or ""
                if isinstance(tags, list):
                    tags = ", ".join(tags)
                catalog[handle] = {
                    "id": str(p.get("id") or ""),
                    "title": p.get("title") or "",
                    "product_type": p.get("product_type") or "",
                    "tags": tags,
                }
            logger.info("collection %s page %s → %s products (total catalog %s)", col, page, len(products), len(catalog))
            if len(products) < 250:
                break
            page += 1
            time.sleep(0.3)
    return catalog


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
    if not os.path.exists(PRODUCTS_FILE):
        logger.error("Products file not found: %s", PRODUCTS_FILE)
        return
    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        urls = [normalize_url(u) for u in json.load(f)]
    urls = list(dict.fromkeys(urls))
    logger.info("Being Human: %s product URLs", len(urls))

    catalog = load_collection_catalog()
    missing = [u for u in urls if handle_from_url(u) not in catalog]
    logger.info(
        "catalog hits %s, missing %s (stale/unpublished URLs skipped, no per-SKU fetch)",
        len(urls) - len(missing),
        len(missing),
    )

    groups: Dict[str, List[str]] = defaultdict(list)
    no_meta: List[str] = []
    for url in urls:
        handle = handle_from_url(url)
        meta = catalog.get(handle)
        if not meta:
            no_meta.append(url)
            continue
        groups[meta.get("tags") or ""].append(url)
    logger.info("%s tag groups, %s with no metadata", len(groups), len(no_meta))

    chart_by_tags: Dict[str, Tuple[str, List[Dict[str, Any]]]] = {}

    def _fetch_one_group(item: Tuple[str, List[str]]) -> Tuple[str, str, List[Dict[str, Any]]]:
        tags_key, group_urls = item
        sample = group_urls[0]
        handle = handle_from_url(sample)
        meta_item = catalog.get(handle) or {}
        try:
            title_res, rows_res = chart_for_group(sample, meta_item)
        except Exception as exc:
            title_res, rows_res = "", []
        return tags_key, title_res, rows_res

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_fetch_one_group, item) for item in groups.items()]
        done_cnt = 0
        for f in as_completed(futures):
            t_key, t_title, t_rows = f.result()
            chart_by_tags[t_key] = (t_title, t_rows)
            done_cnt += 1
            if done_cnt % 25 == 0 or done_cnt == len(groups):
                logger.info("Fetched Kiwi charts: %s/%s tag groups", done_cnt, len(groups))

    results: List[Dict[str, Any]] = []
    for url in urls:
        handle = handle_from_url(url)
        meta = catalog.get(handle)
        if not meta:
            results.append(product_error(
                brand=BRAND, product_url=url, handle=handle,
                error_type="PRODUCT_GONE",
                message="Handle not in live men-t-shirt collection",
            ))
            continue
        title, rows = chart_by_tags.get(meta.get("tags") or "", ("", []))
        if not rows:
            results.append(product_error(
                brand=BRAND, product_url=url, handle=handle,
                error_type="NO_KIWI_CHART", message="KiwiSizing returned no table for this tag set",
            ))
            continue
        product_name = meta.get("title") or handle
        category = infer_category(product_name, handle, url, meta.get("product_type") or "", title)
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
    logger.info("Saved %s Being Human charts (%s success, %s tag groups) → %s",
                len(results), ok, len(groups), OUTPUT_FILE)


if __name__ == "__main__":
    main()
