import sqlite3
import os
import time
from urllib.parse import urlparse
import asyncio
from core.logger import logger

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
DB_PATH = os.path.join(_ROOT, "metrics.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT,
            url TEXT,
            domain TEXT,
            requests_count INTEGER,
            bandwidth_bytes INTEGER,
            execution_time_sec REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class MetricsTracker:
    def __init__(self, stage: str, url: str):
        self.stage = stage
        self.url = url
        self.domain = urlparse(url).netloc
        self.requests_count = 0
        self.bandwidth_bytes = 0
        self.start_time = time.perf_counter()
        self._lock = asyncio.Lock()

    async def on_request(self, request):
        async with self._lock:
            self.requests_count += 1
            # Rough estimate for request headers length
            headers_size = sum(len(k) + len(v) + 4 for k, v in request.headers.items())
            self.bandwidth_bytes += headers_size + len(request.url)

    async def on_response(self, response):
        async with self._lock:
            # Add header sizes
            headers_size = sum(len(k) + len(v) + 4 for k, v in response.headers.items())
            self.bandwidth_bytes += headers_size
            
            # Content length is the most reliable way to get body size without reading it
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit():
                self.bandwidth_bytes += int(content_length)
            else:
                # If chunked or no content-length, we could try to read body, but it might interfere
                # We will just accept the underestimation to avoid side effects
                pass

    def attach_to_page(self, page):
        page.on("request", self.on_request)
        page.on("response", self.on_response)
        
    def attach_to_context(self, context):
        context.on("request", self.on_request)
        context.on("response", self.on_response)

    def save(self):
        execution_time = time.perf_counter() - self.start_time
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO metrics (stage, url, domain, requests_count, bandwidth_bytes, execution_time_sec)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (self.stage, self.url, self.domain, self.requests_count, self.bandwidth_bytes, execution_time))
            conn.commit()
            conn.close()
            logger.info(f"[Metrics] Saved {self.stage} for {self.domain}: {self.requests_count} reqs, "
                        f"{self.bandwidth_bytes / 1024:.2f} KB, {execution_time:.2f}s")
        except Exception as e:
            logger.error(f"[Metrics] Error saving to db: {e}")
