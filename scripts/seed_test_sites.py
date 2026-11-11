"""
seed_test_sites.py
------------------
Loads the 5 Loudoun County ground-truth test sites into the database.

Run this AFTER docker compose up (so the DB is ready):

    python scripts/seed_test_sites.py

Or from inside the backend container:

    docker exec dclens_backend python /app/../scripts/seed_test_sites.py

The script is idempotent — running it twice won't create duplicates.
"""

import os
import sys
import requests

# The API should be running at localhost:8000
BASE_URL = os.getenv("API_URL", "http://localhost:8000")

# ── Ground-truth sites ────────────────────────────────────────────────────────
# Coordinates manually verified by placing pins on ESRI satellite imagery (June 2026).
# Parcel IDs should be confirmed at loudoun.gov/parceldatabase.

SITES = [
    {
        "name": "Equinix DC1 (Ashburn Campus)",
        "address": "21711 Filigree Court, Ashburn, VA 20147",
        "parcel_id": "CONFIRM AT loudoun.gov/parceldatabase",
        "operator": "Equinix",
        "developer": "Equinix",
        "status": "operating",
        "latitude": 39.01598,
        "longitude": -77.46003,
        "notes": (
            "Oldest Equinix facility in Ashburn; operational since 1999. Campus now spans "
            "12+ buildings including DC1 through DC6 along Filigree Court and Beaumeade Circle. "
            "One of the most documented data center sites in the US. Good baseline for CV training."
        ),
        "sources": [
            {
                "url": "https://www.equinix.com/data-centers/americas-colocation/united-states-colocation/washington-dc-data-centers/dc1",
                "title": "Equinix DC1 Official Page",
                "type": "company_announcement"
            },
            {
                "url": "https://www.datacentermap.com/usa/virginia/ashburn/equinix-dc1/",
                "title": "DataCenterMap: Equinix DC1",
                "type": "other"
            }
        ]
    },
    {
        "name": "Amazon AWS IAD23",
        "address": "43830 Devin Shafron Dr, Ashburn, VA 20147",
        "parcel_id": "CONFIRM AT loudoun.gov/parceldatabase",
        "operator": "Amazon Web Services",
        "developer": "Amazon Web Services",
        "status": "operating",
        "latitude": 39.00433,
        "longitude": -77.48435,
        "notes": (
            "AWS does not disclose capacity publicly. This site is one of multiple AWS "
            "IAD-region facilities in Loudoun County. Identified from property records and "
            "third-party data center trackers. Strong test case for the estimation model — "
            "we must derive MW and water use from footprint + public docs only. "
            "Multiple permit filings should exist in Loudoun County LandMARC."
        ),
        "sources": [
            {
                "url": "https://www.datacentermap.com/usa/virginia/ashburn/amazon-aws-iad23-ashburn/",
                "title": "DataCenterMap: Amazon AWS IAD23",
                "type": "other"
            }
        ]
    },
    {
        "name": "QTS Ashburn 3 DC1",
        "address": "22291 Shellhorn Road, Ashburn, VA 20147",
        "parcel_id": "CONFIRM AT loudoun.gov/parceldatabase",
        "operator": "QTS (Blackstone)",
        "developer": "QTS",
        "status": "operating",
        "latitude": 38.99839309217871,
        "longitude": -77.47483491897584,
        "notes": (
            "42 MW facility. QTS (acquired by Blackstone in 2021) historically disclosed "
            "more capacity detail than hyperscalers, making this a good validation target — "
            "we can check our MW estimate against known figures. "
            "Paired with DC2 at 22271 Shellhorn Road as the Ashburn 3 campus."
        ),
        "sources": [
            {
                "url": "https://q.com/data-centers/ashburn-1/",
                "title": "QTS Ashburn Official Page",
                "type": "company_announcement"
            },
            {
                "url": "https://www.datacentermap.com/usa/virginia/ashburn/qts-ash3-dc1/",
                "title": "DataCenterMap: QTS ASH3-DC1",
                "type": "other"
            }
        ]
    },
    {
        "name": "CyrusOne Broadlands Campus",
        "address": "Education Ct / Broadlands Blvd, Ashburn, VA 20148",
        "parcel_id": "CONFIRM AT loudoun.gov/parceldatabase",
        "operator": "CyrusOne (KKR)",
        "developer": "CyrusOne",
        "status": "operating",
        "latitude": 39.02587311045998,
        "longitude": -77.41451740264894,
        "notes": (
            "Multi-building campus in the Broadlands area of Ashburn. "
            "Visible cooling infrastructure makes this a good test case for the CV cooling detector. "
            "Community concerns have been documented in Loudoun County planning records. "
            "CyrusOne acquired by KKR and Goldman Sachs in 2021."
        ),
        "sources": [
            {
                "url": "https://www.datacentermap.com/usa/virginia/ashburn/cyrusone-broadlands-building-1/",
                "title": "DataCenterMap: CyrusOne Broadlands",
                "type": "other"
            }
        ]
    },
    {
        "name": "STACK Infrastructure Sterling",
        "address": "22080 Pacific Blvd, Sterling, VA 20166",
        "parcel_id": "CONFIRM AT loudoun.gov/parceldatabase",
        "operator": "STACK Infrastructure",
        "developer": "STACK Infrastructure (IPI Partners)",
        "status": "operating",
        "latitude": 39.00468959154079,
        "longitude": -77.44179010391237,
        "notes": (
            "Newer facility in Sterling (Loudoun County). "
            "Good test case for the new post-2024 permitting process — any SPEX applications "
            "filed after March 2025 must go through the new conditional use process. "
            "Compare permit timeline to pre-2024 sites to understand regulatory change impact."
        ),
        "sources": [
            {
                "url": "https://www.datacentermap.com/usa/virginia/ashburn/",
                "title": "DataCenterMap: Ashburn Overview",
                "type": "other"
            }
        ]
    },
]


def seed():
    print(f"Connecting to API at {BASE_URL}...")

    # Test connection
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        r.raise_for_status()
        print("✓ API is healthy\n")
    except Exception as e:
        print(f"✗ Cannot reach API: {e}")
        print("  Make sure docker compose up is running and the backend is ready.")
        sys.exit(1)

    # Check existing sites to avoid duplicates
    existing = requests.get(f"{BASE_URL}/sites/").json()
    existing_names = {s["name"] for s in existing}

    created = 0
    skipped = 0

    for site_data in SITES:
        if site_data["name"] in existing_names:
            print(f"  → SKIP (already exists): {site_data['name']}")
            skipped += 1
            continue

        r = requests.post(f"{BASE_URL}/sites/", json=site_data)
        if r.status_code == 201:
            site = r.json()
            print(f"  ✓ Created: {site['name']} (id: {site['id']})")
            created += 1
        else:
            print(f"  ✗ Failed to create {site_data['name']}: {r.status_code} {r.text}")

    print(f"\nDone. Created: {created}, Skipped: {skipped}")
    print(f"\nView your sites at: {BASE_URL}/docs#/sites/list_sites_sites__get")
    print(f"Or open the map at: http://localhost:3000")


if __name__ == "__main__":
    seed()
