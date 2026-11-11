"""
import_osm_footprints.py
------------------------
Pulls building footprints from OpenStreetMap (via Overpass API) for each
site and imports them as detected_infrastructure polygons.

Run after seeding:
    python3 scripts/import_osm_footprints.py

Flags:
    --clear         Delete ALL existing polygons for matched sites before importing
    --site NAME     Import only one site (partial name match, case-insensitive)

Design principles:
  1. FEWER accurate polygons > MANY inaccurate ones.
  2. Each OSM building is assigned to the NEAREST site only — no cross-site duplicates.
  3. Sites with zero explicitly-tagged DC buildings get a fallback: the top 5 largest
     industrial buildings, marked at low confidence for human review.
  4. All auto-imported polygons are dashed on the map until a human verifies them.
"""

import json, time, math, urllib.request, urllib.parse, sys, argparse
import requests

BASE_URL  = "http://localhost:8000"
OVERPASS  = "https://overpass-api.de/api/interpreter"

# Radius per site. Tight enough to capture a campus without sweeping
# up neighboring office parks or overlapping adjacent sites.
RADIUS_M = 300

# Minimum footprint to import (sq meters). Filters out parking booths,
# guard shacks, small ancillary structures.
MIN_AREA_SQM = 2000   # ~21,500 sq ft

# When falling back (no explicit DC tags found), take only the N largest
# buildings and flag them for human review.
FALLBACK_MAX = 5

# Known data center operators — buildings tagged with these are treated as
# main_building even when the building= tag is generic.
DC_OPERATORS = {
    "equinix", "amazon", "aws", "google", "microsoft", "meta",
    "qts", "cyrusone", "iron mountain", "stack infrastructure",
    "coresite", "digital realty", "ntt", "vadata", "switch",
    "vantage", "aligned", "flexential", "evoque", "t5",
}


# ── OSM tag → infrastructure class ────────────────────────────────────────────

def classify(tags: dict) -> tuple[str | None, bool]:
    """
    Returns (class_label, is_explicit).
    - class_label: the infrastructure type, or None to skip entirely.
    - is_explicit: True when OSM has unambiguous DC evidence; False for fallback candidates.
    """
    b  = tags.get("building", "")
    am = tags.get("amenity", "")
    pw = tags.get("power", "")
    nm = tags.get("name", "").lower()
    op = tags.get("operator", "").lower()

    # Power / utility — always explicit
    if pw in ("substation", "transformer"):
        return "substation", True

    # Explicitly tagged data center buildings
    if b in ("data_center", "server_farm") or am == "data_center":
        return "main_building", True

    # Named cooling or generator infrastructure
    if "cooling" in nm or "chiller" in nm:
        return "cooling_equipment", True
    if "generator" in nm or "genset" in nm:
        return "generators", True
    if "substation" in nm or "electrical" in nm:
        return "substation", True
    if "solar" in nm or b == "solar":
        return "solar", True

    # Buildings operated by a known DC company
    if any(dc in op for dc in DC_OPERATORS):
        return "main_building", True

    # Large industrial/warehouse buildings — fallback candidates only, not imported
    # unless this site has zero explicit results (see run() logic below).
    if b in ("warehouse", "industrial", "yes", "commercial") or tags.get("landuse") == "industrial":
        return "main_building", False

    return None, False


# ── Geometry helpers ──────────────────────────────────────────────────────────

def osm_nodes_to_polygon(nodes: list) -> dict:
    coords = [[n["lon"], n["lat"]] for n in nodes]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}


