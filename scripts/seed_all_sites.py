"""
seed_all_sites.py
-----------------
Comprehensive seed of data center sites in Loudoun County / Ashburn, Virginia.

Coverage: Equinix (DC1–DC10), Amazon AWS (IAD23, IAD50, IAD60, IAD71, Loudoun),
QTS (ASH3-DC1, ASH3-DC2, ASH1-DC1), CyrusOne (Broadlands, Beaumeade),
STACK Infrastructure, Digital Realty.

Coordinates: geocoded via Nominatim (OpenStreetMap) from verified public addresses,
cross-referenced against datacentermap.com, baxtel.com, and datacenters.com listings.

Run after docker compose up (or uvicorn app_lite:app):
    python scripts/seed_all_sites.py

Flags:
    --dry-run    Print what would be created without posting to the API
    --skip-existing  Skip sites whose name already exists (default: skip duplicates)
"""

import os, sys, argparse
import requests

BASE_URL = os.getenv("API_URL", "http://localhost:8000")


SITES = [

    # ── Equinix — Filigree Court / Beaumeade campus ───────────────────────────
    # The DC1–DC6 buildings are clustered along Filigree Court in Ashburn.
    # DC10 is at Beaumeade Circle, ~0.7 mi away.
    # Source: equinix.com, datacentermap.com, baxtel.com/data-center/equinix-ashburn-campus
    {
        "name":      "Equinix DC1 (Ashburn)",
        "address":   "21711 Filigree Court, Ashburn, VA 20147",
        "operator":  "Equinix",
        "developer": "Equinix",
        "status":    "operating",
        "latitude":  39.01553,
        "longitude": -77.46034,
        "notes": (
            "Equinix's flagship Ashburn facility, operating since 1999. One of the most "
            "important internet exchange points on the East Coast. Carrier-neutral colocation. "
            "Part of the Equinix Filigree Court campus (DC1–DC6). "
            "Good CV training baseline — well-documented, stable footprint."
        ),
        "sources": [
            {"url": "https://www.equinix.com/data-centers/americas-colocation/united-states-colocation/washington-dc-data-centers/dc1",
             "title": "Equinix DC1 Official Page", "type": "company_announcement"},
            {"url": "https://www.datacenters.com/equinix-dc1-ashburn",
             "title": "datacenters.com: Equinix DC1", "type": "other"},
            {"url": "https://baxtel.com/data-center/equinix-ashburn-campus",
             "title": "Baxtel: Equinix Ashburn Campus", "type": "other"},
        ],
    },
    {
        "name":      "Equinix DC2 (Ashburn)",
        "address":   "21715 Filigree Court, Ashburn, VA 20147",
        "operator":  "Equinix",
        "developer": "Equinix",
        "status":    "operating",
        "latitude":  39.01633,
        "longitude": -77.45876,
        "notes": (
            "Adjacent to DC1 on Filigree Court. Part of the core Equinix Ashburn internet "
            "exchange campus. Carrier-neutral colocation."
        ),
        "sources": [
            {"url": "https://www.equinix.com/data-centers/americas-colocation/united-states-colocation/washington-dc-data-centers/dc2",
             "title": "Equinix DC2 Official Page", "type": "company_announcement"},
        ],
    },
    {
        "name":      "Equinix DC4 (Ashburn)",
        "address":   "21691 Filigree Court, Ashburn, VA 20147",
        "operator":  "Equinix",
        "developer": "Equinix",
        "status":    "operating",
        "latitude":  39.01632,
        "longitude": -77.46190,
        "notes": "Equinix colocation facility on Filigree Court campus.",
        "sources": [
            {"url": "https://www.equinix.com/data-centers/americas-colocation/united-states-colocation/washington-dc-data-centers/dc4",
             "title": "Equinix DC4 Official Page", "type": "company_announcement"},
        ],
    },
    {
        "name":      "Equinix DC6 (Ashburn)",
        "address":   "21721 Filigree Court, Ashburn, VA 20147",
        "operator":  "Equinix",
        "developer": "Equinix",
        "status":    "operating",
        "latitude":  39.01496,
        "longitude": -77.45879,
        "notes": "Equinix colocation facility on Filigree Court campus.",
        "sources": [
            {"url": "https://www.equinix.com/data-centers/americas-colocation/united-states-colocation/washington-dc-data-centers/dc6",
             "title": "Equinix DC6 Official Page", "type": "company_announcement"},
        ],
    },
    {
        "name":      "Equinix DC10 (Beaumeade Circle)",
        "address":   "21551 Beaumeade Circle, Ashburn, VA 20147",
        "operator":  "Equinix",
        "developer": "Equinix",
        "status":    "operating",
        "latitude":  39.02102,
        "longitude": -77.45152,
        "notes": (
            "Equinix DC10 is on Beaumeade Circle, ~0.7 miles from the Filigree Court campus. "
            "Carrier-neutral colocation."
        ),
        "sources": [
            {"url": "https://www.equinix.com/data-centers/americas-colocation/united-states-colocation/washington-dc-data-centers/dc10",
             "title": "Equinix DC10 Official Page", "type": "company_announcement"},
        ],
    },

    # ── Amazon AWS — IAD region ────────────────────────────────────────────────
    # AWS does not disclose capacity. All estimates derived from footprint + permits.
    # Source: datacentermap.com, baxtel.com, property records, WikiLeaks Amazon Atlas
    {
        "name":      "Amazon AWS IAD23",
        "address":   "43830 Devin Shafron Drive, Ashburn, VA 20147",
        "operator":  "Amazon Web Services",
        "developer": "Amazon Web Services",
        "status":    "operating",
        "latitude":  39.00433,
        "longitude": -77.48435,
        "notes": (
            "AWS IAD23 — part of the US East (N. Virginia) / us-east-1 region. "
            "AWS does not disclose capacity publicly; footprint-based estimation required. "
            "Multiple permit filings in Loudoun County LandMARC system."
        ),
        "sources": [
            {"url": "https://www.datacenters.com/amazon-aws-iad23-ashburn",
             "title": "datacenters.com: Amazon AWS IAD23", "type": "other"},
            {"url": "https://www.datacentermap.com/usa/virginia/ashburn/aws-iad-loudoun-county-campus/",
             "title": "datacentermap.com: AWS IAD Loudoun County Campus", "type": "other"},
        ],
    },
    {
        "name":      "Amazon AWS IAD50 (Smith Switch Road)",
        "address":   "21271 Smith Switch Road, Ashburn, VA 20147",
        "operator":  "Amazon Web Services",
        "developer": "Amazon Web Services",
        "status":    "operating",
        "latitude":  39.02791,
        "longitude": -77.45750,
        "notes": (
            "AWS IAD50 — part of the Smith Switch Road cluster (IAD50, IAD60, IAD71). "
            "Three buildings totaling ~430,000 sq ft. Part of us-east-1."
        ),
        "sources": [
            {"url": "https://www.datacentermap.com/usa/virginia/ashburn/amazon-aws-iad-21271-smith/",
             "title": "datacentermap.com: AWS IAD50 (21271 Smith)", "type": "other"},
        ],
    },
    {
        "name":      "Amazon AWS IAD60 (Smith Switch Road)",
        "address":   "21267 Smith Switch Road, Ashburn, VA 20147",
        "operator":  "Amazon Web Services",
        "developer": "Amazon Web Services",
        "status":    "operating",
        "latitude":  39.02877,
        "longitude": -77.45825,
        "notes": "AWS IAD60 — 109,997 sq ft. Smith Switch Road cluster, us-east-1.",
        "sources": [
            {"url": "https://www.datacentermap.com/usa/virginia/ashburn/amazon-aws-iad-21267-smith/",
             "title": "datacentermap.com: AWS IAD60 (21267 Smith)", "type": "other"},
        ],
    },
    {
        "name":      "Amazon AWS IAD71 (Smith Switch Road)",
        "address":   "21263 Smith Switch Road, Ashburn, VA 20147",
        "operator":  "Amazon Web Services",
        "developer": "Amazon Web Services",
        "status":    "operating",
        "latitude":  39.02956,
        "longitude": -77.45965,
        "notes": "AWS IAD71 — 200,000 sq ft. Smith Switch Road cluster, us-east-1.",
        "sources": [
            {"url": "https://www.datacentermap.com/usa/virginia/ashburn/amazon-aws-iad-21263-smith/",
             "title": "datacentermap.com: AWS IAD71 (21263 Smith)", "type": "other"},
            {"url": "https://baxtel.com/data-center/amazon-iad71",
             "title": "Baxtel: Amazon IAD71", "type": "other"},
        ],
    },
    {
        "name":      "Amazon AWS Loudoun County Campus",
        "address":   "20945 Loudoun County Parkway, Ashburn, VA 20147",
        "operator":  "Amazon Web Services",
        "developer": "Amazon Web Services",
        "status":    "operating",
        "latitude":  39.00859,
        "longitude": -77.47011,
        "notes": (
            "AWS Loudoun County Parkway campus. Part of us-east-1. "
            "Multiple buildings along Loudoun County Parkway."
        ),
        "sources": [
            {"url": "https://www.datacentermap.com/usa/virginia/ashburn/aws-iad-loudoun-county-campus/",
             "title": "datacentermap.com: AWS IAD Loudoun County Campus", "type": "other"},
        ],
    },

    # ── QTS (Blackstone) ──────────────────────────────────────────────────────
    # QTS acquired by Blackstone in 2021. Ashburn 3 = Shellhorn Road campus (80+ MW).
    # Ashburn 1 = Broderick Drive / Randolph Drive campus in Sterling.
    {
        "name":      "QTS Ashburn 3 DC1",
        "address":   "22291 Shellhorn Road, Ashburn, VA 20147",
        "operator":  "QTS (Blackstone)",
        "developer": "QTS",
        "status":    "operating",
        "latitude":  39.01603,
        "longitude": -77.49378,
        "notes": (
            "QTS ASH3-DC1 — 42 MW critical power. Part of the 80+ MW Ashburn 3 / "
            "Shellhorn Road campus. Known capacity makes this a good validation target: "
            "our MW estimate can be checked against the 42 MW figure."
        ),
        "sources": [
            {"url": "https://www.datacentermap.com/usa/virginia/ashburn/qts-ash3-dc1/",
             "title": "datacentermap.com: QTS ASH3-DC1", "type": "other"},
            {"url": "https://www.datacenters.com/qts-ashburn-shellhorn-ash3-1",
             "title": "datacenters.com: QTS Ashburn Shellhorn ASH3-1", "type": "other"},
            {"url": "https://qtsdatacenters.com/data-centers/ashburn-3/",
             "title": "QTS Ashburn 3 Official Page", "type": "company_announcement"},
        ],
    },
    {
        "name":      "QTS Ashburn 3 DC2",
        "address":   "22271 Shellhorn Road, Ashburn, VA 20147",
        "operator":  "QTS (Blackstone)",
        "developer": "QTS",
        "status":    "operating",
        "latitude":  39.01560,  # ~50m south of DC1 on same road
        "longitude": -77.49395,
        "notes": (
            "QTS ASH3-DC2 — 42 MW critical power. Adjacent to DC1 on the Shellhorn "
            "Road campus. Together DC1+DC2 form the 80+ MW Ashburn 3 campus."
        ),
        "sources": [
            {"url": "https://www.datacentermap.com/usa/virginia/ashburn/qts-ash3-dc2/",
             "title": "datacentermap.com: QTS ASH3-DC2", "type": "other"},
        ],
    },
    {
        "name":      "QTS Ashburn 1 DC1 (Sterling)",
        "address":   "22271 Broderick Drive, Sterling, VA 20166",
        "operator":  "QTS (Blackstone)",
        "developer": "QTS",
        "status":    "operating",
        "latitude":  38.99961,
        "longitude": -77.45014,
        "notes": (
            "QTS Ashburn 1 campus — located in Sterling (Loudoun County). "
            "Separate campus from the Shellhorn Road facilities."
        ),
        "sources": [
            {"url": "https://q.com/data-centers/ashburn-1/",
             "title": "QTS Ashburn 1 Official Page", "type": "company_announcement"},
        ],
    },

    # ── CyrusOne (KKR) ────────────────────────────────────────────────────────
    # CyrusOne acquired by KKR and Goldman Sachs in 2021.
    # Broadlands = northern Ashburn/Broadlands area campus.
    # Beaumeade = newer PowerHouse ABX-1 building, 54 MW at full buildout.
    {
        "name":      "CyrusOne Broadlands Campus",
        "address":   "Education Court / Broadlands Boulevard, Ashburn, VA 20148",
        "operator":  "CyrusOne (KKR)",
        "developer": "CyrusOne",
        "status":    "operating",
        "latitude":  39.02587,
        "longitude": -77.41451,
        "notes": (
            "CyrusOne multi-building campus in the Broadlands area of Ashburn. "
            "Visible cooling infrastructure makes this a good CV training case for "
            "cooling-equipment detection. Community concerns documented in Loudoun planning records."
        ),
        "sources": [
            {"url": "https://www.datacentermap.com/usa/virginia/ashburn/cyrusone-broadlands-building-1/",
             "title": "datacentermap.com: CyrusOne Broadlands", "type": "other"},
        ],
    },
    {
        "name":      "CyrusOne Beaumeade (PowerHouse ABX-1)",
        "address":   "21529 Beaumeade Circle, Ashburn, VA 20147",
        "operator":  "CyrusOne (KKR)",
        "developer": "PowerHouse",
        "status":    "operating",
        "latitude":  39.02088,
        "longitude": -77.45357,
        "notes": (
            "CyrusOne leases this facility from PowerHouse under a 30-year powered-shell lease. "
            "265,000 sq ft building; 15 MW launched Oct 2023, 54 MW at full buildout across 6 data halls. "
            "Example of powered-shell lease structure — CyrusOne operates but doesn't own."
        ),
        "sources": [
            {"url": "https://www.datacenterdynamics.com/en/news/cyrusone-to-lease-new-powerhouse-data-center-in-ashburn-virginia/",
             "title": "Data Center Dynamics: CyrusOne/PowerHouse ABX-1", "type": "news"},
        ],
    },

    # ── STACK Infrastructure (IPI Partners) ──────────────────────────────────
    {
        "name":      "STACK Infrastructure Sterling",
        "address":   "22080 Pacific Boulevard, Sterling, VA 20166",
        "operator":  "STACK Infrastructure",
        "developer": "STACK Infrastructure (IPI Partners)",
        "status":    "operating",
        "latitude":  39.00515,
        "longitude": -77.44357,
        "notes": (
            "Newer facility in Sterling (Loudoun County). "
            "Any SPEX applications filed after March 2025 use the new Conditional Use "
            "process (§5-1502). Compare permit timeline to pre-2024 sites for regulatory change impact."
        ),
        "sources": [
            {"url": "https://www.datacentermap.com/usa/virginia/ashburn/",
             "title": "datacentermap.com: Ashburn overview", "type": "other"},
        ],
    },

    # ── Digital Realty ────────────────────────────────────────────────────────
    # Chilum Place (IAD18): same address as formerly listed as Equinix DC3 —
    # Digital Realty owns the building; Equinix may co-locate or the address
    # attribution differs by source. Flag for human review.
    {
        "name":      "Digital Realty IAD18 (Chilum Place)",
        "address":   "44470 Chilum Place, Ashburn, VA 20147",
        "operator":  "Digital Realty",
        "developer": "Digital Realty",
        "status":    "operating",
        "latitude":  39.02208,
        "longitude": -77.46078,
        "notes": (
            "Digital Realty IAD18. Note: multiple sources attribute 44470 Chilum Place "
            "to both Digital Realty and Equinix DC3 — ownership vs. colocation tenancy "
            "distinction. Verify via Loudoun County property records."
        ),
        "sources": [
            {"url": "https://www.datacentermap.com/usa/virginia/ashburn/44470-chilum-place-iad18/",
             "title": "datacentermap.com: 44470 Chilum Place IAD18", "type": "other"},
        ],
    },

]


