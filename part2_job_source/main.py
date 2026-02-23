"""
Part 2 — AI Job Source Agent
=============================
CLI entry point.

Usage:
    python main.py "https://www.linkedin.com/jobs/view/1234567890"

Output (printed to stdout):
    Company Name, https://careers.company.com, https://lever.co/company/job-id

Pipeline:
    1. Scrape LinkedIn job URL via Apify → company name + domain
    2. Multi-strategy cascade → career page URL
    3. ATS API / JSON-LD / CSS scraping → first open position URL
"""

import asyncio
import logging
import sys

from dotenv import load_dotenv

from part2_job_source.career_finder import find_career_page
from part2_job_source.job_extractor import get_open_positions
from part2_job_source.linkedin_scraper import LinkedInScraper

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


async def run_pipeline(linkedin_job_url: str) -> None:
    print(f"\n{'='*60}")
    print(f"AI Job Source Agent")
    print(f"Input: {linkedin_job_url}")
    print(f"{'='*60}\n")

    # ── Step 1: LinkedIn scraping ──────────────────────────────────────────
    print("Step 1/3  Scraping LinkedIn job listing via Apify…")
    scraper = LinkedInScraper()
    company_info = scraper.scrape_job_page(linkedin_job_url)

    company_name = company_info["company_name"]
    company_domain = company_info["company_domain"]
    # Build the full company website URL from the domain for display and pipeline use.
    company_website_url = f"https://www.{company_domain}" if company_domain else ""

    print(f"  Company     : {company_name}")
    print(f"  Website URL : {company_website_url}")
    print()

    if not company_domain:
        print("ERROR: Could not determine company website URL from LinkedIn data.")
        sys.exit(1)

    # ── Step 2: Career page discovery ─────────────────────────────────────
    # Pass the domain to the career finder; it navigates from the company
    # website URL to the career page using a multi-strategy cascade
    # (ATS patterns → URL probing → sitemap → AI browser agent).
    print("Step 2/3  Discovering career page (web agent cascade)…")
    career_result = await find_career_page(company_domain)

    career_url = career_result["career_url"]
    strategy = career_result["strategy"]
    confidence = career_result["confidence"]

    print(f"  Career URL : {career_url}")
    print(f"  Strategy   : {strategy}  (confidence {confidence:.0%})")
    print()

    if not career_url:
        print("ERROR: Career page not found after all strategies.")
        sys.exit(1)

    # ── Step 3: Open position extraction ──────────────────────────────────
    print("Step 3/3  Extracting open positions…")
    positions = await get_open_positions(career_url)

    if not positions:
        print("ERROR: No open positions found on the career page.")
        sys.exit(1)

    first = positions[0]
    position_url = first["url"]
    position_title = first["title"] or "Unknown title"

    print(f"  First position : {position_title}")
    print(f"  Position URL   : {position_url}")
    print()

    # ── Final output ───────────────────────────────────────────────────────
    print("─" * 60)
    print("RESULT:")
    print(f"  {company_name}, {career_url}, {position_url}")
    print("─" * 60)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python main.py <linkedin_job_url>")
        print('Example: python main.py "https://www.linkedin.com/jobs/view/1234567890"')
        sys.exit(1)

    linkedin_url = sys.argv[1]
    asyncio.run(run_pipeline(linkedin_url))


if __name__ == "__main__":
    main()
