"""
scrapers/
---------
Public-records scrapers for DataCenter Lens.

Each scraper takes a SiteQuery and returns a list of ScrapedDocument objects.
Call run_all_scrapers() to run all sources for a given site.

Sources implemented:
  - google_news      : Google News RSS (news articles)
  - bing_news        : Bing News RSS — supplements Google, catches local VA outlets
  - ferc             : FERC/PJM filings via RSS + Google News (utility territory search)
  - loudoun          : Loudoun County GIS building outlines + permit portal link
  - epa_echo         : EPA ECHO facility search — CWA/NPDES permits confirm wet cooling
  - sec_edgar        : SEC EDGAR 10-K/8-K filings for public operators (Equinix, DLR, IRM)
  - property_records : Loudoun County parcel layer — owner name, assessed value, LLC lookup
"""

from .base import SiteQuery, ScrapedDocument, ScraperError
from .google_news import GoogleNewsScraper
from .bing_news import BingNewsScraper
from .ferc import FERCScraper
from .loudoun import LoudounScraper
from .epa_echo import EPAECHOScraper
from .sec_edgar import SECEdgarScraper
from .property_records import PropertyRecordsScraper

ALL_SCRAPERS = [
    GoogleNewsScraper(),
    BingNewsScraper(),
    FERCScraper(),
    LoudounScraper(),
    EPAECHOScraper(),
    SECEdgarScraper(),
    PropertyRecordsScraper(),
]


def run_all_scrapers(query: SiteQuery) -> dict:
    """
    Run every scraper for a site. Returns a dict with:
      {
        "results": [ScrapedDocument, ...],   # all documents found
        "summary": {scraper_name: count},    # how many each scraper found
        "errors":  {scraper_name: message},  # any scrapers that failed
      }
    """
    results = []
    summary = {}
    errors = {}

    for scraper in ALL_SCRAPERS:
        try:
            docs = scraper.search(query)
            results.extend(docs)
            summary[scraper.name] = len(docs)
        except ScraperError as e:
            errors[scraper.name] = str(e)
            summary[scraper.name] = 0
        except Exception as e:
            errors[scraper.name] = f"Unexpected error: {e}"
            summary[scraper.name] = 0

    return {"results": results, "summary": summary, "errors": errors}
