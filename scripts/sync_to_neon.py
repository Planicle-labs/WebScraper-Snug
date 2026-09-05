"""CLI script to push clean_size_db.json to Neon PostgreSQL database.

Usage:
    uv run python scripts/sync_to_neon.py
    uv run python scripts/sync_to_neon.py --input outputs/snug/clean_size_db.json --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# Add workspace root to sys.path for core imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import (
    fetch_brand_samples,
    get_clean_size_db_stats,
    get_connection,
    init_table,
    upsert_clean_size_rows,
)


def load_records(path: Path) -> List[Dict[str, Any]]:
    """Load and validate local JSON records."""
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        raise ValueError(f"Expected list of records in {path}, got {type(records)}")

    return records


async def sync(input_path: Path, dry_run: bool = False, verbose: bool = False) -> None:
    print(f"[*] Reading canonical size records from: {input_path}")
    records = load_records(input_path)
    print(f"[+] Loaded {len(records)} records from JSON.")

    # Preliminary validation
    unique_keys = set()
    for idx, r in enumerate(records):
        key = (
            r.get("brand_name"),
            r.get("category"),
            r.get("fit"),
            r.get("size_label"),
            r.get("region", "IN"),
        )
        if any(k is None for k in key):
            raise ValueError(f"Record #{idx} has missing key component: {key}")
        if key in unique_keys:
            raise ValueError(f"Duplicate composite key detected in record #{idx}: {key}")
        unique_keys.add(key)

        if r.get("garment_chest_cm") is None:
            raise ValueError(f"Record #{idx} ({key}) has NULL garment_chest_cm!")

    print(f"[+] Pre-flight validation passed: 0 duplicate keys, 0 missing chest values.")

    if dry_run:
        print("[!] DRY RUN mode: Skipping Neon database connection and ingestion.")
        return

    print("[*] Connecting to Neon PostgreSQL...")
    conn = await get_connection(statement_cache_size=0)
    try:
        print("[*] Ensuring table and indexes exist in Neon...")
        await init_table(conn)

        print(f"[*] Upserting {len(records)} records into `clean_size_db`...")
        count = await upsert_clean_size_rows(conn, records)
        print(f"[+] Successfully upserted {count} records into Neon!")

        print("\n" + "=" * 60)
        print("DATABASE VERIFICATION & AUDIT REPORT")
        print("=" * 60)

        stats = await get_clean_size_db_stats(conn)
        print(f"Total Rows in clean_size_db: {stats['total_rows']}")
        print("\nBreakdown by Brand:")
        for b in stats["brands"]:
            print(f"  - {b['brand_name']:<18}: {b['count']:>3} rows across {b['curve_count']} curves")

        print("\nBreakdown by Category:")
        for c in stats["categories"]:
            print(f"  - {c['category']:<18}: {c['count']:>3} rows")

        print("\nBreakdown by Fit:")
        for f in stats["fits"]:
            print(f"  - {f['fit']:<18}: {f['count']:>3} rows")

        print("\nSpot Checking Samples from Neon:")
        samples = await fetch_brand_samples(conn, limit_per_brand=1)
        for s in samples:
            chest = f"{float(s['garment_chest_cm']):.1f}cm"
            shldr = f"{float(s['garment_shoulder_cm']):.1f}cm" if s['garment_shoulder_cm'] else "N/A"
            lgth = f"{float(s['garment_length_cm']):.1f}cm" if s['garment_length_cm'] else "N/A"
            print(
                f"  [{s['brand_name']}] {s['category']} | {s['fit']} | Size {s['size_label']}: "
                f"chest={chest}, shoulder={shldr}, length={lgth} (conf: {s['confidence']}, source: {s['source']})"
            )
        print("=" * 60 + "\n")

    finally:
        await conn.close()
        print("[*] Neon database connection closed cleanly.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync clean size database to Neon.")
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=Path("outputs/snug/clean_size_db.json"),
        help="Path to clean_size_db.json (default: outputs/snug/clean_size_db.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate JSON records without writing to the database",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args()

    asyncio.run(sync(args.input, dry_run=args.dry_run, verbose=args.verbose))


if __name__ == "__main__":
    main()
