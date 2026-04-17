# Phase 2: Single-Brand HTML Scraper

**Goal:** Build the core extraction logic for standard HTML sites using a single browser tab to ensure the extraction pipeline works reliably without concurrency complication.

> **Learning Objective:** Playwright async API, stealth browser configurations, reliable CSS selectors, and initial structured data mapping.

## What Gets Built
A vertical slice of the scraper that takes one HTML-based brand (like Uniqlo) and extracts its size chart into structured raw data. No queue, no multi-threading yet.

## Deliverables

### 1. `scrapers/html_scraper.py`
Handles both Type A (HTML table) and Type B (JS-rendered) pages.
- Launches a stealth headless browser using Playwright.
- Implements user-agent selection for requests.
- Navigates to the URL and detects page type (waits for JS rendering if needed).
- Selects the target table or elements and extracts the raw rows.

### 2. `parser.py` (Draft)
A preliminary parser to take raw extracted text rows and map them onto the database schema fields.
- Handles matching "Extra Small" to `XS`, or reading numbers from cells like `50 cm`.

## Site Type Detection Focus
In this phase, we establish basic type detection logic:
- A `<table>` means Type A.
- A `<div class="size-chart">` populated after JS load means Type B.
- *Types C & D are deferred to TBD phases later.*

## Acceptance Criteria
- [ ] `main.py` successfully triggers the scraper for one HTML brand.
- [ ] The raw rows from the table are printed correctly to the console.
- [ ] Missing elements or timeout errors are handled gracefully without completely breaking the pipeline.
