"""
scrapers/ferc.py  —  PJM / FERC interconnection filings for Loudoun County sites
----------------------------------------------------------------------------------
WHY THE ORIGINAL APPROACH FAILED
  FERC eLibrary filings are filed by *utilities* (Dominion Energy Virginia, AEP),
  not by data center operators. Searching for "Amazon" or "Equinix" returns nothing
  because neither company is a FERC regulated entity. The correct search strategy is:

    "Dominion Energy Virginia" OR "Virginia Electric and Power" + location keywords

  Additionally, FERC's eLibrary POST endpoint blocks headless requests. We use
  their RSS/Atom feed instead, plus Google News for PJM queue coverage.

WHAT WE SEARCH NOW
  1. FERC RSS feed  — filtered for Dominion Energy Virginia filings mentioning
     Ashburn, Loudoun, Sterling, or the specific operator.
  2. Google News RSS — searches for "[operator] Dominion Energy interconnection
     Virginia" and "data center Loudoun County megawatt PJM". These often surface
     press releases with actual MW numbers.
  3. Virginia SCC + PJM queue reference links — curated starting points for
     policymakers doing manual research.

DATA CENTER → UTILITY MAPPING (Loudoun County)
  All five MVP sites are in Dominion Energy Virginia's service territory (PJM RTO).
  Dominion files with FERC as "Virginia Electric and Power Company" under dockets:
    ER  — rate/tariff (often includes large-load interconnection tariffs)
    EL  — electric complaint/inquiry
    IS  — interconnection service applications
"""

import re
import time
from datetime import date, datetime
from typing import Optional
import httpx

from .base import BaseScraper, SiteQuery, ScrapedDocument, ScraperError

RATE_LIMIT_DELAY = 2.0
MAX_RESULTS = 10

FERC_RSS_URL    = "https://www.ferc.gov/news-events/news/rss"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
PJM_QUEUE_URL   = "https://www.pjm.com/planning/services-requests/interconnection-queues"
SCC_SEARCH_URL  = "https://www.scc.virginia.gov/pages/Electric-Utility-Regulation"

LOUDOUN_KEYWORDS = {
    "ashburn", "loudoun", "sterling", "broadlands", "dulles",
    "northern virginia", "dominion energy virginia", "virginia electric and power",
}


def _parse_rss_date(date_str: str) -> Optional[date]:
    for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]:
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _fetch_rss(url: str, params: Optional[dict] = None) -> str:
    try:
        resp = httpx.get(url, params=params, timeout=15,
                         headers={"User-Agent": "DataCenterLens/0.1 (public-interest research)"},
                         follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPStatusError as e:
        raise ScraperError(f"HTTP {e.response.status_code} from {url}")
    except httpx.RequestError as e:
        raise ScraperError(f"Request failed: {e}")


def _parse_rss_items(xml: str) -> list[dict]:
    """Minimal RSS parser — no external dependencies."""
    items = []
    for chunk in xml.split("<item>")[1:]:
        end = chunk.find("</item>")
        block = chunk[:end] if end != -1 else chunk

        def _tag(tag: str) -> str:
            m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>",
                          block, re.DOTALL | re.IGNORECASE)
            return m.group(1).strip() if m else ""

        items.append({"title": _tag("title"), "link": _tag("link"),
                      "pubDate": _tag("pubDate"), "description": _tag("description")})
    return items


