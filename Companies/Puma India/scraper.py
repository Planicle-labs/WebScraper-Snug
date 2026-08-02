import asyncio
import json
import math
import os
import logging
import time
import re
import httpx

# ── Logger ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("PumaScraper")

# ── Config ───────────────────────────────────────────────────────────────────
SITE_BASE   = "https://in.puma.com"
API_URL     = f"{SITE_BASE}/api/graphql"
SLAS_URL    = "https://in.puma.com/api/shopper/auth/v1/sessions"

# Puma India SLAS client config (from their frontend JS)
SLAS_CLIENT_ID  = "8c233633-746e-4479-b49e-bd9b4c43f199"
SLAS_CHANNEL_ID = "IN"

LIMIT       = 24           # Puma's page size (don't change, their API may reject higher)
CONCURRENCY = 3
MAX_PAGES   = 200

# Category location path for men's t-shirts & tops
LOCATION = (
    "categories<{catalog01_in}"
    "/categories<{catalog01_in_in0mens}"
    "/categories<{catalog01_in_in0mens_in0mens0clothing}"
    "/categories<{catalog01_in_in0mens_in0mens0clothing_in0mens0clothing0t0shirts0and0tops}/"
)

OUTPUT_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "puma_men_tshirts.json")

# GraphQL query (trimmed to only what we need)
GQL_QUERY = """
query searchProducts($location: String!, $limit: Int!, $startIndex: Int!, $sort: String!, $customAttributes: [ProductSearchInputCustom!], $context: ProductSearchInputContext!) {
  searchProducts(
    input: {location: $location, startIndex: $startIndex, limit: $limit, sort: $sort, customAttributes: $customAttributes, context: $context}
  ) {
    itemsSection {
      results {
        totalItems
        startIndex
        viewSize
      }
      items {
        productSearchHit {
          masterId
          id
          masterProduct {
            id
            name
          }
        }
      }
    }
  }
}
"""

BASE_HEADERS = {
    "Accept": "application/graphql-response+json, application/graphql+json, application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": SITE_BASE,
    "Referer": f"{SITE_BASE}/in/en/mens/mens-clothing/mens-clothing-t-shirts-and-tops",
    "locale": "en-IN",
    "customer-group": "7332baf5c5d654112b7e574da6955f1545349655d513427c7a0d25b77f8f781d",
    "puma-request-source": "web",
    "x-graphql-client-name": "nitro-fe",
    "x-graphql-client-version": "b1b7b95d812017f39fa943c26d3774b80d22ccc8",
    "x-operation-name": "searchProducts",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Mobile Safari/537.36"
    ),
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
}


# ── Token management ──────────────────────────────────────────────────────────

