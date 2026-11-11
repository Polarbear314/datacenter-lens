"""
scrapers/sec_edgar.py  —  SEC EDGAR full-text search for data center filings
-----------------------------------------------------------------------------
WHAT THIS FINDS
  SEC EDGAR is the public repository for all filings by US-listed public companies.
  For data center operators, this is a gold mine of confirmed capacity data:

  10-K (Annual Report):
    - Total MW under management by market/region
    - Square footage of data center space
    - Capital expenditure on new construction
    - Named facilities, campus addresses
    - Environmental disclosures (water use, emissions)

  8-K (Material Event):
    - Announced acquisitions of land/buildings
    - New utility agreements (often names Dominion, AWS, etc.)
    - Major customer contracts (implies capacity need)

  DEF 14A (Proxy):
    - Executive bonuses tied to MW targets
    - Community commitments

WHY THIS IS VALUABLE FOR POLICYMAKERS
  Public company filings are legally required to be accurate.
  A confirmed MW figure from a 10-K is far more reliable than an estimate.
  These filings often contain the actual interconnection MW requested,
  square footage of each campus, and water-use disclosures.

PUBLIC COMPANIES WITH MAJOR LOUDOUN / NOVA PRESENCE
  - Equinix (EQIX)         — largest colo operator globally
  - Digital Realty (DLR)   — largest wholesale DC REIT
  - Iron Mountain (IRM)    — data center + document storage
  - American Tower (AMT)   — acquired CoreSite in 2021
  - Vantage Data Centers   — private (KKR-backed), no EDGAR
  - QTS / CyrusOne         — both taken private, older filings still useful

API (FREE, NO KEY REQUIRED)
  Full-text search: https://efts.sec.gov/LATEST/search-index?q=...&forms=10-K,8-K
  Company lookup:   https://data.sec.gov/submissions/{CIK}.json
  Filing index:     https://www.sec.gov/cgi-bin/browse-edgar

SEARCH STRATEGY
  1. Full-text search for operator name + "Ashburn" OR "Loudoun" OR "Northern Virginia"
     in recent 10-K and 8-K filings (last 3 years).
  2. If no operator, search by site name.
  3. Return filing metadata (title, date, URL) + any MW/sqft figures extracted
     from the filing description.

KNOWN LIMITATIONS
  - Private operators (AWS, Google, Microsoft cloud infra) do not file with SEC.
    For these, EDGAR will return 0 results. Use news scrapers instead.
  - Full text search hits are at the filing level, not the specific paragraph.
    Policymakers will need to read the filing to find the specific numbers.
  - EDGAR rate limits: ~10 requests/second. We stay well under with rate limiting.
"""

import re
import time
from datetime import date, datetime
from typing import Optional
import httpx

from .base import BaseScraper, SiteQuery, ScrapedDocument, ScraperError

RATE_LIMIT_DELAY  = 1.5
MAX_RESULTS       = 10

EDGAR_FULL_TEXT   = "https://efts.sec.gov/LATEST/search-index"
EDGAR_COMPANY_URL = "https://data.sec.gov/submissions"
EDGAR_VIEWER_BASE = "https://www.sec.gov/Archives/edgar/full-index"
EDGAR_SEARCH_UI   = "https://efts.sec.gov/LATEST/search-index?q={q}&forms=10-K,8-K&dateRange=custom&startdt={start}&enddt={end}"

# Public data center companies with confirmed EDGAR presence
# CIK = SEC Central Index Key (stable identifier)
PUBLIC_DC_OPERATORS: dict[str, str] = {
    "equinix":       "1101239",
    "digital realty": "1297996",
    "digitalrealty":  "1297996",
    "dlr":            "1297996",
    "iron mountain":  "1020569",
    "ironmountain":   "1020569",
    "american tower": "1053507",   # owns CoreSite
    "americantower":  "1053507",
    "coresite":       "1053507",   # now under American Tower
    "uniti":          "1620459",
    "cyrusone":       "1553023",   # went private 2022, older filings available
    "qts":            "1442236",   # went private 2021, older filings available
}

# Virginia-related keywords that narrow a filing to this region
VA_KEYWORDS = ["ashburn", "loudoun", "sterling", "northern virginia", "nova", "dulles"]

DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y%m%d",
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


