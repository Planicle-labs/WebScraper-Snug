import asyncio
import json
import os
import random
import re
import sys
from urllib.parse import urlparse, urljoin, urlunparse, parse_qs, urlencode, urlsplit, urlunsplit

# ── Logger ──────────────────────────────────────────────────────────────────
try:
    from core.logger import logger
    from core.metrics import MetricsTracker
except ImportError:
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("ProductDiscovery")
    MetricsTracker = None

# ── Constants ────────────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Output goes to the shared root outputs/ folder so Stage 2 (page_search) can read it.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)  # project root
OUTPUT_DIR = os.path.join(_ROOT, "outputs")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "product_pages.json")

# URL path segments that strongly indicate a product detail page
PRODUCT_PATH_PATTERNS = [
    r"/p/",            # Uniqlo: /uk/en/products/E12345-000/
    r"/products?/",    # Shopify / generic
    r"/item/",
    r"/items/",
    r"/pd/",
    r"/detail",
    r"/[a-z0-9-]+-\d{5,}",  # SKU-style slugs with numeric codes
]
PRODUCT_PATH_RE = re.compile("|".join(PRODUCT_PATH_PATTERNS), re.IGNORECASE)

# URL patterns that are clearly NOT product pages — skip these
IGNORE_PATH_RE = re.compile(
    r"/(cart|checkout|account|login|signup|register|wishlist|search|help|about|contact|faq|policy|blog|news|"
    r"size-guide|size_guide|sizeguide|gift|store|stores)(/|$)",
    re.IGNORECASE,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def save_products(products: list[str]):
    ensure_dirs()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(products)} product URLs → {OUTPUT_FILE}")


def normalise_url(href: str, base_domain: str) -> str | None:
    """
    Turn href into an absolute, normalised URL.
    Returns None if it should be discarded.
    """
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None

    # Make absolute
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = base_domain + href

    # Drop query string & fragment — we only want the canonical product URL
    try:
        parsed = urlparse(href)
    except Exception:
        return None

    # Must be on the same host
    base_host = urlparse(base_domain).netloc
    if parsed.netloc and parsed.netloc != base_host:
        return None

    clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
    return clean


def is_product_url(url: str) -> bool:
    """Heuristic: does this URL look like an individual product page?"""
    path = urlparse(url).path
    if IGNORE_PATH_RE.search(path):
        return False
    return bool(PRODUCT_PATH_RE.search(path))


# ── Core scraping helpers ────────────────────────────────────────────────────

async def wait_for_products(page, timeout: int = 3000) -> bool:
    """Wait until at least one product-looking element appears."""
    selectors = [
        "[class*='product']",
        "[class*='item']",
        "article",
        "li[class*='product']",
        "ul > li",
    ]
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def scroll_to_load(page, pause: float = 0.2):
    """
    Scroll incrementally to trigger lazy-loaded products on infinite-scroll
    or lazy-render pages.
    """
    prev_height = await page.evaluate("document.body.scrollHeight")
    while True:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(pause)
        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height == prev_height:
            break
        prev_height = new_height


async def collect_product_links(page, base_domain: str) -> list[str]:
    """
    Harvest all unique product URLs visible on the current page.
    Uses multiple selector strategies to be brand-agnostic.
    """
    seen: set[str] = set()
    found: list[str] = []

    # --- Strategy 1: all <a> tags ---
    hrefs: list[str] = await page.evaluate(
        "Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href'))"
    )

    for href in hrefs:
        url = normalise_url(href, base_domain)
        if url and is_product_url(url) and url not in seen:
            seen.add(url)
            found.append(url)

    # --- Strategy 2: product cards (anchors inside product containers) ---
    card_selectors = [
        "[class*='product'] a",
        "[class*='ProductCard'] a",
        "[class*='product-card'] a",
        "[class*='item'] a",
        "article a",
        "li[class*='product'] a",
    ]
    for sel in card_selectors:
        try:
            hrefs2: list[str] = await page.evaluate(
                f"Array.from(document.querySelectorAll('{sel}')).map(a => a.getAttribute('href'))"
            )
            for href in hrefs2:
                url = normalise_url(href, base_domain)
                if url and url not in seen:
                    # For card links we accept any non-ignored URL since the
                    # card context already implies it's a product
                    path = urlparse(url).path
                    if not IGNORE_PATH_RE.search(path):
                        seen.add(url)
                        found.append(url)
        except Exception:
            continue

    return found


