# Phase 5: Neon DB Integration & Scrape Logs

**Goal:** Connect to the remote Neon PostgreSQL database, handling the secure insertion of successful data, flagged data, and comprehensive run metrics.

> **Learning Objective:** Async database drivers (`asyncpg`), connection pooling, upsert operations (conflict resolution), and telemetry logging.

## What Gets Built
The final stage of the HTML scraping pipeline. Validated records are securely saved using conflict-handling schema logic, while errors and run statistics populate log tables.

## Deliverables

### 1. `db.py`
Manages connection pooling and table interactions.
- **`brand_size_db` Interactions:** Implements `ON CONFLICT (brand_name, category, size_label, region)` upserts to effectively maintain the records without duplications on subsequent runs.
- **`flagged_entries` Interactions:** Safely writes the JSON payload for records that failed the Phase 4 validation.

### 2. Enhanced `logger.py`
Records run-level diagnostics directly to the database.
- Upgrades to write logs into a `scrape_log` table storing run times, success/fail counts, and system failures.

### Database Setup Note
The target table definitions (e.g., UUID primary keys, numeric bindings) need to be configured on Neon prior to running this script integration.

## Acceptance Criteria
- [ ] Validated sizes are successfully visible in the Neon `brand_size_db` via the application logic.
- [ ] Re-running the identical data triggers an upsert instead of erroring on unique constraints.
- [ ] Failed record injections exist inside `flagged_entries` with raw JSON and fail reason attached.
- [ ] The `scrape_log` records exact runtime numbers per execution batch.
