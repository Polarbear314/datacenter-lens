"""
fix_coordinates.py
------------------
Updates the 5 ground-truth sites with accurate coordinates.
Run this once after seeding:

    python3 scripts/fix_coordinates.py
"""

import requests, sys

BASE_URL = "http://localhost:8000"

# Verified coordinates for each site
# Source: OpenStreetMap geocoder + cross-checked against known addresses
COORDS = {
    # All coordinates manually verified and set by user on satellite imagery (June 2026)
    "Equinix DC1 (Ashburn Campus)":   (39.01598,              -77.46003),
    "Amazon AWS IAD23":                (39.00433,              -77.48435),
    "QTS Ashburn 3 DC1":               (38.99839309217871,     -77.47483491897584),
    "CyrusOne Broadlands Campus":      (39.02587311045998,     -77.41451740264894),
    "STACK Infrastructure Sterling":   (39.00468959154079,     -77.44179010391237),
}

try:
    sites = requests.get(f"{BASE_URL}/sites/", timeout=5).json()
except Exception as e:
    print(f"Cannot reach API: {e}\nMake sure uvicorn app_lite:app --reload is running.")
    sys.exit(1)

updated = 0
for site in sites:
    name = site["name"]
    if name in COORDS:
        lat, lon = COORDS[name]
        # PATCH the latitude/longitude — app_lite stores these as separate columns
        import sqlite3, pathlib
        db = pathlib.Path(__file__).parent.parent / "dclens_dev.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "UPDATE sites SET latitude=?, longitude=? WHERE id=?",
            (lat, lon, site["id"])
        )
        conn.commit()
        conn.close()
        print(f"  ✓ {name}: ({lat}, {lon})")
        updated += 1
    else:
        print(f"  — skipped: {name}")

print(f"\nUpdated {updated} sites. Refresh the map to see corrected positions.")
