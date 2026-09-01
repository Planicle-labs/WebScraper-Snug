"""Canonical records for Snug size data.

Product-level JSON is provenance. brand_size_db rows are what the
recommender ingests. Unique key: (brand_name, category, fit, size_label, region).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


VALID_SOURCES = frozenset({"live", "brand_api", "official_guide", "clustered"})
VALID_CATEGORIES = frozenset({"t-shirt", "polo"})
VALID_FITS = frozenset(
    {"slim", "regular", "oversized", "relaxed", "box", "slim_plus", "regular_plus", "oversized_plus"}
)
VALID_SLEEVES = frozenset({"half", "full", "sleeveless", "elbow"})
VALID_CONFIDENCE = frozenset({"high", "medium", "low"})
VALID_VERDICTS = frozenset({"PASS", "WARN", "FAIL"})

SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL", "5XL", "6XL"]
SIZE_RANK = {label: i for i, label in enumerate(SIZE_ORDER)}

SIZE_ALIASES = {
    "XXS": "XS",
    "2XL": "XXL",
    "XXL": "XXL",
    "2X": "XXL",
    "XXXL": "3XL",
    "3XL": "3XL",
    "3X": "3XL",
    "XXXXL": "4XL",
    "4XL": "4XL",
    "4X": "4XL",
    "XXXXXL": "5XL",
    "5XL": "5XL",
    "5X": "5XL",
    "XXXXXXL": "6XL",
    "6XL": "6XL",
    "6X": "6XL",
}

# Garment minus body (cm), menswear tops.
EASE_BY_FIT = {
    "slim": 4.0,
    "regular": 8.0,
    "relaxed": 10.0,
    "box": 10.0,
    "oversized": 14.0,
    "slim_plus": 4.0,
    "regular_plus": 8.0,
    "oversized_plus": 14.0,
}

CHEST_MIN_CM = 80.0
CHEST_MAX_CM = 160.0
SHOULDER_MIN_CM = 35.0
SHOULDER_MAX_CM = 80.0
INCH_CHEST_MAX = 70.0
OVERCONVERTED_CHEST = 160.0


class SizeRow(TypedDict, total=False):
    size: str
    garment_chest_cm: float
    garment_shoulder_cm: float
    garment_length_cm: float
    garment_sleeve_cm: float
    body_chest_cm: float
    body_chest_min: float
    body_chest_max: float
    body_shoulder: float


class ProductChart(TypedDict, total=False):
    status: str
    source: str
    brand: str
    category: str
    fit: str
    sleeve: Optional[str]
    region: str
    product_url: str
    handle: str
    chart_title: str
    product_name: str
    product_id: str
    size_chart: List[SizeRow]
    error_type: str
    message: str
    unit: str


class SnugRow(TypedDict, total=False):
    brand_name: str
    category: str
    fit: str
    size_label: str
    region: str
    body_chest_min: Optional[float]
    body_chest_max: Optional[float]
    body_shoulder: Optional[float]
    body_waist_min: None
    body_waist_max: None
    body_hip_min: None
    body_hip_max: None
    body_inseam: None
    garment_chest_cm: Optional[float]
    garment_shoulder_cm: Optional[float]
    confidence: str
    last_verified: str
    product_count: int
    source: str
    chart_title: str


class FlaggedEntry(TypedDict, total=False):
    verdict: str
    reasons: List[str]
    confidence: str
    brand: str
    product_url: str
    payload: Dict[str, Any]


def product_error(
    *,
    brand: str,
    product_url: str,
    error_type: str,
    message: str,
    handle: str = "",
) -> ProductChart:
    return {
        "status": "error",
        "brand": brand,
        "product_url": product_url,
        "handle": handle,
        "region": "IN",
        "error_type": error_type,
        "message": message,
        "size_chart": [],
    }


def product_success(**kwargs: Any) -> ProductChart:
    record: ProductChart = {
        "status": "success",
        "region": "IN",
        "unit": "cm",
        "sleeve": None,
    }
    record.update(kwargs)  # type: ignore[typeddict-item]
    return record
