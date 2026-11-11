"""
routers/sites.py
----------------
API endpoints for sites — the core data object of the tool.

Endpoints:
  GET    /sites                                    — list all sites
  GET    /sites/{id}                               — full site detail
  POST   /sites                                    — create a new site
  PATCH  /sites/{id}                               — update site fields
  POST   /sites/{id}/infrastructure                — add a polygon
  PATCH  /sites/{id}/infrastructure/{infra_id}     — reclassify a polygon
  DELETE /sites/{id}/infrastructure/{infra_id}     — delete a polygon
  POST   /sites/{id}/documents                     — link a public document
  POST   /sites/{id}/relocate                      — move the site pin
"""

from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from geoalchemy2.functions import ST_AsGeoJSON, ST_GeomFromText, ST_SetSRID
import json

from database import get_db
from models.site import Site
from models.infrastructure import DetectedInfrastructure
from models.document import PublicDocument
from models.estimate import ResourceEstimate
from schemas import (
    SiteCreate, SiteUpdate, SiteResponse, SiteListItem,
    InfrastructureCreate, InfrastructureUpdate, InfrastructureResponse,
    DocumentCreate, DocumentResponse,
    EstimateResponse,
)

router = APIRouter(prefix="/sites", tags=["sites"])


# ── List all sites ──────────────────────────────────────────────────────────

@router.get("/", response_model=list[SiteListItem])
def list_sites(
    status: Optional[str] = Query(None, description="Filter by status"),
    operator: Optional[str] = Query(None, description="Filter by operator name"),
    db: Session = Depends(get_db),
):
    """
    Returns all sites with just enough info to render map markers:
    id, name, operator, status, and coordinates.
    """
    q = db.query(Site)
    if status:
        q = q.filter(Site.status == status)
    if operator:
        q = q.filter(Site.operator.ilike(f"%{operator}%"))

    sites = q.order_by(Site.name).all()

    results = []
    for s in sites:
        # Convert PostGIS geometry to GeoJSON so the frontend can use it directly
        loc = None
        if s.location is not None:
            geojson_str = db.scalar(ST_AsGeoJSON(s.location))
            loc = json.loads(geojson_str) if geojson_str else None

        results.append(SiteListItem(
            id=s.id,
            name=s.name,
            address=s.address,
            operator=s.operator,
            status=s.status,
            location=loc,
        ))
    return results


# ── Get one site (full detail) ───────────────────────────────────────────────

@router.get("/{site_id}", response_model=SiteResponse)
def get_site(site_id: UUID, db: Session = Depends(get_db)):
    """
    Full site record including all linked infrastructure polygons,
    public documents, and resource estimates.
    """
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    # Convert geometries to GeoJSON for the response
    location_geojson = None
    if site.location is not None:
        raw = db.scalar(ST_AsGeoJSON(site.location))
        location_geojson = json.loads(raw) if raw else None

    footprint_geojson = None
    if site.footprint is not None:
        raw = db.scalar(ST_AsGeoJSON(site.footprint))
        footprint_geojson = json.loads(raw) if raw else None

    # Build infrastructure list with geometries
    infra_list = []
    for infra in site.infrastructure:
        geom_geojson = None
        if infra.geometry is not None:
            raw = db.scalar(ST_AsGeoJSON(infra.geometry))
            geom_geojson = json.loads(raw) if raw else None
        infra_list.append(InfrastructureResponse(
            id=infra.id,
            site_id=infra.site_id,
            class_label=infra.class_label,
            geometry=geom_geojson,
            area_sqft=infra.area_sqft,
            confidence_score=float(infra.confidence_score) if infra.confidence_score else None,
            detection_method=infra.detection_method,
            imagery_source=infra.imagery_source,
            imagery_date=infra.imagery_date,
            human_corrected=infra.human_corrected,
            corrected_by=infra.corrected_by,
            original_label=infra.original_label,
        ))

    return SiteResponse(
        id=site.id,
        name=site.name,
        address=site.address,
        parcel_id=site.parcel_id,
        operator=site.operator,
        developer=site.developer,
        status=site.status,
        location=location_geojson,
        footprint=footprint_geojson,
        notes=site.notes,
        sources=site.sources or [],
        first_seen_date=site.first_seen_date,
        last_updated=site.last_updated,
        infrastructure=infra_list,
        documents=[DocumentResponse.model_validate(d) for d in site.documents],
        estimates=[EstimateResponse.model_validate(e) for e in site.estimates],
    )


# ── Create a site ────────────────────────────────────────────────────────────