# ── Pagination strategies ────────────────────────────────────────────────────

# --- Strategy A: Click a "Next" button ---

NEXT_BUTTON_SELECTORS = [
    # Aria labels (case-insensitive handled by CSS :i flag isn't available, so we list variants)
    "a[aria-label='Next page']",
    "a[aria-label='Next Page']",
    "a[aria-label='next page']",
    "button[aria-label='Next page']",
    "button[aria-label='Next Page']",
    "a[aria-label='Next']",
    "button[aria-label='Next']",
    # Text content
    "a:has-text('Next')",
    "button:has-text('Next')",
    "a:has-text('›')",
    "a:has-text('»')",
    "a:has-text('>')",
    # Class-based
    "[class*='pagination'] a[class*='next']",
    "[class*='pagination'] button[class*='next']",
    "[class*='pager'] a[class*='next']",
    "li[class*='next'] a",
    "li.next a",
    # Rel attribute
    "a[rel='next']",
]


async def click_next_button(page) -> bool:
    """Try to click a Next-page button. Returns True if succeeded."""
    for sel in NEXT_BUTTON_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                # Make sure it's not disabled
                disabled = await btn.get_attribute("disabled")
                aria_disabled = await btn.get_attribute("aria-disabled")
                class_attr = await btn.get_attribute("class") or ""
                if disabled is not None:
                    continue
                if aria_disabled == "true":
                    continue
                if "disabled" in class_attr.lower():
                    continue
                await btn.scroll_into_view_if_needed()
                await btn.click()
                return True
        except Exception:
            continue
    return False


# --- Strategy B: URL-based page number increment ---

def build_next_page_url(current_url: str, page_num: int) -> str | None:
    """
    Attempt to construct the URL for the next page by:
      1. Incrementing a known ?page= / ?p= / ?start= query param
      2. Incrementing a page number in the URL path
    Returns None if no pattern is detected.
    """
    parsed = urlparse(current_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)

    # ── Query param patterns ──────────────────────────────────────────────
    for param in ("page", "p", "pg", "pageNumber", "Page", "pageNo"):
        if param in qs:
            try:
                next_val = str(int(qs[param][0]) + 1)
                new_qs = dict(qs)
                new_qs[param] = [next_val]
                new_query = urlencode(new_qs, doseq=True)
                return urlunparse(parsed._replace(query=new_query))
            except ValueError:
                pass

    # ── start= / offset= (e.g. ?start=0&sz=24 → ?start=24&sz=24) ─────────
    for param in ("start", "offset"):
        if param in qs:
            sz_candidates = [v for k, v in qs.items() if k in ("sz", "size", "limit", "pageSize")]
            sz = int(sz_candidates[0][0]) if sz_candidates else 24
            try:
                next_val = str(int(qs[param][0]) + sz)
                new_qs = dict(qs)
                new_qs[param] = [next_val]
                new_query = urlencode(new_qs, doseq=True)
                return urlunparse(parsed._replace(query=new_query))
            except ValueError:
                pass

    # If no query param found and this is page 1, try appending ?page=2
    if page_num == 2 and not parsed.query:
        return current_url + "?page=2"

    # ── Path-based page number ────────────────────────────────────────────
    path_page_re = re.compile(r"([-/])(\d+)(/?)$")
    match = path_page_re.search(parsed.path)
    if match:
        try:
            next_num = int(match.group(2)) + 1
            new_path = parsed.path[: match.start()] + match.group(1) + str(next_num) + match.group(3)
            return urlunparse(parsed._replace(path=new_path))
        except ValueError:
            pass

    return None


