"""
scrapers/property_records.py  —  Loudoun County property assessment records
----------------------------------------------------------------------------
WHY THIS MATTERS
  Many of the 215 OSM-imported sites have "operator unknown" because OSM
  doesn't tag every building. Property assessment records are public in
  Virginia and contain:

  - Current owner name (often an LLC shell, e.g. "IAD PROPCO 1 LLC")
  - Sale history (price + date reveals when a data center company acquired land)
  - Assessed value (useful for fiscal impact estimation)
  - Land area in acres
  - Building square footage (from the assessor's parcel data)
  - Zoning code (confirms data center permitted use)

  The LLC name is usually traceable to the operator via corporate registry
  (Virginia SCC business search or OpenCorporates).

DATA SOURCE
  Loudoun County Real Property Assessment Search
  URL: https://icare.loudoun.gov/LoudounProd/faces/jsp/home.jsp
  API: The underlying ICARE system has a REST-like query endpoint.

  Additionally, Loudoun County's ArcGIS OpenData portal publishes the full
  parcel dataset as a public FeatureServer:
  https://services1.arcgis.com/MxjRokvPm7bjslyR/arcgis/rest/services/

STRATEGY
  1. Coordinate-based parcel lookup via ArcGIS parcel layer
     → Returns parcel ID, owner name, assessed value, land area
  2. Fallback: manual link to the ICARE property search
  3. Virginia SCC business search link for LLC resolution

NATIONAL SCALE NOTE
  Every state and county has different property record systems.
  For national expansion, we'll need a county-specific adapter pattern.
  This scraper is the Loudoun-specific implementation.
  Recommended future scrapers: Maricopa County AZ, Clark County NV,
  Cook County IL, King County WA (all major DC markets with open data portals).
"""

import re
import time
from datetime import date, datetime
from typing import Optional
import httpx

from .base import BaseScraper, SiteQuery, ScrapedDocument, ScraperError

RATE_LIMIT_DELAY = 1.0

# Loudoun County parcel layer (ArcGIS Online — public, no auth required)
PARCEL_LAYER_URL = (
    "https://services1.arcgis.com/MxjRokvPm7bjslyR/arcgis/rest/services"
    "/Loudoun_County_Parcels/FeatureServer/0/query"
)

# Fallback: the human-readable property search
ICARE_URL    = "https://icare.loudoun.gov/LoudounProd/faces/jsp/home.jsp"
VA_SCC_URL   = "https://cis.scc.virginia.gov/"

# Radius for parcel proximity search (degrees, ~0.3 mile)
PARCEL_RADIUS = 0.004

# Known LLC → operator mappings for Loudoun County
# These are public record; LLC names come from county assessment rolls
KNOWN_LLC_OPERATORS: dict[str, str] = {
    "amazon": "Amazon Web Services",
    "aws": "Amazon Web Services",
    "iad propco": "Amazon Web Services",
    "equinix": "Equinix",
    "switch and data": "Equinix",
    "digital realty": "Digital Realty",
    "dupont fabros": "Digital Realty",
    "dft": "Digital Realty",
    "qts": "QTS (Blackstone)",
    "quality technology": "QTS (Blackstone)",
    "cyrusone": "CyrusOne (KKR)",
    "cyrus one": "CyrusOne (KKR)",
    "stack": "STACK Infrastructure",
    "coresite": "CoreSite (American Tower)",
    "vantage": "Vantage Data Centers",
    "ntt": "NTT",
    "databank": "DataBank",
    "iron mountain": "Iron Mountain",
    "microsoft": "Microsoft",
    "google": "Google",
    "meta": "Meta",
    "facebook": "Meta",
    "cloudflare": "Cloudflare",
    "centersquare": "Centersquare",
    "aligned": "Aligned Data Centers",
    "sentient": "Sentient Data Centers",
    "sabey": "Sabey Data Centers",
    "cologix": "Cologix",
    "powerhouse": "PowerHouse Data Centers",
}


