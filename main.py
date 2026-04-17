import json
import os
import asyncio
from logger import logger
from robots import check_robots

# Import the new scraper logic
from scrapers.html_scraper import scrape_html_size_chart

CONFIG_FILE = os.path.join("config", "brands.json")
BLOCKED_FILE = os.path.join("outputs", "blocked.json")

def ensure_dirs():
    os.makedirs("outputs", exist_ok=True)
    os.makedirs("config", exist_ok=True)
    os.makedirs("scrapers", exist_ok=True)

def load_brands():
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"Config file not found: {CONFIG_FILE}. Creating an empty one.")
        with open(CONFIG_FILE, "w") as f:
            json.dump([], f)
        return []
        
    with open(CONFIG_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON in {CONFIG_FILE}: {e}")
            return []

def save_blocked(blocked_brand):
    blocked_list = []
    if os.path.exists(BLOCKED_FILE):
        with open(BLOCKED_FILE, "r") as f:
            try:
                blocked_list = json.load(f)
            except json.JSONDecodeError:
                blocked_list = []
                
    # Avoid duplicates
    if not any(b.get("brand_name") == blocked_brand.get("brand_name") for b in blocked_list):
        blocked_list.append(blocked_brand)
        with open(BLOCKED_FILE, "w") as f:
            json.dump(blocked_list, f, indent=2)
        logger.info(f"Saved {blocked_brand.get('brand_name')} to {BLOCKED_FILE}")

async def async_main():
    logger.info("Starting SnugScraper Phase 2...")
    ensure_dirs()
    brands = load_brands()
    
    if not brands:
        logger.info(f"No brands found in {CONFIG_FILE}. Please add a brand and try again.")
        return

    logger.info(f"Loaded {len(brands)} brand(s) for processing.")

    for brand in brands:
        brand_name = brand.get("brand_name", "Unknown")
        base_url = brand.get("base_url")
        target_url = brand.get("product_url")
        chart_type = brand.get("chart_type", "html")
        
        logger.info(f"--- Processing brand: {brand_name} ---")
        
        if not base_url or not target_url:
            logger.warning(f"Missing base_url or size_chart_url for {brand_name}. Skipping.")
            continue
            
        is_allowed, delay = check_robots(base_url, target_url)
        
        # Phase 1: robots.txt check completed, execute Phase 2: html parsing
        if is_allowed:
            logger.info(f"✅ ALLOWED: {brand_name} (Crawl Delay: {delay}s)")
            
            if chart_type == "html":
                logger.info(f"[{brand_name}] Routing to HTML Scraper...")
                raw_data = await scrape_html_size_chart(brand_name, target_url)
                if raw_data:
                    logger.info(f"[{brand_name}] Successfully extracted {len(raw_data)} rows/entries of data.")
                else:
                    logger.warning(f"[{brand_name}] Extraction returned empty.")
            else:
                logger.info(f"[{brand_name}] Chart type '{chart_type}' not supported in Phase 2.")
                
        else:
            # logger.warning(f"🚫 BLOCKED: {brand_name} per robots.txt. Flagging for manual entry...")
            # save_blocked(brand)
            logger.warning("Scraping a blocked brand")
            if chart_type == "html":
                logger.info(f"[{brand_name}] Routing to HTML Scraper...")
                raw_data = await scrape_html_size_chart(brand_name, target_url)
                if raw_data:
                    logger.info(f"[{brand_name}] Successfully extracted {len(raw_data)} rows/entries of data.")
                else:
                    logger.warning(f"[{brand_name}] Extraction returned empty.")
            else:
                logger.info(f"[{brand_name}] Chart type '{chart_type}' not supported in Phase 2.")

def main():
    asyncio.run(async_main())

if __name__ == "__main__":
    main()
