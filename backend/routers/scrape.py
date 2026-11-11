"""
routers/scrape.py
-----------------
API endpoints that trigger public-records scraping for a site.

Endpoints:
  POST /sites/{site_id}/scrape
    Run all scrapers for a site, save found documents to the database,
    and return a summary of what was found.

  POST /sites/{site_id}/scrape?source=google_news
    Run only the named scraper (useful for testing or re-running one source).

  GET /sites/{site_id}/scrape/preview
    Run scrapers but DON'T save — return what would be saved (dry run).
"""

from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models.site import Site
from models.document import PublicDocument
from scrapers import run_all_scrapers, ALL_SCRAPERS
from scrapers.base import SiteQuery, ScrapedDocument

router = APIRouter(prefix="/sites", tags=["scrape"])


def _site_to_query(site: Site, db: Session) -> SiteQuery:
    """Convert a Site ORM object to a SiteQuery for the scrapers."""
    # Extract lat/lon from PostGIS geometry
    lat, lon = None, None
    if site.location is not None:
        try:
            from geoalchemy2.functions import ST_X, ST_Y
            lon = db.scalar(ST_X(site.location))
            lat = db.scalar(ST_Y(site.location))
        except Exception:
            pass

    return SiteQuery(
        site_id=str(site.id),
        name=site.name,
        operator=site.operator,
        address=site.address,
        latitude=lat,
        longitude=lon,
        parcel_id=site.parcel_id,
    )


def _save_documents(
    site_id: UUID,
    scraped_docs: list[ScrapedDocument],
    db: Session,
) -> tuple[int, int]:
    """
    Save scraped documents to the database, skipping duplicates (by URL).
    Returns (saved_count, skipped_count).
    """
    existing_urls = {
        d.url for d in
        db.query(PublicDocument.url).filter(
            PublicDocument.site_id == site_id,
            PublicDocument.url.isnot(None),
        ).all()
        if d.url
    }

    saved = 0
    skipped = 0

    for sdoc in scraped_docs:
        # Skip if we already have this URL
        if sdoc.url and sdoc.url in existing_urls:
            skipped += 1
            continue

        doc = PublicDocument(
            site_id=site_id,
            document_type=sdoc.document_type,
            title=sdoc.title,
            url=sdoc.url,
            publication_date=sdoc.publication_date,
            issuing_agency=sdoc.issuing_agency,
            extracted_fields={
                **sdoc.extracted_fields,
                "_source_scraper": sdoc.source_scraper,
            },
        )
        db.add(doc)

        if sdoc.url:
            existing_urls.add(sdoc.url)
        saved += 1

    db.commit()
    return saved, skipped


@router.post("/{site_id}/scrape")
def scrape_site(
    site_id: UUID,
    source: Optional[str] = Query(None, description="Run only this scraper (e.g. google_news, ferc, loudoun)"),
    db: Session = Depends(get_db),
):
    """
    Run public-records scrapers for a site and save the results.

    - Runs all scrapers by default, or just one if `source` is specified.
    - Skips documents whose URL already exists for this site (idempotent).
    - Returns a summary of what was found and saved.
    """
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    query = _site_to_query(site, db)

    # Run scrapers
    if source:
        # Find the named scraper
        scraper_map = {s.name: s for s in ALL_SCRAPERS}
        if source not in scraper_map:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scraper '{source}'. "
                       f"Available: {list(scraper_map.keys())}",
            )
        try:
            docs = scraper_map[source].search(query)
            scrape_result = {
                "results": docs,
                "summary": {source: len(docs)},
                "errors": {},
            }
        except Exception as e:
            scrape_result = {
                "results": [],
                "summary": {source: 0},
                "errors": {source: str(e)},
            }
    else:
        scrape_result = run_all_scrapers(query)

    # Save to database
    saved, skipped = _save_documents(site_id, scrape_result["results"], db)

    return {
        "site_id":     str(site_id),
        "site_name":   site.name,
        "documents_found":   len(scrape_result["results"]),
        "documents_saved":   saved,
        "documents_skipped": skipped,
        "by_source":   scrape_result["summary"],
        "errors":      scrape_result["errors"],
    }


@router.get("/{site_id}/scrape/preview")
def preview_scrape(
    site_id: UUID,
    source: Optional[str] = Query(None, description="Preview only this scraper"),
    db: Session = Depends(get_db),
):
    """
    Dry-run: run scrapers and return what would be saved, without saving anything.
    Useful for testing scrapers or reviewing results before committing.
    """
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    query = _site_to_query(site, db)

    if source:
        scraper_map = {s.name: s for s in ALL_SCRAPERS}
        if source not in scraper_map:
            raise HTTPException(status_code=400, detail=f"Unknown scraper '{source}'")
        try:
            docs = scraper_map[source].search(query)
            scrape_result = {"results": docs, "summary": {source: len(docs)}, "errors": {}}
        except Exception as e:
            scrape_result = {"results": [], "summary": {source: 0}, "errors": {source: str(e)}}
    else:
        scrape_result = run_all_scrapers(query)

    return {
        "site_id":   str(site_id),
        "site_name": site.name,
        "dry_run":   True,
        "by_source": scrape_result["summary"],
        "errors":    scrape_result["errors"],
        "documents": [d.to_document_create_dict() for d in scrape_result["results"]],
    }
