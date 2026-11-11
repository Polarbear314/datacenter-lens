"""
import_osm_sites.py
-------------------
Queries OpenStreetMap (via the Overpass API) for data center infrastructure
within a bounding box, then imports each result as a site + infrastructure
polygon into the DataCenter Lens database.

This script is footprint-first: it does not require knowing the operator.
Ownership/company info is filled in from OSM tags when available, and can
always be enriched later via the parcel → assessor pipeline.

DEFAULT REGION: Loudoun County, Virginia
  (the data center capital of the world — good for MVP)

USAGE:
  # From the datacenter-lens/ directory with venv active:
  python scripts/import_osm_sites.py

  # Dry run — shows what would be imported, saves nothing:
  python scripts/import_osm_sites.py --dry-run

  # Different bounding box (south, west, north, east):
  python scripts/import_osm_sites.py --bbox "38.7,-77.6,39.2,-77.0"

  # Different API base URL:
  python scripts/import_osm_sites.py --api http://localhost:8000

  # Only import OSM ways/relations tagged as building=data_center
  # (default also includes landuse=industrial + name hints — see QUERIES below)
  python scripts/import_osm_sites.py --strict

HOW IT WORKS:
  1. Sends Overpass QL queries for known data center tags within the bbox.
  2. For each OSM element, converts the geometry to a GeoJSON polygon.
  3. Computes a centroid for the site's lat/lon.
  4. Calls POST /sites/ to create the site record (skips if name+coords already exist).
  5. Calls POST /sites/{id}/infrastructure to save the building outline polygon.

WHAT IT IMPORTS:
  - OSM ways/relations tagged: building=data_center, landuse=data_center,
    man_made=data_center, or name contains "data center/centre" (case-insensitive)
  - Each becomes one Site record with:
      name        = OSM name tag (or "Data Center at <lat>,<lon>")
      operator    = OSM operator tag (if present)
      address     = OSM addr:full or constructed from addr:* tags
      latitude    = centroid latitude
      longitude   = centroid longitude
      status      = "operating" (assumed; OSM tags operating facilities)
      sources     = [{"type": "osm", "url": "https://osm.org/way/<id>", ...}]
  - One infrastructure polygon per element:
      class_label      = "main_building"
      detection_method = "osm_import"
      confidence_score = 0.85 (OSM-tagged facilities are high-confidence)

NATIONAL SCALE:
  This script works for any bounding box in the world.
  To scan the full US, tile it into state-level bboxes and run in batches
  (Overpass has a timeout/node limit per query — ~500k nodes works fine).

KNOWN LIMITATIONS:
  - OSM coverage is uneven outside dense data center clusters.
  - Some facilities are tagged as generic "warehouse" or "industrial" —
    those are NOT imported here (that's the CV pipeline's job for Phase 2).
  - Multipolygon relations are simplified to their outer ring only.
  - Area calculation uses flat-earth approximation (fine for < 50km areas).

REQUIRES:
  - requests (already in requirements_lite.txt)
  - The DataCenter Lens API running at --api (default: http://localhost:8000)
"""

import argparse
import json
import math
import sys
import time
from typing import Optional

import requests

# ── Configuration ──────────────────────────────────────────────────────────────

# Default bounding box: Loudoun County, Virginia
# Format: south, west, north, east (Overpass convention)
DEFAULT_BBOX = "38.85,-77.65,39.15,-77.30"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Seconds to wait between Overpass requests (be polite to the free service)
OVERPASS_RATE_LIMIT = 2.0

# Overpass query timeout in seconds
OVERPASS_TIMEOUT = 60

# ── Overpass queries ───────────────────────────────────────────────────────────
# We run multiple queries and merge results to maximize coverage.
# Each query targets a different OSM tagging convention.

