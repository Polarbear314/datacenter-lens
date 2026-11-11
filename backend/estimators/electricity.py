"""
electricity.py — Estimate electricity demand from building footprint.

Method:
  IT_load_MW = area_sqft × W_per_sqft / 1_000_000
  Total_load_MW = IT_load_MW / PUE   (Power Usage Effectiveness)

W/sqft benchmarks by facility type:
  Source: Lawrence Berkeley National Laboratory (LBNL), "United States Data Center
  Energy Usage Report" (2016, 2024 update); Uptime Institute Annual Survey 2023.

  - Hyperscale (AWS/Azure/GCP-style):   150 W/sqft  (range 100–220)
  - Colocation (Equinix/QTS/CyrusOne): 110 W/sqft  (range 70–160)
  - Enterprise:                          75 W/sqft  (range 50–100)
  - Generic/unknown:                    100 W/sqft  (range 50–200)

PUE benchmarks:
  - Hyperscale: 1.2  (world-class, often lower — Google reported 1.10 in 2022)
  - Colocation: 1.4  (industry average)
  - Enterprise: 1.6  (older facilities)
  - Unknown:    1.5  (Uptime Institute 2023 global average)
"""

from typing import Optional

# ── Coefficients ───────────────────────────────────────────────────────────────

FACILITY_PROFILES: dict[str, dict] = {
    "hyperscale": {
        "w_per_sqft":       150.0,
        "w_per_sqft_low":   100.0,
        "w_per_sqft_high":  220.0,
        "pue":              1.2,
        "pue_low":          1.10,
        "pue_high":         1.35,
        "description":      "Hyperscale cloud facility (AWS, Azure, Google-style)",
    },
    "colocation": {
        "w_per_sqft":       110.0,
        "w_per_sqft_low":    70.0,
        "w_per_sqft_high":  160.0,
        "pue":              1.4,
        "pue_low":          1.25,
        "pue_high":         1.65,
        "description":      "Colocation facility (Equinix, QTS, CyrusOne-style)",
    },
    "enterprise": {
        "w_per_sqft":        75.0,
        "w_per_sqft_low":    50.0,
        "w_per_sqft_high":  100.0,
        "pue":              1.6,
        "pue_low":          1.40,
        "pue_high":         1.90,
        "description":      "Enterprise or on-premises data center",
    },
    "unknown": {
        "w_per_sqft":       100.0,
        "w_per_sqft_low":    50.0,
        "w_per_sqft_high":  200.0,
        "pue":              1.5,
        "pue_low":          1.20,
        "pue_high":         1.90,
        "description":      "Facility type unknown — using industry-wide average",
    },
}

# Map operator names to likely facility type
OPERATOR_PROFILES: dict[str, str] = {
    "equinix":      "colocation",
    "qts":          "colocation",
    "cyrusone":     "colocation",
    "stack":        "colocation",
    "coresite":     "colocation",
    "ironmountain": "colocation",
    "amazon":       "hyperscale",
    "aws":          "hyperscale",
    "google":       "hyperscale",
    "microsoft":    "hyperscale",
    "meta":         "hyperscale",
    "apple":        "hyperscale",
    "oracle":       "hyperscale",
}

SOURCES = [
    "LBNL, 'United States Data Center Energy Usage Report', 2016 + 2024 update",
    "Uptime Institute, 'Annual Global Data Center Survey', 2023",
    "DOE, 'Data Centers and Servers Energy Efficiency', energy.gov",
]


def _sqft_to_mw(sqft: float, w_per_sqft: float, pue: float) -> float:
    """Convert area and density to total facility load in MW."""
    it_watts = sqft * w_per_sqft
    total_watts = it_watts * pue           # PUE multiplies IT load → total load
    return round(total_watts / 1_000_000, 3)


def _detect_facility_type(operator: Optional[str], infra_list: list[dict]) -> tuple[str, float]:
    """
    Guess facility type from operator name and visible infrastructure.
    Returns (facility_type, confidence).
    """
    if operator:
        key = operator.lower().replace(" ", "").replace("-", "")
        for pattern, ftype in OPERATOR_PROFILES.items():
            if pattern in key:
                return ftype, 0.75   # confident but not certain — operator names can be misleading

    # Infer from infrastructure: if a substation is visible, likely a large facility
    labels = {i["class_label"].lower() for i in infra_list}
    if "substation" in labels or "electrical_substation" in labels:
        return "colocation", 0.45   # large enough to need its own substation

    return "unknown", 0.30


