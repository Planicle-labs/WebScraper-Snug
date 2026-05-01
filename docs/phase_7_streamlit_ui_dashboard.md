it # Phase 7: Streamlit UI Dashboard

## Overview

Added a Streamlit-based web dashboard for the Snug Web Scraper to provide a user-friendly interface for running scrapers and monitoring system metrics.

---

## Files Created

### 1. `requirements.txt` (New)

Dependencies for the UI:
```
streamlit
psutil
playwright
playwright-stealth
```

### 2. `core/utils.py` (New)

Generic utility functions that can be used by any layer (CLI, API, UI):

| Function | Description |
|----------|-------------|
| `get_system_metrics()` | Returns CPU% and RAM% using psutil |
| `get_process_metrics()` | Returns current process memory/CPU info |
| `read_json_file(path)` | Reads and parses JSON files safely |
| `get_output_file_path(filename)` | Returns full path to output files |

### 3. `ui/__init__.py` (New)

Package initialization for the UI module.

### 4. `ui/utils.py` (New)

Streamlit-specific wrapper functions:

| Function | Description |
|----------|-------------|
| `init_session_state()` | Initializes Streamlit session state variables |
| `reset_all_state()` | Deletes output JSON files from disk AND wipes session state |
| `check_step1_completed()` | Checks if Product Discovery has run |
| `check_step2_completed()` | Checks if Page Search has run |
| `load_current_results()` | Loads existing results from JSON files (skipped after a reset) |
| `run_product_discovery(url, max_pages)` | Runs scraper with live metrics display |
| `run_page_search()` | Runs size chart extraction with live metrics |
| `display_metrics_card()` | Renders 5-column metrics bar: CPU, RAM, BW↑, BW↓, Requests |

### 5. `streamlit_app.py` (New)

Main Streamlit dashboard with:

- **URL Input**: Text field for category URL
- **Max Pages**: Configurable (1-200, default 50)
- **Step 1 Button**: "Run Product Discovery"
- **Step 2 Button**: "Run Page Search" (enabled only after Step 1)
- **Live Metrics**: CPU and RAM gauges updating during execution
- **Output Tabs**: Product URLs and Size Chart Data with download buttons

---

## Architecture

```
WebScraper-Snug/
├── streamlit_app.py              # Main dashboard (entry point)
├── requirements.txt             # Dependencies
├── core/
│   ├── logger.py               # Logging (existing)
│   ├── metrics.py              # SQLite metrics (existing)
│   ├── robots.py               # robots.txt checker (existing)
│   └── utils.py                # NEW - Generic utilities
├── ui/
│   ├── __init__.py             # NEW - Package init
│   └── utils.py                # NEW - Streamlit wrappers
└── [existing scraper modules]
```

---

## Data Flow

```
User Input (URL)
    │
    ▼
[Step 1: Product Discovery]
    │→ Scrapes category page
    │→ Finds product URLs
    │→ Saves to outputs/product_pages.json
    ▼
[Step 2: Page Search]
    │→ Reads product URLs
    │→ Scrapes size charts
    │→ Saves to outputs/size_charts.json
    ▼
Output Display (Tabs)
    │
    └── Download as JSON
```

---

## System Metrics

Live monitoring during scraper execution:

- **CPU Usage**: Percentage of CPU being used (system-wide, updated every 1 s)
- **RAM Usage**: Percentage of system memory used (system-wide, updated every 1 s)
- **Bandwidth ↑**: Upload speed in KB/s or MB/s since last sample
- **Bandwidth ↓**: Download speed in KB/s or MB/s since last sample
- **Requests**: Count of scraper requests made in this run

Metrics are displayed in a 5-column card row. When idle, CPU/RAM show a live snapshot; bandwidth shows 0 KB/s (no traffic). While a run is active Streamlit auto-reruns every 2 s so cards refresh in near-real-time.

Color coding:
- CPU: Green (`#00FF00`)
- RAM: Blue (`#00BFFF`)
- BW ↑: Orange (`#FF8C00`)
- BW ↓: Purple (`#DA70D6`)
- Requests: Gold (`#FFD700`)

---

## Session State

Streamlit session variables used:

| Variable | Type | Description |
|----------|------|-------------|
| `product_discovery_done` | bool | Whether Step 1 has completed |
| `page_search_done` | bool | Whether Step 2 has completed |
| `product_urls` | list | List of scraped product URLs |
| `size_chart_data` | list | Extracted size chart data |
| `execution_log` | list | Log messages during execution |
| `current_metrics` | dict | Live CPU/RAM/bandwidth/requests values |
| `is_running` | bool | Whether a scraper is currently running |
| `running_step` | str | Which step is running (`product_discovery`/`page_search`) |
| `_reset_mode` | bool | Internal flag: skip `load_current_results()` on the rerun after a reset |
| `_net_baseline` | dict | Net I/O snapshot taken at the start of a run for delta calculation |

---

## Running the Dashboard

```bash
# From project root
streamlit run streamlit_app.py
```

Opens at: **http://localhost:8501**

---

## Future Enhancements (Phase 8+)

1. **Real-time Progress Bars**: Show page-by-page progress
2. **Cancel Button**: Ability to stop running scraper
3. **Authentication**: Basic auth for remote access
4. **Job Queue**: Background job processing with status
5. **FastAPI Migration**: Move to FastAPI + React for production

---

## Dependencies Added

- `streamlit` - Web UI framework
- `psutil` - System monitoring (CPU, RAM)
- `playwright` - Browser automation (existing)
- `playwright-stealth` - Anti-detection (existing)