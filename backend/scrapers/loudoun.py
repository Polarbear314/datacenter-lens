"""
scrapers/loudoun.py  —  Loudoun County planning records + official building footprints
---------------------------------------------------------------------------------------
WHY THE ORIGINAL FAILED
  The original scraper queried maps.loudoun.gov, which fails DNS resolution from
  many networks. Loudoun County's GIS data is also published to ArcGIS Online
  (data-loudoungis.opendata.arcgis.com) and is reachable from any network.

TWO DATA SOURCES

1. Loudoun County Data Center Building Outlines (LoudounGIS, updated Aug 2024)
   URL: services1.arcgis.com/MxjRokvPm7bjslyR/.../Data_Center_Building_Outlines/FeatureServer/0
   What: 271 official data center building footprints mapped by Loudoun County GIS.
   Fields: Name (anonymized "Data Center - XXXX BP"), Shape__Area (sq meters), FolderPath
   How we use it: proximity query → return nearby buildings with actual sq ft.
   Shape__Area × 10.7639 = sq ft. Operator names are redacted ("XXXX").

2. Loudoun County Permit Portal (manual link — JavaScript-rendered, not scrapeable)
   URL: loudounportal.loudoun.gov
   What: SPEX applications, building permits, zoning records.
   Data centers >25,000 sqft require SPEX (Special Exception) approval in Loudoun.
   SPEX records often contain: proposed sqft, MW connections, water demand, traffic studies.
   Since March 2025: Conditional Use process (§5-1502) with stricter disclosure requirements.

COORDINATE SYSTEM NOTE
  ArcGIS accepts WGS84 bounding boxes (inSR=4326). Shape__Area is in sq meters.
"""

import re
import time
from datetime import date, datetime
from typing import Optional
import httpx

from .base import BaseScraper, SiteQuery, ScrapedDocument, ScraperError

RATE_LIMIT_DELAY = 1.0

DC_OUTLINES_URL = (
    "https://services1.arcgis.com/MxjRokvPm7bjslyR/arcgis/rest/services"
    "/Data_Center_Building_Outlines/FeatureServer/0/query"
)
PERMIT_PORTAL_URL = (
    "https://loudounportal.loudoun.gov/LoudounPortal/ACA/GeneralProperty/PropertyLookUp.aspx"
)

SQM_TO_SQFT  = 10.7639
TIGHT_RADIUS  = 0.008   # ~0.5 mile — used for document scraping
POLY_RADIUS   = 0.003   # ~0.2 mile — used for polygon import (single campus only)
WIDE_RADIUS   = 0.025   # ~1.5 miles — used for document scraping fallback


