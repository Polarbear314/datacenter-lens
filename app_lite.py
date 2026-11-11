"""
app_lite.py — DataCenter Lens, no-Docker version
-------------------------------------------------
A self-contained FastAPI app using SQLite (built into Python).
No Docker, no PostgreSQL, no installation beyond pip.

HOW TO RUN:
  cd datacenter-lens
  pip3 install fastapi uvicorn pydantic
  uvicorn app_lite:app --reload

Then open:
  http://localhost:8000/docs   ← interactive API docs
  frontend/index.html          ← open this file directly in your browser
"""

import sys
import os
import uuid
import json
from datetime import date, datetime
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
import sqlite3

# Make the scrapers in backend/ importable from here
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# ── Database setup ─────────────────────────────────────────────────────────────
# SQLite stores everything in a single file — no server needed.
DB_PATH = Path(__file__).parent / "dclens_dev.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)  # wait up to 15s if DB is locked
    conn.row_factory = sqlite3.Row   # Rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent reads
    conn.execute("PRAGMA busy_timeout=15000") # additional busy wait in ms
    return conn

def init_db():
    """Create tables if they don't exist."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sites (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            address       TEXT,
            parcel_id     TEXT,
            operator      TEXT,
            developer     TEXT,
            latitude      REAL,
            longitude     REAL,
            status        TEXT DEFAULT 'unknown',
            first_seen    TEXT DEFAULT (date('now')),
            last_updated  TEXT DEFAULT (datetime('now')),
            notes         TEXT,
            sources       TEXT DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS infrastructure (
            id               TEXT PRIMARY KEY,
            site_id          TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            class_label      TEXT NOT NULL DEFAULT 'unknown',
            geometry_json    TEXT NOT NULL,
            area_sqft        REAL,
            confidence_score REAL DEFAULT 1.0,
            detection_method TEXT DEFAULT 'manual',
            imagery_source   TEXT,
            imagery_date     TEXT,
            human_corrected  INTEGER DEFAULT 0,
            corrected_by     TEXT,
            original_label   TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS documents (
            id               TEXT PRIMARY KEY,
            site_id          TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            document_type    TEXT,
            title            TEXT NOT NULL,
            url              TEXT,
            publication_date TEXT,
            issuing_agency   TEXT,
            extracted_fields TEXT DEFAULT '{}',
            created_at       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS estimates (
            id               TEXT PRIMARY KEY,
            site_id          TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            estimate_type    TEXT NOT NULL,
            value            REAL,
            uncertainty_low  REAL,
            uncertainty_high REAL,
            unit             TEXT,
            confidence_score REAL DEFAULT 0.5,
            status           TEXT DEFAULT 'estimated',
            method           TEXT,
            assumptions      TEXT,
            sources          TEXT DEFAULT '[]',
            created_at       TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


# ── Pydantic schemas ────────────────────────────────────────────────────────────

class SiteCreate(BaseModel):
    name:      str
    address:   Optional[str]   = None
    parcel_id: Optional[str]   = None
    operator:  Optional[str]   = None
    developer: Optional[str]   = None
    status:    str              = "unknown"
    latitude:  Optional[float] = None
    longitude: Optional[float] = None
    notes:     Optional[str]   = None
    sources:   Optional[list]  = None

class SiteUpdate(BaseModel):
    name:      Optional[str] = None
    address:   Optional[str] = None
    parcel_id: Optional[str] = None
    operator:  Optional[str] = None
    developer: Optional[str] = None
    status:    Optional[str] = None
    notes:     Optional[str] = None

class InfraCreate(BaseModel):
    class_label:      str
    geometry:         dict           # GeoJSON Polygon
    area_sqft:        Optional[float] = None   # computed server-side if omitted
    confidence_score: float = 1.0
    detection_method: str   = "manual"
    imagery_source:   Optional[str]  = None
    imagery_date:     Optional[str]  = None

class InfraUpdate(BaseModel):
    class_label:      Optional[str]   = None
    confidence_score: Optional[float] = None
    corrected_by:     Optional[str]   = None

class DocCreate(BaseModel):
    document_type:    Optional[str] = None
    title:            str
    url:              Optional[str] = None
    publication_date: Optional[str] = None
    issuing_agency:   Optional[str] = None
    extracted_fields: Optional[dict] = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row) if row else None


def polygon_area_sqft(geometry: Optional[dict]) -> Optional[float]:
    """
    Compute the area of a GeoJSON Polygon in square feet.
    Latitude-aware flat-earth Shoelace — accurate to ~1% at data-center scale.
    Returns None if geometry is missing/invalid.
    """
    import math
    if not geometry or geometry.get("type") != "Polygon":
        return None
    try:
        coords = geometry["coordinates"][0]
    except (KeyError, IndexError, TypeError):
        return None
    if not coords or len(coords) < 3:
        return None

    lat0 = sum(c[1] for c in coords) / len(coords)
    lon0 = coords[0][0]
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))

    pts = [((c[0] - lon0) * m_per_deg_lon, (c[1] - lat0) * m_per_deg_lat) for c in coords]
    n = len(pts)
    area_m2 = abs(sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
        for i in range(n)
    )) / 2.0
    return round(area_m2 * 10.7639, 1)

def site_to_response(row: dict, conn) -> dict:
    """Build a full site response including linked objects."""
    site = dict(row)

    # Add GeoJSON point for map display
    if site.get("latitude") and site.get("longitude"):
        site["location"] = {
            "type": "Point",
            "coordinates": [site["longitude"], site["latitude"]]
        }
    else:
        site["location"] = None

    site["sources"] = json.loads(site.get("sources") or "[]")

    # Infrastructure polygons
    infra_rows = conn.execute(
        "SELECT * FROM infrastructure WHERE site_id = ? ORDER BY created_at", (site["id"],)
    ).fetchall()
    site["infrastructure"] = []
    for r in infra_rows:
        d = dict(r)
        d["geometry"] = json.loads(d.pop("geometry_json", "null"))
        d["human_corrected"] = bool(d["human_corrected"])
        site["infrastructure"].append(d)

    # Documents
    doc_rows = conn.execute(
        "SELECT * FROM documents WHERE site_id = ? ORDER BY publication_date DESC", (site["id"],)
    ).fetchall()
    site["documents"] = []
    for r in doc_rows:
        d = dict(r)
        d["extracted_fields"] = json.loads(d.get("extracted_fields") or "{}")
        site["documents"].append(d)

    # Estimates
    est_rows = conn.execute(
        "SELECT * FROM estimates WHERE site_id = ? ORDER BY estimate_type", (site["id"],)
    ).fetchall()
    site["estimates"] = []
    for r in est_rows:
        d = dict(r)
        d["sources"] = json.loads(d.get("sources") or "[]")
        site["estimates"].append(d)

    return site


# ── App ────────────────────────────────────────────────────────────────────────

init_db()

app = FastAPI(
    title="DataCenter Lens API (lite)",
    description="Local SQLite version — no Docker required.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend at http://localhost:8000/app
# So you don't even need a second terminal — one command runs everything
frontend_path = Path(__file__).parent / "frontend"
if frontend_path.exists():
    app.mount("/app", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


@app.get("/")
def root():
    return {
        "status": "ok",
        "api_docs": "http://localhost:8000/docs",
        "map": "http://localhost:8000/app",
        "note": "This is the SQLite lite version. No Docker required."
    }

@app.get("/health")
def health():
    return {"status": "healthy", "db": str(DB_PATH)}


# ── Sites ──────────────────────────────────────────────────────────────────────

@app.get("/sites/")
def list_sites(status: Optional[str] = None, operator: Optional[str] = None):
    conn = get_conn()
    q    = "SELECT * FROM sites WHERE 1=1"
    args = []
    if status:
        q += " AND status = ?"; args.append(status)
    if operator:
        q += " AND operator LIKE ?"; args.append(f"%{operator}%")
    q += " ORDER BY name"

    rows = conn.execute(q, args).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("latitude") and d.get("longitude"):
            d["location"] = {"type": "Point", "coordinates": [d["longitude"], d["latitude"]]}
        else:
            d["location"] = None
        result.append(d)
    conn.close()
    return result


@app.get("/sites/{site_id}")
def get_site(site_id: str):
    conn = get_conn()
    row  = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, f"Site {site_id} not found")
    result = site_to_response(dict(row), conn)
    conn.close()
    return result


@app.post("/sites/", status_code=201)
def create_site(payload: SiteCreate):
    site_id = str(uuid.uuid4())
    conn    = get_conn()
    conn.execute("""
        INSERT INTO sites (id, name, address, parcel_id, operator, developer,
                           latitude, longitude, status, notes, sources)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        site_id, payload.name, payload.address, payload.parcel_id,
        payload.operator, payload.developer,
        payload.latitude, payload.longitude,
        payload.status, payload.notes,
        json.dumps(payload.sources or [])
    ))
    conn.commit()
    row    = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    result = site_to_response(dict(row), conn)
    conn.close()
    return result