def build_queries(bbox: str, strict: bool = False) -> list[str]:
    """
    Return a list of Overpass QL query strings for the given bounding box.

    strict=True: only return elements explicitly tagged as data centers.
    strict=False (default): also include name-based matches (catches facilities
        tagged as generic industrial that are clearly data centers by name).
    """
    south, west, north, east = [x.strip() for x in bbox.split(",")]
    bb = f"{south},{west},{north},{east}"

    queries = [
        # Explicitly tagged as data center building
        f"""
        [out:json][timeout:{OVERPASS_TIMEOUT}];
        (
          way["building"="data_center"]({bb});
          relation["building"="data_center"]({bb});
          way["landuse"="data_center"]({bb});
          relation["landuse"="data_center"]({bb});
          way["man_made"="data_center"]({bb});
          relation["man_made"="data_center"]({bb});
        );
        out body geom;
        """,
    ]

    if not strict:
        # Name-based matches — catches facilities tagged generically
        queries.append(f"""
        [out:json][timeout:{OVERPASS_TIMEOUT}];
        (
          way["name"~"[Dd]ata [Cc]ent(er|re)"]({bb});
          relation["name"~"[Dd]ata [Cc]ent(er|re)"]({bb});
          way["name"~"[Dd]ata[Cc]ent(er|re)"]({bb});
          relation["name"~"[Dd]ata[Cc]ent(er|re)"]({bb});
        );
        out body geom;
        """)

        # Operator-named facilities (e.g. "Equinix", "AWS", "CyrusOne")
        # These are large companies known to operate data centers
        dc_operators = [
            "Equinix", "CyrusOne", "QTS", "Digital Realty", "Iron Mountain",
            "CoreSite", "NTT", "Vantage", "Aligned", "STACK Infrastructure",
            "Amazon", "AWS", "Microsoft", "Google", "Meta", "Apple",
            "Cloudflare", "Oracle", "IBM", "Rackspace", "Switch",
        ]
        operator_pattern = "|".join(dc_operators)
        queries.append(f"""
        [out:json][timeout:{OVERPASS_TIMEOUT}];
        (
          way["operator"~"{operator_pattern}"]["building"]({bb});
          relation["operator"~"{operator_pattern}"]["building"]({bb});
        );
        out body geom;
        """)

    return queries


# ── Geometry helpers ───────────────────────────────────────────────────────────

def way_to_polygon(geometry: list[dict]) -> Optional[dict]:
    """
    Convert an OSM way geometry (list of {lat, lon} nodes) to a GeoJSON Polygon.
    Returns None if the geometry is invalid (< 3 nodes).
    """
    if not geometry or len(geometry) < 3:
        return None

    # GeoJSON uses [lon, lat] order
    coords = [[node["lon"], node["lat"]] for node in geometry]

    # Close the ring if not already closed
    if coords[0] != coords[-1]:
        coords.append(coords[0])

    return {
        "type": "Polygon",
        "coordinates": [coords]
    }


def relation_to_polygon(members: list[dict]) -> Optional[dict]:
    """
    Extract the outer ring from an OSM multipolygon relation.
    Returns a GeoJSON Polygon using only the outer ring (simplified).
    """
    for member in members:
        if member.get("role") == "outer" and member.get("type") == "way":
            geometry = member.get("geometry", [])
            if geometry:
                return way_to_polygon(geometry)
    return None


def centroid_of_polygon(polygon: dict) -> tuple[float, float]:
    """
    Compute the geographic centroid of a GeoJSON Polygon.
    Uses simple average of coordinate pairs (accurate enough for < 10km areas).
    Returns (latitude, longitude).
    """
    coords = polygon["coordinates"][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def area_sqft_of_polygon(polygon: dict) -> float:
    """
    Estimate the area of a GeoJSON Polygon in square feet.
    Uses the Shoelace formula on a flat-earth approximation.
    Accurate to within ~1% for data center-sized areas (< 5km).
    """
    coords = polygon["coordinates"][0]
    if len(coords) < 3:
        return 0.0

    # Convert to approximate meters using a local origin
    lat0 = coords[0][1]
    lon0 = coords[0][0]
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))

    points = [
        (
            (c[0] - lon0) * meters_per_deg_lon,
            (c[1] - lat0) * meters_per_deg_lat,
        )
        for c in coords
    ]

    # Shoelace formula
    n = len(points)
    area_m2 = abs(
        sum(
            points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1]
            for i in range(n)
        )
    ) / 2.0

    # 1 m² = 10.7639 ft²
    return area_m2 * 10.7639


# ── OSM tag helpers ────────────────────────────────────────────────────────────

