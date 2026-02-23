"""
LinkedIn Job Scraper — Part 2
==============================
Extracts company name and website URL from a LinkedIn job listing page.

Primary:  Apify actor (bebity/linkedin-jobs-scraper) — requires a free-trial or
          paid subscription.
Fallback: Direct HTTP scrape of the public LinkedIn job page.
          LinkedIn embeds the company name and job title in <title> and
          OpenGraph meta tags even for unauthenticated visitors.
"""

import logging
import os
import re
from urllib.parse import parse_qs, urlparse

import httpx
from apify_client import ApifyClient
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ACTOR_ID = "bebity/linkedin-jobs-scraper"

_DIRECT_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _normalize_linkedin_job_url(url: str) -> str:
    """
    Normalize any LinkedIn job URL variant to the canonical /jobs/view/<id> form.

    Handles:
      - /jobs/view/1234567890                 → unchanged
      - /jobs/collections/...?currentJobId=X  → /jobs/view/X
      - /jobs/search/...?currentJobId=X       → /jobs/view/X
    """
    parsed = urlparse(url)
    # Already in canonical form
    if "/jobs/view/" in parsed.path:
        return url
    # Try to extract currentJobId from query string
    qs = parse_qs(parsed.query)
    job_id = qs.get("currentJobId", [None])[0]
    if job_id:
        normalized = f"https://www.linkedin.com/jobs/view/{job_id}"
        logger.info("Normalized LinkedIn URL: %s → %s", url, normalized)
        return normalized
    # Can't normalize — return as-is and let the actor decide
    return url


def _extract_company_slug(linkedin_url: str) -> str | None:
    """
    Extract the company slug from a LinkedIn company URL.
    e.g. https://www.linkedin.com/company/stripe/ → "stripe"
    """
    match = re.search(r"/company/([^/?#]+)", linkedin_url)
    return match.group(1) if match else None


