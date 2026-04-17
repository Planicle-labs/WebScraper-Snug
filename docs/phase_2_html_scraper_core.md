# Phase 2: Single-Brand HTML Scraper

**Goal:** Build the core extraction logic for standard HTML sites using a single browser tab to ensure the extraction pipeline works reliably without concurrency complication.

> **Learning Objective:** Playwright async API, stealth browser configurations, reliable CSS selectors, and initial structured data mapping.

## What Gets Built
A vertical slice of the scraper that takes one HTML-based brand (like Uniqlo) and extracts its size chart into structured raw data. No queue, no multi-threading yet.

## Deliverables

### 1. `scrapers/html_scraper.py`
Handles both Type A (HTML table) and Type B (JS-rendered) pages.
- Launches a headless browser using Playwright stealth mode.
- Implements user-agent selection for requests.
- Navigates to the URL and detects page type (waits for JS rendering if needed).
- Selects the target table or elements and extracts the raw rows.


## Site Type Detection Focus
In this phase, we establish basic type detection logic:
- A `<table>` means Type A.
- A `<div class="size-chart">` populated after JS load means Type B.

## Acceptance Criteria
- [ ] `main.py` successfully triggers the scraper for one HTML brand.
- [ ] The raw rows from the table are printed correctly to the console.
- [ ] Missing elements or timeout errors are handled gracefully without completely breaking the pipeline.
