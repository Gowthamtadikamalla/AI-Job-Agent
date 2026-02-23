"""
Job Listing Extractor — Part 2
================================
Given a career page URL, extract at least one open job position URL.

Extraction strategies (tried in order):
  1. ATS free public APIs   — Greenhouse, Lever           (structured JSON, free)
  2. JSON-LD schema.org     — JobPosting markup           (structured, no auth)
  3. CSS selector scraping  — common link patterns        (heuristic fallback)
"""

import json
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

GET_TIMEOUT = 15.0


# ── ATS API helpers ──────────────────────────────────────────────────────────

async def _try_greenhouse(company_token: str) -> list[dict]:
    """Query the free Greenhouse boards API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_token}/jobs"
    async with httpx.AsyncClient(timeout=GET_TIMEOUT) as client:
        try:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                jobs = data.get("jobs", [])
                return [
                    {
                        "title": j.get("title", ""),
                        "url": j.get("absolute_url", ""),
                        "location": (j.get("location") or {}).get("name", ""),
                    }
                    for j in jobs
                    if j.get("absolute_url")
                ]
        except Exception as exc:
            logger.debug("Greenhouse API error for %s: %s", company_token, exc)
    return []


async def _try_ashby(company_slug: str) -> list[dict]:
    """Query the Ashby public GraphQL API for job postings."""
    url = "https://jobs.ashbyhq.com/api/non-user-graphql"
    query = """
        query ApiJobBoard($organizationHostedJobsPageName: String!) {
            jobBoard: jobBoardWithTeams(
                organizationHostedJobsPageName: $organizationHostedJobsPageName
            ) {
                jobPostings {
                    id
                    title
                    locationName
                    employmentType
                }
            }
        }
    """
    async with httpx.AsyncClient(timeout=GET_TIMEOUT) as client:
        try:
            r = await client.post(
                url,
                json={
                    "operationName": "ApiJobBoard",
                    "variables": {"organizationHostedJobsPageName": company_slug},
                    "query": query,
                },
            )
            if r.status_code == 200:
                data = r.json()
                postings = (
                    (data.get("data") or {})
                    .get("jobBoard") or {}
                ).get("jobPostings", [])
                if postings is None:
                    postings = []
                return [
                    {
                        "title": j.get("title", ""),
                        "url": f"https://jobs.ashbyhq.com/{company_slug}/{j['id']}",
                        "location": j.get("locationName", ""),
                    }
                    for j in postings
                    if j.get("id")
                ]
        except Exception as exc:
            logger.debug("Ashby API error for %s: %s", company_slug, exc)
    return []


async def _try_smartrecruiters(company_slug: str) -> list[dict]:
    """Query the free SmartRecruiters postings API."""
    url = f"https://api.smartrecruiters.com/v1/companies/{company_slug}/postings"
    async with httpx.AsyncClient(timeout=GET_TIMEOUT) as client:
        try:
            r = await client.get(url, params={"limit": 10})
            if r.status_code == 200:
                data = r.json()
                jobs = data.get("content", [])
                return [
                    {
                        "title": j.get("name", ""),
                        "url": f"https://jobs.smartrecruiters.com/{company_slug}/{j.get('id', '')}",
                        "location": (j.get("location") or {}).get("city", ""),
                    }
                    for j in jobs
                    if j.get("id")
                ]
        except Exception as exc:
            logger.debug("SmartRecruiters API error for %s: %s", company_slug, exc)
    return []


async def _try_lever(company_slug: str) -> list[dict]:
    """Query the free Lever postings API."""
    url = f"https://api.lever.co/v0/postings/{company_slug}"
    async with httpx.AsyncClient(timeout=GET_TIMEOUT) as client:
        try:
            r = await client.get(url)
            if r.status_code == 200:
                jobs = r.json()
                return [
                    {
                        "title": j.get("text", ""),
                        "url": j.get("hostedUrl", ""),
                        "location": (j.get("categories") or {}).get("location", ""),
                    }
                    for j in jobs
                    if j.get("hostedUrl")
                ]
        except Exception as exc:
            logger.debug("Lever API error for %s: %s", company_slug, exc)
    return []


# ── Strategy dispatchers ─────────────────────────────────────────────────────

def _detect_ats(career_url: str) -> tuple[str, str] | None:
    """
    Detect which ATS is hosting the career page and return (ats_name, slug).
    Returns None if the URL doesn't match a known ATS pattern.
    """
    url_lower = career_url.lower()

    m = re.search(r"boards\.greenhouse\.io/([^/?#]+)", url_lower)
    if m:
        return ("greenhouse", m.group(1))

    m = re.search(r"jobs\.lever\.co/([^/?#]+)", url_lower)
    if m:
        return ("lever", m.group(1))

    m = re.search(r"jobs\.smartrecruiters\.com/([^/?#]+)", url_lower)
    if m:
        return ("smartrecruiters", m.group(1))

    m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", url_lower)
    if m:
        return ("ashby", m.group(1))

    return None


async def _extract_via_ats_api(career_url: str) -> list[dict]:
    """Try ATS-specific free APIs based on the career page URL."""
    ats = _detect_ats(career_url)
    if not ats:
        return []

    ats_name, slug = ats
    logger.info("  Detected ATS: %s (slug=%s)", ats_name, slug)

    if ats_name == "greenhouse":
        return await _try_greenhouse(slug)
    if ats_name == "lever":
        return await _try_lever(slug)
    if ats_name == "smartrecruiters":
        return await _try_smartrecruiters(slug)
    if ats_name == "ashby":
        return await _try_ashby(slug)

    return []


async def _extract_via_json_ld(career_url: str) -> list[dict]:
    """Parse JSON-LD JobPosting schema from the career page HTML."""
    async with httpx.AsyncClient(timeout=GET_TIMEOUT) as client:
        try:
            r = await client.get(career_url, follow_redirects=True)
            if r.status_code != 200:
                return []
        except Exception as exc:
            logger.debug("JSON-LD GET failed: %s", exc)
            return []

    soup = BeautifulSoup(r.text, "html.parser")
    results: list[dict] = []

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue

        # Handle single object or @graph array
        items = data if isinstance(data, list) else [data]
        for item in items:
            if item.get("@type") == "JobPosting":
                job_url = item.get("url") or item.get("sameAs") or ""
                if job_url:
                    results.append({
                        "title": item.get("title", ""),
                        "url": job_url,
                        "location": (item.get("jobLocation") or {}).get("name", ""),
                    })

    return results


async def _extract_via_css(career_url: str) -> list[dict]:
    """Heuristic CSS scraping: find links on the career page that look like job listings."""
    async with httpx.AsyncClient(timeout=GET_TIMEOUT) as client:
        try:
            r = await client.get(career_url, follow_redirects=True)
            if r.status_code != 200:
                return []
        except Exception as exc:
            logger.debug("CSS scraping GET failed: %s", exc)
            return []

    soup = BeautifulSoup(r.text, "html.parser")
    base_domain = urlparse(str(r.url)).netloc
    results: list[dict] = []

    # Look for links that contain job-like keywords in their href or text
    # Note: match /jobs/ (plural) as well as /job/, /posting/, etc.
    job_path_kw = re.compile(r"/(jobs?|position|opening|posting|role|career)/", re.I)
    job_text_kw = re.compile(
        r"\b(engineer|developer|manager|analyst|designer|scientist|director|lead|intern)\b",
        re.I,
    )

    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#"):
            continue

        full_url = urljoin(str(r.url), href)
        parsed = urlparse(full_url)

        # Only follow links on the same domain or known ATS domains
        link_domain = parsed.netloc
        known_ats = any(
            ats in link_domain
            for ats in ["lever.co", "greenhouse.io", "workday.com", "smartrecruiters.com"]
        )
        if link_domain not in (base_domain, f"www.{base_domain}") and not known_ats:
            continue

        text = a.get_text(strip=True)
        if (job_path_kw.search(parsed.path) or job_text_kw.search(text)) and full_url not in seen:
            seen.add(full_url)
            results.append({"title": text, "url": full_url, "location": ""})

    return results


# ── Public API ────────────────────────────────────────────────────────────────

async def get_open_positions(career_url: str) -> list[dict]:
    """
    Return a list of open job positions from the given career page URL.
    Each entry has keys: title, url, location.

    Strategies are tried in order until results are found.
    """
    # Strategy 1: ATS-specific free API
    logger.info("[Job Extractor] Strategy 1: ATS API for %s", career_url)
    jobs = await _extract_via_ats_api(career_url)
    if jobs:
        logger.info("  Found %d jobs via ATS API.", len(jobs))
        return jobs

    # Strategy 2: JSON-LD schema.org
    logger.info("[Job Extractor] Strategy 2: JSON-LD for %s", career_url)
    jobs = await _extract_via_json_ld(career_url)
    if jobs:
        logger.info("  Found %d jobs via JSON-LD.", len(jobs))
        return jobs

    # Strategy 3: CSS heuristic scraping
    logger.info("[Job Extractor] Strategy 3: CSS scraping for %s", career_url)
    jobs = await _extract_via_css(career_url)
    if jobs:
        logger.info("  Found %d jobs via CSS scraping.", len(jobs))
        return jobs

    logger.warning("No open positions found at %s", career_url)
    return []
