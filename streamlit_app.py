import streamlit as st
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.utils import (
    init_session_state,
    reset_all_state,
    load_current_results,
    start_product_discovery,
    start_page_search,
    check_task_completion,
    get_current_logs,
    display_metrics_card,
    PRODUCT_PAGES_FILE,
)

st.set_page_config(
    page_title="Snug Web Scraper",
    page_icon="🕷️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.main-header  { font-size:32px; font-weight:700; color:#FF4B4B; margin-bottom:4px; }
.main-sub     { color:#555; font-size:14px; margin-bottom:10px; }
.step-header  { font-size:18px; font-weight:600; color:#FF4B4B; margin-top:20px; }
.step-desc    { color:#777; font-size:14px; }
.success-box  { background:#1A2E1A; padding:14px; border-radius:10px;
                border-left:4px solid #00CC44; margin-top:10px; }
.warning-box  { background:#2E2A1A; padding:14px; border-radius:10px;
                border-left:4px solid #FFA500; margin-top:10px; }
.error-box    { background:#2E1A1A; padding:14px; border-radius:10px;
                border-left:4px solid #FF4444; margin-top:10px; }
div.stButton > button { width:100%; padding:14px; font-size:15px; font-weight:700;
                        border-radius:10px; transition:all .2s ease; }
.new-test-btn > button { background:transparent!important; border:2px solid #FF4B4B!important;
                         color:#FF4B4B!important; font-size:14px!important; font-weight:700!important;
                         border-radius:8px!important; }
.new-test-btn > button:hover { background:#FF4B4B!important; color:#fff!important; }
.live-badge { display:inline-block; background:#00CC44; color:#000;
              font-size:10px; font-weight:700; padding:2px 8px; border-radius:20px;
              margin-left:8px; vertical-align:middle;
              animation:pulse 1.2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
.metrics-title { font-size:16px; font-weight:600; color:#aaa; margin-bottom:6px; }
</style>
""", unsafe_allow_html=True)

# ── Init & check task completion ───────────────────────────────────────────────
init_session_state()

finished = check_task_completion()   # non-None only on the rerun when task just finished

if st.session_state.get("_reset_mode", False):
    st.session_state._reset_mode = False
else:
    if not st.session_state.is_running:
        load_current_results()

# ── Header ─────────────────────────────────────────────────────────────────────
h_col, r_col = st.columns([5, 1])
with h_col:
    st.markdown('<div class="main-header">🕷️ Snug Web Scraper V1</div>', unsafe_allow_html=True)
    st.markdown('<div class="main-sub">Fashion brand size-chart scraper · Phase 7 Dashboard</div>', unsafe_allow_html=True)
with r_col:
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)
    st.markdown('<div class="new-test-btn">', unsafe_allow_html=True)
    if st.button("🗑️ New Test", help="Erase all results and start fresh"):
        reset_all_state()
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("---")

# ── URL inputs ─────────────────────────────────────────────────────────────────
ci, cc = st.columns([3, 1])
with ci:
    url_input = st.text_input(
        "📌 Enter Category URL",
        placeholder="https://www.uniqlo.com/uk/en/men/t-shirts",
        disabled=st.session_state.is_running,
    )
with cc:
    max_pages = st.number_input("⚙️ Max Pages", 1, 200, 50,
                                disabled=st.session_state.is_running)
with st.expander("⚙️ Page Search Settings", expanded=False):
    max_products = st.number_input(
        "Products to scrape in Page Search",
        min_value=1, max_value=500, value=5,
        help="How many product URLs from Step 1 to process in Step 2",
        disabled=st.session_state.is_running,
    )

st.markdown("---")

# ── Step buttons ───────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)

with col1:
    st.markdown('<div class="step-header">🔘 Step 1: Product Discovery</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-desc">Scrapes product URLs from category pages</div>', unsafe_allow_html=True)

    step1_disabled = st.session_state.is_running or not url_input
    if st.button("▶ Run Product Discovery", disabled=step1_disabled, type="primary"):
        start_product_discovery(url_input, max_pages)
        st.rerun()

    if st.session_state.product_discovery_done:
        n = len(st.session_state.product_urls)
        st.markdown(f'<div class="success-box">✓ Step 1 Complete — <b>{n}</b> products found</div>',
                    unsafe_allow_html=True)

with col2:
    st.markdown('<div class="step-header">🔘 Step 2: Page Search</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-desc">Extract size charts from product pages</div>', unsafe_allow_html=True)

    step2_disabled = st.session_state.is_running or not st.session_state.product_discovery_done
    if step2_disabled and not st.session_state.product_discovery_done:
        st.markdown('<div class="warning-box">⚠️ Run Product Discovery first</div>', unsafe_allow_html=True)

    if st.button("▶ Run Page Search", disabled=step2_disabled, type="primary"):
        start_page_search(max_products)
        st.rerun()

    if st.session_state.page_search_done:
        n = len(st.session_state.size_chart_data)
        st.markdown(f'<div class="success-box">✓ Step 2 Complete — <b>{n}</b> charts extracted</div>',
                    unsafe_allow_html=True)

# ── Live execution log (while running) ────────────────────────────────────────
if st.session_state.is_running:
    logs = get_current_logs()
    if logs:
        st.code("\n".join(logs), language="bash")

st.markdown("---")

# ── Metrics ────────────────────────────────────────────────────────────────────
live = st.session_state.is_running
badge = '<span class="live-badge">LIVE</span>' if live else ""
st.markdown(f'<div class="metrics-title">📊 System Metrics{badge}</div>', unsafe_allow_html=True)
display_metrics_card()

# Auto-refresh every 1 s while scraper thread is running
if st.session_state.is_running:
    time.sleep(1)
    st.rerun()

st.markdown("---")

# ── Result tabs ────────────────────────────────────────────────────────────────
import json as _json

tab1, tab2 = st.tabs(["📁 Product URLs", "📊 Size Chart Data"])

with tab1:
    if st.session_state.product_urls:
        st.success(f"Found {len(st.session_state.product_urls)} product URLs")
        st.dataframe(st.session_state.product_urls, use_container_width=True, height=400)
        st.download_button(
            "💾 Download as JSON",
            _json.dumps(st.session_state.product_urls, indent=2),
            "product_pages.json", "application/json",
        )
    else:
        st.info("No product URLs yet. Run Product Discovery first.")

with tab2:
    if st.session_state.size_chart_data:
        st.success(f"Extracted {len(st.session_state.size_chart_data)} size charts")
        for i, item in enumerate(st.session_state.size_chart_data):
            with st.expander(f"Product {i+1}: {item.get('url','')[:60]}..."):
                st.json(item.get("data", []))
        st.download_button(
            "💾 Download as JSON",
            _json.dumps(st.session_state.size_chart_data, indent=2),
            "size_charts.json", "application/json",
        )
    else:
        st.info("No size chart data yet. Run Page Search to extract data.")

# ── Footer ─────────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    "<div style='text-align:center;color:#444;font-size:13px'>"
    "Snug Web Scraper V1 &nbsp;·&nbsp; Built with Streamlit</div>",
    unsafe_allow_html=True,
)