def build_address(tags: dict) -> Optional[str]:
    """Construct a human-readable address from OSM addr:* tags."""
    addr_full = tags.get("addr:full")
    if addr_full:
        return addr_full

    parts = []
    housenumber = tags.get("addr:housenumber", "")
    street      = tags.get("addr:street", "")
    city        = tags.get("addr:city", "")
    state       = tags.get("addr:state", "")
    postcode    = tags.get("addr:postcode", "")

    if housenumber and street:
        parts.append(f"{housenumber} {street}")
    elif street:
        parts.append(street)

    if city:
        parts.append(city)
    if state:
        parts.append(state)
    if postcode:
        parts.append(postcode)

    return ", ".join(parts) if parts else None


def build_site_name(tags: dict, lat: float, lon: float) -> str:
    """Determine a display name for the site from OSM tags."""
    name     = tags.get("name")
    operator = tags.get("operator")
    brand    = tags.get("brand")

    if name:
        return name
    if operator:
        addr = build_address(tags)
        if addr:
            return f"{operator} — {addr}"
        return f"{operator} Data Center ({lat:.4f}, {lon:.4f})"
    if brand:
        return f"{brand} Data Center"

    return f"Data Center at {lat:.4f}, {lon:.4f}"


def build_sources(osm_type: str, osm_id: int, tags: dict) -> list[dict]:
    """Build the sources list for a site."""
    osm_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
    sources = [
        {
            "type":  "osm",
            "url":   osm_url,
            "title": f"OpenStreetMap: {osm_type}/{osm_id}",
        }
    ]

    website = tags.get("website") or tags.get("url") or tags.get("contact:website")
    if website:
        sources.append({
            "type":  "company_website",
            "url":   website,
            "title": "Operator website (from OSM tag)",
        })

    return sources


# ── Overpass fetch ─────────────────────────────────────────────────────────────

def fetch_overpass(query: str) -> list[dict]:
    """
    Execute an Overpass QL query and return the list of OSM elements.
    Raises requests.HTTPError on failure.
    """
    resp = requests.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=OVERPASS_TIMEOUT + 10,
        headers={"User-Agent": "DataCenterLens/0.1 (civic-tech research tool; contact: research@datacenter-lens.org)"},
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("elements", [])


def deduplicate_elements(elements: list[dict]) -> list[dict]:
    """Remove duplicate OSM elements (same type + id) that appear across multiple queries."""
    seen = set()
    result = []
    for el in elements:
        key = (el.get("type"), el.get("id"))
        if key not in seen:
            seen.add(key)
            result.append(el)
    return result


# ── API helpers ────────────────────────────────────────────────────────────────

def check_api(base_url: str) -> bool:
    try:
        r = requests.get(f"{base_url}/health", timeout=5)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"✗ Cannot reach API at {base_url}: {e}")
        print("  Start the server: uvicorn app_lite:app --reload")
        return False


def get_existing_sites(base_url: str) -> set[tuple]:
    """
    Return a set of (name, rounded_lat, rounded_lon) tuples for all existing sites.
    Used to skip duplicates.
    """
    r = requests.get(f"{base_url}/sites/", timeout=10)
    r.raise_for_status()
    existing = set()
    for site in r.json():
        lat = round(site.get("latitude") or 0, 4)
        lon = round(site.get("longitude") or 0, 4)
        existing.add((site["name"], lat, lon))
    return existing


def create_site(base_url: str, payload: dict) -> Optional[dict]:
    r = requests.post(f"{base_url}/sites/", json=payload, timeout=10)
    if r.status_code == 201:
        return r.json()
    print(f"  ✗ Failed to create site: {r.status_code} — {r.text[:200]}")
    return None


def classify_element(tags: dict) -> str:
    """
    Decide the infrastructure class from OSM tags.
    landuse/man_made polygons without a building tag are site parcels ("campus"),
    not building footprints — labeling them main_building inflates estimates.
    """
    if tags.get("building"):
        return "main_building"
    if tags.get("landuse") or tags.get("man_made"):
        return "campus"
    return "main_building"


