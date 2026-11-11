"""
models/site.py
--------------
The Site model represents a single data center location.
One site can have many DetectedInfrastructure objects, PublicDocuments,
ResourceEstimates, and eventually a generated Report.
"""

import uuid
from datetime import date, datetime
from sqlalchemy import Column, String, Numeric, Date, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry
from database import Base


class Site(Base):
    __tablename__ = "sites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Identity
    name        = Column(String, nullable=False)          # Common project or facility name
    address     = Column(String)                          # Street address
    parcel_id   = Column(String)                          # Loudoun County parcel number (e.g. "123-45-6789")
    operator    = Column(String)                          # Company operating the facility
    developer   = Column(String)                          # Company that built / is building it (may differ from operator)

    # Geometry — PostGIS stores these as spatial types
    # location: a single lat/lon point (the site centroid)
    # footprint: the site boundary as a polygon
    location  = Column(Geometry("POINT", srid=4326))
    footprint = Column(Geometry("POLYGON", srid=4326))

    # Status
    status = Column(
        String,
        default="unknown",
        comment="operating | under_construction | proposed | rumored | cancelled | unknown"
    )

    # Metadata
    first_seen_date = Column(Date, default=date.today)
    last_updated    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    notes           = Column(Text)

    # sources is a JSON array of objects like:
    # [{"url": "https://...", "title": "Permit #12345", "date": "2024-03-01", "type": "permit"}]
    sources = Column(JSONB, default=list)

    # Relationships — SQLAlchemy will load these on demand
    infrastructure = relationship("DetectedInfrastructure", back_populates="site", cascade="all, delete-orphan")
    documents      = relationship("PublicDocument",          back_populates="site", cascade="all, delete-orphan")
    estimates      = relationship("ResourceEstimate",        back_populates="site", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Site {self.name} ({self.status})>"
