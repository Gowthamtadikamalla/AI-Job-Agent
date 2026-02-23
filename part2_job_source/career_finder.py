"""
Career Page Finder — Part 2
============================
Multi-strategy cascade to locate a company's career page URL given
its domain name.

Strategy success rates (cumulative):
  1. ATS pattern matching      ~60%    (free, <100 ms)
  2. Direct URL probing        +20%    (free, <1 s)
  3. Sitemap XML parsing       +15%    (free, <2 s)
  4. AI browser agent          + 5%    (paid LLM, ~30 s)
                           = ~100%
"""

import asyncio
import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Timeout settings ─────────────────────────────────────────────────────────
HEAD_TIMEOUT = 8.0   # seconds for HEAD / fast GET requests
GET_TIMEOUT  = 12.0  # seconds for full page GET requests

# ── ATS patterns ─────────────────────────────────────────────────────────────
# Map ATS name → URL template (use {slug} as placeholder for company slug)
ATS_PATTERNS: list[tuple[str, str]] = [
    ("greenhouse",      "https://boards.greenhouse.io/{slug}"),
    ("lever",           "https://jobs.lever.co/{slug}"),
    ("workday_wd1",     "https://{slug}.wd1.myworkdayjobs.com"),
    ("workday_wd3",     "https://{slug}.wd3.myworkdayjobs.com"),
    ("workday_wd5",     "https://{slug}.wd5.myworkdayjobs.com"),
    ("smartrecruiters", "https://jobs.smartrecruiters.com/{slug}"),
    ("ashby",           "https://jobs.ashbyhq.com/{slug}"),
    ("rippling",        "https://{slug}.rippling.com/jobs"),
]

# ── Common career path suffixes ───────────────────────────────────────────────
CAREER_PATHS = [
    "/careers",
    "/jobs",
    "/join-us",
    "/join",
    "/work-with-us",
    "/opportunities",
    "/open-positions",
    "/about/careers",
    "/about-us/careers",
    "/company/careers",
]

CAREER_SUBDOMAINS = ["careers", "jobs"]

# Keywords that indicate a page is a careers / jobs listing page.
CAREER_KEYWORDS = [
    "job openings",
    "career opportunities",
    "we're hiring",
    "we are hiring",
    "open positions",
    "apply now",
    "current openings",
    "join our team",
    "work with us",
]


def _slug_from_domain(domain: str) -> str:
    """'stripe.com' → 'stripe'"""
    return re.sub(r"\.[^.]+$", "", domain.split(".")[-2] if domain.count(".") > 1 else domain)


def _looks_like_career_page(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in CAREER_KEYWORDS)


async def _head_ok(client: httpx.AsyncClient, url: str, slug_check: str | None = None) -> bool:
    """
    Return True if a HEAD request to *url* returns HTTP 200.

    If *slug_check* is given the final (post-redirect) URL must still contain
    that string — this prevents accepting redirects to an ATS homepage when
    the company slug doesn't actually exist on that platform.
    """
    try:
        r = await client.head(url, follow_redirects=True, timeout=HEAD_TIMEOUT)
        if r.status_code != 200:
            return False
        if slug_check and slug_check.lower() not in str(r.url).lower():
            return False
        return True
    except Exception:
        return False


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def _get_ok(client: httpx.AsyncClient, url: str) -> tuple[bool, str, str]:
    """
    Return (success, final_url, body_text).
    success=True when status==200.
    """
    try:
        r = await client.get(url, follow_redirects=True, timeout=GET_TIMEOUT, headers=_BROWSER_HEADERS)
        if r.status_code == 200:
            return True, str(r.url), r.text
        return False, url, ""
    except Exception:
        return False, url, ""


# ── ATS-specific org verification ────────────────────────────────────────────

async def _ashby_org_exists(client: httpx.AsyncClient, slug: str) -> bool:
    """
    Verify that an Ashby org slug actually exists.

    Ashby returns HTTP 200 for any path (including non-existent companies),
    so a HEAD check alone is not sufficient. This function queries Ashby's
    public GraphQL API and checks that jobBoard is not null.
    """
    _ASHBY_CHECK_QUERY = """
        query AshbyCheck($organizationHostedJobsPageName: String!) {
            jobBoard: jobBoardWithTeams(
                organizationHostedJobsPageName: $organizationHostedJobsPageName
            ) { jobPostings { id } }
        }
    """
    try:
        r = await client.post(
            "https://jobs.ashbyhq.com/api/non-user-graphql",
            json={
                "operationName": "AshbyCheck",
                "variables": {"organizationHostedJobsPageName": slug},
                "query": _ASHBY_CHECK_QUERY,
            },
            timeout=HEAD_TIMEOUT,
        )
        if r.status_code == 200:
            return (r.json().get("data") or {}).get("jobBoard") is not None
    except Exception:
        pass
    return False


# ── Public API ────────────────────────────────────────────────────────────────