def add_infrastructure(base_url: str, site_id: str, polygon: dict, area_sqft: float,
                       class_label: str = "main_building") -> bool:
    payload = {
        "class_label":      class_label,
        "geometry":         polygon,
        "area_sqft":        round(area_sqft, 1),
        "confidence_score": 0.85,
        "detection_method": "osm_import",
        "imagery_source":   "OpenStreetMap",
        "imagery_date":     None,
    }
    r = requests.post(f"{base_url}/sites/{site_id}/infrastructure", json=payload, timeout=10)
    return r.status_code == 201


# ── Main import logic ──────────────────────────────────────────────────────────

def process_element(el: dict) -> Optional[dict]:
    """
    Convert a single OSM element into an import record:
      { site_payload, polygon, area_sqft, osm_type, osm_id }
    Returns None if the element cannot be converted (no geometry, etc.)
    """
    osm_type = el.get("type")
    osm_id   = el.get("id")
    tags     = el.get("tags", {})

    # Build polygon from geometry
    polygon = None
    if osm_type == "way":
        geometry = el.get("geometry", [])
        polygon  = way_to_polygon(geometry)
    elif osm_type == "relation":
        members = el.get("members", [])
        polygon = relation_to_polygon(members)

    if not polygon:
        return None

    lat, lon     = centroid_of_polygon(polygon)
    area_sqft    = area_sqft_of_polygon(polygon)
    name         = build_site_name(tags, lat, lon)
    address      = build_address(tags)
    operator     = tags.get("operator") or tags.get("brand") or None
    sources      = build_sources(osm_type, osm_id, tags)

    # Determine status from OSM tags
    lifecycle = tags.get("lifecycle:phase") or tags.get("construction") or ""
    if "construction" in tags or lifecycle == "construction":
        status = "under_construction"
    elif tags.get("disused") or tags.get("abandoned"):
        status = "decommissioned"
    else:
        status = "operating"

    site_payload = {
        "name":      name,
        "address":   address,
        "operator":  operator,
        "latitude":  round(lat, 6),
        "longitude": round(lon, 6),
        "status":    status,
        "sources":   sources,
        "notes":     (
            f"Imported from OpenStreetMap ({osm_type}/{osm_id}). "
            f"Tags: {json.dumps({k: v for k, v in tags.items() if k not in ('name', 'operator', 'addr:full')})[:300]}"
        ),
    }

    return {
        "site_payload": site_payload,
        "polygon":      polygon,
        "area_sqft":    area_sqft,
        "class_label":  classify_element(tags),
        "osm_type":     osm_type,
        "osm_id":       osm_id,
        "name":         name,
    }


