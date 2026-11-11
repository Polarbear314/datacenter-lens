"""
scripts/scrape_direct.py
------------------------
Standalone scraper runner — writes directly to SQLite, no API server needed.

Unlike scrape_documents.py (which POSTs to the running FastAPI server), this
imports the scrapers and app_lite helpers directly. Useful for batch runs and
cron jobs where you don't want a server running.

Usage:
  python scripts/scrape_direct.py                  # all sites, all scrapers
  python scripts/scrape_direct.py --named-only     # skip "Data Center at <coords>" sites
  python scripts/scrape_direct.py --site "Equinix" # filter by name substring
  python scripts/scrape_direct.py --source google_news
  python scripts/scrape_direct.py --limit 20       # first N sites only
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app_lite  # noqa: E402

BETWEEN_SITES_DELAY = 3.0


def main():
    p = argparse.ArgumentParser(description="Scrape public records directly to SQLite")
    p.add_argument("--site", help="Only sites whose name contains this substring")
    p.add_argument("--source", help="Run only this scraper")
    p.add_argument("--limit", type=int, help="Max number of sites to process")
    p.add_argument("--named-only", action="store_true",
                   help="Skip anonymous sites (name like 'Data Center at <lat>,<lon>') — "
                        "news queries on those names return junk")
    p.add_argument("--skip-scraped", action="store_true",
                   help="Skip sites that already have documents")
    args = p.parse_args()

    conn = app_lite.get_conn()
    q = "SELECT * FROM sites WHERE 1=1"
    params = []
    if args.site:
        q += " AND name LIKE ?"
        params.append(f"%{args.site}%")
    q += " ORDER BY (operator IS NULL), name"   # sites with operators first
    sites = [dict(r) for r in conn.execute(q, params).fetchall()]
    if args.skip_scraped:
        have = {r["site_id"] for r in conn.execute("SELECT DISTINCT site_id FROM documents").fetchall()}
        sites = [s for s in sites if s["id"] not in have]
    conn.close()

    if args.named_only:
        sites = [s for s in sites if not s["name"].startswith("Data Center at ")]
    if args.limit:
        sites = sites[: args.limit]

    print(f"Scraping {len(sites)} site(s)... (source={args.source or 'all'})", flush=True)
    total_saved = 0
    for i, site in enumerate(sites, 1):
        try:
            result = app_lite._run_scrapers_for_site(site, source=args.source)
            saved, skipped = app_lite._save_scraped_docs(site["id"], result["results"])
            total_saved += saved
            errs = f"  errors: {result['errors']}" if result["errors"] else ""
            print(f"[{i}/{len(sites)}] {site['name'][:50]:50s} "
                  f"found={len(result['results'])} saved={saved} skipped={skipped}{errs}", flush=True)
        except Exception as e:
            print(f"[{i}/{len(sites)}] {site['name'][:50]:50s} FAILED: {str(e)[:120]}", flush=True)
        if i < len(sites):
            time.sleep(BETWEEN_SITES_DELAY)

    print(f"\nDone. {total_saved} new documents saved.", flush=True)


if __name__ == "__main__":
    main()