async def find_career_page(company_domain: str) -> dict:
    """
    Find the career page URL for a company given its primary domain.

    Returns a dict:
        career_url  : str  — the discovered URL (or None if not found)
        strategy    : str  — which strategy succeeded
        confidence  : float — 0.0 – 1.0
    """
    slug = _slug_from_domain(company_domain)

    # ── Strategy 1: ATS pattern matching ─────────────────────────────────────
    logger.info("[Strategy 1] ATS pattern matching for slug=%s", slug)
    async with httpx.AsyncClient() as client:
        for ats_name, template in ATS_PATTERNS:
            url = template.format(slug=slug)
            # Validate the slug persists in the final URL after redirects
            # (some ATS platforms redirect unknown slugs to their homepage).
            if not await _head_ok(client, url, slug_check=slug):
                continue
            # Ashby requires additional GraphQL verification since it
            # returns HTTP 200 for any path, including non-existent orgs.
            if ats_name == "ashby" and not await _ashby_org_exists(client, slug):
                logger.debug("  Ashby org '%s' does not exist — skipping.", slug)
                continue
            logger.info("  Found via %s: %s", ats_name, url)
            return {"career_url": url, "strategy": f"ats_{ats_name}", "confidence": 0.95}

    # ── Strategy 2: Direct URL probing ───────────────────────────────────────
    logger.info("[Strategy 2] Direct URL probing for domain=%s", company_domain)
    # Try both bare domain and www. prefix — many sites only respond on one.
    base_domains = [company_domain]
    if not company_domain.startswith("www."):
        base_domains.append(f"www.{company_domain}")

    async with httpx.AsyncClient() as client:
        # Pass 1: keyword-matched career pages
        for base in base_domains:
            for path in CAREER_PATHS:
                url = f"https://{base}{path}"
                ok, final_url, body = await _get_ok(client, url)
                if ok and _looks_like_career_page(body):
                    logger.info("  Found via direct path: %s", final_url)
                    return {"career_url": final_url, "strategy": "direct_path", "confidence": 0.85}

        # Pass 2: accept /careers or /jobs even without keyword match
        # (many modern career pages are SPAs that render client-side).
        for base in base_domains:
            for path in ["/careers", "/jobs"]:
                url = f"https://{base}{path}"
                ok, final_url, body = await _get_ok(client, url)
                if ok:
                    logger.info("  Found via direct path (relaxed): %s", final_url)
                    return {"career_url": final_url, "strategy": "direct_path", "confidence": 0.75}

        # Subdomain variants
        for sub in CAREER_SUBDOMAINS:
            url = f"https://{sub}.{company_domain}"
            ok, final_url, body = await _get_ok(client, url)
            if ok:
                logger.info("  Found via subdomain: %s", final_url)
                return {"career_url": final_url, "strategy": "subdomain", "confidence": 0.90}

    # ── Strategy 3: Sitemap XML parsing ─────────────────────────────────────
    logger.info("[Strategy 3] Sitemap parsing for domain=%s", company_domain)
    sitemap_candidates = [
        f"https://{company_domain}/sitemap.xml",
        f"https://{company_domain}/sitemap_index.xml",
        f"https://www.{company_domain}/sitemap.xml",
    ]
    career_kw = {"career", "job", "hiring", "join", "work"}

    async with httpx.AsyncClient() as client:
        for sitemap_url in sitemap_candidates:
            ok, _, body = await _get_ok(client, sitemap_url)
            if not ok:
                continue
            try:
                soup = BeautifulSoup(body, "lxml-xml")
                locs = [tag.text.strip() for tag in soup.find_all("loc")]
                for loc in locs:
                    path_lower = urlparse(loc).path.lower()
                    if any(kw in path_lower for kw in career_kw):
                        logger.info("  Found via sitemap: %s", loc)
                        return {"career_url": loc, "strategy": "sitemap", "confidence": 0.75}
            except Exception as exc:
                logger.debug("  Sitemap parse error: %s", exc)

    # ── Strategy 4: AI browser agent (last resort) ────────────────────────────
    logger.info("[Strategy 4] AI browser agent for domain=%s", company_domain)
    try:
        from browser_use import Agent as BrowserAgent
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="gpt-4o-mini")
        # Ensure the provider attribute exists (required by browser-use).
        if not hasattr(llm, "provider"):
            llm.provider = "openai"  # type: ignore[attr-defined]
        browser_agent = BrowserAgent(
            task=(
                f"Navigate to https://{company_domain} and find the URL of the "
                "careers or jobs page.  Return ONLY the URL, nothing else."
            ),
            llm=llm,
        )
        result = await browser_agent.run()
        career_url = str(result.final_result()).strip()

        if career_url and career_url.startswith("http"):
            logger.info("  Found via AI browser agent: %s", career_url)
            return {"career_url": career_url, "strategy": "ai_browser", "confidence": 0.60}
    except ImportError:
        logger.warning("  browser-use not installed — skipping Strategy 4.")
    except Exception as exc:
        logger.warning("  AI browser agent failed: %s", exc)

    logger.warning("Career page not found for domain=%s", company_domain)
    return {"career_url": None, "strategy": "not_found", "confidence": 0.0}