def run_import(
    base_url: str,
    bbox: str,
    dry_run: bool = False,
    strict: bool  = False,
    verbose: bool = True,
) -> dict:
    """
    Full import pipeline. Returns summary dict.

    Parameters
    ----------
    base_url : str  API base URL
    bbox     : str  "south,west,north,east"
    dry_run  : bool If True, print what would happen but save nothing
    strict   : bool If True, only import explicitly-tagged data centers
    verbose  : bool Print progress to stdout
    """

    def log(msg):
        if verbose:
            print(msg)

    log(f"\n{'[DRY RUN] ' if dry_run else ''}DataCenter Lens — OSM Import")
    log(f"  Bounding box : {bbox}")
    log(f"  Strict mode  : {strict}")
    log(f"  API endpoint : {base_url}\n")

    # Step 1: Check API is up
    if not dry_run:
        if not check_api(base_url):
            return {"status": "error", "message": "API unreachable"}

    # Step 2: Fetch OSM data
    queries  = build_queries(bbox, strict=strict)
    elements = []
    for i, query in enumerate(queries, 1):
        log(f"Querying Overpass ({i}/{len(queries)})...")
        try:
            batch = fetch_overpass(query)
            log(f"  → {len(batch)} elements returned")
            elements.extend(batch)
        except Exception as e:
            log(f"  ✗ Query {i} failed: {e}")
        if i < len(queries):
            time.sleep(OVERPASS_RATE_LIMIT)

    elements = deduplicate_elements(elements)
    log(f"\nTotal unique OSM elements: {len(elements)}")

    if not elements:
        log("No data center elements found. Try a larger bounding box or disable --strict.")
        return {"status": "ok", "found": 0, "imported": 0, "skipped": 0, "failed": 0}

    # Step 3: Process elements into import records
    records = []
    for el in elements:
        rec = process_element(el)
        if rec:
            records.append(rec)
        else:
            log(f"  ⚠ Skipped {el.get('type')}/{el.get('id')} — no geometry")

    log(f"Valid records with geometry: {len(records)}")

    if dry_run:
        log("\n── DRY RUN PREVIEW ──────────────────────────────────────────────")
        for rec in records:
            lat  = rec["site_payload"]["latitude"]
            lon  = rec["site_payload"]["longitude"]
            area = rec["area_sqft"]
            op   = rec["site_payload"].get("operator") or "operator unknown"
            log(f"  [{rec['osm_type']}/{rec['osm_id']}] {rec['name']}")
            log(f"    operator={op}  lat={lat}  lon={lon}  area={area:,.0f} sqft")
        log(f"\nWould import {len(records)} site(s). Run without --dry-run to save.")
        return {
            "status":   "dry_run",
            "found":    len(elements),
            "valid":    len(records),
            "imported": 0,
            "skipped":  0,
            "failed":   0,
            "records":  records,
        }

    # Step 4: Get existing sites to avoid duplicates
    log("\nChecking for existing sites...")
    try:
        existing = get_existing_sites(base_url)
        log(f"  {len(existing)} site(s) already in database")
    except Exception as e:
        log(f"  ✗ Could not fetch existing sites: {e}")
        existing = set()

    # Step 5: Import
    imported = 0
    skipped  = 0
    failed   = 0

    log(f"\nImporting {len(records)} site(s)...\n")
    for rec in records:
        sp  = rec["site_payload"]
        lat = round(sp["latitude"], 4)
        lon = round(sp["longitude"], 4)

        # Skip if name+coords already exist
        if (sp["name"], lat, lon) in existing:
            log(f"  → SKIP (already exists): {sp['name']}")
            skipped += 1
            continue

        # Create the site
        site = create_site(base_url, sp)
        if not site:
            failed += 1
            continue

        log(f"  ✓ Created: {site['name']} (id: {site['id'][:8]}...)")

        # Add the building outline polygon
        ok = add_infrastructure(base_url, site["id"], rec["polygon"], rec["area_sqft"],
                                class_label=rec.get("class_label", "main_building"))
        if not ok:
            log(f"    ⚠ Site created but polygon import failed for {site['id'][:8]}")

        existing.add((sp["name"], lat, lon))
        imported += 1

    # ── Summary ──────────────────────────────────────────────────────────────
    log("\n── IMPORT COMPLETE ──────────────────────────────────────────────────")
    log(f"  OSM elements fetched  : {len(elements)}")
    log(f"  Valid (had geometry)  : {len(records)}")
    log(f"  Imported              : {imported}")
    log(f"  Skipped (duplicates)  : {skipped}")
    log(f"  Failed                : {failed}")
    log(f"\nView your sites at: {base_url}/app")
    log(f"API docs:           {base_url}/docs")

    return {
        "status":   "ok",
        "found":    len(elements),
        "valid":    len(records),
        "imported": imported,
        "skipped":  skipped,
        "failed":   failed,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Import OSM data center sites into DataCenter Lens",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/import_osm_sites.py
  python scripts/import_osm_sites.py --dry-run
  python scripts/import_osm_sites.py --bbox "33.2,-112.5,33.7,-111.8"   # Phoenix, AZ
  python scripts/import_osm_sites.py --bbox "41.6,-88.5,42.1,-87.5"     # Chicago area
  python scripts/import_osm_sites.py --strict --dry-run
        """
    )
    parser.add_argument(
        "--bbox",
        default=DEFAULT_BBOX,
        help=f"Bounding box: south,west,north,east (default: Loudoun County, VA = {DEFAULT_BBOX})",
    )
    parser.add_argument(
        "--api",
        default="http://localhost:8000",
        help="DataCenter Lens API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be imported without saving anything",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Only import elements explicitly tagged as data centers (no name/operator heuristics)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args   = parse_args()
    result = run_import(
        base_url=args.api,
        bbox=args.bbox,
        dry_run=args.dry_run,
        strict=args.strict,
    )
    sys.exit(0 if result.get("status") in ("ok", "dry_run") else 1)