class RelocatePayload(BaseModel):
    latitude:  float
    longitude: float

@app.post("/sites/{site_id}/relocate")
def relocate_site(site_id: str, payload: RelocatePayload):
    """Update a site's lat/lon by clicking on the map."""
    conn = get_conn()
    if not conn.execute("SELECT id FROM sites WHERE id = ?", (site_id,)).fetchone():
        conn.close()
        raise HTTPException(404, f"Site {site_id} not found")
    conn.execute(
        "UPDATE sites SET latitude=?, longitude=?, last_updated=datetime('now') WHERE id=?",
        (payload.latitude, payload.longitude, site_id)
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "latitude": payload.latitude, "longitude": payload.longitude}


@app.patch("/sites/{site_id}")
def update_site(site_id: str, payload: SiteUpdate):
    conn = get_conn()
    row  = conn.execute("SELECT id FROM sites WHERE id = ?", (site_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, f"Site {site_id} not found")

    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE sites SET {set_clause}, last_updated = datetime('now') WHERE id = ?",
                     [*updates.values(), site_id])
        conn.commit()

    row    = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    result = site_to_response(dict(row), conn)
    conn.close()
    return result


# ── Infrastructure ─────────────────────────────────────────────────────────────

@app.post("/sites/{site_id}/infrastructure", status_code=201)
def add_infrastructure(site_id: str, payload: InfraCreate):
    conn = get_conn()
    if not conn.execute("SELECT id FROM sites WHERE id = ?", (site_id,)).fetchone():
        conn.close()
        raise HTTPException(404, f"Site {site_id} not found")

    infra_id = str(uuid.uuid4())
    # Compute area server-side so every polygon has a usable area_sqft
    # (previously only loudoun_gis imports had areas — OSM/manual rows were NULL)
    area = payload.area_sqft if payload.area_sqft else polygon_area_sqft(payload.geometry)
    conn.execute("""
        INSERT INTO infrastructure
            (id, site_id, class_label, geometry_json, area_sqft, confidence_score,
             detection_method, imagery_source, imagery_date)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        infra_id, site_id, payload.class_label,
        json.dumps(payload.geometry), area,
        payload.confidence_score, payload.detection_method,
        payload.imagery_source, payload.imagery_date
    ))
    conn.commit()

    row = conn.execute("SELECT * FROM infrastructure WHERE id = ?", (infra_id,)).fetchone()
    d   = dict(row)
    d["geometry"]       = json.loads(d.pop("geometry_json"))
    d["human_corrected"] = bool(d["human_corrected"])
    conn.close()
    return d


@app.patch("/sites/{site_id}/infrastructure/{infra_id}")
def update_infrastructure(site_id: str, infra_id: str, payload: InfraUpdate):
    """Reclassify or correct a polygon. Sets human_corrected=True automatically."""
    conn = get_conn()
    row  = conn.execute("SELECT * FROM infrastructure WHERE id = ? AND site_id = ?",
                        (infra_id, site_id)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Infrastructure polygon not found")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        conn.close()
        raise HTTPException(400, "No fields to update")

    # If label is changing, preserve the original for the training log
    if "class_label" in updates and updates["class_label"] != row["class_label"]:
        conn.execute(
            "UPDATE infrastructure SET original_label = COALESCE(original_label, class_label) WHERE id = ?",
            (infra_id,)
        )

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE infrastructure SET {set_clause}, human_corrected = 1 WHERE id = ?",
        [*updates.values(), infra_id]
    )
    conn.commit()

    row = conn.execute("SELECT * FROM infrastructure WHERE id = ?", (infra_id,)).fetchone()
    d   = dict(row)
    d["geometry"]        = json.loads(d.pop("geometry_json"))
    d["human_corrected"] = bool(d["human_corrected"])
    conn.close()
    return d


@app.delete("/sites/{site_id}/infrastructure/{infra_id}", status_code=204)
def delete_infrastructure(site_id: str, infra_id: str):
    """Permanently delete a polygon (e.g. an office building incorrectly included)."""
    conn = get_conn()
    row  = conn.execute("SELECT id FROM infrastructure WHERE id = ? AND site_id = ?",
                        (infra_id, site_id)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Infrastructure polygon not found")
    conn.execute("DELETE FROM infrastructure WHERE id = ?", (infra_id,))
    conn.commit()
    conn.close()
    return


# Legacy endpoint — kept for backwards compat, prefer PATCH above
@app.patch("/sites/{site_id}/infrastructure/{infra_id}/correct")
def correct_label_legacy(site_id: str, infra_id: str, new_label: str, corrected_by: str = "user"):
    """Deprecated: use PATCH /sites/{site_id}/infrastructure/{infra_id} instead."""
    return update_infrastructure(site_id, infra_id, InfraUpdate(
        class_label=new_label, corrected_by=corrected_by
    ))


# ── Import polygons from LoudounGIS ───────────────────────────────────────────

@app.post("/sites/{site_id}/import-loudoun-polygons")
def import_loudoun_polygons(site_id: str):
    """
    Fetch official building footprint polygons from the Loudoun County GIS dataset
    and insert them as infrastructure rows. Skips any already imported from LoudounGIS.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
    from scrapers.loudoun import fetch_building_polygons

    conn = get_conn()
    site = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if not site:
        conn.close()
        raise HTTPException(404, f"Site {site_id} not found")

    lat = site["latitude"]
    lon = site["longitude"]
    if not lat or not lon:
        conn.close()
        raise HTTPException(400, "Site has no coordinates — add lat/lon first")

    # Delete any previously auto-imported LoudounGIS polygons so we get a fresh set
    conn.execute(
        "DELETE FROM infrastructure WHERE site_id = ? AND detection_method = 'loudoun_gis'",
        (site_id,)
    )

    buildings = fetch_building_polygons(lat, lon)
    if not buildings:
        conn.close()
        return {"imported": 0, "message": "No buildings found in LoudounGIS near this site"}

    imported = 0
    for b in buildings:
        infra_id = str(uuid.uuid4())
        sqm = b["sqft"] / 10.7639
        conn.execute("""
            INSERT INTO infrastructure
                (id, site_id, class_label, geometry_json, area_sqft,
                 confidence_score, detection_method, imagery_source, imagery_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            infra_id,
            site_id,
            "main_building",
            json.dumps(b["geojson_polygon"]),
            b["sqft"],
            0.90,
            "loudoun_gis",
            "Loudoun County GIS — Data Center Building Outlines (Aug 2024)",
            b["year"] or "2024",
        ))
        imported += 1

    conn.commit()
    conn.close()
    return {"imported": imported, "message": f"Imported {imported} building polygon(s) from LoudounGIS"}


# ── Documents ──────────────────────────────────────────────────────────────────

@app.post("/sites/{site_id}/documents", status_code=201)
def add_document(site_id: str, payload: DocCreate):
    conn = get_conn()
    if not conn.execute("SELECT id FROM sites WHERE id = ?", (site_id,)).fetchone():
        conn.close()
        raise HTTPException(404, f"Site {site_id} not found")

    doc_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO documents
            (id, site_id, document_type, title, url, publication_date,
             issuing_agency, extracted_fields)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        doc_id, site_id, payload.document_type, payload.title, payload.url,
        payload.publication_date, payload.issuing_agency,
        json.dumps(payload.extracted_fields or {})
    ))
    conn.commit()
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    d   = dict(row)
    d["extracted_fields"] = json.loads(d.get("extracted_fields") or "{}")
    conn.close()
    return d


# ── Scraping ────────────────────────────────────────────────────────────────────

def _load_scrapers():
    """Import scrapers lazily so a missing dependency doesn't break the whole app."""
    from scrapers import run_all_scrapers, ALL_SCRAPERS
    from scrapers.base import SiteQuery
    return run_all_scrapers, ALL_SCRAPERS, SiteQuery


def _run_scrapers_for_site(site: dict, source: Optional[str] = None) -> dict:
    """Run scrapers for a site dict and return {results, summary, errors}."""
    run_all_scrapers, ALL_SCRAPERS, SiteQuery = _load_scrapers()

    query = SiteQuery(
        site_id=site["id"],
        name=site["name"],
        operator=site.get("operator"),
        address=site.get("address"),
        latitude=site.get("latitude"),
        longitude=site.get("longitude"),
        parcel_id=site.get("parcel_id"),
    )

    if source:
        scraper_map = {s.name: s for s in ALL_SCRAPERS}
        if source not in scraper_map:
            raise HTTPException(
                400,
                detail=f"Unknown scraper '{source}'. "
                       f"Available: {list(scraper_map.keys())}"
            )
        try:
            docs = scraper_map[source].search(query)
            return {"results": docs, "summary": {source: len(docs)}, "errors": {}}
        except Exception as e:
            return {"results": [], "summary": {source: 0}, "errors": {source: str(e)}}
    else:
        return run_all_scrapers(query)


def _save_scraped_docs(site_id: str, scraped_docs: list) -> tuple[int, int]:
    """Save scraped documents to SQLite, skipping duplicates by URL."""
    conn = get_conn()
    existing_urls = {
        r["url"] for r in conn.execute(
            "SELECT url FROM documents WHERE site_id = ? AND url IS NOT NULL",
            (site_id,)
        ).fetchall()
    }

    saved = 0
    skipped = 0
    for sdoc in scraped_docs:
        if sdoc.url and sdoc.url in existing_urls:
            skipped += 1
            continue
        doc_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO documents
                (id, site_id, document_type, title, url, publication_date,
                 issuing_agency, extracted_fields)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            doc_id, site_id,
            sdoc.document_type, sdoc.title, sdoc.url,
            sdoc.publication_date.isoformat() if sdoc.publication_date else None,
            sdoc.issuing_agency,
            json.dumps({**sdoc.extracted_fields, "_source_scraper": sdoc.source_scraper}),
        ))
        if sdoc.url:
            existing_urls.add(sdoc.url)
        saved += 1

    conn.commit()
    conn.close()
    return saved, skipped