def polygon_area_sqm(polygon: dict) -> float:
    coords = polygon["coordinates"][0]
    if len(coords) < 3:
        return 0.0
    n = len(coords)
    area_deg2 = 0.0
    for i in range(n - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        area_deg2 += x1 * y2 - x2 * y1
    area_deg2 = abs(area_deg2) / 2.0
    lat_rad = math.radians(coords[0][1])
    return area_deg2 * 111_320 * (111_320 * math.cos(lat_rad))


def centroid(polygon: dict) -> tuple[float, float]:
    """Return (lat, lon) centroid of a GeoJSON polygon."""
    coords = polygon["coordinates"][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def dist_m(lat1, lon1, lat2, lon2) -> float:
    dlat = (lat2 - lat1) * 111_320
    dlon = (lon2 - lon1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.sqrt(dlat ** 2 + dlon ** 2)


# ── Overpass query ────────────────────────────────────────────────────────────

def fetch_buildings(lat: float, lon: float, radius_m: int) -> list:
    """
    Query Overpass for buildings near (lat, lon).
    Returns list of {osm_id, tags, polygon, area_sqm}.
    """
    query = f"""
[out:json][timeout:30];
(
  way["building"](around:{radius_m},{lat},{lon});
  way["power"="substation"](around:{radius_m},{lat},{lon});
  way["amenity"="data_center"](around:{radius_m},{lat},{lon});
);
out geom;
"""
    data = urllib.parse.urlencode({"data": query}).encode()
    req  = urllib.request.Request(
        OVERPASS, data=data,
        headers={"User-Agent": "DataCenterLens/0.1 (civic-tech research tool)"}
    )
    with urllib.request.urlopen(req, timeout=35) as r:
        result = json.loads(r.read())

    out = []
    for el in result.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        polygon = osm_nodes_to_polygon(el["geometry"])
        area    = polygon_area_sqm(polygon)
        if area < MIN_AREA_SQM:
            continue
        out.append({
            "osm_id":  el["id"],
            "tags":    el.get("tags", {}),
            "polygon": polygon,
            "area_sqm": area,
        })
    return out


# ── Per-site clear ────────────────────────────────────────────────────────────

def clear_site_polygons(site_id: str) -> int:
    detail = requests.get(f"{BASE_URL}/sites/{site_id}").json()
    count  = 0
    for infra in detail.get("infrastructure", []):
        res = requests.delete(f"{BASE_URL}/sites/{site_id}/infrastructure/{infra['id']}")
        if res.status_code in (200, 204):
            count += 1
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def run(clear: bool = False, site_filter: str = None):
    try:
        requests.get(f"{BASE_URL}/health", timeout=5).raise_for_status()
    except Exception as e:
        print(f"Cannot reach API: {e}\nMake sure the app is running.")
        sys.exit(1)

    sites = requests.get(f"{BASE_URL}/sites/").json()
    if not sites:
        print("No sites found. Run seed_test_sites.py first.")
        sys.exit(1)

    if site_filter:
        sites = [s for s in sites if site_filter.lower() in s["name"].lower()]
        if not sites:
            print(f"No sites matching '{site_filter}'.")
            sys.exit(1)

    # Build a lookup of site locations for deduplication
    site_locs = {}
    for s in sites:
        loc = s.get("location")
        if loc:
            site_locs[s["id"]] = (loc["coordinates"][1], loc["coordinates"][0])

    # ── Phase 1: collect all candidate buildings per site ─────────────────────
    # We do this before importing so we can deduplicate across sites.
    print("Querying Overpass for all sites…")
    site_buildings = {}   # site_id → list of building dicts

    for site in sites:
        loc = site.get("location")
        if not loc:
            site_buildings[site["id"]] = []
            continue
        lat, lon = loc["coordinates"][1], loc["coordinates"][0]
        try:
            buildings = fetch_buildings(lat, lon, RADIUS_M)
            site_buildings[site["id"]] = buildings
            print(f"  {site['name']}: {len(buildings)} buildings ≥ {MIN_AREA_SQM} m² found")
        except Exception as e:
            print(f"  ✗ Overpass error for {site['name']}: {e}")
            site_buildings[site["id"]] = []
        time.sleep(1.2)

    # ── Phase 2: deduplicate — assign each OSM building to its nearest site ───
    # Collect every (osm_id, site_id, distance) triple, then keep only the min.
    osm_assignment = {}   # osm_id → site_id of the nearest site
    for site in sites:
        loc = site.get("location")
        if not loc:
            continue
        slat, slon = loc["coordinates"][1], loc["coordinates"][0]
        for b in site_buildings[site["id"]]:
            blat, blon = centroid(b["polygon"])
            d = dist_m(slat, slon, blat, blon)
            oid = b["osm_id"]
            if oid not in osm_assignment or d < osm_assignment[oid][1]:
                osm_assignment[oid] = (site["id"], d)

    # ── Phase 3: import ────────────────────────────────────────────────────────
    total_imported = 0
    total_skipped  = 0

    for site in sites:
        name = site["name"]
        print(f"\n{'─'*60}")
        print(f"  {site['name']}")

        if clear:
            deleted = clear_site_polygons(site["id"])
            print(f"  🗑  Cleared {deleted} existing polygons")
        else:
            existing = requests.get(f"{BASE_URL}/sites/{site['id']}").json()
            if len(existing.get("infrastructure", [])) > 0:
                print(f"  ⚠  Already has polygons — skipping (use --clear to replace)")
                continue

        buildings = site_buildings[site["id"]]
        loc = site.get("location")
        if not loc:
            print(f"  SKIP — no coordinates")
            continue

        # Apply deduplication: keep only buildings assigned to this site
        owned = [b for b in buildings if osm_assignment.get(b["osm_id"], (None,))[0] == site["id"]]
        deduped_away = len(buildings) - len(owned)
        if deduped_away:
            print(f"  ⚡ Deduplicated {deduped_away} building(s) assigned to a nearer site")

        # Split into explicit (strong evidence) vs fallback candidates
        explicit   = [(b, classify(b["tags"])) for b in owned]
        explicit   = [(b, lbl) for b, (lbl, is_ex) in explicit if is_ex and lbl]
        fallback   = [(b, classify(b["tags"])) for b in owned]
        fallback   = [(b, lbl) for b, (lbl, is_ex) in fallback if not is_ex and lbl]

        use_fallback = len(explicit) == 0
        if use_fallback:
            # Take the top-N largest buildings; mark at low confidence for human review
            fallback.sort(key=lambda x: x[0]["area_sqm"], reverse=True)
            to_import = fallback[:FALLBACK_MAX]
            confidence = 0.35
            method     = "fallback_large_building"
            note = f"  ⚠  No explicit DC tags found — importing top {len(to_import)} largest buildings for human review"
            print(note)
        else:
            to_import  = explicit
            confidence = 0.75
            method     = "rule_based"

        imported = 0
        for b, class_label in to_import:
            area_sqft = b["area_sqm"] * 10.764
            tag_str   = ", ".join(f"{k}={v}" for k, v in list(b["tags"].items())[:2])

            payload = {
                "class_label":      class_label,
                "geometry":         b["polygon"],
                "confidence_score": confidence,
                "detection_method": method,
                "imagery_source":   "OpenStreetMap / Overpass API",
            }
            res = requests.post(f"{BASE_URL}/sites/{site['id']}/infrastructure", json=payload)
            if res.ok:
                print(f"    ✓ {class_label:<22} {area_sqft:>8,.0f} sq ft   [{tag_str}]")
                imported += 1
            else:
                print(f"    ✗ Failed: {res.status_code} {res.text[:80]}")
                total_skipped += 1

        if imported == 0:
            print(f"  — Nothing to import (no DC-tagged or large industrial buildings found)")
            print(f"    → Draw polygons manually using the annotation tool on the map.")

        print(f"  → Imported {imported} polygon(s)  ({'fallback — needs human review' if use_fallback else 'explicit tags'})")
        total_imported += imported

    print(f"\n{'='*60}")
    print(f"Done. Imported: {total_imported}")
    print(f"")
    print(f"Next steps:")
    print(f"  1. Open the map and click each dashed polygon to verify or correct it.")
    print(f"  2. For sites with no polygons, use 'Draw a polygon' on the map.")
    print(f"  3. Fallback polygons (low confidence) need the most attention.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear", action="store_true",
                        help="Delete existing polygons before importing")
    parser.add_argument("--site", metavar="NAME",
                        help="Import only this site (partial name match)")
    args = parser.parse_args()
    run(clear=args.clear, site_filter=args.site)