def _extract_mw(text: str) -> Optional[float]:
    m = re.search(r'(\d[\d,]*(?:\.\d+)?)\s*(?:MW|megawatt)', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


class FERCScraper(BaseScraper):
    name = "ferc"

    def search(self, query: SiteQuery) -> list[ScrapedDocument]:
        docs: list[ScrapedDocument] = []
        seen_urls: set[str] = set()

        # 1. FERC news RSS — filter for Dominion + Loudoun keywords
        try:
            docs.extend(self._search_ferc_rss(query, seen_urls))
        except ScraperError:
            pass

        time.sleep(RATE_LIMIT_DELAY)

        # 2. Google News — utility territory + MW keyword searches
        try:
            docs.extend(self._search_google_news(query, seen_urls))
        except ScraperError:
            pass

        # 3. Reference docs (always included)
        docs.extend(self._reference_docs(query))

        return docs[:MAX_RESULTS]

    def _search_ferc_rss(self, query: SiteQuery, seen_urls: set) -> list[ScrapedDocument]:
        xml = _fetch_rss(FERC_RSS_URL)
        results = []
        for item in _parse_rss_items(xml):
            combined = f"{item['title']} {item['description']}".lower()
            if not any(kw in combined for kw in LOUDOUN_KEYWORDS):
                continue
            url = item["link"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            docket_m = re.search(r'\b((?:ER|EL|IS|EC)\d{2}-\d+(?:-\d+)?)\b',
                                  f"{item['title']} {item['description']}")
            results.append(ScrapedDocument(
                title=item["title"],
                document_type="utility_filing",
                source_scraper=self.name,
                url=url or None,
                publication_date=_parse_rss_date(item["pubDate"]) if item["pubDate"] else None,
                issuing_agency="FERC",
                extracted_fields={
                    "docket_number": docket_m.group(1) if docket_m else None,
                    "_search_strategy": "ferc_rss_dominion_loudoun",
                    "_note": "Verify at ferc.gov/elibrary. Filtered by Dominion Energy Virginia + Loudoun keywords.",
                },
            ))
        return results

    def _search_google_news(self, query: SiteQuery, seen_urls: set) -> list[ScrapedDocument]:
        op = query.operator or "data center"
        search_queries = [
            f'"{op}" "Dominion Energy" interconnection Virginia MW',
            '"data center" "Loudoun County" megawatt interconnection PJM',
        ]
        results = []
        for q in search_queries:
            time.sleep(RATE_LIMIT_DELAY)
            try:
                xml = _fetch_rss(GOOGLE_NEWS_RSS,
                                  params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
                for item in _parse_rss_items(xml)[:4]:
                    url = item["link"]
                    if url in seen_urls:
                        continue
                    combined = f"{item['title']} {item['description']}"
                    if not re.search(r'\b(?:mw|megawatt|interconnect|pjm|dominion)\b',
                                     combined, re.IGNORECASE):
                        continue
                    seen_urls.add(url)
                    extracted: dict = {"search_query": q,
                                       "_search_strategy": "google_news_dominion_territory"}
                    mw = _extract_mw(combined)
                    if mw:
                        extracted["claimed_mw"] = mw
                    results.append(ScrapedDocument(
                        title=item["title"],
                        document_type="utility_filing",
                        source_scraper=self.name,
                        url=url,
                        publication_date=_parse_rss_date(item["pubDate"]) if item["pubDate"] else None,
                        issuing_agency="Google News / utility filing coverage",
                        extracted_fields=extracted,
                    ))
            except ScraperError:
                continue
        return results

    def _reference_docs(self, query: SiteQuery) -> list[ScrapedDocument]:
        op = query.operator or query.name
        return [
            ScrapedDocument(
                title=f"PJM Interconnection Queue — Dominion territory, Loudoun County (manual research: {op})",
                document_type="utility_filing",
                source_scraper=self.name,
                url=PJM_QUEUE_URL,
                issuing_agency="PJM Interconnection",
                extracted_fields={
                    "_note": (
                        "PJM publishes a downloadable active queue. Filter by: State=VA, "
                        "County=Loudoun, Transmission Owner=Dominion Energy Virginia. "
                        "Loads >20 MW in Loudoun are almost certainly data centers. "
                        "Fields include: MW requested, queue date, status, project name."
                    ),
                    "_manual_research": True,
                    "utility": "Dominion Energy Virginia (Virginia Electric and Power Company)",
                    "rto": "PJM Interconnection",
                },
            ),
            ScrapedDocument(
                title=f"Virginia SCC — Dominion Energy Electric Cases (search: {op})",
                document_type="utility_filing",
                source_scraper=self.name,
                url=SCC_SEARCH_URL,
                issuing_agency="Virginia State Corporation Commission",
                extracted_fields={
                    "_note": (
                        "The Virginia SCC reviews Dominion Energy interconnection agreements "
                        "and large-load tariff filings. Case numbers typically PUE-XXXX or "
                        "PUR-XXXX. Search scc.virginia.gov for the operator name or address."
                    ),
                    "_manual_research": True,
                },
            ),
        ]
