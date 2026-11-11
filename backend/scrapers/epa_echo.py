"""
scrapers/epa_echo.py  —  EPA ECHO facility search for water permit data
------------------------------------------------------------------------
WHAT EPA ECHO IS
  EPA's Enforcement and Compliance History Online (ECHO) system tracks
  facilities regulated under federal environmental laws. For data centers,
  the most relevant program is the Clean Water Act (CWA), specifically
  NPDES (National Pollutant Discharge Elimination System) permits.

WHY THIS HELPS WATER CONFIDENCE
  Data centers using wet/evaporative cooling discharge concentrated mineral
  water (blowdown) from their cooling towers. If they discharge to:
    - A waterway → they need an individual NPDES permit (tracked in ECHO)
    - Municipal sewer → they need an industrial pretreatment permit (sometimes
      tracked in ECHO under the POTW's permit, sometimes not)

  The presence of an NPDES permit confirms:
    1. The facility uses wet cooling (cooling towers produce blowdown)
    2. The facility is large enough to trigger permitting thresholds
    3. The actual discharge volume may be in their Discharge Monitoring Reports (DMR)

  Even without exact discharge volumes, CWA permit existence is strong
  evidence for wet cooling, which raises the cooling method confidence
  and therefore narrows the water use estimate.

ECHO API
  Base: https://echodata.epa.gov/echo/
  Step 1: echo_rest_services.get_facilities  → get QueryID + count
  Step 2: echo_rest_services.get_qid         → get actual facility records
  Step 3: (optional) facility detail page    → get NPDES permit IDs

  Key search params:
    p_fn   — facility name (partial match)
    p_st   — state (2-letter)
    p_co   — county name
    p_lat, p_lon, p_radius_miles — coordinate + radius
    output — JSON

SCALES NATIONALLY
  This scraper requires no state-specific configuration. Any data center
  operator name + coordinates works anywhere in the US. ECHO covers all
  50 states and territories.

KNOWN LIMITATIONS
  - Most Loudoun County data centers discharge to Loudoun Water's sewer
    system, not directly to waterways. Those won't have individual NPDES
    permits in ECHO. ECHO will return 0 CWA records for most sites.
  - ECHO's facility names are often abbreviated or use legal entity names
    (e.g., "AMAZON DATA SERVICES INC" not "Amazon AWS").
  - When ECHO finds nothing, this is not evidence of dry cooling — it
    just means the facility uses municipal sewer discharge.
"""

import re
import time
from datetime import date
from typing import Optional
import httpx

from .base import BaseScraper, SiteQuery, ScrapedDocument, ScraperError

RATE_LIMIT_DELAY = 1.5

ECHO_SEARCH_URL  = "https://echodata.epa.gov/echo/echo_rest_services.get_facilities"
ECHO_QID_URL     = "https://echodata.epa.gov/echo/echo_rest_services.get_qid"
ECHO_DETAIL_BASE = "https://echo.epa.gov/facilities/facility-search/results/facility-detail"

# Operator name aliases — ECHO uses legal entity names
OPERATOR_ALIASES: dict[str, list[str]] = {
    "equinix":   ["Equinix"],
    "amazon":    ["Amazon Data Services", "Amazon Com Services", "AWS"],
    "aws":       ["Amazon Data Services", "Amazon Com Services"],
    "qts":       ["QTS", "Quality Technology Services"],
    "cyrusone":  ["CyrusOne", "Cyrus One"],
    "stack":     ["STACK Infrastructure", "Stack Infrastructure"],
    "google":    ["Google"],
    "microsoft": ["Microsoft"],
    "meta":      ["Meta Platforms", "Facebook"],
    "coresite":  ["CoreSite"],
    "ironmountain": ["Iron Mountain"],
}


def _get_aliases(operator: str) -> list[str]:
    key = operator.lower().replace(" ", "").replace("-", "")
    for pattern, aliases in OPERATOR_ALIASES.items():
        if pattern in key:
            return aliases
    # Fall back to first word of operator name
    first = operator.split()[0] if operator else operator
    return [first]


