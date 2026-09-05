"""Neon PostgreSQL database interface for Snug size database.

Handles connection pooling, table schema initialization, and high-throughput
upserts into the dedicated `clean_size_db` table.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from dotenv import load_dotenv

# Ensure .env is loaded from project root if not already set
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

TABLE_NAME = "clean_size_db"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_name TEXT NOT NULL,
    category TEXT NOT NULL,
    fit TEXT NOT NULL,
    size_label TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'IN',
    garment_chest_cm NUMERIC NOT NULL,
    garment_shoulder_cm NUMERIC,
    garment_length_cm NUMERIC,
    garment_sleeve_cm NUMERIC,
    to_fit_chest_cm NUMERIC,
    confidence TEXT NOT NULL,
    last_verified DATE NOT NULL,
    product_count INTEGER NOT NULL,
    source TEXT NOT NULL,
    chart_title TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    CONSTRAINT {TABLE_NAME}_unique_curve UNIQUE (brand_name, category, fit, size_label, region)
);

CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_lookup
    ON {TABLE_NAME} (brand_name, category, fit, region);

CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_confidence
    ON {TABLE_NAME} (confidence);
"""

UPSERT_ROW_SQL = f"""
INSERT INTO {TABLE_NAME} (
    brand_name,
    category,
    fit,
    size_label,
    region,
    garment_chest_cm,
    garment_shoulder_cm,
    garment_length_cm,
    garment_sleeve_cm,
    to_fit_chest_cm,
    confidence,
    last_verified,
    product_count,
    source,
    chart_title
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15
)
ON CONFLICT (brand_name, category, fit, size_label, region)
DO UPDATE SET
    garment_chest_cm = EXCLUDED.garment_chest_cm,
    garment_shoulder_cm = EXCLUDED.garment_shoulder_cm,
    garment_length_cm = EXCLUDED.garment_length_cm,
    garment_sleeve_cm = EXCLUDED.garment_sleeve_cm,
    to_fit_chest_cm = EXCLUDED.to_fit_chest_cm,
    confidence = EXCLUDED.confidence,
    last_verified = EXCLUDED.last_verified,
    product_count = EXCLUDED.product_count,
    source = EXCLUDED.source,
    chart_title = EXCLUDED.chart_title,
    updated_at = NOW();
"""


def get_db_url() -> str:
    """Retrieve the NEON_DB connection URI from environment."""
    url = os.getenv("NEON_DB")
    if not url:
        raise ValueError(
            "NEON_DB connection string not found. Please verify .env exists in project root."
        )
    return url


async def get_connection(statement_cache_size: int = 0) -> asyncpg.Connection:
    """Create a direct asyncpg connection to Neon.

    Note: statement_cache_size is set to 0 by default to ensure 100% compatibility
    with Neon connection poolers (PgBouncer transaction mode).
    """
    db_url = get_db_url()
    return await asyncpg.connect(
        db_url,
        ssl="require",
        statement_cache_size=statement_cache_size,
    )


async def init_table(conn: asyncpg.Connection) -> None:
    """Ensure clean_size_db table and indexes exist in Neon."""
    await conn.execute(CREATE_TABLE_SQL)


def _normalize_date(val: Any) -> date:
    """Normalize string or date objects into a Python date."""
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        return date.fromisoformat(val[:10])
    return date.today()


def _row_to_tuple(record: Dict[str, Any]) -> Tuple[Any, ...]:
    """Convert a clean_size_db JSON dictionary to a parameter tuple."""
    return (
        record["brand_name"],
        record["category"],
        record["fit"],
        record["size_label"],
        record.get("region", "IN"),
        float(record["garment_chest_cm"]),
        float(record["garment_shoulder_cm"]) if record.get("garment_shoulder_cm") is not None else None,
        float(record["garment_length_cm"]) if record.get("garment_length_cm") is not None else None,
        float(record["garment_sleeve_cm"]) if record.get("garment_sleeve_cm") is not None else None,
        float(record["to_fit_chest_cm"]) if record.get("to_fit_chest_cm") is not None else None,
        record.get("confidence", "high"),
        _normalize_date(record.get("last_verified", date.today())),
        int(record.get("product_count", 1)),
        record.get("source", "live"),
        record.get("chart_title"),
    )


async def upsert_clean_size_rows(
    conn: asyncpg.Connection, records: List[Dict[str, Any]]
) -> int:
    """Upsert a list of clean_size_db records in a single transaction.

    Returns the count of records processed.
    """
    if not records:
        return 0

    tuples = [_row_to_tuple(r) for r in records]

    async with conn.transaction():
        await conn.executemany(UPSERT_ROW_SQL, tuples)

    return len(tuples)


async def get_clean_size_db_stats(conn: asyncpg.Connection) -> Dict[str, Any]:
    """Fetch database metrics and summary breakdown."""
    total_rows = await conn.fetchval(f"SELECT count(*) FROM {TABLE_NAME};")
    brand_counts = await conn.fetch(
        f"""
        SELECT brand_name, count(*) as count, count(DISTINCT (category || ':' || fit)) as curve_count
        FROM {TABLE_NAME}
        GROUP BY brand_name
        ORDER BY brand_name;
        """
    )
    category_counts = await conn.fetch(
        f"""
        SELECT category, count(*) as count
        FROM {TABLE_NAME}
        GROUP BY category
        ORDER BY count DESC;
        """
    )
    fit_counts = await conn.fetch(
        f"""
        SELECT fit, count(*) as count
        FROM {TABLE_NAME}
        GROUP BY fit
        ORDER BY count DESC;
        """
    )

    return {
        "total_rows": total_rows,
        "brands": [dict(r) for r in brand_counts],
        "categories": [dict(r) for r in category_counts],
        "fits": [dict(r) for r in fit_counts],
    }


async def fetch_brand_samples(
    conn: asyncpg.Connection, limit_per_brand: int = 2
) -> List[Dict[str, Any]]:
    """Fetch sample rows per brand for audit and verification."""
    query = f"""
    WITH ranked AS (
        SELECT *, ROW_NUMBER() OVER(PARTITION BY brand_name ORDER BY size_label) as rn
        FROM {TABLE_NAME}
    )
    SELECT brand_name, category, fit, size_label, garment_chest_cm,
           garment_shoulder_cm, garment_length_cm, garment_sleeve_cm,
           to_fit_chest_cm, confidence, product_count, source, chart_title
    FROM ranked
    WHERE rn <= $1
    ORDER BY brand_name, size_label;
    """
    rows = await conn.fetch(query, limit_per_brand)
    return [dict(r) for r in rows]