def _extract_domain(url: str) -> str | None:
    """
    Parse a full URL and return just the hostname (e.g. 'stripe.com').
    Returns None if the URL is empty or unparseable.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        host = parsed.hostname or ""
        # Strip common subdomain prefixes
        host = re.sub(r"^www\.", "", host)
        return host if host else None
    except Exception:
        return None


def _domain_from_company_name(company_name: str) -> str:
    """
    Best-effort domain guess from a company name.
    'GHR Healthcare' → 'ghrhealthcare.com'
    'Acme Corp'      → 'acmecorp.com'
    """
    slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
    return f"{slug}.com" if slug else ""


def _domain_from_og_url(og_url: str) -> str | None:
    """
    Extract the company slug from an og:url like:
      https://www.linkedin.com/jobs/view/machine-learning-engineer-at-ghr-healthcare-4362654337
    Pattern: {job-title}-at-{company-slug}-{job-id}
    Returns a guessed domain like 'ghr-healthcare.com'.
    """
    path = urlparse(og_url).path  # /jobs/view/machine-learning-engineer-at-ghr-healthcare-4362654337
    slug_part = path.split("/jobs/view/")[-1] if "/jobs/view/" in path else ""
    if not slug_part:
        return None
    # Remove trailing numeric job ID
    slug_part = re.sub(r"-\d+$", "", slug_part)
    # Split on '-at-' to get company slug
    at_parts = slug_part.split("-at-", 1)
    company_slug = at_parts[-1] if len(at_parts) > 1 else slug_part
    # Convert hyphenated slug to domain: 'ghr-healthcare' → 'ghrhealthcare.com'
    domain = re.sub(r"-", "", company_slug) + ".com"
    logger.debug("Derived domain from og:url slug '%s': %s", company_slug, domain)
    return domain


def _scrape_linkedin_direct(job_url: str) -> dict:
    """
    Fallback: directly fetch the LinkedIn job page and parse meta tags.

    LinkedIn embeds the company name + job title in <title> and og: meta tags
    for unauthenticated visitors, e.g.:
      <title>GHR Healthcare hiring Machine Learning Engineer in United States | LinkedIn</title>
      <meta property="og:url" content="https://www.linkedin.com/jobs/view/machine-learning-engineer-at-ghr-healthcare-4362654337">
    """
    logger.info("Direct LinkedIn scrape for: %s", job_url)
    try:
        r = httpx.get(
            job_url,
            headers=_DIRECT_SCRAPE_HEADERS,
            follow_redirects=True,
            timeout=15.0,
        )
        r.raise_for_status()
    except Exception as exc:
        raise ValueError(f"Direct LinkedIn GET failed: {exc}") from exc

    soup = BeautifulSoup(r.text, "html.parser")

    def meta(prop: str) -> str:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return (tag.get("content", "") if tag else "") or ""

    og_title = meta("og:title")  # "Company hiring Title in Location | LinkedIn"
    og_url   = meta("og:url")    # contains job slug with company name

    # Parse company name from title: "{Company} hiring {Title} in {Location} | LinkedIn"
    company_name = ""
    job_title = ""
    m = re.match(r"^(.+?)\s+hiring\s+(.+?)\s+in\s+.+?\s*\|", og_title)
    if m:
        company_name = m.group(1).strip()
        job_title    = m.group(2).strip()
    else:
        # Fall back to the page <title>
        title_text = soup.title.string if soup.title else ""
        m2 = re.match(r"^(.+?)\s+hiring\s+(.+?)\s+in\s+.+?\s*\|", title_text or "")
        if m2:
            company_name = m2.group(1).strip()
            job_title    = m2.group(2).strip()

    # Derive company domain from the og:url slug, fall back to name-based guess
    company_domain = _domain_from_og_url(og_url) or _domain_from_company_name(company_name)

    logger.info(
        "Direct scrape: company=%s  domain=%s  title=%s",
        company_name, company_domain, job_title,
    )

    return {
        "company_name":         company_name,
        "company_linkedin_url": "",
        "company_domain":       company_domain,
        "job_title":            job_title,
        "apply_url":            job_url,
    }


class LinkedInScraper:
    """
    Scrapes a LinkedIn job page to extract company name and domain.

    Tries Apify first; falls back to direct HTTP scraping if Apify is
    unavailable or the account does not have access to the actor.

    Usage:
        scraper = LinkedInScraper()
        result = scraper.scrape_job_page("https://www.linkedin.com/jobs/view/1234567890")
        # Returns: {"company_name": "...", "company_domain": "...", "job_title": "...", "apply_url": "..."}
    """

    def __init__(self, api_token: str | None = None) -> None:
        token = api_token or os.environ.get("APIFY_API_TOKEN")
        if not token:
            raise ValueError(
                "APIFY_API_TOKEN not set.  "
                "Add it to .env or pass api_token= explicitly."
            )
        self._client = ApifyClient(token)

    def scrape_job_page(self, linkedin_job_url: str) -> dict:
        """
        Extract company info from a LinkedIn job URL (any variant).

        Args:
            linkedin_job_url: Full LinkedIn job listing URL.

        Returns:
            dict with keys: company_name, company_linkedin_url, company_domain,
                            job_title, apply_url
        """
        # Normalize to /jobs/view/<id> — handles collections/recommended URLs
        canonical_url = _normalize_linkedin_job_url(linkedin_job_url)

        # ── Primary: Apify actor ───────────────────────────────────────────────
        try:
            return self._scrape_via_apify(canonical_url)
        except Exception as apify_exc:
            logger.warning(
                "Apify actor failed (%s) — falling back to direct LinkedIn scrape.",
                apify_exc,
            )

        # ── Fallback: direct HTTP scraping ────────────────────────────────────
        return _scrape_linkedin_direct(canonical_url)

    def _scrape_via_apify(self, canonical_url: str) -> dict:
        logger.info("Running Apify actor %s for: %s", ACTOR_ID, canonical_url)

        run_input = {
            "startUrls": [{"url": canonical_url}],
            "maxResults": 1,
        }

        run = self._client.actor(ACTOR_ID).call(run_input=run_input)

        items = list(
            self._client.dataset(run["defaultDatasetId"]).iterate_items()
        )

        if not items:
            raise ValueError(
                f"Apify actor returned no results for URL: {canonical_url}"
            )

        item = items[0]
        logger.debug("Apify raw item keys: %s", list(item.keys()))

        # Field names vary by actor — try multiple aliases in priority order.
        company_name = (
            item.get("companyName")
            or item.get("company")
            or item.get("hiringOrganization", {}).get("name", "")
            or ""
        )
        company_linkedin_url = (
            item.get("companyUrl")
            or item.get("companyLinkedinUrl")
            or item.get("companyLink")
            or ""
        )
        job_title = (
            item.get("title")
            or item.get("jobTitle")
            or item.get("positionName")
            or ""
        )
        apply_url = (
            item.get("applyUrl")
            or item.get("jobUrl")
            or item.get("url")
            or canonical_url
        )

        company_website = (
            item.get("companyWebsite")
            or item.get("websiteUrl")
            or item.get("companyDomain")
            or ""
        )
        company_domain = _extract_domain(company_website)

        if not company_domain and company_linkedin_url:
            slug = _extract_company_slug(company_linkedin_url)
            company_domain = f"{slug}.com" if slug else None

        logger.info(
            "Apify scraped: company=%s  domain=%s  title=%s",
            company_name, company_domain, job_title,
        )

        return {
            "company_name":         company_name,
            "company_linkedin_url": company_linkedin_url,
            "company_domain":       company_domain or "",
            "job_title":            job_title,
            "apply_url":            apply_url,
        }