@router.post("/", response_model=SiteResponse, status_code=201)
def create_site(payload: SiteCreate, db: Session = Depends(get_db)):
    """
    Create a new site record. Accepts lat/lon as separate fields and
    converts them to a PostGIS POINT geometry.
    """
    site = Site(
        name=payload.name,
        address=payload.address,
        parcel_id=payload.parcel_id,
        operator=payload.operator,
        developer=payload.developer,
        status=payload.status,
        notes=payload.notes,
        sources=payload.sources or [],
    )

    # Convert lat/lon to PostGIS geometry
    if payload.latitude is not None and payload.longitude is not None:
        wkt = f"POINT({payload.longitude} {payload.latitude})"
        site.location = ST_SetSRID(ST_GeomFromText(wkt), 4326)

    db.add(site)
    db.commit()
    db.refresh(site)

    # Return via the detail endpoint to get the full response shape
    return get_site(site.id, db)


# ── Update a site ────────────────────────────────────────────────────────────

@router.patch("/{site_id}", response_model=SiteResponse)
def update_site(site_id: UUID, payload: SiteUpdate, db: Session = Depends(get_db)):
    """
    Update mutable site fields. Only fields included in the request body are changed.
    """
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(site, field, value)

    db.commit()
    db.refresh(site)
    return get_site(site_id, db)


# ── Add infrastructure to a site ─────────────────────────────────────────────

@router.post("/{site_id}/infrastructure", response_model=InfrastructureResponse, status_code=201)
def add_infrastructure(
    site_id: UUID,
    payload: InfrastructureCreate,
    db: Session = Depends(get_db),
):
    """
    Save a manually drawn or model-detected polygon to a site.
    The geometry should be a GeoJSON Polygon object (as a dict).
    """
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    # Convert GeoJSON polygon to WKT for PostGIS
    from shapely.geometry import shape
    geom_shape = shape(payload.geometry)
    wkt = geom_shape.wkt

    infra = DetectedInfrastructure(
        site_id=site_id,
        class_label=payload.class_label,
        geometry=ST_SetSRID(ST_GeomFromText(wkt), 4326),
        area_sqft=geom_shape.area * 1_196_000,  # Rough conversion from degrees² to sqft at mid-latitudes
        confidence_score=payload.confidence_score,
        detection_method=payload.detection_method,
        imagery_source=payload.imagery_source,
        imagery_date=payload.imagery_date,
    )

    db.add(infra)
    db.commit()
    db.refresh(infra)

    # Return the geometry as GeoJSON
    raw = db.scalar(ST_AsGeoJSON(infra.geometry))
    geom_geojson = json.loads(raw) if raw else None

    return InfrastructureResponse(
        id=infra.id,
        site_id=infra.site_id,
        class_label=infra.class_label,
        geometry=geom_geojson,
        area_sqft=float(infra.area_sqft) if infra.area_sqft else None,
        confidence_score=float(infra.confidence_score) if infra.confidence_score else None,
        detection_method=infra.detection_method,
        imagery_source=infra.imagery_source,
        imagery_date=infra.imagery_date,
        human_corrected=infra.human_corrected,
        corrected_by=infra.corrected_by,
        original_label=infra.original_label,
    )


# ── Reclassify an infrastructure polygon ────────────────────────────────────

@router.patch("/{site_id}/infrastructure/{infra_id}", response_model=InfrastructureResponse)
def update_infrastructure(
    site_id: UUID,
    infra_id: UUID,
    payload: InfrastructureUpdate,
    db: Session = Depends(get_db),
):
    """
    Reclassify or correct an existing polygon.
    Sets human_corrected=True automatically so we know a human reviewed it.
    """
    infra = db.query(DetectedInfrastructure).filter(
        DetectedInfrastructure.id == infra_id,
        DetectedInfrastructure.site_id == site_id,
    ).first()
    if not infra:
        raise HTTPException(status_code=404, detail="Infrastructure polygon not found")

    update_data = payload.model_dump(exclude_unset=True)

    # If the label is changing, save the original for the training log
    if "class_label" in update_data and update_data["class_label"] != infra.class_label:
        infra.original_label = infra.class_label

    for field, value in update_data.items():
        setattr(infra, field, value)

    infra.human_corrected = True

    db.commit()
    db.refresh(infra)

    raw = db.scalar(ST_AsGeoJSON(infra.geometry))
    geom_geojson = json.loads(raw) if raw else None

    return InfrastructureResponse(
        id=infra.id,
        site_id=infra.site_id,
        class_label=infra.class_label,
        geometry=geom_geojson,
        area_sqft=float(infra.area_sqft) if infra.area_sqft else None,
        confidence_score=float(infra.confidence_score) if infra.confidence_score else None,
        detection_method=infra.detection_method,
        imagery_source=infra.imagery_source,
        imagery_date=infra.imagery_date,
        human_corrected=infra.human_corrected,
        corrected_by=infra.corrected_by,
        original_label=infra.original_label,
    )