def _infer_operator_from_llc(owner: str) -> Optional[str]:
    """Try to map a raw owner/LLC name to a known data center operator."""
    if not owner:
        return None
    lower = owner.lower()
    for pattern, operator in KNOWN_LLC_OPERATORS.items():
        if pattern in lower:
            return operator
    return None


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"]:
        try:
            return datetime.strptime(str(date_str).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _query_parcels(lat: float, lon: float) -> list[dict]:
    """
    Query Loudoun County parcel layer by bounding box around site coordinates.
    Returns raw feature attribute dicts.
    """
    params = {
        "where":           "1=1",
        "geometry":        f"{lon-PARCEL_RADIUS},{lat-PARCEL_RADIUS},{lon+PARCEL_RADIUS},{lat+PARCEL_RADIUS}",
        "geometryType":    "esriGeometryEnvelope",
        "inSR":            "4326",
        "outSR":           "4326",
        "spatialRel":      "esriSpatialRelIntersects",
        "outFields":       "*",
        "returnGeometry":  "false",
        "resultRecordCount": 10,
        "f":               "json",
    }
    try:
        resp = httpx.get(
            PARCEL_LAYER_URL,
            params=params,
            timeout=20,
            headers={"User-Agent": "DataCenterLens/0.1 (public-interest research)"},
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        raise ScraperError(f"Parcel layer HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        raise ScraperError(f"Parcel layer request failed: {e}")

    if "error" in data:
        raise ScraperError(f"ArcGIS error: {data['error'].get('message', 'unknown')}")

    return [f.get("attributes", {}) for f in data.get("features", [])]


class PropertyRecordsScraper(BaseScraper):
    name = "property_records"

    def search(self, query: SiteQuery) -> list[ScrapedDocument]:
        docs: list[ScrapedDocument] = []

        # Source 1: Parcel layer proximity query
        if query.latitude and query.longitude:
            try:
                docs.extend(self._search_parcels(query))
            except ScraperError as e:
                docs.append(ScrapedDocument(
                    title=f"Loudoun parcel query failed: {e}",
                    document_type="other",
                    source_scraper=self.name,
                    extracted_fields={"_error": str(e)},
                ))
        else:
            docs.append(ScrapedDocument(
                title="Property records — no coordinates for this site",
                document_type="other",
                source_scraper=self.name,
                extracted_fields={"_note": "Add lat/lon to enable parcel lookup."},
            ))

        # Source 2: Manual research links (always included)
        docs.extend(self._manual_research_links(query))

        return docs

    def _search_parcels(self, query: SiteQuery) -> list[ScrapedDocument]:
        parcels = _query_parcels(query.latitude, query.longitude)

        if not parcels:
            return [ScrapedDocument(
                title=f"Property records: No parcels found near {query.name}",
                document_type="other",
                source_scraper=self.name,
                extracted_fields={
                    "_note": (
                        "No parcels returned from Loudoun County parcel layer "
                        f"within ~0.3 miles of ({query.latitude}, {query.longitude}). "
                        "Try the manual ICARE search below."
                    ),
                },
            )]

        docs = []
        for attrs in parcels:
            # Field names vary across ArcGIS parcel datasets — try common variants
            owner     = (
                attrs.get("OWNER") or attrs.get("Owner") or
                attrs.get("OWNERNAME") or attrs.get("OwnerName") or
                attrs.get("GRANTEE") or ""
            ).strip()

            parcel_id = (
                attrs.get("PIN") or attrs.get("ParcelID") or
                attrs.get("PARCEL_ID") or attrs.get("APN") or
                attrs.get("OBJECTID") or ""
            )

            address   = (
                attrs.get("SITEADDRESS") or attrs.get("SiteAddress") or
                attrs.get("ADDRESS") or attrs.get("ADDR") or ""
            ).strip()

            assessed  = (
                attrs.get("ASSESSED") or attrs.get("AssessedValue") or
                attrs.get("TOTALVALUE") or attrs.get("ASMTVALUE") or 0
            )

            land_area = (
                attrs.get("LANDAREA") or attrs.get("LandArea") or
                attrs.get("ACRES") or attrs.get("Shape__Area") or 0
            )

            sale_price = (
                attrs.get("SALEPRICE") or attrs.get("SalePrice") or
                attrs.get("SALESPRICE") or 0
            )

            sale_date_raw = (
                attrs.get("SALEDATE") or attrs.get("SaleDate") or
                attrs.get("SALESDATE") or ""
            )

            zoning = (
                attrs.get("ZONING") or attrs.get("Zoning") or
                attrs.get("ZONE") or ""
            ).strip()

            # Try to infer operator from LLC name
            inferred_op = _infer_operator_from_llc(owner)
            if not owner:
                owner = "Unknown owner"

            # Build ICARE direct link if we have a parcel ID
            icare_url = (
                f"https://icare.loudoun.gov/LoudounProd/faces/jsp/home.jsp"
                if not parcel_id
                else f"https://icare.loudoun.gov/LoudounProd/faces/jsp/home.jsp"
                     f"?search=parcel&pin={parcel_id}"
            )

            extracted: dict = {
                "parcel_id":        str(parcel_id) if parcel_id else None,
                "owner_name":       owner,
                "address":          address or None,
                "zoning":           zoning or None,
                "assessed_value":   float(assessed) if assessed else None,
                "land_area_raw":    land_area,
                "sale_price":       float(sale_price) if sale_price else None,
                "sale_date":        str(sale_date_raw) if sale_date_raw else None,
                "_source":          "loudoun_parcel_layer",
                "_note": (
                    f"Owner: {owner}. "
                    + (f"Inferred operator: {inferred_op}. " if inferred_op else
                       "Operator not recognized — search LLC name at Virginia SCC. ")
                    + (f"Assessed: ${float(assessed):,.0f}. " if assessed else "")
                    + (f"Zoning: {zoning}. " if zoning else "")
                    + "Use parcel ID at ICARE portal for full permit history."
                ),
            }

            if inferred_op:
                extracted["inferred_operator"] = inferred_op

            title = f"Parcel Record: {owner}"
            if address:
                title += f" — {address}"
            if inferred_op and inferred_op != (query.operator or ""):
                title += f" [likely {inferred_op}]"

            docs.append(ScrapedDocument(
                title=title,
                document_type="planning_doc",
                source_scraper=self.name,
                url=icare_url,
                publication_date=_parse_date(str(sale_date_raw)) if sale_date_raw else None,
                issuing_agency="Loudoun County Assessor",
                extracted_fields=extracted,
            ))

        return docs

    def _manual_research_links(self, query: SiteQuery) -> list[ScrapedDocument]:
        """
        Return curated manual research links:
        1. ICARE property search (Loudoun permit + assessment portal)
        2. Virginia SCC business entity search (resolves LLCs to operators)
        """
        search_text = (query.address or query.name or "").replace(" ", "+")

        return [
            ScrapedDocument(
                title=f"Loudoun County ICARE — Property & Permit Search: {query.name}",
                document_type="planning_doc",
                source_scraper=self.name,
                url=f"https://icare.loudoun.gov/LoudounProd/faces/jsp/home.jsp?search=address&addr={search_text}",
                issuing_agency="Loudoun County",
                extracted_fields={
                    "_note": (
                        "Loudoun County's property and permit portal. "
                        "Search by address or parcel ID to find: owner name, "
                        "assessed value, zoning, building permits, SPEX applications. "
                        "Data centers >25,000 sq ft require SPEX (Special Exception) — "
                        "SPEX files often contain MW load, sq ft, water demand, traffic studies. "
                        "Since March 2025, Conditional Use process (§5-1502) with "
                        "stricter disclosure."
                    ),
                    "_manual_research": True,
                },
            ),
            ScrapedDocument(
                title=f"Virginia SCC Business Entity Search — Resolve LLC owner: {query.name}",
                document_type="other",
                source_scraper=self.name,
                url=VA_SCC_URL,
                issuing_agency="Virginia State Corporation Commission",
                extracted_fields={
                    "_note": (
                        "Use to trace LLC names to parent companies. "
                        "Example: 'IAD PROPCO 1 LLC' → search at cis.scc.virginia.gov → "
                        "find registered agent → likely Amazon Web Services. "
                        "Shell LLCs are common in data center real estate to limit "
                        "public disclosure of acquisition plans. Registered agent "
                        "law firms often reappear across multiple data center LLCs "
                        "for the same operator."
                    ),
                    "_manual_research": True,
                },
            ),
        ]
