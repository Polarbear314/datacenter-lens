"""
models/document.py
------------------
A public document linked to a site — permit, utility filing, news article,
tax agreement, planning board document, etc.

We store the URL and extracted structured fields rather than full text,
to avoid copyright issues and keep the database lean.
"""

import uuid
from datetime import date, datetime
from sqlalchemy import Column, String, Boolean, Date, DateTime, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from database import Base


class PublicDocument(Base):
    __tablename__ = "public_documents"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)

    # Document identity
    document_type = Column(
        String,
        comment="permit | tax_agreement | utility_filing | planning_doc | news | "
                "environmental_review | court_filing | company_announcement | other"
    )
    title            = Column(String, nullable=False)
    url              = Column(String)
    publication_date = Column(Date)
    issuing_agency   = Column(String)   # e.g., "Loudoun County Planning & Zoning"

    # Structured fields extracted from the document by LLM or manual review.
    # Example:
    # {
    #   "claimed_mw": 48,
    #   "claimed_sqft": 450000,
    #   "claimed_jobs_permanent": 50,
    #   "tax_abatement_value": 12000000,
    #   "water_source": "municipal",
    #   "permit_number": "SPEX-2024-0032"
    # }
    extracted_fields = Column(JSONB, default=dict)

    full_text_available = Column(Boolean, default=False)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    site = relationship("Site", back_populates="documents")

    def __repr__(self):
        return f"<Document '{self.title}' ({self.document_type})>"
