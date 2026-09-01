"""Shared KiwiSizing client (Being Human, The Bear House)."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.parser import normalize_chart_rows, normalize_measurement_key, to_cm


KIWI_API = "https://app.kiwisizing.com/api/getSizingChart"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_DATA_RE = re.compile(r"KiwiSizing\.data\s*=\s*\{([\s\S]*?)\};")


def parse_bootstrap(html: str) -> Optional[Dict[str, str]]:
    """Extract shop/product/tags/collections/title/type from PDP HTML."""
    if not html or "KiwiSizing" not in html:
        return None
    m = _DATA_RE.search(html)
    if not m:
        return None
    blob = m.group(1)

    def _field(name: str) -> str:
        mm = re.search(rf'{name}:\s*"([^"]*)"', blob)
        if mm:
            return mm.group(1).encode("utf-8").decode("unicode_escape")
        mm = re.search(rf'{name}:\s*["\']?([^"\',\s]+)', blob)
        return mm.group(1) if mm else ""

    shop_m = re.search(r'KiwiSizing\.shop\s*=\s*"([^"]+)"', html)
    return {
        "shop": shop_m.group(1) if shop_m else "",
        "product": _field("product"),
        "tags": _field("tags"),
        "collections": _field("collections"),
        "title": _field("title"),
        "type": _field("type"),
        "vendor": _field("vendor"),
    }


def fetch_sizing_chart(
    *,
    shop: str,
    product: str,
    tags: str = "",
    collections: str = "",
    timeout: int = 12,
    referer: str = "",
) -> Optional[Dict[str, Any]]:
    """Return the first matching Kiwi sizing object, or None."""
    if not shop or not product:
        return None
    params = urlencode(
        {
            "shop": shop,
            "product": product,
            "tags": tags,
            "collections": collections,
        }
    )
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = referer.rstrip("/")
    req = Request(
        f"{KIWI_API}?{params}",
        headers=headers,
    )
    data = None
    for attempt in range(4):
        try:
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            break
        except HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(2.0 * (attempt + 1))
                continue
            return None
        except Exception:
            if attempt < 3:
                time.sleep(1.0)
                continue
            return None
    if not data:
        return None
    sizings = data.get("sizings") or []
    if not sizings:
        return None
    return sizings[0]


def parse_kiwi_table(data_matrix: list) -> List[Dict[str, Any]]:
    """Convert a Kiwi table matrix into garment_* cm rows."""
    if not data_matrix or len(data_matrix) < 2:
        return []

    headers = [str(col.get("value", "")).strip() for col in data_matrix[0]]
    size_idx = 0
    for i, h in enumerate(headers):
        if h.lower() in {"brand size", "size", "tag size"}:
            size_idx = i
            break

    parsed: List[Dict[str, Any]] = []
    for row in data_matrix[1:]:
        if len(row) <= size_idx:
            continue
        size_val = str(row[size_idx].get("value", "")).strip()
        if not size_val:
            continue
        out: Dict[str, Any] = {"size": size_val}
        for i, col in enumerate(row):
            if i == size_idx:
                continue
            raw_h = headers[i] if i < len(headers) else ""
            key = normalize_measurement_key(raw_h)
            val_str = str(col.get("value", "")).strip()
            unit = str(col.get("unitType", "in") or "in").lower()
            try:
                val = float(val_str)
            except (TypeError, ValueError):
                continue
            header_has_cm = "(cm)" in raw_h.lower() or unit == "cm"
            header_has_in = unit in {"in", "inch"} or "(in" in raw_h.lower()
            if header_has_cm:
                out[key] = round(val, 1)
            elif header_has_in:
                out[key] = to_cm(val)
            else:
                # Kiwi default for these brands is inches.
                out[key] = to_cm(val) if val < 70 else round(val, 1)
        if len(out) > 1:
            parsed.append(out)
    return normalize_chart_rows(parsed)


def chart_from_sizing(sizing: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    title = str(sizing.get("name") or "Size Chart")
    tables = sizing.get("tables") or {}
    for table in tables.values():
        if not isinstance(table, dict):
            continue
        rows = parse_kiwi_table(table.get("data") or [])
        if rows:
            return title, rows
    return title, []
