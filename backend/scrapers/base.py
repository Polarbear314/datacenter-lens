"""
scrapers/base.py
----------------
Shared types for all public-records scrapers.

  SiteQuery      – what we pass INTO a scraper (site info)
  ScrapedDocument – what a scraper returns (one public record)
  BaseScraper     – interface every scraper must implement
  ScraperError    – raised when a scraper fails gracefully
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from abc import ABC, abstractmethod


@dataclass
class SiteQuery:
    """
    Everything we know about a site that a scraper might use to find records.
    All fields except site_id and name are optional — not every scraper needs
    all of them.
    """
    site_id:   str              # UUID of the site in our database
    name:      str              # Human-readable name, e.g. "Equinix DC1 (Ashburn Campus)"
    operator:  Optional[str] = None   # e.g. "Equinix", "Amazon Web Services"
    address:   Optional[str] = None   # e.g. "21711 Filigree Court, Ashburn, VA 20147"
    latitude:  Optional[float] = None
    longitude: Optional[float] = None
    parcel_id: Optional[str]  = None


@dataclass
class ScrapedDocument:
    """
    One public record found by a scraper.
    Maps directly to the DocumentCreate Pydantic schema.
    """
    title:            str
    document_type:    str   # permit | tax_agreement | utility_filing | planning_doc |
                            # news | environmental_review | court_filing |
                            # company_announcement | other
    source_scraper:   str   # which scraper found this, e.g. "google_news"

    url:              Optional[str]  = None
    publication_date: Optional[date] = None
    issuing_agency:   Optional[str]  = None

    # Structured fields extracted from the document.
    # Keys should be consistent across scrapers where possible:
    #   claimed_mw, claimed_sqft, claimed_jobs_permanent,
    #   tax_abatement_value, water_source, permit_number,
    #   project_name, applicant, decision, docket_number
    extracted_fields: dict = field(default_factory=dict)

    def to_document_create_dict(self) -> dict:
        """Convert to the dict shape expected by POST /sites/{id}/documents."""
        return {
            "title":            self.title,
            "document_type":    self.document_type,
            "url":              self.url,
            "publication_date": self.publication_date.isoformat() if self.publication_date else None,
            "issuing_agency":   self.issuing_agency,
            "extracted_fields": {
                **self.extracted_fields,
                "_source_scraper": self.source_scraper,
            },
        }


class BaseScraper(ABC):
    """Interface that every scraper must implement."""

    name: str  # Short identifier, e.g. "google_news"

    @abstractmethod
    def search(self, query: SiteQuery) -> list[ScrapedDocument]:
        """
        Search this source for public records related to the given site.
        Returns a (possibly empty) list of ScrapedDocument objects.
        Raises ScraperError on a non-retryable failure.
        """
        ...


class ScraperError(Exception):
    """
    Raised when a scraper encounters a non-retryable failure —
    e.g. the upstream API is down or returned an unexpected format.
    Distinct from an empty result (which is just an empty list).
    """
    pass