def _fetch(url: str, params: dict) -> dict:
    try:
        resp = httpx.get(url, params=params, timeout=20,
                         headers={"User-Agent": "DataCenterLens/0.1 (public-interest research)"},
                         follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        raise ScraperError(f"ECHO HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        raise ScraperError(f"ECHO request failed: {e}")
    except Exception as e:
        raise ScraperError(f"ECHO parse error: {e}")


def _near_site(fac_lat: Optional[float], fac_lon: Optional[float],
               site_lat: float, site_lon: float, radius_deg: float = 0.05) -> bool:
    if fac_lat is None or fac_lon is None:
        return True  # can't filter, keep it
    try:
        return abs(float(fac_lat) - site_lat) < radius_deg and \
               abs(float(fac_lon) - site_lon) < radius_deg
    except (TypeError, ValueError):
        return True


class EPAECHOScraper(BaseScraper):
    name = "epa_echo"

    def search(self, query: SiteQuery) -> list[ScrapedDocument]:
        docs: list[ScrapedDocument] = []
        seen_ids: set[str] = set()

        if not query.operator and not (query.latitude and query.longitude):
            docs.append(self._no_data_doc(query, "No operator name or coordinates — cannot search ECHO."))
            return docs

        names_to_try = _get_aliases(query.operator) if query.operator else []

        # ── Strategy 1: search by operator name + state ──────────────────────
        for name in names_to_try[:2]:
            time.sleep(RATE_LIMIT_DELAY)
            try:
                new = self._search_by_name(name, query, seen_ids)
                docs.extend(new)
            except ScraperError:
                continue

        # ── Strategy 2: coordinate proximity if name search found nothing ────
        if not docs and query.latitude and query.longitude:
            time.sleep(RATE_LIMIT_DELAY)
            try:
                new = self._search_by_coords(query, seen_ids)
                docs.extend(new)
            except ScraperError:
                pass

        # ── Always add reference link ─────────────────────────────────────────
        docs.append(self._echo_search_link(query))

        if not any(d.extracted_fields.get("registry_id") for d in docs):
            docs.insert(0, self._no_data_doc(
                query,
                "No ECHO records found for this operator in this county. "
                "This is common — most Loudoun County data centers discharge "
                "cooling tower blowdown to municipal sewer (Loudoun Water), "
                "not to waterways, so they don't have individual NPDES permits "
                "tracked in ECHO. This does not indicate dry cooling."
            ))

        return docs[:12]

    def _search_by_name(
        self, name: str, query: SiteQuery, seen_ids: set
    ) -> list[ScrapedDocument]:
        # Step 1: get query ID
        params = {
            "p_fn":  name,
            "p_st":  "VA",
            "output": "JSON",
        }
        if query.latitude and query.longitude:
            params["p_lat"] = str(query.latitude)
            params["p_lon"] = str(query.longitude)
            params["p_radius_miles"] = "10"

        data = _fetch(ECHO_SEARCH_URL, params)
        results = data.get("Results", {})
        qid = results.get("QueryID")
        count = int(results.get("QueryRows", 0) or 0)

        if not qid or count == 0:
            return []

        # Step 2: fetch facility records
        time.sleep(RATE_LIMIT_DELAY)
        fac_data = _fetch(ECHO_QID_URL, {"qid": qid, "output": "JSON"})
        facilities = fac_data.get("Results", {}).get("Facilities", [])

        return self._facilities_to_docs(facilities, query, seen_ids, search_name=name)

    def _search_by_coords(
        self, query: SiteQuery, seen_ids: set
    ) -> list[ScrapedDocument]:
        params = {
            "p_lat":          str(query.latitude),
            "p_lon":          str(query.longitude),
            "p_radius_miles": "1",
            "p_st":           "VA",
            "output":         "JSON",
        }
        data = _fetch(ECHO_SEARCH_URL, params)
        results = data.get("Results", {})
        qid = results.get("QueryID")
        count = int(results.get("QueryRows", 0) or 0)

        if not qid or count == 0:
            return []

        time.sleep(RATE_LIMIT_DELAY)
        fac_data = _fetch(ECHO_QID_URL, {"qid": qid, "output": "JSON"})
        facilities = fac_data.get("Results", {}).get("Facilities", [])

        return self._facilities_to_docs(facilities, query, seen_ids, search_name=None)

    def _facilities_to_docs(
        self,
        facilities: list[dict],
        query: SiteQuery,
        seen_ids: set,
        search_name: Optional[str],
    ) -> list[ScrapedDocument]:
        docs = []
        site_lat = query.latitude or 0.0
        site_lon = query.longitude or 0.0

        for fac in facilities:
            reg_id = fac.get("RegistryID", "")
            if reg_id in seen_ids:
                continue

            fac_lat = fac.get("FacLat")
            fac_lon = fac.get("FacLong") or fac.get("FacLon")

            # Filter by proximity if we have coords
            if query.latitude and query.longitude:
                if not _near_site(fac_lat, fac_lon, site_lat, site_lon, radius_deg=0.12):
                    continue

            seen_ids.add(reg_id)

            fac_name = fac.get("FacName", "Unknown facility")
            address  = f"{fac.get('FacStreet','')} {fac.get('FacCity','')} {fac.get('FacState','')} {fac.get('FacZip','')}".strip()
            cwa_status = fac.get("CWAComplianceStatus", "")
            has_cwa  = bool(cwa_status and cwa_status.strip() not in ("", "No Violation", "No Data"))
            caa_status = fac.get("CAAComplianceStatus", "")

            # CWA compliance history — non-empty means they have active CWA permits
            cwa_history = fac.get("CWA13qtrsEfflntExceedances", "")

            # If CWA data exists → confirms permit → confirms water discharge → confirms wet cooling
            if has_cwa or cwa_history:
                cooling_signal = "wet"
                cooling_note = (
                    "ECHO shows an active CWA/NPDES permit for this facility. "
                    "Presence of an NPDES permit confirms cooling tower blowdown discharge, "
                    "which confirms wet/evaporative cooling. "
                    "This anchors the cooling method to 'wet' with high confidence."
                )
            else:
                cooling_signal = None
                cooling_note = (
                    "No active CWA/NPDES permit found for this facility in ECHO. "
                    "This likely means discharge goes to municipal sewer (Loudoun Water), "
                    "not a waterway — which is normal and does not indicate dry cooling."
                )

            detail_url = f"{ECHO_DETAIL_BASE}?id={reg_id}" if reg_id else None

            extracted: dict = {
                "registry_id":     reg_id or None,
                "facility_name":   fac_name,
                "address":         address or None,
                "cwa_status":      cwa_status or None,
                "has_cwa_permit":  has_cwa or bool(cwa_history),
                "cooling_signal":  cooling_signal,
                "_source":         "epa_echo",
                "_note":           cooling_note,
            }
            if has_cwa or cwa_history:
                # This is the key signal — cooling method confirmed as wet
                extracted["confirmed_cooling_method"] = "wet"

            docs.append(ScrapedDocument(
                title=f"EPA ECHO: {fac_name}" + (" [CWA permit]" if has_cwa else ""),
                document_type="environmental_review",
                source_scraper=self.name,
                url=detail_url,
                issuing_agency="U.S. EPA ECHO",
                extracted_fields=extracted,
            ))

        return docs

    def _echo_search_link(self, query: SiteQuery) -> ScrapedDocument:
        op = (query.operator or query.name or "").replace(" ", "+")
        url = f"https://echo.epa.gov/facilities/facility-search/results?p_fn={op}&p_st=VA"
        return ScrapedDocument(
            title=f"EPA ECHO Facility Search — {query.operator or query.name} (Virginia)",
            document_type="environmental_review",
            source_scraper=self.name,
            url=url,
            issuing_agency="U.S. EPA",
            extracted_fields={
                "_note": (
                    "Direct link to ECHO facility search. Filter by 'Clean Water Act' program "
                    "to find NPDES permits. CWA permit = cooling tower blowdown discharge = "
                    "wet cooling confirmed. Works for any US state — change p_st parameter."
                ),
                "_manual_research": True,
            },
        )

    def _no_data_doc(self, query: SiteQuery, note: str) -> ScrapedDocument:
        return ScrapedDocument(
            title=f"EPA ECHO: No CWA permit records found — {query.name}",
            document_type="environmental_review",
            source_scraper=self.name,
            url=f"https://echo.epa.gov/facilities/facility-search/results?p_fn={query.operator or ''}&p_st=VA",
            issuing_agency="U.S. EPA ECHO",
            extracted_fields={"_note": note, "_source": "epa_echo"},
        )