@app.post("/sites/{site_id}/scrape")
def scrape_site(
    site_id: str,
    source: Optional[str] = Query(None, description="Run only this scraper (google_news, ferc, loudoun)"),
):
    """Run public-records scrapers for a site and save results to the database."""
    conn = get_conn()
    row  = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"Site {site_id} not found")

    result = _run_scrapers_for_site(dict(row), source=source)
    saved, skipped = _save_scraped_docs(site_id, result["results"])

    return {
        "site_id":           site_id,
        "site_name":         row["name"],
        "documents_found":   len(result["results"]),
        "documents_saved":   saved,
        "documents_skipped": skipped,
        "by_source":         result["summary"],
        "errors":            result["errors"],
    }


@app.post("/sites/{site_id}/estimate")
def estimate_site(
    site_id: str,
    facility_type: Optional[str] = Query(
        None,
        description="Override facility type: hyperscale | colocation | enterprise | unknown"
    ),
):
    """
    Run resource estimation for a site and save results to the database.

    Uses annotated infrastructure polygons to estimate:
      - Cooling method (inferred from visible infrastructure)
      - Electricity demand (MW)
      - Water use (gallons/day)
      - Land use (sq ft)

    Each estimate includes a method, assumptions, uncertainty range, and confidence score.
    Existing estimates for this site are replaced.
    """
    conn = get_conn()
    row  = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, f"Site {site_id} not found")

    site = dict(row)

    # Load infrastructure annotations
    infra_rows = conn.execute(
        "SELECT * FROM infrastructure WHERE site_id = ?", (site_id,)
    ).fetchall()
    infra_list = []
    for r in infra_rows:
        d = dict(r)
        d["geometry"] = json.loads(d.pop("geometry_json", "null"))
        infra_list.append(d)

    # Load documents (for anchoring)
    doc_rows = conn.execute(
        "SELECT * FROM documents WHERE site_id = ?", (site_id,)
    ).fetchall()
    doc_list = []
    for r in doc_rows:
        d = dict(r)
        try:
            d["extracted_fields"] = json.loads(d.get("extracted_fields") or "{}")
        except Exception:
            d["extracted_fields"] = {}
        doc_list.append(d)

    # Run estimators
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
    from estimators import run_all_estimates, anchor_estimates

    # Pull claimed_sqft from documents so electricity works even with no drawn polygons.
    # Priority: planning_doc > permit > utility_filing > news
    DOC_TYPE_PRIORITY = ["planning_doc", "permit", "utility_filing", "environmental_review", "news", "other"]
    best_sqft_doc = None
    for dtype in DOC_TYPE_PRIORITY:
        for d in doc_list:
            if d.get("document_type") != dtype:
                continue
            ef = d.get("extracted_fields", {})
            sqft = ef.get("claimed_sqft")
            if sqft and 5000 < float(sqft) < 20_000_000:
                best_sqft_doc = d
                break
        if best_sqft_doc:
            break

    doc_sqft = float(best_sqft_doc["extracted_fields"]["claimed_sqft"]) if best_sqft_doc else 0

    estimates = run_all_estimates(site, infra_list, doc_sqft=doc_sqft)

    # Anchor estimates using figures found in scraped documents
    estimates = anchor_estimates(estimates, doc_list)

    # If electricity was anchored but water still has no IT load (no drawn polygons),
    # re-run water using the anchored MW value as IT load (÷ typical PUE of 1.4)
    elec_est  = next((e for e in estimates if e.get("estimate_type") == "electricity_demand_mw"), None)
    water_est = next((e for e in estimates if e.get("estimate_type") == "water_use_gpd"), None)
    if elec_est and water_est and elec_est.get("value") and not water_est.get("value"):
        from estimators.water import estimate_water
        from estimators.cooling import infer_cooling_method
        anchored_mw = elec_est["value"]
        # Estimate IT load from total MW using typical PUE 1.4
        it_load_mw = round(anchored_mw / 1.4, 2)
        cooling = infer_cooling_method(infra_list)
        water = estimate_water(
            it_load_mw=it_load_mw,
            cooling_method=cooling["method"],
            cooling_confidence=cooling["confidence"],
            operator=site.get("operator"),
            latitude=site.get("latitude"),
            longitude=site.get("longitude"),
        )
        # Replace the empty water estimate
        for i, e in enumerate(estimates):
            if e.get("estimate_type") == "water_use_gpd":
                estimates[i] = {
                    "estimate_type":    water["estimate_type"],
                    "value":            water["value"],
                    "uncertainty_low":  water["uncertainty_low"],
                    "uncertainty_high": water["uncertainty_high"],
                    "unit":             water["unit"],
                    "confidence_score": water["confidence_score"],
                    "status":           water["status"],
                    "method":           f"IT load derived from anchored electricity ({anchored_mw} MW ÷ PUE 1.4 = {it_load_mw} MW IT). " + water["method"],
                    "assumptions":      water["assumptions"],
                    "sources":          json.dumps(water["sources"]),
                }
                break

    # Optionally override facility type (re-runs electricity + water with that type)
    if facility_type:
        from estimators.electricity import estimate_electricity, FACILITY_PROFILES
        from estimators.water import estimate_water
        from estimators.cooling import infer_cooling_method

        if facility_type not in FACILITY_PROFILES:
            conn.close()
            raise HTTPException(400, f"Invalid facility_type. Must be one of: {list(FACILITY_PROFILES.keys())}")

        area_sqft = sum(
            (i.get("area_sqft") or 0)
            for i in infra_list
            if i.get("class_label", "").lower() in {
                "main_building", "server_hall", "data_center_building", "warehouse", "primary_building"
            }
        )
        elec     = estimate_electricity(area_sqft=area_sqft, operator=site.get("operator"),
                                        infra_list=infra_list, facility_type=facility_type)
        cooling  = infer_cooling_method(infra_list)
        it_load  = elec.get("_detail", {}).get("it_load_mw", 0)
        water    = estimate_water(it_load_mw=it_load, cooling_method=cooling["method"],
                                  cooling_confidence=cooling["confidence"])

        # Replace electricity and water in the estimates list
        estimates = [e for e in estimates if e["estimate_type"] not in ("electricity_demand_mw", "water_use_gpd")]
        for est in [elec, water]:
            estimates.append({
                "estimate_type":    est["estimate_type"],
                "value":            est["value"],
                "uncertainty_low":  est["uncertainty_low"],
                "uncertainty_high": est["uncertainty_high"],
                "unit":             est["unit"],
                "confidence_score": est["confidence_score"],
                "status":           est["status"],
                "method":           est["method"],
                "assumptions":      est["assumptions"],
                "sources":          json.dumps(est["sources"]),
            })

    # Delete old estimates for this site, insert new ones
    conn.execute("DELETE FROM estimates WHERE site_id = ?", (site_id,))
    inserted = []
    for est in estimates:
        est_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO estimates
              (id, site_id, estimate_type, value, uncertainty_low, uncertainty_high,
               unit, confidence_score, status, method, assumptions, sources)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            est_id, site_id,
            est["estimate_type"],
            est.get("value"),
            est.get("uncertainty_low"),
            est.get("uncertainty_high"),
            est.get("unit"),
            est.get("confidence_score", 0.5),
            est.get("status", "estimated"),
            est.get("method"),
            est.get("assumptions"),
            est.get("sources", "[]"),
        ))
        inserted.append({**est, "id": est_id, "site_id": site_id,
                         "sources": json.loads(est.get("sources", "[]"))})

    conn.execute(
        "UPDATE sites SET last_updated = datetime('now') WHERE id = ?", (site_id,)
    )
    conn.commit()
    conn.close()

    return {
        "site_id":         site_id,
        "site_name":       site["name"],
        "estimates_saved": len(inserted),
        "estimates":       inserted,
    }


