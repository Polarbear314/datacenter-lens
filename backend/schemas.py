"""
schemas.py
----------
Pydantic models for API request/response validation.

These are separate from the SQLAlchemy models in /models/.
SQLAlchemy models describe the database tables.
Pydantic schemas describe what the API accepts and returns.
"""

from __future__ import annotations
from typing import Optional, Any
from uuid import UUID
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict


# ── Infrastructure ────────────────────────────────────────────────────────────

class InfrastructureCreate(BaseModel):
    """Used when saving a manually drawn or model-detected polygon."""
    class_label:      str
    geometry:         dict          # GeoJSON Polygon object
    confidence_score: float = 1.0
    detection_method: str   = "manual"
    imagery_source:   Optional[str]  = None
    imagery_date:     Optional[date] = None


class InfrastructureUpdate(BaseModel):
    """Used when a human corrects or reclassifies a polygon."""
    class_label:      Optional[str]   = None
    confidence_score: Optional[float] = None
    corrected_by:     Optional[str]   = None


class InfrastructureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               UUID
    site_id:          UUID
    class_label:      str
    geometry:         Optional[dict] = None   # GeoJSON Polygon
    area_sqft:        Optional[float] = None
    confidence_score: Optional[float] = None
    detection_method: Optional[str]   = None
    imagery_source:   Optional[str]   = None
    imagery_date:     Optional[date]  = None
    human_corrected:  Optional[bool]  = None
    corrected_by:     Optional[str]   = None
    original_label:   Optional[str]   = None


# ── Documents ─────────────────────────────────────────────────────────────────

class DocumentCreate(BaseModel):
    document_type:    Optional[str]  = None
    title:            str
    url:              Optional[str]  = None
    publication_date: Optional[date] = None
    issuing_agency:   Optional[str]  = None
    extracted_fields: Optional[dict] = None


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               UUID
    site_id:          UUID
    document_type:    Optional[str]  = None
    title:            str
    url:              Optional[str]  = None
    publication_date: Optional[date] = None
    issuing_agency:   Optional[str]  = None
    extracted_fields: Optional[dict] = None
    created_at:       Optional[datetime] = None


# ── Estimates ─────────────────────────────────────────────────────────────────

class EstimateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               UUID
    site_id:          UUID
    estimate_type:    str
    value:            Optional[float] = None
    uncertainty_low:  Optional[float] = None
    uncertainty_high: Optional[float] = None
    unit:             Optional[str]   = None
    confidence_score: Optional[float] = None
    status:           Optional[str]   = None
    method:           Optional[str]   = None
    assumptions:      Optional[str]   = None
    sources:          Optional[list]  = None
    created_at:       Optional[datetime] = None


# ── Sites ─────────────────────────────────────────────────────────────────────

class SiteCreate(BaseModel):
    """Fields accepted when creating a new site."""
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
    """Fields that can be updated after creation. All optional."""
    name:      Optional[str] = None
    address:   Optional[str] = None
    parcel_id: Optional[str] = None
    operator:  Optional[str] = None
    developer: Optional[str] = None
    status:    Optional[str] = None
    notes:     Optional[str] = None
    sources:   Optional[list] = None


class SiteListItem(BaseModel):
    """Lightweight representation for map marker rendering."""
    id:       UUID
    name:     str
    address:  Optional[str] = None
    operator: Optional[str] = None
    status:   Optional[str] = None
    location: Optional[dict] = None   # GeoJSON Point


class SiteResponse(BaseModel):
    """Full site detail including all linked objects."""
    model_config = ConfigDict(from_attributes=True)

    id:         UUID
    name:       str
    address:    Optional[str]      = None
    parcel_id:  Optional[str]      = None
    operator:   Optional[str]      = None
    developer:  Optional[str]      = None
    status:     Optional[str]      = None
    location:   Optional[dict]     = None   # GeoJSON Point
    footprint:  Optional[dict]     = None   # GeoJSON Polygon
    notes:      Optional[str]      = None
    sources:    Optional[list]     = None
    first_seen_date: Optional[date]     = None
    last_updated:    Optional[datetime] = None

    infrastructure: list[InfrastructureResponse] = []
    documents:      list[DocumentResponse]        = []
    estimates:      list[EstimateResponse]        = []
