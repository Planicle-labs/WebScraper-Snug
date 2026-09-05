"""Collapse product-level charts into Snug brand_size_db rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.schema import SIZE_RANK, FlaggedEntry, ProductChart, SnugRow


PLUS_SIZES = {"3XL", "4XL", "5XL", "6XL"}
S_CHEST_SPLIT_CM = 2.0


def _fingerprint(chart: List[Dict[str, Any]]) -> Tuple[Tuple[str, float], ...]:
    pairs = []
    for row in chart:
        chest = row.get("garment_chest_cm")
        if not isinstance(chest, (int, float)):
            continue
        pairs.append((row["size"], round(float(chest) * 2) / 2.0))
    return tuple(pairs)


def _is_plus_only(chart: List[Dict[str, Any]]) -> bool:
    sizes = {r.get("size") for r in chart}
    return bool(sizes) and sizes <= PLUS_SIZES


def _s_chest(chart: List[Dict[str, Any]]) -> Optional[float]:
    for preferred in ("S", "XS", "M"):
        for row in chart:
            if row.get("size") == preferred and isinstance(row.get("garment_chest_cm"), (int, float)):
                return float(row["garment_chest_cm"])
    for row in chart:
        if isinstance(row.get("garment_chest_cm"), (int, float)):
            return float(row["garment_chest_cm"])
    return None


def _median_chart(members: List[ProductChart]) -> List[Dict[str, Any]]:
    by_size: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    all_fields: set[str] = set()
    for rec in members:
        for row in rec.get("size_chart") or []:
            by_size[str(row.get("size"))].append(row)
            for k, v in row.items():
                if k != "size" and isinstance(v, (int, float)):
                    all_fields.add(k)
    out: List[Dict[str, Any]] = []
    for size in sorted(by_size, key=lambda s: SIZE_RANK.get(s, 99)):
        rows = by_size[size]
        merged: Dict[str, Any] = {"size": size}
        for field in sorted(all_fields):
            vals = [r[field] for r in rows if isinstance(r.get(field), (int, float))]
            if vals:
                merged[field] = round(float(median(vals)), 1)
        out.append(merged)
    return out


def collapse_products(
    records: Iterable[ProductChart],
    *,
    now: Optional[str] = None,
) -> Tuple[List[SnugRow], List[FlaggedEntry]]:
    """Dominant cluster per (brand, category, fit) becomes snug rows.

    A second cluster whose S-chest differs by > 2cm is kept as `{fit}_plus`
    only when it is plus-size-only; otherwise it is flagged.
    """
    verified = now or datetime.now(timezone.utc).date().isoformat()
    groups: Dict[Tuple[str, str, str], Dict[Tuple, List[ProductChart]]] = defaultdict(
        lambda: defaultdict(list)
    )
    flagged: List[FlaggedEntry] = []

    for rec in records:
        if rec.get("status") != "success":
            flagged.append(
                {
                    "verdict": "FAIL",
                    "reasons": [rec.get("message") or "extract error"],
                    "confidence": "low",
                    "brand": rec.get("brand") or "",
                    "product_url": rec.get("product_url") or "",
                    "payload": dict(rec),
                }
            )
            continue
        brand = rec.get("brand") or ""
        category = rec.get("category") or "t-shirt"
        fit = rec.get("fit") or "regular"
        fp = _fingerprint(list(rec.get("size_chart") or []))
        groups[(brand, category, fit)][fp].append(rec)

    snug: List[SnugRow] = []

    for (brand, category, fit), clusters in groups.items():
        ranked = sorted(clusters.items(), key=lambda kv: -len(kv[1]))
        primary_fp, primary_members = ranked[0]
        primary_s = _s_chest(list(primary_members[0].get("size_chart") or []))

        accepted: List[Tuple[str, List[ProductChart]]] = [(fit, primary_members)]
        plus_members: List[ProductChart] = []
        for fp, members in ranked[1:]:
            sample = list(members[0].get("size_chart") or [])
            other_s = _s_chest(sample)
            delta = abs((other_s or 0) - (primary_s or 0))
            if _is_plus_only(sample):
                plus_members.extend(members)
            elif other_s is not None and primary_s is not None and delta > S_CHEST_SPLIT_CM:
                flagged.append(
                    {
                        "verdict": "WARN",
                        "reasons": [
                            f"secondary cluster S-chest delta {delta:.1f}cm vs dominant {fit}"
                        ],
                        "confidence": "medium",
                        "brand": brand,
                        "product_url": members[0].get("product_url") or "",
                        "payload": {
                            "category": category,
                            "fit": fit,
                            "product_count": len(members),
                            "s_chest": other_s,
                        },
                    }
                )
            else:
                accepted[0] = (fit, accepted[0][1] + members)

        if plus_members:
            accepted.append((f"{fit}_plus", plus_members))

        for fit_name, members in accepted:
            chart = _median_chart(members)
            source_counts: Dict[str, int] = defaultdict(int)
            titles: Dict[str, int] = defaultdict(int)
            conf_rank = {"high": 3, "medium": 2, "low": 1}
            worst_conf = "high"
            for rec in members:
                source_counts[str(rec.get("source") or "live")] += 1
                if rec.get("chart_title"):
                    titles[str(rec["chart_title"])] += 1
                c = rec.get("confidence") or "medium"
                if conf_rank.get(c, 0) < conf_rank.get(worst_conf, 3):
                    worst_conf = c
            source = max(source_counts, key=source_counts.get)
            title = max(titles, key=titles.get) if titles else ""
            for row in chart:
                snug_row: Dict[str, Any] = {
                    "brand_name": brand,
                    "category": category,
                    "fit": fit_name,
                    "size_label": row["size"],
                    "region": "IN",
                    "garment_chest_cm": row.get("garment_chest_cm"),
                    "garment_shoulder_cm": row.get("garment_shoulder_cm"),
                    "garment_length_cm": row.get("garment_length_cm"),
                    "garment_sleeve_cm": row.get("garment_sleeve_cm"),
                }
                to_fit = row.get("to_fit_chest_cm") if row.get("to_fit_chest_cm") is not None else row.get("body_chest_cm")
                if to_fit is not None:
                    snug_row["to_fit_chest_cm"] = to_fit

                snug_row.update(
                    {
                        "confidence": worst_conf,
                        "last_verified": verified,
                        "product_count": len(members),
                        "source": source,
                        "chart_title": title,
                    }
                )
                # Attach any other authentic brand-specific measurements (e.g. neck, bicep, etc.)
                # Never insert or re-attach fabricated body_* metrics
                for k, v in row.items():
                    if k not in snug_row and k != "size":
                        if k.startswith("body_"):
                            continue
                        snug_row[k] = v
                snug.append(snug_row)

    snug.sort(
        key=lambda r: (
            r.get("brand_name") or "",
            r.get("category") or "",
            r.get("fit") or "",
            SIZE_RANK.get(r.get("size_label") or "", 99),
        )
    )
    return snug, flagged
