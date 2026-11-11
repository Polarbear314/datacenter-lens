"""
models/estimate.py
------------------
A resource estimate for a site — electricity demand, water use, CO2 emissions, jobs, etc.

Every estimate has:
  - a point value (our best single guess)
  - an uncertainty range (low / high)
  - a confidence score (0.0–1.0)
  - a status label: confirmed | estimated | inferred | unknown
  - a plain-language method description and list of assumptions

This is the core of the tool's policy value. A wide uncertainty range is honest;
a narrow range without sources is misleading. Always prefer honesty.
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, DateTime, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from database import Base

# Valid estimate types
ESTIMATE_TYPES = [
    "electricity_mw",           # Peak power demand in megawatts
    "electricity_mwh_annual",   # Annual electricity consumption in MWh
    "water_gpd",                # Water use in gallons per day
    "water_gpy",                # Water use in gallons per year
    "co2e_tons_annual",         # Grid carbon emissions (tons CO2-equivalent/year)
    "jobs_construction",        # Construction jobs (temporary)
    "jobs_permanent",           # Permanent operations jobs
    "tax_revenue_annual",       # Estimated annual property/BPOL tax revenue ($)
    "subsidy_value",            # Total value of tax exemptions/incentives ($)
    "construction_cost",        # Estimated construction cost ($)
]


class ResourceEstimate(Base):
    __tablename__ = "resource_estimates"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)

    estimate_type = Column(String, nullable=False)  # One of ESTIMATE_TYPES

    # The estimate itself
    value           = Column(Numeric)  # Point estimate (best single guess)
    uncertainty_low  = Column(Numeric)  # Lower bound
    uncertainty_high = Column(Numeric)  # Upper bound
    unit            = Column(String)   # "MW", "gallons/day", "USD", "tons CO2e", etc.

    # Confidence and status
    confidence_score = Column(Numeric(3, 2), default=0.5)
    status           = Column(
        String,
        default="estimated",
        comment="confirmed | estimated | inferred | unknown"
    )

    # Methodology — these must be filled in for every estimate.
    # They are what make the tool credible to policy users.
    method      = Column(Text)   # Plain English: how was this calculated?
    assumptions = Column(Text)   # Key assumptions that could change the estimate
    sources     = Column(JSONB, default=list)  # [{url, title, date}]

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    site = relationship("Site", back_populates="estimates")

    def __repr__(self):
        return f"<Estimate {self.estimate_type}: {self.value} ({self.status})>"
