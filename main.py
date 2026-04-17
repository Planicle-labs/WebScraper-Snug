import json
import os
from logger import logger
from robots import check_robots

CONFIG_FILE = os.path.join("config", "brands.json")
BLOCKED_FILE = os.path.join("outputs", "blocked.json")

def ensure_dirs():
    os.makedirs("outputs", exist_ok=True)
    os.makedirs("config", exist_ok=True)

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

def main():
    logger.info("Starting SnugScraper Phase 1...")
    ensure_dirs()
    brands = load_brands()
    
    if not brands:
        logger.info(f"No brands found in {CONFIG_FILE}. Please add a brand and try again.")
        return

    logger.info(f"Loaded {len(brands)} brand(s) for processing.")

    for brand in brands:
        brand_name = brand.get("brand_name", "Unknown")
        base_url = brand.get("base_url")
        target_url = brand.get("size_chart_url")
        
        logger.info(f"--- Processing brand: {brand_name} ---")
        
        if not base_url or not target_url:
            logger.warning(f"Missing base_url or size_chart_url for {brand_name}. Skipping.")
            continue
            
        is_allowed, delay = check_robots(base_url, target_url)
        
        if is_allowed:
            logger.info(f"✅ ALLOWED: {brand_name} (Crawl Delay: {delay}s)")
            # In Phase 2, we will hand this off to the HTML scraper.
        else:
            logger.warning(f"🚫 BLOCKED: {brand_name} per robots.txt. Flagging for manual entry...")
            save_blocked(brand)

if __name__ == "__main__":
    main()
