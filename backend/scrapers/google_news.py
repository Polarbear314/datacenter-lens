"""
scrapers/google_news.py
-----------------------
Finds recent news articles about a data center site using Google News RSS.

No API key required. Google News RSS is a public, undocumented but stable endpoint.

URL pattern:
  https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en

What it returns:
  Recent news articles mentioning the operator and location.
  Good for finding: local opposition, permit decisions, announced expansions,
  utility agreements, community meetings, and company announcements.

Limitations:
  - Only returns recent articles (last ~1–2 years typically)
  - Titles only — full article text requires fetching each URL
  - Results are Google's relevance ranking, not chronological
  - Rate-limited if called too frequently (add a delay between sites)
"""

import time
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Optional
import httpx

from .base import BaseScraper, SiteQuery, ScrapedDocument, ScraperError

# Seconds to wait between searches to avoid rate-limiting
RATE_LIMIT_DELAY = 2.0

# Google News RSS base URL
GNEWS_RSS = "https://news.google.com/rss/search"

# Max articles to return per site
MAX_RESULTS = 15

# How to parse Google's "Mon, 16 Jun 2025 10:30:00 GMT" date format
DATE_FORMATS = [
    "%a, %d %b %Y %H:%M:%S %Z",
    "%a, %d %b %Y %H:%M:%S %z",
]


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _build_queries(query: SiteQuery) -> list[str]:
    """
    Build 2–3 search queries for a site, from most specific to most general.
    We run multiple queries because Google News sometimes misses results
    on exact operator names (especially hyperscalers that use project names).
    """
    queries = []

    # Most specific: exact address or site name
    if query.address:
        city = _extract_city(query.address)
        if city and query.operator:
            queries.append(f'"{query.operator}" "{city}" data center')

    # Operator + "Ashburn" or "Loudoun"
    if query.operator:
        queries.append(f'"{query.operator}" Ashburn OR Loudoun data center')

    # Site name (handles cases like "CyrusOne Broadlands")
    # Strip things like "(Blackstone)" from names for cleaner search
    clean_name = re.sub(r"\s*\([^)]+\)", "", query.name).strip()
    if clean_name not in [q for q in queries]:
        queries.append(f'"{clean_name}" data center')

    return queries[:2]  # Run at most 2 queries to stay polite


def _extract_city(address: str) -> Optional[str]:
    """Extract city from an address string, e.g. 'Ashburn' from '21711 Filigree Ct, Ashburn, VA'."""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 2:
        return parts[-2]  # Usually the city is second-to-last
    return None


class GoogleNewsScraper(BaseScraper):
    name = "google_news"

    def search(self, query: SiteQuery) -> list[ScrapedDocument]:
        """Search Google News RSS for articles about this site."""
        all_docs: list[ScrapedDocument] = []
        seen_urls: set[str] = set()

        search_queries = _build_queries(query)

        for i, search_q in enumerate(search_queries):
            if i > 0:
                time.sleep(RATE_LIMIT_DELAY)

            try:
                docs = self._fetch_rss(search_q, seen_urls)
                all_docs.extend(docs)
                for d in docs:
                    if d.url:
                        seen_urls.add(d.url)
            except ScraperError:
                # If first query fails, skip; if second fails, that's ok too
                if i == 0:
                    raise
                continue

        # Deduplicate by URL and cap at MAX_RESULTS
        return all_docs[:MAX_RESULTS]

    def _fetch_rss(self, search_query: str, seen_urls: set) -> list[ScrapedDocument]:
        """Fetch and parse one Google News RSS query."""
        params = {
            "q": search_query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }

        try:
            resp = httpx.get(
                GNEWS_RSS,
                params=params,
                timeout=15,
                headers={"User-Agent": "DataCenterLens/0.1 (public-interest research tool)"},
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ScraperError(f"Google News returned HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            raise ScraperError(f"Google News request failed: {e}")

        return self._parse_rss(resp.text, seen_urls)

    def _parse_rss(self, xml_text: str, seen_urls: set) -> list[ScrapedDocument]:
        """Parse RSS XML and extract items as ScrapedDocument objects."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise ScraperError(f"Failed to parse Google News RSS: {e}")

        # RSS namespace: items are at channel > item
        channel = root.find("channel")
        if channel is None:
            return []

        docs = []
        for item in channel.findall("item"):
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            pub   = item.findtext("pubDate", "").strip()

            if not title or not link:
                continue
            if link in seen_urls:
                continue

            # Google News sometimes wraps the real URL; keep the Google link
            # as-is since it redirects to the source article
            pub_date = _parse_date(pub)

            # Extract source name from title if present (Google formats as "Title - Source")
            source_name = None
            if " - " in title:
                source_name = title.rsplit(" - ", 1)[-1]
                title = title.rsplit(" - ", 1)[0].strip()

            docs.append(ScrapedDocument(
                title=title,
                document_type="news",
                source_scraper=self.name,
                url=link,
                publication_date=pub_date,
                issuing_agency=source_name,
                extracted_fields={
                    "search_query": "google_news_rss",
                },
            ))

        return docs
