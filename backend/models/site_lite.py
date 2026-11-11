"""
models/site_lite.py  — SQLite-compatible version of site.py

Geometry is stored as a JSON string instead of PostGIS geometry.
Everything else is identical to the PostGIS version.
"""

import uuid
import json
from datetime import date, datetime
from sqlalchemy import Column, String, Numeric, Date, DateTime, Text, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import relationship
from database import Base


class Site(Base):
    __tablename__ = "sites"

    id        = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name      = Column(String, nullable=False)
    address   = Column(String)
    parcel_id = Column(String)
    operator  = Column(String)
    developer = Column(String)

    # Stored as GeoJSON strings, e.g. '{"type":"Point","coordinates":[-77.49,39.04]}'
    location_json  = Column(Text)
    footprint_json = Column(Text)

    status          = Column(String, default="unknown")
    first_seen_date = Column(String, default=lambda: date.today().isoformat())
    last_updated    = Column(DateTime, server_default=func.now(), onupdate=func.now())
    notes           = Column(Text)
    sources_json    = Column(Text, default="[]")  # JSON array as string

    infrastructure = relationship("DetectedInfrastructure", back_populates="site", cascade="all, delete-orphan")
    documents      = relationship("PublicDocument",          back_populates="site", cascade="all, delete-orphan")
    estimates      = relationship("ResourceEstimate",        back_populates="site", cascade="all, delete-orphan")

    @property
    def location(self):
        return json.loads(self.location_json) if self.location_json else None

    @property
    def footprint(self):
        return json.loads(self.footprint_json) if self.footprint_json else None

    @property
    def sources(self):
        return json.loads(self.sources_json) if self.sources_json else []
