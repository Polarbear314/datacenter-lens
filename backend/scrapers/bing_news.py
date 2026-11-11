"""
scrapers/bing_news.py  —  Bing News RSS scraper
------------------------------------------------
Supplements google_news with Microsoft Bing's news index.

WHY BOTH GOOGLE AND BING?
  Google News and Bing News use different crawling and ranking algorithms.
  In practice, Bing often surfaces:
    - Local Virginia/Loudoun outlets (Loudoun Times-Mirror, WTOP, Virginia Mercury)
    - State government press releases
    - Utility/grid news (Dominion Energy, PJM announcements)
    - Business journal articles (Washington Business Journal, Virginia Business)
  ...that Google News either ranks lower or omits entirely.

  For policymakers, local news coverage is often the most actionable:
  it shows community opposition, planning board hearings, and permit decisions
  that national outlets don't cover.

NO API KEY REQUIRED
  Bing publishes a free, undocumented but stable news RSS endpoint:
    https://www.bing.com/news/search?q={query}&format=RSS

SEARCH STRATEGY
  Same multi-query approach as google_news:
  1. Operator + city (most specific, e.g. "Equinix" Ashburn data center)
  2. Site name (catches branded campus names)
  3. Tax/permit targeted search (catches policy-relevant docs)
"""

import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Optional
import httpx

from .base import BaseScraper, SiteQuery, ScrapedDocument, ScraperError

RATE_LIMIT_DELAY = 2.0
BING_NEWS_RSS    = "https://www.bing.com/news/search"
MAX_RESULTS      = 15

DATE_FORMATS = [
    "%a, %d %b %Y %H:%M:%S %Z",
    "%a, %d %b %Y %H:%M:%S %z",
    "%Y-%m-%dT%H:%M:%SZ",
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


def _extract_city(address: Optional[str]) -> Optional[str]:
    if not address:
        return None
    parts = [p.strip() for p in address.split(",")]
    return parts[-2] if len(parts) >= 2 else None


def _build_queries(query: SiteQuery) -> list[str]:
    """
    Build targeted search queries. We include one policy-specific query
    that Google News tends to miss: permit, tax abatement, and opposition coverage.
    """
    queries = []
    op   = query.operator
    city = _extract_city(query.address) or "Ashburn"

    # 1. Operator + location (standard news)
    if op:
        queries.append(f'"{op}" "{city}" data center')

    # 2. Clean site name
    clean_name = re.sub(r"\s*\([^)]+\)", "", query.name).strip()
    if len(clean_name) > 5:
        queries.append(f'"{clean_name}"')

    # 3. Policy/permit angle — catches tax breaks, opposition, hearings
    if op:
        queries.append(
            f'"{op}" "data center" '
            f'(permit OR "tax abatement" OR "special exception" OR SPEX OR opposition OR "planning board")'
        )

    return queries[:2]  # 2 queries max per site


class BingNewsScraper(BaseScraper):
    name = "bing_news"

    def search(self, query: SiteQuery) -> list[ScrapedDocument]:
        all_docs: list[ScrapedDocument] = []
        seen_urls: set[str] = set()

        for i, q in enumerate(_build_queries(query)):
            if i > 0:
                time.sleep(RATE_LIMIT_DELAY)
            try:
                docs = self._fetch_rss(q, seen_urls)
                all_docs.extend(docs)
                for d in docs:
                    if d.url:
                        seen_urls.add(d.url)
            except ScraperError:
                if i == 0:
                    raise
                continue

        return all_docs[:MAX_RESULTS]

    def _fetch_rss(self, search_query: str, seen_urls: set) -> list[ScrapedDocument]:
        try:
            resp = httpx.get(
                BING_NEWS_RSS,
                params={"q": search_query, "format": "RSS"},
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; DataCenterLens/0.1; "
                        "+https://datacenter-lens.org; public-interest research)"
                    )
                },
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ScraperError(f"Bing News returned HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            raise ScraperError(f"Bing News request failed: {e}")

        return self._parse_rss(resp.text, seen_urls)

    def _parse_rss(self, xml_text: str, seen_urls: set) -> list[ScrapedDocument]:
        # Bing sometimes returns malformed XML — strip namespace declarations AND
        # namespace prefixes (e.g. <News:Image>), which otherwise raise
        # "unbound prefix" once their xmlns declaration is removed.
        xml_text = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", xml_text)
        xml_text = re.sub(r'<(/?)\w+:', r'<\1', xml_text)          # prefixed tags
        xml_text = re.sub(r'\s\w+:(\w+=")', r' \1', xml_text)      # prefixed attributes

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise ScraperError(f"Failed to parse Bing News RSS: {e}")

        channel = root.find("channel")
        if channel is None:
            return []

        docs = []
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()
            desc  = (item.findtext("description") or "").strip()

            # Strip HTML tags from description
            desc = re.sub(r"<[^>]+>", " ", desc).strip()

            if not title or not link:
                continue
            if link in seen_urls:
                continue

            pub_date    = _parse_date(pub)
            source_name = None

            # Bing formats titles as "Title - Source Name"
            if " - " in title:
                source_name = title.rsplit(" - ", 1)[-1]
                title       = title.rsplit(" - ", 1)[0].strip()

            # Flag articles with policy keywords so the report generator can
            # highlight them
            policy_keywords = {
                "permit", "spex", "tax abatement", "special exception",
                "opposition", "planning board", "zoning", "moratorium",
                "lawsuit", "community", "hearing", "council"
            }
            combined_lower = f"{title} {desc}".lower()
            is_policy = any(kw in combined_lower for kw in policy_keywords)

            # Try to extract a MW figure from the description
            mw_match = re.search(r'(\d[\d,]*(?:\.\d+)?)\s*(?:MW|megawatt)', desc, re.IGNORECASE)
            claimed_mw = float(mw_match.group(1).replace(",", "")) if mw_match else None

            extracted: dict = {
                "_source_scraper": "bing_news",
                "is_policy_relevant": is_policy,
            }
            if claimed_mw:
                extracted["claimed_mw"] = claimed_mw

            docs.append(ScrapedDocument(
                title=title,
                document_type="news",
                source_scraper=self.name,
                url=link,
                publication_date=pub_date,
                issuing_agency=source_name,
                extracted_fields=extracted,
            ))

        return docs
