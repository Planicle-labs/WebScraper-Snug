"""Build Snug brand_size_db JSON from the five brand size-chart files."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import List

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.collapse import collapse_products
from core.parser import parse_product
from core.schema import ProductChart
from core.validator import validate_product

BRAND_FILES = [
    ("Being Human", os.path.join(_ROOT, "Companies", "Being Human", "outputs", "beinghuman_size_chart.json")),
    ("Bewakoof", os.path.join(_ROOT, "Companies", "Bewakoof", "outputs", "bewakoof_size_chart.json")),
    ("Nobero", os.path.join(_ROOT, "Companies", "Nobero", "outputs", "nobero_size_chart.json")),
    ("Snitch", os.path.join(_ROOT, "Companies", "Snitch", "outputs", "snitch_size_chart.json")),
    ("The Bear House", os.path.join(_ROOT, "Companies", "The Bear House", "outputs", "bearhouse_size_chart.json")),
]

OUT_DIR = os.path.join(_ROOT, "outputs", "snug")


def _load(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} is not a JSON list")
    return data


def process_brand(brand: str, path: str) -> List[ProductChart]:
    raw_rows = _load(path)
    out: List[ProductChart] = []
    for raw in raw_rows:
        parsed = parse_product(raw, brand)
        if parsed is None:
            out.append(
                {
                    "status": "error",
                    "brand": brand,
                    "product_url": "",
                    "region": "IN",
                    "error_type": "NULL_RECORD",
                    "message": "null extractor row",
                    "size_chart": [],
                }
            )
            continue
        if parsed.get("status") == "success":
            verdict, confidence, reasons = validate_product(parsed)
            parsed["confidence"] = confidence  # type: ignore[typeddict-unknown-key]
            parsed["verdict"] = verdict  # type: ignore[typeddict-unknown-key]
            parsed["validation_reasons"] = reasons  # type: ignore[typeddict-unknown-key]
            if verdict == "FAIL":
                parsed["status"] = "error"
                parsed["error_type"] = "VALIDATION_FAIL"
                parsed["message"] = "; ".join(reasons) or "validation fail"
        out.append(parsed)
    return out


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    products: List[ProductChart] = []
    for brand, path in BRAND_FILES:
        if not os.path.exists(path):
            print(f"SKIP missing {brand}: {path}")
            continue
        brand_rows = process_brand(brand, path)
        products.extend(brand_rows)
        ok = sum(1 for r in brand_rows if r.get("status") == "success")
        err = len(brand_rows) - ok
        src = Counter(r.get("source") for r in brand_rows if r.get("status") == "success")
        print(f"{brand}: {ok} ok / {err} err / sources={dict(src)}")

    snug, flagged = collapse_products(products)

    clean_size_path = os.path.join(OUT_DIR, "clean_size_db.json")
    brand_size_path = os.path.join(OUT_DIR, "brand_size_db.json")
    product_path = os.path.join(OUT_DIR, "product_charts.json")
    flag_path = os.path.join(OUT_DIR, "flagged_entries.json")

    verified_products = [p for p in products if p.get("status") == "success"]

    with open(clean_size_path, "w", encoding="utf-8") as f:
        json.dump(snug, f, indent=2, ensure_ascii=False)
    with open(brand_size_path, "w", encoding="utf-8") as f:
        json.dump(verified_products, f, indent=2, ensure_ascii=False)
    with open(product_path, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    with open(flag_path, "w", encoding="utf-8") as f:
        json.dump(flagged, f, indent=2, ensure_ascii=False)

    print(
        f"Wrote {len(products)} total product rows, {len(verified_products)} verified brand rows (brand_size_db.json),\n"
        f"{len(snug)} canonical rows (clean_size_db.json), and {len(flagged)} flagged entries → {OUT_DIR}"
    )
    by_key = Counter((r["brand_name"], r["category"], r["fit"]) for r in snug)
    for key, n in sorted(by_key.items()):
        print(f"  {key[0]} | {key[1]} | {key[2]} → {n} sizes")


if __name__ == "__main__":
    main()
