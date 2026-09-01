"""Normalize raw extractor rows into the Snug product-level schema."""

from __future__ import annotations

import re
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from core.schema import (
    INCH_CHEST_MAX,
    OVERCONVERTED_CHEST,
    SIZE_ALIASES,
    SIZE_ORDER,
    SIZE_RANK,
    ProductChart,
    VALID_SOURCES,
)


_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Raw header / key -> canonical garment_* or body_* field.
_KEY_MAP = {
    "chest": "garment_chest_cm",
    "chest_cm": "garment_chest_cm",
    "garment_chest": "garment_chest_cm",
    "garment_chest_cm": "garment_chest_cm",
    "shirt_size": "garment_chest_cm",
    "shirt_size_cm": "garment_chest_cm",
    "to_fit_chest": "body_chest_cm",
    "to_fit_chest_cm": "body_chest_cm",
    "body_chest": "body_chest_cm",
    "body_chest_cm": "body_chest_cm",
    "shoulder": "garment_shoulder_cm",
    "shoulder_cm": "garment_shoulder_cm",
    "across_shoulder": "garment_shoulder_cm",
    "across_shoulder_cm": "garment_shoulder_cm",
    "garment_shoulder": "garment_shoulder_cm",
    "garment_shoulder_cm": "garment_shoulder_cm",
    "garment_shoulder_length": "garment_shoulder_cm",
    "garment_shoulder_length_cm": "garment_shoulder_cm",
    "length": "garment_length_cm",
    "length_cm": "garment_length_cm",
    "front_length": "garment_length_cm",
    "front_length_cm": "garment_length_cm",
    "full_length": "garment_length_cm",
    "full_length_cm": "garment_length_cm",
    "garment_length": "garment_length_cm",
    "garment_length_cm": "garment_length_cm",
    "sleeve": "garment_sleeve_cm",
    "sleeve_cm": "garment_sleeve_cm",
    "sleeve_length": "garment_sleeve_cm",
    "sleeve_length_cm": "garment_sleeve_cm",
    "garment_sleeve": "garment_sleeve_cm",
    "garment_sleeve_cm": "garment_sleeve_cm",
}


def to_cm(inches: float) -> float:
    return round(float(inches) * 2.54, 1)


