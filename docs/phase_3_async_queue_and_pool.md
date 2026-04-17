# Phase 3: Async Queue, Browser Pool & Limiting

**Goal:** Transform the single-shot scraper into a robust, concurrent system capable of processing multiple brands while respecting system limits and domain delays.

> **Learning Objective:** Advanced `asyncio` (Queues, Semaphores), exponential backoff, resource management via `psutil`, and concurrent pool management.

## What Gets Built
A task queue that manages browser instances. It handles crashes, regulates domain request rates, and dynamically monitors hardware memory to avoid overloading the system.

## Deliverables

### 1. `queue_manager.py`
The orchestration engine for the scraping jobs.
- **Queue Generation:** Loads brands list, excludes blocked, and seeds the `asyncio.Queue`.
- **Browser Pool:** Maintains 2 browser instances with up to 8 tabs each (as per PRD configurations).
- **Domain Rate Limiting:** Enforces `min_delay` and `max_concurrent` settings per domain.
- **Resource Monitoring:** Uses `psutil` to track memory (e.g., restart tabs/browsers if RAM > 80%).

### 2. Configurations Integration
Applying the limits from the PRD setup:
- `base_delay`, `delay_multiplier`, `max_delay`
- Retry logic for status codes like `429`, `502`, `503`.
- Stricter limits applied for explicitly marked `high_risk` domains.

### 3. Updated `main.py`
Refactored to initialize the `queue_manager`, spin up the event loop, and gracefully await jobs to drain.

## Acceptance Criteria
- [ ] Multiple brands are scraped concurrently up to the configured limits.
- [ ] A simulated HTTP 429 correctly triggers the exponential backoff logic.
- [ ] Memory threshold logs demonstrate `psutil` integration, pausing tab creation when limit hit.
- [ ] Intentional browser tab crashes are caught, logged, and trigger a retry without stopping the parent process.
