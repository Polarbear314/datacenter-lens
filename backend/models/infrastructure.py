"""
models/infrastructure.py
------------------------
Represents a single detected or manually annotated polygon on a site —
e.g., a main building, substation, cooling tower cluster, or generator pad.

Every polygon has:
  - a class label (what type of infrastructure it is)
  - a confidence score (0.0 = no confidence, 1.0 = certain)
  - a detection method (manual, SAM, YOLO, etc.)
  - human correction tracking (so we never lose the original model output)
"""

import uuid
from datetime import date, datetime
from sqlalchemy import Column, String, Numeric, Boolean, Date, DateTime, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry
from database import Base

# Valid class labels — extend this list as the CV model improves
INFRASTRUCTURE_CLASSES = [
    "campus",               # Whole-site boundary — used when classifying individual structures isn't yet practical
    "main_building",        # Primary server hall / data hall
    "substation",           # Electrical substation
    "cooling_equipment",    # Cooling towers, chillers, CRAC units
    "generators",           # Backup diesel or gas generators
    "water_storage",        # Cooling ponds, stormwater retention
    "utility_corridor",     # Power lines, pipe runs, cable trays
    "construction_staging", # Active construction area
    "parking",              # Parking lots and roads
    "solar",                # Solar arrays or battery storage
    "other",                # Unclassified
    "unknown",              # Not enough information to classify
]


class DetectedInfrastructure(Base):
    __tablename__ = "detected_infrastructure"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)

    # What type of infrastructure is this polygon?
    class_label = Column(String, nullable=False, default="unknown")

    # The polygon itself (in WGS84 lat/lon)
    geometry    = Column(Geometry("POLYGON", srid=4326), nullable=False)

    # Computed from the geometry (stored for fast queries)
    area_sqft   = Column(Numeric)

    # How confident is the model (or annotator) in this classification?
    # 1.0 = certain (e.g., human annotator confirmed), 0.5 = uncertain
    confidence_score = Column(Numeric(3, 2), default=1.0)

    # How was this polygon detected?
    detection_method = Column(
        String,
        default="manual",
        comment="manual | sam | yolo | rule_based"
    )

    # Imagery info
    imagery_source = Column(String)   # e.g., "Google Maps Static API", "NAIP 2022"
    imagery_date   = Column(Date)     # Date the imagery was captured (not downloaded)
    model_version  = Column(String)   # e.g., "sam-vit-h", "yolov8-dc-v1"

    # Human correction tracking
    # If a human corrects a model label, we keep the original for retraining purposes
    human_corrected  = Column(Boolean, default=False)
    corrected_by     = Column(String)                   # Analyst name or user ID
    correction_date  = Column(DateTime(timezone=True))
    original_label   = Column(String)                   # The model's original classification

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship back to parent site
    site = relationship("Site", back_populates="infrastructure")

    def __repr__(self):
        return f"<Infrastructure {self.class_label} ({self.confidence_score:.0%} confidence)>"
