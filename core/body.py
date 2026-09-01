"""Derive body chest min/max from published to-fit or garment + ease."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.schema import EASE_BY_FIT, ProductChart


def _ease(fit: Optional[str]) -> float:
    return EASE_BY_FIT.get(fit or "regular", EASE_BY_FIT["regular"])


def attach_body(record: ProductChart) -> ProductChart:
    """Add body_chest_min/max and body_shoulder onto each size row (in place)."""
    if record.get("status") != "success":
        return record
    chart: List[Dict[str, Any]] = list(record.get("size_chart") or [])
    if not chart:
        return record

    ease = _ease(record.get("fit"))
    mids: List[Optional[float]] = []
    for row in chart:
        published = row.get("body_chest_cm")
        garment = row.get("garment_chest_cm")
        if isinstance(published, (int, float)):
            mids.append(float(published))
        elif isinstance(garment, (int, float)):
            mids.append(round(float(garment) - ease, 1))
        else:
            mids.append(None)

    for i, row in enumerate(chart):
        mid = mids[i]
        if mid is None:
            continue
        prev_mid = next((mids[j] for j in range(i - 1, -1, -1) if mids[j] is not None), None)
        next_mid = next((mids[j] for j in range(i + 1, len(mids)) if mids[j] is not None), None)
        if prev_mid is not None:
            body_min = round((prev_mid + mid) / 2.0, 1)
        else:
            step = (next_mid - mid) if next_mid is not None else 5.0
            body_min = round(mid - abs(step) / 2.0, 1)
        if next_mid is not None:
            body_max = round((mid + next_mid) / 2.0, 1)
        else:
            step = (mid - prev_mid) if prev_mid is not None else 5.0
            body_max = round(mid + abs(step) / 2.0, 1)
        if body_min > body_max:
            body_min, body_max = body_max, body_min
        row["body_chest_min"] = body_min
        row["body_chest_max"] = body_max
        shoulder = row.get("garment_shoulder_cm")
        if isinstance(shoulder, (int, float)):
            row["body_shoulder"] = round(float(shoulder), 1)

    record["size_chart"] = chart  # type: ignore[typeddict-item]
    return record