async def scrape_category(
    category_url: str,
    max_pages: int = 50,
    delay_between_pages: float = 0.0,
    stealth_enabled: bool = True,
) -> list[str]:
    """
    Scrape product URLs from category listing pages.
    Returns unique product URLs found across all pagination pages.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
        return []

    try:
        from playwright_stealth.stealth import Stealth
        stealth = Stealth()
        use_stealth = True
    except ImportError:
        logger.warning("playwright-stealth not installed — proceeding without stealth")
        stealth = None
        use_stealth = False

    ensure_dirs()

    parsed = urlparse(category_url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"

    all_products: list[str] = []
    seen_products: set[str] = set()
    logger.info(f"Starting product discovery: {category_url}")
    logger.info(f"Max pages: {max_pages} | Base domain: {base_domain}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
        )
        page = await context.new_page()

        if use_stealth:
            await stealth.apply_stealth_async(page)

        tracker = MetricsTracker(stage="ProductDiscovery", url=category_url) if MetricsTracker else None
        if tracker:
            tracker.attach_to_context(context)

        try:
            await page.goto(category_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_selector("[class*='product'], [class*='item'], article, ul", timeout=3000)
            except Exception:
                pass

            current_url = category_url
            empty_count = 0

            for page_num in range(1, max_pages + 1):
                await asyncio.sleep(0.05)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(0.05)

                products = await collect_product_links(page, base_domain)
                new_count = 0
                for url in products:
                    if url not in seen_products:
                        seen_products.add(url)
                        all_products.append(url)
                        new_count += 1

                if new_count > 0:
                    empty_count = 0
                else:
                    empty_count += 1

                logger.info(f"[Page {page_num}] +{new_count} products (total: {len(all_products)})")

                if empty_count >= 3:
                    logger.info("No more products found")
                    break

                if page_num >= max_pages:
                    break

                clicked = await click_next_button(page)
                if clicked:
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                        current_url = page.url
                    except Exception:
                        break
                else:
                    next_url = build_next_page_url(current_url, page_num + 1)
                    if next_url and next_url != current_url:
                        try:
                            await page.goto(next_url, wait_until="domcontentloaded", timeout=20000)
                            current_url = next_url
                        except Exception:
                            break
                    else:
                        break

        except Exception as e:
            logger.error(f"Error during scraping: {e}")
        finally:
            if tracker:
                tracker.save()
            await browser.close()

    save_products(all_products)
    logger.info(f"Discovery complete. Total product URLs: {len(all_products)}")
    return all_products


# ── Config loading ────────────────────────────────────────────────────────────

CONFIG_FILE = os.path.join(_THIS_DIR, "config.json")


def load_config() -> list[dict]:
    """Load scrape targets from config.json."""
    if not os.path.exists(CONFIG_FILE):
        return []
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


# ── Entry point ───────────────────────────────────────────────────────────────
#
# Priority order:
#   1. CLI args   → python -m product_discovery.scraper <url> [max_pages]
#   2. config.json → just run:  python -m product_discovery.scraper

async def main():
    # ── Option 1: CLI override ────────────────────────────────────────────
    if len(sys.argv) >= 2:
        category_url = sys.argv[1]
        max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        logger.info(f"Using CLI-provided URL: {category_url}")
        await scrape_category(category_url, max_pages)
        return

    # ── Option 2: read from config.json ──────────────────────────────────
    entries = load_config()
    if not entries:
        logger.error(
            f"No URL provided and config.json is empty or missing.\n"
            f"  • Either add an entry to: {CONFIG_FILE}\n"
            f"  • Or run:  python -m product_discovery.scraper <url> [max_pages]"
        )
        return

    # Run each entry in config.json sequentially
    for entry in entries:
        category_url = entry.get("category_url", "").strip()
        brand       = entry.get("brand_name", "Unknown")
        category    = entry.get("category", "")
        max_pages   = int(entry.get("max_pages", 50))

        if not category_url:
            logger.warning(f"Skipping entry with no category_url: {entry}")
            continue

        logger.info(f"--- Starting: {brand} / {category} ---")
        await scrape_category(category_url, max_pages)


if __name__ == "__main__":
    asyncio.run(main())