def _query_dc_outlines(lat: float, lon: float, radius: float, return_geometry: bool = False) -> list[dict]:
    params = {
        "where":           "1=1",                 # Required by ArcGIS REST API for spatial queries
        "geometry":        f"{lon-radius},{lat-radius},{lon+radius},{lat+radius}",
        "geometryType":    "esriGeometryEnvelope",
        "inSR":            "4326",
        "outSR":           "4326",
        "spatialRel":      "esriSpatialRelIntersects",
        "outFields":       "FID,Name,FolderPath,Shape__Area,Shape_Area",
        "returnGeometry":  "true" if return_geometry else "false",
        "resultRecordCount": 50,
        "f":               "json",
    }
    try:
        resp = httpx.get(DC_OUTLINES_URL, params=params, timeout=20,
                         headers={"User-Agent": "DataCenterLens/0.1 (public-interest research)"},
                         follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        raise ScraperError(f"LoudounGIS HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        raise ScraperError(f"LoudounGIS request failed: {e}")
    if "error" in data:
        raise ScraperError(f"ArcGIS error: {data['error'].get('message')}")
    return data.get("features", [])   # full feature dicts (attributes + geometry)


def fetch_building_polygons(lat: float, lon: float) -> list[dict]:
    """
    Fetch official building footprint polygons from LoudounGIS with actual geometry.
    Returns list of dicts with keys: name, sqft, acres, year, geojson_polygon.
    geojson_polygon is a GeoJSON Polygon dict ready to insert into infrastructure.

    Uses TIGHT_RADIUS only (0.008° ≈ 0.5 mile) to avoid pulling in neighboring
    campuses from other operators. The wide radius is reserved for document scraping
    where we want to know about nearby facilities, not for polygon attribution.
    """
    features = _query_dc_outlines(lat, lon, POLY_RADIUS, return_geometry=True)

    results = []
    for feat in features:
        attrs = feat.get("attributes", {})
        geom  = feat.get("geometry", {})

        sqm   = attrs.get("Shape__Area") or attrs.get("Shape_Area") or 0
        sqft  = round(sqm * SQM_TO_SQFT, 0)
        name  = attrs.get("Name", "Data Center Building")
        acres = _parse_acreage(name)
        year  = _parse_folder_year(attrs.get("FolderPath", ""))

        # ArcGIS polygon geometry has 'rings' — convert to GeoJSON Polygon
        rings = geom.get("rings", [])
        if not rings:
            continue

        geojson = {
            "type": "Polygon",
            "coordinates": rings,   # ArcGIS rings are already [[lon,lat],...] when outSR=4326
        }

        results.append({
            "name":            name,
            "sqft":            sqft,
            "acres":           acres,
            "year":            year,
            "geojson_polygon": geojson,
        })

    return results


def _parse_acreage(name: str) -> Optional[float]:
    m = re.search(r'\((\d+\.?\d*)\s+Acres?\)', name or "", re.IGNORECASE)
    return float(m.group(1)) if m else None


def _parse_folder_year(folder: str) -> Optional[str]:
    m = re.search(r'(\d{4})', folder or "")
    return m.group(1) if m else None


class LoudounScraper(BaseScraper):
    name = "loudoun"

    def search(self, query: SiteQuery) -> list[ScrapedDocument]:
        docs: list[ScrapedDocument] = []

        # ── Source 1: LoudounGIS building outlines ────────────────────────────
        if query.latitude and query.longitude:
            try:
                docs.extend(self._search_building_outlines(query))
            except ScraperError as e:
                docs.append(ScrapedDocument(
                    title=f"LoudounGIS Building Outlines query failed: {e}",
                    document_type="other",
                    source_scraper=self.name,
                    extracted_fields={"_error": str(e)},
                ))
        else:
            docs.append(ScrapedDocument(
                title="LoudounGIS Building Outlines — no coordinates for this site",
                document_type="other",
                source_scraper=self.name,
                extracted_fields={
                    "_note": "Add lat/lon to this site to enable the proximity footprint query."
                },
            ))

        # ── Source 2: Permit portal link ──────────────────────────────────────
        docs.append(self._permit_portal_link(query))
        return docs

    def _search_building_outlines(self, query: SiteQuery) -> list[ScrapedDocument]:
        lat, lon = query.latitude, query.longitude

        features = _query_dc_outlines(lat, lon, TIGHT_RADIUS)
        if not features:
            time.sleep(RATE_LIMIT_DELAY)
            features = _query_dc_outlines(lat, lon, WIDE_RADIUS)

        if not features:
            return [ScrapedDocument(
                title=f"LoudounGIS: No data center buildings found within 1.5 miles of {query.name}",
                document_type="other",
                source_scraper=self.name,
                extracted_fields={
                    "_note": (
                        "No matches in the Loudoun County Data Center Building Outlines dataset "
                        "(271 buildings, last updated Aug 2024). The site may not yet be in the "
                        "county dataset, or coordinates may need adjustment."
                    ),
                },
            )]

        total_sqft  = sum((f.get("Shape__Area") or f.get("Shape_Area") or 0) * SQM_TO_SQFT
                          for f in features)
        total_acres = sum(_parse_acreage(f.get("Name", "")) or 0 for f in features)
        count = len(features)

        # Summary document — this is the one the estimator uses for claimed_sqft
        docs = [ScrapedDocument(
            title=(
                f"LoudounGIS: {count} data center building(s) near {query.name} "
                f"— {total_sqft:,.0f} sq ft total"
            ),
            document_type="planning_doc",
            source_scraper=self.name,
            url="https://data-loudoungis.opendata.arcgis.com/datasets/MxjRokvPm7bjslyR_0",
            issuing_agency="Loudoun County GIS",
            extracted_fields={
                "claimed_sqft":   round(total_sqft, 0),
                "claimed_acres":  round(total_acres, 2),
                "building_count": count,
                "_source": "loudoun_gis_dc_outlines_summary",
                "_note": (
                    f"Official Loudoun County GIS footprint data: {count} building(s) "
                    f"within ~1.5 miles, {total_sqft:,.0f} sq ft total. "
                    f"Operator names are redacted in the county dataset. "
                    f"Use claimed_sqft to anchor the electricity estimate."
                ),
            },
        )]

        # Individual building records
        for f in features:
            sqm  = f.get("Shape__Area") or f.get("Shape_Area") or 0
            sqft = sqm * SQM_TO_SQFT
            name = f.get("Name", "Data Center building")
            folder = f.get("FolderPath", "")
            acres = _parse_acreage(name)
            year  = _parse_folder_year(folder)
            docs.append(ScrapedDocument(
                title=f"Loudoun GIS Building: {name}",
                document_type="planning_doc",
                source_scraper=self.name,
                url="https://data-loudoungis.opendata.arcgis.com/datasets/MxjRokvPm7bjslyR_0",
                issuing_agency="Loudoun County GIS",
                extracted_fields={
                    "claimed_sqft": round(sqft, 0) if sqft else None,
                    "claimed_acres": acres,
                    "shape_area_sqm": round(sqm, 1),
                    "data_year": year,
                    "_source": "loudoun_gis_dc_outlines",
                    "_note": (
                        f"Official county building footprint: {sqft:,.0f} sq ft "
                        f"({acres or '?'} acres). Matched by proximity to site coordinates."
                    ),
                },
            ))

        return docs

    def _permit_portal_link(self, query: SiteQuery) -> ScrapedDocument:
        search_text = (query.address or query.name or "").replace(" ", "+")
        url = f"{PERMIT_PORTAL_URL}?SearchText={search_text}"
        return ScrapedDocument(
            title=f"Loudoun County Permit Portal — Manual Search: {query.name}",
            document_type="planning_doc",
            source_scraper=self.name,
            url=url,
            issuing_agency="Loudoun County",
            extracted_fields={
                "_note": (
                    "Direct link to the Loudoun permit portal. Search for SPEX applications — "
                    "data centers >25,000 sq ft require Special Exception approval. SPEX records "
                    "often contain proposed sq ft, MW utility connections, and water demand. "
                    "Since March 2025, new apps use Conditional Use process (§5-1502) with "
                    "stricter disclosure requirements."
                ),
                "_manual_research": True,
            },
        )