def normalize_size_label(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    label = str(raw).strip().upper().replace(" ", "")
    if not label or label in {"UNKNOWN", "SIZE", "BRANDSIZE"}:
        return None
    if label in SIZE_ALIASES:
        return SIZE_ALIASES[label]
    if label in SIZE_RANK:
        return label
    return label


def normalize_measurement_key(raw_key: str) -> str:
    key = (raw_key or "").lower().strip()
    key = re.sub(r"\s*\((in inch|inch|in|cm)\)", "", key)
    key = key.replace("front length", "length")
    slug = _NON_ALNUM.sub("_", key).strip("_")
    if slug in _KEY_MAP:
        return _KEY_MAP[slug]
    if slug.endswith("_cm") and slug[:-3] in _KEY_MAP:
        return _KEY_MAP[slug[:-3]]
    mapped = _KEY_MAP.get(slug)
    if mapped:
        return mapped
    if not slug.endswith("_cm"):
        slug = f"{slug}_cm"
    return _KEY_MAP.get(slug, slug)


def infer_category(*texts: str) -> str:
    blob = " ".join(t or "" for t in texts).lower()
    if "polo" in blob:
        return "polo"
    return "t-shirt"


def infer_fit(*texts: str, default: Optional[str] = None) -> Optional[str]:
    blob = " ".join(t or "" for t in texts).lower().replace("-", " ")
    blob = re.sub(r"\s+", " ", blob)
    if "oversize" in blob or "over size" in blob:
        return "oversized"
    if re.search(r"\bbox\b", blob):
        return "box"
    if "slim" in blob:
        return "slim"
    if "relax" in blob:
        return "relaxed"
    if any(tok in blob for tok in ("regular", "easy fit", "moderno", "classic")):
        return "regular"
    return default


def infer_sleeve(*texts: str, sleeve_cm: Optional[float] = None) -> Optional[str]:
    blob = " ".join(t or "" for t in texts).lower()
    if "sleeveless" in blob or "tank" in blob:
        return "sleeveless"
    if "full sleeve" in blob or "long sleeve" in blob:
        return "full"
    if "elbow" in blob:
        return "elbow"
    if "half sleeve" in blob or "short sleeve" in blob:
        return "half"
    if sleeve_cm is not None:
        if sleeve_cm > 50:
            return "full"
        if sleeve_cm > 0:
            return "half"
    return None


def _convert_row_units(row: Dict[str, Any], *, treat_as_inches: bool) -> Dict[str, Any]:
    out = dict(row)
    for key, val in list(out.items()):
        if key == "size" or not isinstance(val, (int, float)):
            continue
        if treat_as_inches:
            out[key] = to_cm(float(val))
    return out


def normalize_chart_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mapped: List[Dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        size = normalize_size_label(raw.get("size"))
        if not size:
            continue
        item: Dict[str, Any] = {"size": size}
        for key, val in raw.items():
            if key == "size" or val is None:
                continue
            try:
                num = float(val)
            except (TypeError, ValueError):
                continue
            item[normalize_measurement_key(str(key))] = round(num, 1)
        if len(item) > 1:
            mapped.append(item)

    chests = [r["garment_chest_cm"] for r in mapped if "garment_chest_cm" in r]
    treat_as_inches = bool(chests) and median(chests) < INCH_CHEST_MAX
    if treat_as_inches:
        mapped = [_convert_row_units(r, treat_as_inches=True) for r in mapped]

    # Drop impossible leftover conversions rather than silently keeping them.
    cleaned: List[Dict[str, Any]] = []
    for row in mapped:
        chest = row.get("garment_chest_cm")
        if chest is not None and chest > OVERCONVERTED_CHEST:
            continue
        cleaned.append(row)

    seen = set()
    deduped: List[Dict[str, Any]] = []
    for row in cleaned:
        if row["size"] in seen:
            continue
        seen.add(row["size"])
        deduped.append(row)

    deduped.sort(key=lambda r: SIZE_RANK.get(r["size"], 99))
    return deduped


def _infer_legacy_source(brand: str, raw: Dict[str, Any]) -> Optional[str]:
    """Map old extractor files that have no `source` field.

    Being Human templates and Bear House silent fallbacks are rejected.
    """
    if brand == "Snitch":
        return "brand_api"
    if brand in {"Bewakoof", "Nobero"}:
        return "live"
    if brand == "The Bear House":
        return "live" if raw.get("chart_title") else None
    return None


def parse_product(raw: Any, brand: str) -> Optional[ProductChart]:
    """Accept current extractor JSON (old or new) and return a product record."""
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("product_url") or raw.get("url") or "")
    handle = str(raw.get("handle") or "")
    if raw.get("status") and raw.get("status") != "success":
        return {
            "status": "error",
            "brand": brand,
            "product_url": url,
            "handle": handle,
            "region": "IN",
            "error_type": str(raw.get("error_type") or "EXTRACT_ERROR"),
            "message": str(raw.get("message") or raw.get("status")),
            "size_chart": [],
        }

    chart = normalize_chart_rows(raw.get("size_chart") or [])
    if not chart:
        return {
            "status": "error",
            "brand": brand,
            "product_url": url,
            "handle": handle,
            "region": "IN",
            "error_type": str(raw.get("error_type") or "NO_SIZE_CHART"),
            "message": str(raw.get("message") or "No size measurements"),
            "size_chart": [],
        }

    title = str(raw.get("product_name") or raw.get("chart_title") or "")
    source = raw.get("source")
    if source not in VALID_SOURCES:
        source = _infer_legacy_source(brand, raw)
        if source is None:
            return {
                "status": "error",
                "brand": brand,
                "product_url": url,
                "handle": handle,
                "region": "IN",
                "error_type": "MISSING_SOURCE",
                "message": "Refusing unlabeled/hardcoded size chart",
                "size_chart": [],
            }

    category = raw.get("category") if raw.get("category") in {"t-shirt", "polo"} else None
    if not category:
        category = infer_category(
            title,
            handle,
            url,
            str(raw.get("product_type") or ""),
            str(raw.get("chart_title") or ""),
        )

    fit = raw.get("fit")
    if isinstance(fit, str):
        fit = infer_fit(fit, default=fit.lower().replace(" fit", "").strip())
    if fit not in {
        "slim",
        "regular",
        "oversized",
        "relaxed",
        "box",
        "slim_plus",
        "regular_plus",
        "oversized_plus",
    }:
        fit = infer_fit(str(raw.get("chart_title") or ""), title, handle, url, default="regular")

    sleeve_vals = [r.get("garment_sleeve_cm") for r in chart if r.get("garment_sleeve_cm") is not None]
    sleeve = raw.get("sleeve")
    if sleeve not in {"half", "full", "sleeveless", "elbow"}:
        sleeve = infer_sleeve(
            title,
            handle,
            url,
            str(raw.get("chart_title") or ""),
            sleeve_cm=sleeve_vals[0] if sleeve_vals else None,
        )

    record: ProductChart = {
        "status": "success",
        "source": source or "live",
        "brand": brand,
        "category": category,
        "fit": fit or "regular",
        "sleeve": sleeve,
        "region": str(raw.get("region") or "IN"),
        "product_url": url,
        "handle": handle,
        "chart_title": str(raw.get("chart_title") or ""),
        "product_name": title,
        "product_id": str(raw.get("product_id") or ""),
        "size_chart": chart,  # type: ignore[typeddict-item]
        "unit": "cm",
    }
    return record