@app.post("/estimate-all")
def estimate_all_sites(only_missing: bool = Query(False, description="Skip sites that already have estimates")):
    """Run resource estimation for every site. Returns per-site summary."""
    conn = get_conn()
    site_ids = [r["id"] for r in conn.execute("SELECT id FROM sites ORDER BY name").fetchall()]
    if only_missing:
        have = {r["site_id"] for r in conn.execute("SELECT DISTINCT site_id FROM estimates").fetchall()}
        site_ids = [s for s in site_ids if s not in have]
    conn.close()

    ok, failed = 0, []
    for sid in site_ids:
        try:
            estimate_site(sid, facility_type=None)
            ok += 1
        except Exception as e:
            failed.append({"site_id": sid, "error": str(e)[:200]})

    return {"sites_processed": ok, "sites_failed": len(failed), "failures": failed[:20]}


@app.get("/stats")
def stats():
    """Aggregate coverage stats across the database."""
    conn = get_conn()
    out = {
        "sites":            conn.execute("SELECT COUNT(*) c FROM sites").fetchone()["c"],
        "infrastructure":   conn.execute("SELECT COUNT(*) c FROM infrastructure").fetchone()["c"],
        "documents":        conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"],
        "estimates":        conn.execute("SELECT COUNT(*) c FROM estimates").fetchone()["c"],
        "sites_with_estimates": conn.execute("SELECT COUNT(DISTINCT site_id) c FROM estimates").fetchone()["c"],
        "sites_with_documents": conn.execute("SELECT COUNT(DISTINCT site_id) c FROM documents").fetchone()["c"],
        "total_estimated_mw": conn.execute(
            "SELECT ROUND(SUM(value),1) c FROM estimates WHERE estimate_type='electricity_demand_mw'"
        ).fetchone()["c"],
        "total_estimated_gpd": conn.execute(
            "SELECT ROUND(SUM(value),0) c FROM estimates WHERE estimate_type='water_use_gpd'"
        ).fetchone()["c"],
    }
    conn.close()
    return out


