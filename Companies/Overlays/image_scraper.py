import json
import asyncio
import random
import os
import httpx
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

# List of user agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Default output directory for images
DEFAULT_IMAGE_OUTPUT_DIR = os.path.join("outputs", "overlaysnow_output_img")

async def download_image(client, url, filename, output_dir):
    """Downloads an image from a URL and saves it to the specified directory."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        response = await client.get(url, timeout=30.0)
        if response.status_code == 200:
            file_path = os.path.join(output_dir, filename)
            with open(file_path, "wb") as f:
                f.write(response.content)
            return file_path
    except Exception as e:
        print(f"Error downloading image {url}: {e}")
    return None

async def _scrape_single_url(page, url, client, output_dir):
    """Internal helper to scrape a single product URL and capture the CM image."""
    results = []
    try:
        print(f"Navigating to: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # 1. Close the initial discount/newsletter popup
        try:
            popup_close = page.locator(".popup_close_btn")
            if await popup_close.is_visible(timeout=5000):
                await popup_close.dispatch_event("click")
        except Exception:
            pass

        # 2. Click the 'Size chart' button
        try:
            size_chart_btn = page.get_by_role("button", name="Size chart")
            if await size_chart_btn.count() > 0:
                await size_chart_btn.wait_for(state="visible", timeout=10000)
                await size_chart_btn.click(force=True)
                await asyncio.sleep(2)
            else:
                return [{"url": url, "status": "error", "message": "Size chart button not found"}]
        except Exception as e:
            return [{"url": url, "status": "error", "message": str(e)}]

        # 3. Capture the WebP image after clicking the 'CM' button
        try:
            async with page.expect_response(
                    lambda response: "image/webp" in response.headers.get("content-type", "")
                                     and response.status == 200,
                    timeout=15000
            ) as response_info:
                # The interaction that triggers the network request
                await page.get_by_role("button", name="CM").dispatch_event("click")

            final_response = await response_info.value
            image_url = final_response.url
            
            # Generate a filename from the URL slug
            product_slug = url.split("/")[-1].split("?")[0]
            filename = f"{product_slug}_cm.webp"
            
            # Download the image
            local_path = await download_image(client, image_url, filename, output_dir)
            
            if local_path:
                results.append({
                    "url": url,
                    "image_path": os.path.abspath(local_path),
                    "label": "CM",
                    "status": "ok"
                })
            else:
                results.append({
                    "url": url,
                    "status": "error",
                    "message": "Failed to download image"
                })

        except Exception as e:
            results.append({
                "url": url,
                "status": "error",
                "message": "No new WebP request detected after clicking 'CM'"
            })

    except Exception as e:
        results.append({"url": url, "status": "error", "message": str(e)})
    
    return results

async def scrape_image_size_chart(brand_name, url, output_dir=None):
    """
    Entry point for the Streamlit UI.
    Scrapes a single URL and returns the result.
    """
    if not output_dir:
        output_dir = DEFAULT_IMAGE_OUTPUT_DIR

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        stealth = Stealth()
        ua = random.choice(USER_AGENTS)
        context = await browser.new_context(user_agent=ua)
        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        
        async with httpx.AsyncClient() as client:
            results = await _scrape_single_url(page, url, client, output_dir)
            
        await browser.close()
        return results

async def run_scraper():
    """Batch entry point to run against product_pages.json."""
    json_path = os.path.join("outputs", "product_pages.json")
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        return

    try:
        with open(json_path, "r") as f:
            urls = json.load(f)
    except Exception as e:
        print(f"Error reading {json_path}: {e}")
        return

    if not urls:
        print("No URLs found.")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        stealth = Stealth()
        
        async with httpx.AsyncClient() as client:
            for index, url in enumerate(urls):
                print(f"\n[{index+1}/{len(urls)}] Processing...")
                context = await browser.new_context(user_agent=random.choice(USER_AGENTS))
                page = await context.new_page()
                await stealth.apply_stealth_async(page)
                
                res = await _scrape_single_url(page, url, client, DEFAULT_IMAGE_OUTPUT_DIR)
                print(f"Result: {res}")
                
                await context.close()
                await asyncio.sleep(random.uniform(2, 5))

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_scraper())