class TokenManager:
    """Fetches and caches the Puma guest JWT. Auto-refreshes before expiry."""

    def __init__(self):
        self._token: str | None = None
        self._expires_at: float = 0

    async def get_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        await self._fetch(client)
        return self._token

    async def _fetch(self, client: httpx.AsyncClient):
        logger.info("Fetching guest JWT from Puma SLAS …")
        try:
            # Step 1: get usid + code_challenge via guest session
            resp = await client.post(
                SLAS_URL,
                json={
                    "type": "guest",
                    "channel_id": SLAS_CHANNEL_ID,
                    "client_id": SLAS_CLIENT_ID,
                },
                headers={
                    "Content-Type": "application/json",
                    "Origin": SITE_BASE,
                    "Referer": f"{SITE_BASE}/",
                    "User-Agent": BASE_HEADERS["User-Agent"],
                },
                timeout=20,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()

            token = data.get("access_token") or data.get("token")
            expires_in = data.get("expires_in", 1800)

            if not token:
                # Fallback: try Authorization header returned
                auth = resp.headers.get("authorization", "")
                if auth.startswith("Bearer "):
                    token = auth[7:]

            if not token:
                raise ValueError(f"No token in response: {list(data.keys())}")

            self._token = token
            self._expires_at = time.time() + expires_in
            logger.info(f"JWT obtained, expires in {expires_in}s")

        except Exception as e:
            logger.error(f"Token fetch failed: {e}")
            logger.warning("Falling back to hardcoded token — may be expired")
            # Fallback to the token captured from DevTools (will work for ~30min)
            self._token = (
                "eyJ2ZXIiOiIxLjAiLCJqa3UiOiJzbGFzL3Byb2QvYmN3cl9wcmQiLCJraWQiOiI4MmI2YTg4My1iZjUyLTRmNzctOGJiNi05ZWY2NjdkNDgwYzkiLCJ0eXAiOiJqd3QiLCJjbHYiOiJKMi4zLjQiLCJhbGciOiJFUzI1NiJ9.eyJhdXQiOiJHVUlEIiwic2NwIjoic2ZjYy5zaG9wcGVyLW15YWNjb3VudC5iYXNrZXRzIGNfZ2lmdGNhcmRiYWxhbmNlIHNmY2Muc2hvcHBlci1teWFjY291bnQucGF5bWVudGluc3RydW1lbnRzIGNfcG9zdGFsQ29kZUluZm9fciBzZmNjLnNob3BwZXItY3VzdG9tZXJzLmxvZ2luIHNmY2Muc2hvcHBlci1teWFjY291bnQub3JkZXJzIGNfZW1haWxTbXNUcmlnZ2VyIHNmY2Muc2hvcHBlci1wcm9kdWN0bGlzdHMgc2ZjYy5zaG9wcGVyLXByb21vdGlvbnMgc2ZjYy5zaG9wcGVyLW15YWNjb3VudC5wYXltZW50aW5zdHJ1bWVudHMucncgc2ZjYy5zaG9wcGVyLW15YWNjb3VudC5wcm9kdWN0bGlzdHMgc2ZjYy5zaG9wcGVyLWNhdGVnb3JpZXMgc2ZjYy5zaG9wcGVyLW15YWNjb3VudCBzZmNjLnNob3BwZXItbXlhY2NvdW50LmFkZHJlc3NlcyBzZmNjLnNob3BwZXItcHJvZHVjdHMgc2ZjYy5zaG9wcGVyLW15YWNjb3VudC5ydyBjX2hlYWRsZXNzQ3VzdG9tQXBpIHNmY2Muc2hvcHBlci1jb250ZXh0LnJ3IHNmY2Muc2hvcHBlci1jdXN0b21lcnMucmVnaXN0ZXIgc2ZjYy5zaG9wcGVyLWJhc2tldHMtb3JkZXJzIHNmY2Muc2hvcHBlci1teWFjY291bnQuYWRkcmVzc2VzLnJ3IHNmY2Muc2hvcHBlci1teWFjY291bnQucHJvZHVjdGxpc3RzLnJ3IHNmY2Muc2hvcHBlci1iYXNrZXRzLW9yZGVycy5ydyBjX3ByaWNpbmdBbmRQcm9tb3Rpb25zX3Igc2ZjYy5zaG9wcGVyLWdpZnQtY2VydGlmaWNhdGVzIHNmY2Muc2hvcHBlci1wcm9kdWN0LXNlYXJjaCIsInN1YiI6ImNjLXNsYXM6OmJjd3JfcHJkOjpzY2lkOjhjMjMzNjMzLTc0NmUtNDQ3OS1iNDllLWJkOWI0YzQzZjE5OTo6dXNpZDo2NjIxMDU2Ny1iNWQ4LTRlYWMtODNhMi02ODU1MTMxMDJmZjkiLCJzc2MiOiIzbm16bzZtZSIsImN0eCI6InNsYXMiLCJpc3MiOiJzbGFzL3Byb2QvYmN3cl9wcmQiLCJpc3QiOjEsImRudCI6IjAiLCJhdWQiOiJjb21tZXJjZWNsb3VkL3Byb2QvYmN3cl9wcmQiLCJuYmYiOjE3Nzg1Mjk2NzksInN0eSI6IlVzZXIiLCJpc2IiOiJ1aWRvOnNsYXM6OnVwbjpHdWVzdDo6dWlkbjpHdWVzdCBVc2VyOjpnY2lkOmJkbEh3V2tyYVpsSEFSd0h0SW1iWVl4dWRIOjpjaGlkOklOIiwiZXhwIjoxNzc4NTMxNTA5LCJpYXQiOjE3Nzg1Mjk3MDksImp0aSI6IkMyQy0xNDcwNTc4NzY1MDQ0ODA4OTQ0MjIwODM5MDcwOTU2Njk3MzI2In0.Pu8s7czc18lV950afiewM_byHwaMb9pffz9M6aNF8UomLEmqbChEp4Pb1SfNXGHrYYb5WLmJg0VIR0vNM4QQRg"
            )
            self._expires_at = time.time() + 300


token_manager = TokenManager()


# ── Helpers ───────────────────────────────────────────────────────────────────

def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def save_products(urls: list[str]):
    ensure_dirs()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(urls, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(urls)} product URLs → {OUTPUT_FILE}")


def build_url(item: dict) -> str | None:
    hit = item.get("productSearchHit", {})
    master = hit.get("masterProduct", {})
    master_id = hit.get("masterId") or hit.get("id")
    name = master.get("name", "")
    
    if master_id and name:
        slug = name.lower().strip()
        slug = slug.replace(" ", "-")
        # remove special chars except hyphens
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        slug = re.sub(r"-+", "-", slug)  # collapse multiple hyphens
        return f"{SITE_BASE}/in/en/pd/{slug}/{master_id}?swatch=01"
    
    if master_id:
        return f"{SITE_BASE}/in/en/pd/{master_id}?swatch=01"
    
    return None


def build_payload(start_index: int) -> dict:
    return {
        "operationName": "searchProducts",
        "query": GQL_QUERY,
        "variables": {
            "context": {"platform": "web", "view": "lister"},
            "limit": LIMIT,
            "location": LOCATION,
            "sort": "default",
            "startIndex": start_index,
            "customAttributes": None,
        },
    }


# ── Core fetch ────────────────────────────────────────────────────────────────

async def fetch_page(client: httpx.AsyncClient, start_index: int) -> dict | None:
    token = await token_manager.get_token(client)
    headers = {**BASE_HEADERS, "authorization": f"Bearer {token}"}
    try:
        resp = await client.post(
            API_URL,
            json=build_payload(start_index),
            headers=headers,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"startIndex={start_index} HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        logger.error(f"startIndex={start_index} error: {e}")
    return None


def extract_items(data: dict) -> list[dict]:
    return (
        data.get("data", {})
            .get("searchProducts", {})
            .get("itemsSection", {})
            .get("items", [])
        or []
    )


def get_total(data: dict) -> int:
    return (
        data.get("data", {})
            .get("searchProducts", {})
            .get("itemsSection", {})
            .get("results", {})
            .get("totalItems", 0)
        or 0
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def scrape_all() -> list[str]:
    ensure_dirs()
    all_urls: list[str] = []
    seen: set[str] = set()

    async with httpx.AsyncClient() as client:
        # Step 1: fetch first page to get total
        logger.info("Fetching startIndex=0 to get total count …")
        data = await fetch_page(client, start_index=0)
        if data is None:
            logger.error("Failed to fetch startIndex=0 — aborting")
            return []

        total = get_total(data)
        logger.info(f"Total products: {total}")

        for item in extract_items(data):
            url = build_url(item)
            if url and url not in seen:
                seen.add(url)
                all_urls.append(url)
        logger.info(f"startIndex=0: {len(all_urls)} URLs")

        total_pages = math.ceil(total / LIMIT)
        total_pages = min(total_pages, MAX_PAGES)
        logger.info(f"Total pages: {total_pages}")

        # Step 2: remaining pages concurrently
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def fetch_and_collect(start_index: int):
            async with semaphore:
                logger.info(f"Fetching startIndex={start_index}/{total} …")
                d = await fetch_page(client, start_index=start_index)
                return start_index, d

        offsets = list(range(LIMIT, total, LIMIT))
        results = await asyncio.gather(*[fetch_and_collect(s) for s in offsets])

        for start_index, d in sorted(results, key=lambda x: x[0]):
            if d is None:
                continue
            items = extract_items(d)
            if not items:
                continue
            new = 0
            for item in items:
                url = build_url(item)
                if url and url not in seen:
                    seen.add(url)
                    all_urls.append(url)
                    new += 1
            logger.info(f"startIndex={start_index}: +{new} new URLs (total: {len(all_urls)})")

    logger.info(f"Done. Total unique product URLs: {len(all_urls)}")
    save_products(all_urls)
    return all_urls


if __name__ == "__main__":
    asyncio.run(scrape_all())