def _extract_mw(text: str) -> Optional[float]:
    """Extract a megawatt figure from text."""
    m = re.search(r'(\d[\d,]*(?:\.\d+)?)\s*(?:MW|megawatt)', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _extract_sqft(text: str) -> Optional[float]:
    """Extract a square footage figure from text."""
    m = re.search(r'(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft|square\s*feet)', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _get_cik(operator: Optional[str]) -> Optional[str]:
    """Look up CIK for known public operators."""
    if not operator:
        return None
    key = operator.lower().replace(" ", "").replace("-", "")
    for pattern, cik in PUBLIC_DC_OPERATORS.items():
        if pattern.replace(" ", "") in key or key in pattern.replace(" ", ""):
            return cik
    return None


def _fetch(url: str, params: dict = None) -> dict:
    try:
        resp = httpx.get(
            url,
            params=params or {},
            timeout=20,
            headers={
                "User-Agent": "DataCenterLens/0.1 research@datacenter-lens.org",
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        raise ScraperError(f"EDGAR HTTP {e.response.status_code} for {url}")
    except httpx.RequestError as e:
        raise ScraperError(f"EDGAR request failed: {e}")
    except Exception as e:
        raise ScraperError(f"EDGAR parse error: {e}")


class SECEdgarScraper(BaseScraper):
    name = "sec_edgar"

    def search(self, query: SiteQuery) -> list[ScrapedDocument]:
        docs: list[ScrapedDocument] = []
        seen_urls: set[str] = set()

        # Strategy 1: CIK-based search for known public companies
        # Returns the most recent 10-K and 8-K filings, then filters for VA keywords
        cik = _get_cik(query.operator)
        if cik:
            try:
                docs.extend(self._search_by_cik(cik, query, seen_urls))
            except ScraperError:
                pass
            time.sleep(RATE_LIMIT_DELAY)

        # Strategy 2: EDGAR full-text search for operator + Virginia location keywords
        if query.operator:
            try:
                docs.extend(self._full_text_search(query, seen_urls))
            except ScraperError:
                pass

        # Strategy 3: If still empty and we have a named site, search by site name
        if not docs and len(query.name) > 5 and "unknown" not in query.name.lower():
            time.sleep(RATE_LIMIT_DELAY)
            try:
                docs.extend(self._search_by_name(query, seen_urls))
            except ScraperError:
                pass

        # Always add EDGAR search link so policymakers can dig further
        docs.append(self._edgar_search_link(query))

        return docs[:MAX_RESULTS]

    def _search_by_cik(
        self, cik: str, query: SiteQuery, seen_urls: set
    ) -> list[ScrapedDocument]:
        """
        Fetch the company's recent filings index from EDGAR and return
        the most recent 10-K and 8-K filings (up to 5 each).
        """
        url  = f"{EDGAR_COMPANY_URL}/CIK{cik.zfill(10)}.json"
        data = _fetch(url)

        company_name = data.get("name", query.operator or "")
        filings_data = data.get("filings", {}).get("recent", {})

        forms         = filings_data.get("form", [])
        accession_nums = filings_data.get("accessionNumber", [])
        file_dates    = filings_data.get("filingDate", [])
        descriptions  = filings_data.get("primaryDocument", [])

        docs = []
        count = 0

        for i, form in enumerate(forms):
            if form not in ("10-K", "8-K", "DEF 14A"):
                continue
            if count >= 6:
                break

            acc = accession_nums[i] if i < len(accession_nums) else ""
            filed = file_dates[i] if i < len(file_dates) else ""
            primary_doc = descriptions[i] if i < len(descriptions) else ""

            # Build the filing viewer URL
            acc_clean = acc.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}"
                f"/{acc_clean}/{primary_doc}"
                if primary_doc
                else f"https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&CIK={cik}&type={form}&dateb=&owner=include&count=40"
            )

            if filing_url in seen_urls:
                continue
            seen_urls.add(filing_url)

            # Annotate 10-K with what policymakers should look for
            if form == "10-K":
                note = (
                    f"{company_name} annual report. Search for: 'Ashburn', 'Northern Virginia', "
                    f"'megawatt', 'MW', 'square feet', 'interconnection'. "
                    f"10-K filings disclose total capacity by market and capital expenditure "
                    f"on new construction. This is the most reliable source for confirmed MW figures."
                )
                doc_type = "company_announcement"
            elif form == "8-K":
                note = (
                    f"{company_name} material event filing. May announce new land acquisitions, "
                    f"utility agreements, or large customer contracts in Northern Virginia. "
                    f"Check for MW figures and named facility addresses."
                )
                doc_type = "company_announcement"
            else:
                note = f"{company_name} proxy statement. May contain capacity targets and community commitments."
                doc_type = "company_announcement"

            docs.append(ScrapedDocument(
                title=f"SEC {form}: {company_name} ({filed})",
                document_type=doc_type,
                source_scraper=self.name,
                url=filing_url,
                publication_date=_parse_date(filed),
                issuing_agency=f"SEC EDGAR — {company_name}",
                extracted_fields={
                    "form_type":      form,
                    "accession_number": acc,
                    "company_name":   company_name,
                    "cik":            cik,
                    "_source":        "sec_edgar_cik",
                    "_note":          note,
                    "_manual_research": True,
                },
            ))
            count += 1

        return docs

    def _full_text_search(
        self, query: SiteQuery, seen_urls: set
    ) -> list[ScrapedDocument]:
        """
        EDGAR full-text search: finds filings that mention the operator AND
        a Virginia location keyword. Best for finding filings by private
        companies that appear in public filings as tenants or partners.
        """
        op = query.operator or ""

        # Build search: operator name + VA location keywords
        va_terms = " OR ".join(f'"{kw}"' for kw in VA_KEYWORDS[:3])
        q = f'"{op}" ({va_terms}) "data center"'

        params = {
            "q":         q,
            "forms":     "10-K,8-K,DEF 14A",
            "dateRange": "custom",
            "startdt":   "2021-01-01",
            "enddt":     "2025-12-31",
        }

        data = _fetch(EDGAR_FULL_TEXT, params)
        hits = data.get("hits", {}).get("hits", [])

        docs = []
        for hit in hits[:5]:
            source  = hit.get("_source", {})
            url     = source.get("file_date", "")
            form    = source.get("form_type", "")
            company = source.get("entity_name", op)
            filed   = source.get("file_date", "")
            acc     = source.get("accession_no", "").replace("-", "")
            cik_val = source.get("entity_id", "")

            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_val}/{acc}/"
                if cik_val and acc
                else "https://efts.sec.gov/LATEST/search-index"
            )

            if filing_url in seen_urls:
                continue
            seen_urls.add(filing_url)

            # Extract any MW/sqft figures from the highlighted text snippet
            snippet = " ".join(
                hit.get("highlight", {}).get("file_text", [""])
            )
            claimed_mw   = _extract_mw(snippet)
            claimed_sqft = _extract_sqft(snippet)

            extracted: dict = {
                "form_type":    form,
                "company_name": company,
                "cik":          cik_val,
                "snippet":      snippet[:300] if snippet else None,
                "_source":      "sec_edgar_fulltext",
                "_note": (
                    f"EDGAR full-text search matched '{op}' + Virginia keyword in a "
                    f"{form} filing by {company}. This may indicate {company} is a "
                    f"tenant, utility partner, or mentioned in context of this site."
                ),
                "_manual_research": True,
            }
            if claimed_mw:
                extracted["claimed_mw"] = claimed_mw
            if claimed_sqft:
                extracted["claimed_sqft"] = claimed_sqft

            docs.append(ScrapedDocument(
                title=f"SEC {form}: {company} — mentions {op} + Northern Virginia ({filed})",
                document_type="company_announcement",
                source_scraper=self.name,
                url=filing_url,
                publication_date=_parse_date(filed),
                issuing_agency=f"SEC EDGAR — {company}",
                extracted_fields=extracted,
            ))

        return docs

    def _search_by_name(
        self, query: SiteQuery, seen_urls: set
    ) -> list[ScrapedDocument]:
        """Search by site name for cases where the operator is unknown."""
        clean_name = re.sub(r"\s*\([^)]+\)", "", query.name).strip()
        if not clean_name or len(clean_name) < 4:
            return []

        q = f'"{clean_name}" "data center"'
        params = {
            "q":     q,
            "forms": "10-K,8-K",
        }

        data  = _fetch(EDGAR_FULL_TEXT, params)
        hits  = data.get("hits", {}).get("hits", [])

        docs = []
        for hit in hits[:3]:
            source  = hit.get("_source", {})
            company = source.get("entity_name", "Unknown company")
            form    = source.get("form_type", "")
            filed   = source.get("file_date", "")
            cik_val = source.get("entity_id", "")
            acc     = source.get("accession_no", "").replace("-", "")

            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_val}/{acc}/"
                if cik_val and acc else "https://efts.sec.gov/LATEST/search-index"
            )
            if filing_url in seen_urls:
                continue
            seen_urls.add(filing_url)

            docs.append(ScrapedDocument(
                title=f"SEC {form}: {company} mentions '{clean_name}' ({filed})",
                document_type="company_announcement",
                source_scraper=self.name,
                url=filing_url,
                publication_date=_parse_date(filed),
                issuing_agency=f"SEC EDGAR — {company}",
                extracted_fields={
                    "form_type":    form,
                    "company_name": company,
                    "_source":      "sec_edgar_name_search",
                    "_note": (
                        f"EDGAR full-text search found '{clean_name}' in a {form} by {company}. "
                        f"This may identify the operator or a related entity."
                    ),
                    "_manual_research": True,
                },
            ))

        return docs

    def _edgar_search_link(self, query: SiteQuery) -> ScrapedDocument:
        op = query.operator or query.name
        q  = f'"{op}" "data center" "Northern Virginia"'
        url = (
            f"https://efts.sec.gov/LATEST/search-index"
            f"?q={q.replace(' ', '+')}&forms=10-K,8-K"
        )
        return ScrapedDocument(
            title=f"SEC EDGAR Full-Text Search — {op}",
            document_type="company_announcement",
            source_scraper=self.name,
            url=url,
            issuing_agency="SEC EDGAR",
            extracted_fields={
                "_note": (
                    "Manual EDGAR search link. 10-K filings from public operators "
                    "(Equinix EQIX, Digital Realty DLR, Iron Mountain IRM) contain "
                    "confirmed MW capacity by market, sq ft of data center space, "
                    "and capital expenditure by region. These are legally required "
                    "to be accurate. Most useful for Equinix and Digital Realty sites."
                ),
                "_manual_research": True,
            },
        )
