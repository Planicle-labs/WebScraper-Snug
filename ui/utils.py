import os
import asyncio
import time
import json
import threading
import uuid
import psutil
import streamlit as st
from urllib.parse import urlparse
from typing import Dict, Any

try:
    from core.utils import read_json_file, get_output_file_path
    from core.metrics import get_run_metrics_since
except ImportError:
    from core.utils import read_json_file, get_output_file_path
    from core.metrics import get_run_metrics_since

PRODUCT_PAGES_FILE = get_output_file_path("product_pages.json")

# ---------------------------------------------------------------------------
# Module-level stores (survive across Streamlit reruns in the same process)
# ---------------------------------------------------------------------------
_task_results: dict = {}   # task_id -> result dict
_task_logs: dict    = {}   # task_id -> list[str]
_task_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

def init_session_state():
    defaults = {
        "product_discovery_done": False,
        "page_search_done":       False,
        "product_urls":           [],
        "size_chart_data":        [],
        "is_running":             False,
        "running_step":           None,
        "_task_id":               None,
        "_run_start_ts":          None,   # UTC string, for SQLite filter
        # psutil baseline recorded when a run starts
        "_net_sent_base":         None,
        "_net_recv_base":         None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_all_state():
    for fname in ("product_pages.json", "size_charts.json"):
        p = get_output_file_path(fname)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    keys = list(init_session_state.__code__.co_consts)  # not reliable; list explicitly
    for k in [
        "product_discovery_done", "page_search_done", "product_urls",
        "size_chart_data", "is_running", "running_step",
        "_task_id", "_run_start_ts", "_net_sent_base", "_net_recv_base",
        "_reset_mode",
    ]:
        st.session_state.pop(k, None)
    st.session_state["_reset_mode"] = True


def load_current_results():
    products = read_json_file(PRODUCT_PAGES_FILE)
    if products:
        st.session_state.product_urls           = products
        st.session_state.product_discovery_done = True

    size_chart_file = get_output_file_path("size_charts.json")
    size_data = read_json_file(size_chart_file)
    if size_data:
        st.session_state.size_chart_data  = size_data
        st.session_state.page_search_done = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _record_net_baseline():
    net = psutil.net_io_counters()
    st.session_state._net_sent_base = net.bytes_sent
    st.session_state._net_recv_base = net.bytes_recv


def _bw_delta_kb():
    if st.session_state._net_sent_base is None:
        return 0.0, 0.0
    net = psutil.net_io_counters()
    sent = max(0, net.bytes_sent - st.session_state._net_sent_base) / 1024
    recv = max(0, net.bytes_recv - st.session_state._net_recv_base) / 1024
    return sent, recv


def _db_requests() -> int:
    ts = st.session_state.get("_run_start_ts")
    if not ts:
        return 0
    try:
        return get_run_metrics_since(ts)["requests_count"]
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------

def _thread_product_discovery(task_id: str, url: str, max_pages: int):
    logs = _task_logs[task_id]
    try:
        from product_discovery.scraper import scrape_category
        logs.append(f"Starting Product Discovery for: {url}")
        logs.append(f"Max pages: {max_pages}")
        result = asyncio.run(scrape_category(url, max_pages))
        logs.append(f"✓ Completed — {len(result)} product URLs found")
        with _task_lock:
            _task_results[task_id] = {"status": "success", "type": "product_discovery", "data": result}
    except Exception as e:
        logs.append(f"✗ Error: {e}")
        with _task_lock:
            _task_results[task_id] = {"status": "error", "type": "product_discovery", "message": str(e)}


def _thread_page_search(task_id: str, urls: list):
    logs = _task_logs[task_id]
    try:
        from core.utils import read_json_file, get_output_file_path
        from page_search.run import load_brands, url_matches_brand
        from page_search.scrapers.html_scraper import scrape_html_size_chart
        from page_search.scrapers.image_scraper import scrape_image_size_chart

        brands = load_brands()
        brand_by_host = {
            urlparse(brand.get("base_url", "")).netloc: brand
            for brand in brands
            if brand.get("base_url")
        }
        all_data = []
        logs.append(f"Starting Page Search on {len(urls)} URLs...")
        for i, url in enumerate(urls):
            logs.append(f"Processing {i+1}/{len(urls)}: {url[:55]}...")
            try:
                host = urlparse(url).netloc
                brand = brand_by_host.get(host)

                if not brand:
                    brand = next((candidate for candidate in brands if url_matches_brand(url, candidate)), None)

                brand_name = brand.get("brand_name", f"Product {i+1}") if brand else f"Product {i+1}"
                chart_type = brand.get("chart_type", "html") if brand else "html"

                if chart_type == "image":
                    folder_name = brand_name.lower().replace(" ", "_") + "_output_img"
                    output_dir = get_output_file_path(folder_name)
                    result = asyncio.run(scrape_image_size_chart(brand_name, url, output_dir))
                    all_data.append({"url": url, "data": result, "type": "image"})
                    ok_count = sum(1 for item in result if item.get("status") == "ok")
                    logs.append(f"  Saved {ok_count} CM image(s)")
                else:
                    result = asyncio.run(scrape_html_size_chart(brand_name, url))
                    if result:
                        all_data.append({"url": url, "data": result, "type": "html"})
            except Exception as e:
                logs.append(f"  Failed: {e}")

        out = get_output_file_path("size_charts.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(all_data, f, indent=2)
        logs.append(f"✓ Completed — {len(all_data)} size charts extracted")
        with _task_lock:
            _task_results[task_id] = {"status": "success", "type": "page_search", "data": all_data}
    except Exception as e:
        logs.append(f"✗ Error: {e}")
        with _task_lock:
            _task_results[task_id] = {"status": "error", "type": "page_search", "message": str(e)}


# ---------------------------------------------------------------------------
# Public: start scrapers
# ---------------------------------------------------------------------------

def start_product_discovery(url: str, max_pages: int):
    task_id = str(uuid.uuid4())
    _task_logs[task_id] = []
    _record_net_baseline()
    st.session_state._run_start_ts       = _now_utc()
    st.session_state.is_running          = True
    st.session_state.running_step        = "product_discovery"
    st.session_state._task_id            = task_id
    st.session_state.product_discovery_done = False
    st.session_state.product_urls        = []
    t = threading.Thread(
        target=_thread_product_discovery,
        args=(task_id, url, max_pages),
        daemon=True,
    )
    t.start()


def start_page_search(max_products: int = 5):
    task_id = str(uuid.uuid4())
    _task_logs[task_id] = []
    if st.session_state._net_sent_base is None:
        _record_net_baseline()
    # Don't reset _run_start_ts so bandwidth accumulates across both steps
    if not st.session_state._run_start_ts:
        st.session_state._run_start_ts = _now_utc()
    st.session_state.is_running       = True
    st.session_state.running_step     = "page_search"
    st.session_state._task_id         = task_id
    st.session_state.page_search_done = False
    st.session_state.size_chart_data  = []
    urls = st.session_state.product_urls[:max_products]
    t = threading.Thread(
        target=_thread_page_search,
        args=(task_id, urls),
        daemon=True,
    )
    t.start()


# ---------------------------------------------------------------------------
# Public: check if background task finished (call at top of each rerun)
# ---------------------------------------------------------------------------

def check_task_completion() -> dict | None:
    task_id = st.session_state.get("_task_id")
    if not task_id:
        return None
    with _task_lock:
        result = _task_results.pop(task_id, None)
    if result is None:
        return None

    # Task finished — update session state
    st.session_state.is_running  = False
    st.session_state.running_step = None
    st.session_state._task_id    = None

    if result["status"] == "success":
        if result["type"] == "product_discovery":
            st.session_state.product_urls           = result["data"]
            st.session_state.product_discovery_done = True
        elif result["type"] == "page_search":
            st.session_state.size_chart_data  = result["data"]
            st.session_state.page_search_done = True

    return result


def get_current_logs() -> list[str]:
    task_id = st.session_state.get("_task_id")
    if task_id and task_id in _task_logs:
        return list(_task_logs[task_id])
    return []


# ---------------------------------------------------------------------------
# Metrics display
# ---------------------------------------------------------------------------

def display_metrics_card():
    """
    All psutil reads happen here inline on every Streamlit rerun.
    CPU/RAM are fresh on each call. Bandwidth is cumulative delta from
    run-start baseline. Requests come from MetricsTracker SQLite.
    """
    cpu  = psutil.cpu_percent(interval=0.3)
    ram  = psutil.virtual_memory().percent
    sent_kb, recv_kb = _bw_delta_kb()
    reqs = _db_requests()

    def fmt(kb: float) -> str:
        if kb >= 1024:
            return f"{kb/1024:.2f} MB"
        return f"{kb:.1f} KB"

    st.markdown("""
    <style>
    .mc { background:linear-gradient(135deg,#1A1A1A,#272727);
          padding:18px 14px; border-radius:14px; border:1px solid #333;
          text-align:center; }
    .ml { color:#666; font-size:11px; text-transform:uppercase;
          letter-spacing:1.2px; margin-bottom:8px; }
    .mv { font-size:28px; font-weight:700; line-height:1.1; }
    .ms { color:#555; font-size:11px; margin-top:5px; }
    .cpu { color:#00FF7F; } .ram { color:#00BFFF; }
    .bwu { color:#FF8C00; } .bwd { color:#DA70D6; }
    .req { color:#FFD700; }
    </style>
    """, unsafe_allow_html=True)

    c1, c2, c3, c4, c5 = st.columns(5)
    cards = [
        (c1, "CPU Usage",      f"{cpu:.1f}%",     "cpu",  "System-wide"),
        (c2, "RAM Usage",      f"{ram:.1f}%",     "ram",  "System-wide"),
        (c3, "Bandwidth ↑",    fmt(sent_kb),      "bwu",  "Uploaded this run"),
        (c4, "Bandwidth ↓",    fmt(recv_kb),      "bwd",  "Downloaded this run"),
        (c5, "HTTP Requests",  str(reqs),         "req",  "Via Playwright"),
    ]
    for col, label, val, cls, sub in cards:
        with col:
            st.markdown(f"""
            <div class="mc">
                <div class="ml">{label}</div>
                <div class="mv {cls}">{val}</div>
                <div class="ms">{sub}</div>
            </div>""", unsafe_allow_html=True)
