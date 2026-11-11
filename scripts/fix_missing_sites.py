"""
fix_missing_sites.py
--------------------
One-shot fix for QTS Ashburn 3 DC1 and STACK Infrastructure Sterling,
which had no outlines due to a stale import from before the script was tightened.

OSM data confirmed present at both locations (June 2026):
  QTS   → 2x building=data_center (DC1 + DC2) + Shellhorn Substation
  STACK → 1x building=data_center (NVA01A)

Run:
    python3 scripts/fix_missing_sites.py
"""

import json, math, time, urllib.request, urllib.parse, sys
import requests

BASE_URL = "http://localhost:8000"
OVERPASS = "https://overpass-api.de/api/interpreter"
RADIUS_M = 300
MIN_AREA_SQM = 2000

DC_OPERATORS = {
    "equinix", "amazon", "aws", "google", "microsoft", "meta",
    "qts", "quality technology services", "cyrusone", "iron mountain",
    "stack infrastructure", "coresite", "digital realty", "ntt", "vadata",
}

TARGET_SITES = ["QTS Ashburn 3 DC1", "STACK Infrastructure Sterling"]


def classify(tags):
    b  = tags.get("building", "")
    am = tags.get("amenity", "")
    pw = tags.get("power", "")
    nm = tags.get("name", "").lower()
    op = tags.get("operator", "").lower()

    if pw in ("substation", "transformer"):          return "substation", True
    if b in ("data_center", "server_farm"):          return "main_building", True
    if am == "data_center":                          return "main_building", True
    if "cooling" in nm or "chiller" in nm:           return "cooling_equipment", True
    if "generator" in nm or "genset" in nm:          return "generators", True
    if "substation" in nm or "electrical" in nm:     return "substation", True
    if any(dc in op for dc in DC_OPERATORS):         return "main_building", True
    if b in ("warehouse", "industrial"):             return "main_building", False
    return None, False


def osm_nodes_to_polygon(nodes):
    coords = [[n["lon"], n["lat"]] for n in nodes]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}


def area_sqm(polygon):
    coords = polygon["coordinates"][0]
    if len(coords) < 3:
        return 0.0
    n = len(coords); a = 0.0
    for i in range(n - 1):
        a += coords[i][0] * coords[i+1][1] - coords[i+1][0] * coords[i][1]
    a = abs(a) / 2.0
    lat_r = math.radians(coords[0][1])
    return a * 111_320 * (111_320 * math.cos(lat_r))


def fetch(lat, lon):
    q = f"""[out:json][timeout:25];
(
  way["building"](around:{RADIUS_M},{lat},{lon});
  way["power"="substation"](around:{RADIUS_M},{lat},{lon});
  way["amenity"="data_center"](around:{RADIUS_M},{lat},{lon});
);
out geom;"""
    data = urllib.parse.urlencode({"data": q}).encode()
    req  = urllib.request.Request(OVERPASS, data=data,
                                   headers={"User-Agent": "DataCenterLens/0.1 (civic-tech research tool)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def clear_polygons(site_id):
    detail = requests.get(f"{BASE_URL}/sites/{site_id}").json()
    count = 0
    for infra in detail.get("infrastructure", []):
        res = requests.delete(f"{BASE_URL}/sites/{site_id}/infrastructure/{infra['id']}")
        if res.status_code in (200, 204):
            count += 1
    return count


def run():
    try:
        requests.get(f"{BASE_URL}/health", timeout=5).raise_for_status()
    except Exception as e:
        print(f"✗ Cannot reach API: {e}")
        print("  Make sure 'uvicorn app_lite:app --reload' is running.")
        sys.exit(1)

    all_sites = requests.get(f"{BASE_URL}/sites/").json()
    sites = [s for s in all_sites if s["name"] in TARGET_SITES]

    if not sites:
        print("✗ Target sites not found in the database. Run seed_test_sites.py first.")
        sys.exit(1)

    # For deduplication: also get all OTHER site locations so we don't steal their buildings
    other_sites = [(s["name"],
                    s["location"]["coordinates"][1],
                    s["location"]["coordinates"][0])
                   for s in all_sites
                   if s["name"] not in TARGET_SITES and s.get("location")]

    total = 0

    for site in sites:
        loc = site.get("location")
        if not loc:
            print(f"\n✗ {site['name']} — no coordinates set, skipping")
            continue

        lat = loc["coordinates"][1]
        lon = loc["coordinates"][0]

        print(f"\n{'─'*60}")
        print(f"  {site['name']}")

        # Clear existing
        deleted = clear_polygons(site["id"])
        if deleted:
            print(f"  🗑  Cleared {deleted} existing polygon(s)")

        # Query Overpass
        print(f"  Querying OSM within {RADIUS_M}m…")
        try:
            result = fetch(lat, lon)
        except Exception as e:
            print(f"  ✗ Overpass error: {e}")
            continue

        elements = [e for e in result.get("elements", [])
                    if e.get("type") == "way" and "geometry" in e]

        candidates = []
        for el in elements:
            polygon = osm_nodes_to_polygon(el["geometry"])
            a = area_sqm(polygon)
            if a < MIN_AREA_SQM:
                continue
            label, explicit = classify(el.get("tags", {}))
            if label is None:
                continue

            # Deduplication: skip if this building is closer to another site's pin
            blat = sum(n["lat"] for n in el["geometry"]) / len(el["geometry"])
            blon = sum(n["lon"] for n in el["geometry"]) / len(el["geometry"])

            def dist(la, lo):
                dlat = (blat - la) * 111_320
                dlon = (blon - lo) * 111_320 * math.cos(math.radians((blat+la)/2))
                return math.sqrt(dlat**2 + dlon**2)

            my_dist = dist(lat, lon)
            closer = [n for n, olat, olon in other_sites if dist(olat, olon) < my_dist]
            if closer:
                print(f"  — Skip (closer to {closer[0]}): osm={el['id']}")
                continue

            candidates.append((el, polygon, a, label, explicit))

        if not candidates:
            print(f"  ✗ No buildings passed filters — pin may need adjustment")
            print(f"    Draw polygons manually using the annotation tool.")
            continue

        imported = 0
        for el, polygon, a, label, explicit in candidates:
            tags = el.get("tags", {})
            confidence = 0.80 if explicit else 0.40
            method     = "rule_based" if explicit else "fallback_large_building"
            tag_str    = ", ".join(f"{k}={v}" for k, v in list(tags.items())[:3])

            payload = {
                "class_label":      label,
                "geometry":         polygon,
                "confidence_score": confidence,
                "detection_method": method,
                "imagery_source":   "OpenStreetMap / Overpass API",
            }
            res = requests.post(f"{BASE_URL}/sites/{site['id']}/infrastructure", json=payload)
            if res.ok:
                print(f"  ✓ {label:<22} {a*10.764:>8,.0f} sq ft   [{tag_str}]")
                imported += 1
            else:
                print(f"  ✗ Failed: {res.status_code} {res.text[:80]}")

        print(f"  → {imported} polygon(s) imported")
        total += imported
        time.sleep(1.5)

    print(f"\n{'='*60}")
    print(f"Done. Total imported: {total}")
    print(f"Refresh the map — both sites should now have colored outlines.")
    print(f"Click any dashed polygon to verify or correct the label.")


if __name__ == "__main__":
    run()
