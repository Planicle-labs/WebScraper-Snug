"""Rules-based validation and confidence scoring for Snug rows."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from core.schema import (
    CHEST_MAX_CM,
    CHEST_MIN_CM,
    SHOULDER_MAX_CM,
    SHOULDER_MIN_CM,
    SIZE_RANK,
    VALID_SOURCES,
    ProductChart,
)


def _monotonic_chests(chart: List[Dict[str, Any]]) -> bool:
    last = None
    for row in chart:
        chest = row.get("garment_chest_cm")
        if not isinstance(chest, (int, float)):
            continue
        if last is not None and chest + 0.15 < last:
            return False
        last = chest
    return True


def _max_step(chart: List[Dict[str, Any]]) -> float:
    chests = [r["garment_chest_cm"] for r in chart if isinstance(r.get("garment_chest_cm"), (int, float))]
    if len(chests) < 2:
        return 0.0
    return max(chests[i + 1] - chests[i] for i in range(len(chests) - 1))


def validate_product(record: ProductChart) -> Tuple[str, str, List[str]]:
    """Return (verdict, confidence, reasons). FAIL never belongs in brand_size_db."""
    reasons: List[str] = []
    if record.get("status") != "success":
        return "FAIL", "low", [record.get("message") or "extract error"]

    source = record.get("source")
    if source not in VALID_SOURCES:
        return "FAIL", "low", [f"invalid source {source!r}"]

    chart = list(record.get("size_chart") or [])
    if not chart:
        return "FAIL", "low", ["empty size chart"]

    has_chest = 0
    for row in chart:
        size = row.get("size")
        if size not in SIZE_RANK:
            reasons.append(f"non-canonical size {size}")
        chest = row.get("garment_chest_cm")
        if not isinstance(chest, (int, float)):
            reasons.append(f"{size} missing garment_chest_cm")
            continue
        has_chest += 1
        if chest < CHEST_MIN_CM or chest > CHEST_MAX_CM:
            reasons.append(f"{size} chest {chest} outside {CHEST_MIN_CM}-{CHEST_MAX_CM}")
        shoulder = row.get("garment_shoulder_cm")
        if isinstance(shoulder, (int, float)):
            if shoulder < SHOULDER_MIN_CM or shoulder > SHOULDER_MAX_CM:
                reasons.append(f"{size} shoulder {shoulder} outside bounds")
        if any(row.get(k) for k in ("body_hip_min", "body_hip_max", "body_inseam", "garment_inseam_cm", "inseam", "inseam_cm", "hip", "hip_cm")):
            reasons.append("bottom-wear fields on a top")

    if has_chest == 0:
        return "FAIL", "low", reasons + ["no garment chest"]

    if not _monotonic_chests(chart):
        reasons.append("chest not monotonic with size")
        return "FAIL", "low", reasons

    step = _max_step(chart)
    if step > 10:
        reasons.append(f"chest jump {step:.1f}cm between adjacent sizes")

    fail_markers = [r for r in reasons if "outside" in r or "bottom-wear" in r]
    if fail_markers:
        return "FAIL", "low", reasons

    clustered = source == "clustered"
    if clustered:
        if reasons:
            confidence = "low"
            verdict = "WARN"
        else:
            confidence = "medium"
            verdict = "PASS"
    else:
        # Authentic brand data (live, brand_api, official_guide).
        # Garment measurements are authentic; missing shoulder width or body metrics
        # are normal and not penalized.
        if reasons:
            confidence = "medium"
            verdict = "WARN"
        else:
            confidence = "high"
            verdict = "PASS"

    return verdict, confidence, reasons
