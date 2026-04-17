# Phase 6: Advanced Scrapers (TBD)

**Goal:** Incorporate handling for complex, non-HTML size chart representations like static images and PDF documents.

> **Status Insight:** As explicitly declared in the PRD, the libraries, techniques, and final structural setup for these data types remain **TBD**.

## Types Remaining

### Type C: Image-Based Charts
Some brands upload snapshot imagery containing their size charts to mitigate manual scraping or due to legacy platform capabilities.
* **Process Flow:** The initial setup dictates that Playwright will be utilized to take screenshot captures of the target areas.
* **AI Processing:** Target logic (such as passing screenshots to GPT-4 Vision, etc.) is currently **TBD**.

### Type D: PDF Guide Charts
Certain technical or legacy brands provide PDFs directly to detail their size requirements.
* **Process Flow:** Playwright will detect and download the PDF elements.
* **Parsing Pipeline:** The local parsing technology (e.g., pdfplumber) or extraction techniques to isolate structured table data from standard PDF styling is **TBD**.

## Next Iterative Blockers
To unblock Phase 6 work in the future, the following conceptual decisions must be established:
1. Decision on AI extraction service usage for imagery.
2. Tooling review for optimal PDF localized extraction strategies.
3. Defining how TBD elements intersect with the existing parser and validation layers.