def estimate_electricity(
    area_sqft: float,
    operator: Optional[str] = None,
    infra_list: Optional[list[dict]] = None,
    facility_type: Optional[str] = None,
) -> dict:
    """
    Estimate total electricity demand in MW.

    Args:
        area_sqft:      Total annotated floor area of main building(s) in sq ft.
        operator:       Operator name (used to infer facility type).
        infra_list:     List of infrastructure annotation dicts.
        facility_type:  Override facility type ('hyperscale','colocation','enterprise','unknown').

    Returns:
        {
            estimate_type: "electricity_demand_mw",
            value:         float,   # central estimate in MW
            uncertainty_low, uncertainty_high,
            unit:          "MW",
            confidence_score: float,
            status:        "estimated",
            method:        str,
            assumptions:   str,
            sources:       list[str],
            _detail: { facility_type, w_per_sqft, pue, it_load_mw, ... }
        }
    """
    infra_list = infra_list or []

    if area_sqft <= 0:
        return {
            "estimate_type":    "electricity_demand_mw",
            "value":            None,
            "uncertainty_low":  None,
            "uncertainty_high": None,
            "unit":             "MW",
            "confidence_score": 0.0,
            "status":           "unknown",
            "method":           "No annotated building footprint available.",
            "assumptions":      "Cannot estimate without a building footprint. "
                                "Draw a polygon around the main building in the map to enable estimation.",
            "sources":          SOURCES,
            "_detail":          {"area_sqft": area_sqft},
        }

    # Determine facility type
    if facility_type and facility_type in FACILITY_PROFILES:
        ftype = facility_type
        type_confidence = 0.80
    else:
        ftype, type_confidence = _detect_facility_type(operator, infra_list)

    profile = FACILITY_PROFILES[ftype]

    # Central estimate
    mw_central = _sqft_to_mw(area_sqft, profile["w_per_sqft"], profile["pue"])
    mw_low     = _sqft_to_mw(area_sqft, profile["w_per_sqft_low"],  profile["pue_high"])
    mw_high    = _sqft_to_mw(area_sqft, profile["w_per_sqft_high"], profile["pue_low"])

    it_mw = round(area_sqft * profile["w_per_sqft"] / 1_000_000, 3)

    # Confidence: type confidence × annotation quality factor
    # More annotated infrastructure = higher confidence
    infra_bonus = min(0.15, len(infra_list) * 0.03)
    confidence  = round(min(0.90, type_confidence + infra_bonus), 2)

    method = (
        f"IT load = {area_sqft:,.0f} sqft × {profile['w_per_sqft']} W/sqft = {it_mw} MW (IT). "
        f"Total facility load = IT load × PUE {profile['pue']} = {mw_central} MW. "
        f"Facility type assumed: {ftype} ({profile['description']})."
    )

    assumptions = (
        f"Building footprint: {area_sqft:,.0f} sq ft (from annotated polygon area). "
        f"Power density: {profile['w_per_sqft']} W/sqft (range {profile['w_per_sqft_low']}–"
        f"{profile['w_per_sqft_high']} W/sqft for {ftype} facilities). "
        f"PUE: {profile['pue']} (range {profile['pue_low']}–{profile['pue_high']}). "
        f"Assumes all annotated main-building floor area is active server floor space "
        f"(no partial-occupancy adjustment). "
        f"Does not account for phased construction or partial operation."
    )

    return {
        "estimate_type":    "electricity_demand_mw",
        "value":            mw_central,
        "uncertainty_low":  mw_low,
        "uncertainty_high": mw_high,
        "unit":             "MW",
        "confidence_score": confidence,
        "status":           "estimated",
        "method":           method,
        "assumptions":      assumptions,
        "sources":          SOURCES,
        "_detail": {
            "area_sqft":    area_sqft,
            "facility_type": ftype,
            "w_per_sqft":   profile["w_per_sqft"],
            "pue":          profile["pue"],
            "it_load_mw":   it_mw,
        },
    }
