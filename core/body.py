"""DEPRECATED: Body derivation module.

Do NOT invent synthetic body measurements in the scraper database.
Brands provide garment measurements, not body measurements.
"""

from __future__ import annotations

import warnings
from typing import Optional

from core.schema import EASE_BY_FIT, ProductChart


def _ease(fit: Optional[str]) -> float:
    return EASE_BY_FIT.get(fit or "regular", EASE_BY_FIT["regular"])


def attach_body(record: ProductChart) -> ProductChart:
    """Deprecated: No-op. Returns record unmodified to avoid synthetic body fabrication."""
    warnings.warn(
        "attach_body is deprecated: fabricating body metrics is disallowed.",
        DeprecationWarning,
        stacklevel=2,
    )
    return record