# ── Delete an infrastructure polygon ─────────────────────────────────────────

@router.post("/{site_id}/import-loudoun-polygons")
def import_loudoun_polygons(site_id: UUID, db: Session = Depends(get_db)):
    """
    Fetch official building footprints from Loudoun County GIS
    (Data Center Building Outlines dataset, 271 buildings) and save them
    as DetectedInfrastructure polygons for this site.

    Uses a ~0.2-mile search radius so only the local campus is captured.
    Clears any previously-imported LoudounGIS polygons first to avoid duplicates.
    All imported polygons are marked detection_method="loudoun_gis" and
    human_corrected=False so they appear dashed on the map until reviewed.
    """
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    if site.location is None:
        raise HTTPException(
            status_code=400,
            detail="This site has no coordinates. Use 'Fix pin location' first.",
        )

    # Extract lat/lon from PostGIS geometry
    loc_raw = db.scalar(ST_AsGeoJSON(site.location))
    loc     = json.loads(loc_raw)
    lon, lat = loc["coordinates"]

    # Fetch official footprints from LoudounGIS (returns geometry)
    from scrapers.loudoun import fetch_building_polygons, ScraperError as LoudounError
    try:
        buildings = fetch_building_polygons(lat, lon)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LoudounGIS query failed: {e}")

    if not buildings:
        return {
            "imported": 0,
            "message": (
                "No buildings found in LoudounGIS within the search radius. "
                "The site may not yet be in the county dataset, or the pin "
                "location may need adjustment."
            ),
        }

    # Clear any previous LoudounGIS imports for this site to avoid duplicates
    existing_gis = (
        db.query(DetectedInfrastructure)
        .filter(
            DetectedInfrastructure.site_id == site_id,
            DetectedInfrastructure.detection_method == "loudoun_gis",
        )
        .all()
    )
    for old in existing_gis:
        db.delete(old)

    # Import each building footprint
    from shapely.geometry import shape as shapely_shape
    imported = 0
    for b in buildings:
        geojson_poly = b["geojson_polygon"]

        # Validate / close the ring if needed
        rings = geojson_poly.get("coordinates", [])
        closed_rings = []
        for ring in rings:
            if ring and ring[0] != ring[-1]:
                ring = ring + [ring[0]]
            closed_rings.append(ring)
        geojson_poly["coordinates"] = closed_rings

        try:
            geom   = shapely_shape(geojson_poly)
            wkt    = geom.wkt
        except Exception:
            continue   # Skip malformed geometries silently

        # Prefer the authoritative sqft from the county; fall back to shapely estimate
        area_sqft = b.get("sqft") or (geom.area * 1_196_000)

        infra = DetectedInfrastructure(
            site_id          = site_id,
            class_label      = "main_building",
            geometry         = ST_SetSRID(ST_GeomFromText(wkt), 4326),
            area_sqft        = area_sqft,
            confidence_score = 0.85,
            detection_method = "loudoun_gis",
            imagery_source   = (
                "Loudoun County GIS — Data Center Building Outlines "
                "(data-loudoungis.opendata.arcgis.com)"
            ),
        )
        db.add(infra)
        imported += 1

    db.commit()
    return {
        "imported": imported,
        "message": (
            f"Imported {imported} building outline(s) from Loudoun County GIS. "
            "Click each dashed polygon to verify and correct the class label."
        ),
    }


@router.delete("/{site_id}/infrastructure/{infra_id}", status_code=204)
def delete_infrastructure(
    site_id: UUID,
    infra_id: UUID,
    db: Session = Depends(get_db),
):
    """
    Permanently delete a polygon. Use when a polygon was incorrectly
    placed (e.g., an office building mistaken for a data center building).
    """
    infra = db.query(DetectedInfrastructure).filter(
        DetectedInfrastructure.id == infra_id,
        DetectedInfrastructure.site_id == site_id,
    ).first()
    if not infra:
        raise HTTPException(status_code=404, detail="Infrastructure polygon not found")

    db.delete(infra)
    db.commit()
    return Response(status_code=204)


# ── Add a document to a site ──────────────────────────────────────────────────

@router.post("/{site_id}/documents", response_model=DocumentResponse, status_code=201)
def add_document(site_id: UUID, payload: DocumentCreate, db: Session = Depends(get_db)):
    """Link a public document to a site."""
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    doc = PublicDocument(
        site_id=site_id,
        document_type=payload.document_type,
        title=payload.title,
        url=payload.url,
        publication_date=payload.publication_date,
        issuing_agency=payload.issuing_agency,
        extracted_fields=payload.extracted_fields or {},
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return DocumentResponse.model_validate(doc)