@app.get("/sites/{site_id}/estimate")
def get_estimates(site_id: str):
    """Return the most recent estimates for a site."""
    conn = get_conn()
    row  = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, f"Site {site_id} not found")

    rows = conn.execute(
        "SELECT * FROM estimates WHERE site_id = ? ORDER BY estimate_type", (site_id,)
    ).fetchall()
    conn.close()

    estimates = []
    for r in rows:
        d = dict(r)
        d["sources"] = json.loads(d.get("sources") or "[]")
        estimates.append(d)

    return {
        "site_id":   site_id,
        "site_name": row["name"],
        "estimates": estimates,
    }


@app.get("/sites/{site_id}/report")
def site_report(site_id: str):
    """
    Milestone 1.8 — Generate the 14-section community impact report as HTML.
    Print → Save as PDF in the browser for a shareable copy.
    """
    from fastapi.responses import HTMLResponse
    from reports import generate_report_html

    conn = get_conn()
    row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, f"Site {site_id} not found")
    site = site_to_response(dict(row), conn)
    conn.close()

    return HTMLResponse(generate_report_html(site))


@app.get("/sites/{site_id}/scrape/preview")
def preview_scrape(
    site_id: str,
    source: Optional[str] = Query(None, description="Preview only this scraper"),
):
    """Dry run: show what scrapers would find without saving anything."""
    conn = get_conn()
    row  = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"Site {site_id} not found")

    result = _run_scrapers_for_site(dict(row), source=source)

    return {
        "site_id":   site_id,
        "site_name": row["name"],
        "dry_run":   True,
        "by_source": result["summary"],
        "errors":    result["errors"],
        "documents": [d.to_document_create_dict() for d in result["results"]],
    }
