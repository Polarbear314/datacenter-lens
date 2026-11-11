"""
scripts/scrape_documents.py
----------------------------
CLI tool to run public-records scrapers for the 5 Loudoun County test sites.

Usage:

  # Scrape all 5 sites with all sources (saves to database via API)
  python scripts/scrape_documents.py

  # Preview what would be saved without actually saving
  python scripts/scrape_documents.py --dry-run

  # Scrape only one source
  python scripts/scrape_documents.py --source google_news

  # Scrape only one site (by partial name match)
  python scripts/scrape_documents.py --site "Equinix"

  # Show what sources are available
  python scripts/scrape_documents.py --list-sources

Requires the API to be running (docker compose up).
"""

import os
import sys
import time
import argparse
import requests
from datetime import datetime

# The API should be running at localhost:8000
BASE_URL = os.getenv("API_URL", "http://localhost:8000")

# Seconds to wait between scraping each site (be polite to public APIs)
BETWEEN_SITES_DELAY = 3.0


def check_api():
    """Verify the API is reachable before starting."""
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"✗ Cannot reach API at {BASE_URL}: {e}")
        print("  Run: docker compose up")
        return False


def get_sites(name_filter: str = None) -> list[dict]:
    """Fetch all sites from the API, optionally filtered by name."""
    try:
        r = requests.get(f"{BASE_URL}/sites/", timeout=10)
        r.raise_for_status()
        sites = r.json()
    except Exception as e:
        print(f"✗ Failed to fetch sites: {e}")
        return []

    if name_filter:
        sites = [
            s for s in sites
            if name_filter.lower() in s.get("name", "").lower()
            or name_filter.lower() in (s.get("operator") or "").lower()
        ]

    return sites


def scrape_site(site: dict, source: str = None, dry_run: bool = False) -> dict:
    """
    Run scrapers for one site via the API.
    Returns the API response dict.
    """
    site_id = site["id"]
    params  = {}
    if source:
        params["source"] = source

    try:
        if dry_run:
            # Preview is a GET (read-only — nothing is saved)
            url = f"{BASE_URL}/sites/{site_id}/scrape/preview"
            r   = requests.get(url, params=params, timeout=120)
        else:
            # Live scrape is a POST (writes to the database)
            url = f"{BASE_URL}/sites/{site_id}/scrape"
            r   = requests.post(url, params=params, timeout=120)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except requests.RequestException as e:
        return {"error": str(e)}


def print_result(site: dict, result: dict, dry_run: bool):
    """Print a formatted summary of one site's scrape results."""
    name = site.get("name", "Unknown")

    if "error" in result:
        print(f"\n  ✗ {name}")
        print(f"    Error: {result['error']}")
        return

    found   = result.get("documents_found", len(result.get("documents", [])))
    saved   = result.get("documents_saved", 0)
    skipped = result.get("documents_skipped", 0)
    by_src  = result.get("by_source", {})
    errors  = result.get("errors", {})

    if dry_run:
        print(f"\n  [DRY RUN] {name}")
        print(f"    Would find: {found} documents")
    else:
        print(f"\n  ✓ {name}")
        print(f"    Found: {found} | Saved: {saved} | Skipped (duplicate): {skipped}")

    # Per-source breakdown
    for src, count in by_src.items():
        status = f"{count} docs"
        if src in errors:
            status = f"FAILED — {errors[src][:80]}"
        print(f"    {src:20s} → {status}")

    # In dry-run mode, show titles of what was found
    if dry_run and result.get("documents"):
        print(f"    Documents that would be saved:")
        for doc in result["documents"][:8]:
            dtype = doc.get("document_type", "?")
            title = doc.get("title", "")[:70]
            date  = doc.get("publication_date") or "no date"
            print(f"      [{dtype}] {title} ({date})")
        if len(result["documents"]) > 8:
            print(f"      ... and {len(result['documents']) - 8} more")


def list_sources():
    """Print available scraper names."""
    # Import directly to avoid needing the API for this
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    try:
        from scrapers import ALL_SCRAPERS
        print("\nAvailable sources:")
        for s in ALL_SCRAPERS:
            print(f"  {s.name}")
    except ImportError as e:
        print(f"Could not import scrapers: {e}")
        print("Available sources: google_news, ferc, loudoun")


def main():
    parser = argparse.ArgumentParser(
        description="Scrape public records for DataCenter Lens test sites.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/scrape_documents.py                       # all sites, all sources
  python scripts/scrape_documents.py --dry-run             # preview without saving
  python scripts/scrape_documents.py --source google_news  # one source only
  python scripts/scrape_documents.py --site "Equinix"      # one site only
  python scripts/scrape_documents.py --list-sources        # show available sources
        """,
    )
    parser.add_argument("--dry-run",      action="store_true",
                        help="Preview results without saving to database")
    parser.add_argument("--source",       type=str, default=None,
                        help="Run only this scraper (google_news, ferc, loudoun)")
    parser.add_argument("--site",         type=str, default=None,
                        help="Only scrape sites whose name/operator matches this string")
    parser.add_argument("--list-sources", action="store_true",
                        help="List available scraper names and exit")
    parser.add_argument("--api-url",      type=str, default=None,
                        help="API base URL (default: http://localhost:8000)")
    args = parser.parse_args()

    if args.list_sources:
        list_sources()
        return

    global BASE_URL
    if args.api_url:
        BASE_URL = args.api_url

    print(f"\nDataCenter Lens — Public Records Scraper")
    print(f"API: {BASE_URL}")
    print(f"Mode: {'DRY RUN (preview only)' if args.dry_run else 'LIVE (saving to database)'}")
    print(f"Source: {args.source or 'all'}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("─" * 60)

    if not check_api():
        sys.exit(1)

    sites = get_sites(name_filter=args.site)
    if not sites:
        print("No sites found. Run scripts/seed_test_sites.py first.")
        sys.exit(1)

    print(f"\nScraping {len(sites)} site(s)...\n")

    total_found = 0
    total_saved = 0

    for i, site in enumerate(sites):
        if i > 0:
            time.sleep(BETWEEN_SITES_DELAY)

        print(f"[{i+1}/{len(sites)}] {site.get('name', site['id'])}")
        result = scrape_site(site, source=args.source, dry_run=args.dry_run)
        print_result(site, result, dry_run=args.dry_run)

        if "error" not in result:
            total_found += result.get("documents_found", 0)
            total_saved += result.get("documents_saved", 0)

    print("\n" + "─" * 60)
    print(f"Done. Total documents found: {total_found}")
    if not args.dry_run:
        print(f"Total documents saved:  {total_saved}")
        print(f"\nView documents at: {BASE_URL}/docs#/sites/get_site_sites__site_id__get")
    else:
        print("(Dry run — nothing was saved)")


if __name__ == "__main__":
    main()
