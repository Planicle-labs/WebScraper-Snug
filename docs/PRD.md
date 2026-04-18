**Project Codename:** Snug  
**Component:** Brand Size Database Scraper  
**Last Updated:** 17th April 2026

---

## 1. Objective

Build a scraper that visits fashion brand websites, extracts their size chart data (chest, shoulder, waist, hip, inseam measurements per size per category), and dumps structured data into a Postgres/Neon database.

This database powers the core recommendation engine of Snug — a sizing integration.

---

## 2. Output Schema

Every scraped entry must conform to this structure:

<!-- ```
brand_size_db {
  brand_id          // auto generated uuid
  brand_name        // e.g. "Zara"
  category          // e.g. "t-shirt", "polo", "jeans", "jacket", "trousers"
  size_label        // "XS", "S", "M", "L", "XL", "XXL"
  body_chest_min    // in cm — minimum chest body measurement this size fits
  body_chest_max    // in cm — maximum chest body measurement this size fits
  body_shoulder     // in cm — shoulder measurement
  body_waist_min    // in cm
  body_waist_max    // in cm
  body_hip_min      // in cm — for bottoms only, null for tops
  body_hip_max      // in cm — for bottoms only, null for tops
  body_inseam       // in cm — for bottoms only, null for tops
  region            // "US", "UK", "EU", "IN"
  last_verified     // ISO date string
  confidence        // "high", "medium", "low"
}
``` -->

## 3. Tech Stack

```
Language:   Python
Browser:    Playwright (headless, stealth mode)
Monitoring: psutil (dynamic RAM monitoring)
AI:         TBD
PDF:        TBD
Database:   Neon
Queue:      Custom async queue
```

---

## 4. Scraper Architecture

### 4.1 Site Type Detection

Before extracting, detect what type of size chart the brand uses:

```
Type A: HTML table       → extract text directly via Playwright
Type B: JS-rendered page → Playwright renders, then extract text
Type C: Image-based      → Playwright screenshots → TBD
Type D: PDF guide        → Playwright downloads PDF → TBD
Type E: Blocked/robots   → flag for manual entry, enter them in a blocked.json file will review those sites later
```

### 4.2 Queue Configuration

```
Browser Pool:
- browser_pool_size: 2 (for resilience, replaces single instance)
- browser_max_restarts: 3 (restart after X crashes)
- tabs_per_browser: 8 (8 tabs × 2 browsers = 16 total)

Timing:
- base_delay: 2s (between requests)
- max_delay: 30s (before retry)
- delay_multiplier: 1.5 (exponential backoff: 2s → 3s → 4.5s)
- request_timeout: 30s (per-request timeout)

Domain-Level Rate Limiting (per brand):
- default: min_delay=2s, max_concurrent=2, requests_per_minute=20
- high_risk domains (zara.com, hm.com, nike.com): stricter limits

Retry Logic:
- max_retries: 3
- retry_on_status: [408, 429, 500, 502, 503, 504] (429 = rate limit)
- exponential_backoff: true

Resource Management:
- Library: psutil for dynamic RAM monitoring
- memory_threshold_percent: 80 (trigger cleanup if RAM > 80%)
- tab_cleanup_strategy: restart_browser when threshold hit
- Auto-detect available RAM at startup and scale tabs accordingly

User Agent:
- Rotate through pool of realistic user agents per request -- very important
```

### 4.4 Validation Rules

```
Should be having some data validation logic before dumping to db
```

---

## 5. Allowed Categories

Only scrape data for these categories. Ignore everything else (swimwear, lingerie, accessories etc.):

```
Tops:    "t-shirt", "polo", "shirt", "sweatshirt", "hoodie", "jacket", "blazer"
```

---

## 6. Output Tables in Neon

**Main table:** `brand_size_db`  
**Flagged entries:** `flagged_entries` (failed validation, needs manual review)  
**Scrape log:** `scrape_log` (brand, timestamp, status, error if any)

---

## 7. Robots.txt Check (Required)

Before scraping any brand, the scraper must:

```python
fetch https://brand.com/robots.txt
parse Disallow rules
if size chart URL path is disallowed → skip brand, log as "blocked" and add to blocked.json
if Crawl-delay specified → respect it (override default 2-3s delay)
```

Brands that are typically blocked: Nike, Zara (verify at runtime).  
Blocked brands → added to manual entry queue, not skipped entirely.

---

## 10. Deliverables

The codebase is split into two pipeline stages with shared core utilities:

```
WebScraper-Snug/
│
├── product_discovery/          # Stage 1: crawl category pages → collect product URLs
│   ├── scraper.py              # category listing scraper (multi-page, pagination-aware)
│   └── config.json             # category URLs to scrape per brand
│
├── page_search/                # Stage 2: visit product URLs → extract size charts
│   ├── run.py                  # entry point for Stage 2
│   ├── scrapers/
│   │   ├── html_scraper.py     # Type A/B sites (HTML table / JS-rendered)
│   │   ├── vision_scraper.py   # Type C sites (image-based) — TBD
│   │   └── pdf_scraper.py      # Type D sites (PDF guides) — TBD
│   └── config/
│       └── brands.json         # per-brand config (base URL, chart type, region)
│
├── core/                       # Shared utilities used by both stages
│   ├── logger.py               # consistent logging setup
│   └── robots.py               # robots.txt checker (gate before any scraping)
│
├── outputs/                    # Shared output layer — THE HANDOFF between stages
│   ├── product_pages.json      # ← written by Stage 1, read by Stage 2
│   └── blocked.json            # brands blocked by robots.txt (flagged for manual entry)
│
├── docs/                       # PRD and phase implementation notes
├── main.py                     # thin pipeline runner (chains Stage 1 → Stage 2)
│
│   # Planned (future phases):
│   ├── parser.py               # raw data → schema fields
│   ├── validator.py            # validation rules
│   └── db.py                   # Neon DB write logic
```

**Pipeline flow:**
```
product_discovery/scraper.py
        │  reads:   product_discovery/config.json
        │  writes:  outputs/product_pages.json
        ▼
page_search/run.py
        │  reads:   outputs/product_pages.json
        │            page_search/config/brands.json
        │  writes:  outputs/blocked.json
        ▼
    [parser → validator → db]  (future phases)
```