def seed(dry_run=False, skip_existing=True):
    if not dry_run:
        try:
            requests.get(f"{BASE_URL}/health", timeout=5).raise_for_status()
            print(f"✓ API healthy at {BASE_URL}\n")
        except Exception as e:
            print(f"✗ Cannot reach API: {e}")
            print("  Start the server first: uvicorn app_lite:app --reload")
            sys.exit(1)

    existing_names = set()
    if skip_existing and not dry_run:
        existing = requests.get(f"{BASE_URL}/sites/").json()
        existing_names = {s["name"] for s in existing}

    created = skipped = errors = 0

    for site in SITES:
        if dry_run:
            print(f"  [DRY RUN] Would create: {site['name']}  ({site['latitude']:.5f}, {site['longitude']:.5f})")
            created += 1
            continue

        if site["name"] in existing_names:
            print(f"  → SKIP (exists): {site['name']}")
            skipped += 1
            continue

        r = requests.post(f"{BASE_URL}/sites/", json=site)
        if r.status_code == 201:
            print(f"  ✓ Created: {site['name']}")
            created += 1
        else:
            print(f"  ✗ Failed:  {site['name']}  {r.status_code} {r.text[:80]}")
            errors += 1

    print(f"\n{'─'*50}")
    print(f"{'[DRY RUN] ' if dry_run else ''}Created: {created}  Skipped: {skipped}  Errors: {errors}")
    print(f"Total sites in this script: {len(SITES)}")
    if not dry_run and created > 0:
        print(f"\nNext steps:")
        print(f"  1. Open the map at http://localhost:8000/app")
        print(f"  2. Click each site → 'Import Official Outlines (LoudounGIS)'")
        print(f"  3. Draw a campus polygon for sites with no LoudounGIS match")
        print(f"  4. Click 'Run Resource Estimates'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed comprehensive Loudoun County data center sites")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be created, don't post")
    parser.add_argument("--no-skip", action="store_true", help="Create all sites even if name exists")
    args = parser.parse_args()
    seed(dry_run=args.dry_run, skip_existing=not args.no_skip)
