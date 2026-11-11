"""
water.py — Estimate daily water consumption with climate adjustment + operator lookup.

Three confidence tiers, applied in order:

  Tier 1 — Operator WUE (highest confidence)
    Major hyperscalers publish WUE in annual sustainability reports.
    If the operator is known, use their published range instead of generic benchmarks.
    Sources: Google ESG Report 2023, Microsoft Environmental Sustainability Report 2023,
             Meta Sustainability Report 2022, Amazon Sustainability Report 2023.

  Tier 2 — Climate-adjusted WUE (medium confidence)
    WUE is not fixed — it drops when outside air is cold enough to provide free cooling
    (economization). We estimate the fraction of the year when outdoor temps are below
    the economizer threshold (~10°C / 50°F) using the Open-Meteo forecast API.
    For every economizer-eligible day, effective WUE is ~30% lower.
    Falls back to a latitude-band lookup if Open-Meteo is unreachable.

  Tier 3 — Generic WUE (baseline)
    Global industry average from Uptime Institute / Pacific Institute.
    Used when neither operator data nor climate data is available.

SCALES NATIONALLY / GLOBALLY
  Open-Meteo works for any coordinates worldwide, no API key required.
  Operator lookup covers the top 10 operators by US capacity.
  Latitude-band fallback works everywhere with zero external calls.
"""

from __future__ import annotations
import math
from typing import Optional

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

# ── Operator WUE lookup ────────────────────────────────────────────────────────
# Sources: operator sustainability/ESG reports (most recent available as of 2024).
# Values are fleet-wide averages; individual sites may differ.
# Format: {key: (central_L_per_kWh, low, high, source_year, disclosure_type)}

OPERATOR_WUE: dict[str, tuple] = {
    "google":       (0.90,  0.80, 1.10, 2023, "published_fleet_average"),
    "microsoft":    (0.30,  0.10, 0.60, 2023, "published_fleet_average"),   # targeting net-zero water
    "meta":         (0.26,  0.15, 0.50, 2022, "published_fleet_average"),
    "amazon":       (1.80,  1.20, 2.50, 2023, "estimated_from_annual_report"),
    "aws":          (1.80,  1.20, 2.50, 2023, "estimated_from_annual_report"),
    "equinix":      (1.40,  0.90, 2.00, 2023, "estimated_from_sustainability_report"),
    "qts":          (1.50,  1.00, 2.20, 2023, "estimated_colocation_average"),
    "cyrusone":     (1.50,  1.00, 2.20, 2023, "estimated_colocation_average"),
    "stack":        (1.40,  0.90, 2.00, 2023, "estimated_colocation_average"),
    "coresite":     (1.30,  0.80, 1.80, 2023, "estimated_colocation_average"),
    "ironmountain": (1.40,  0.90, 2.00, 2023, "estimated_colocation_average"),
    "oracle":       (1.60,  1.00, 2.20, 2023, "estimated_hyperscale_average"),
}

# ── Generic cooling-method WUE ─────────────────────────────────────────────────
COOLING_WUE: dict[str, dict] = {
    "wet":     {"central": 1.80, "low": 1.50, "high": 2.50},
    "hybrid":  {"central": 0.90, "low": 0.50, "high": 1.50},
    "dry":     {"central": 0.10, "low": 0.05, "high": 0.20},
    "unknown": {"central": 1.00, "low": 0.05, "high": 2.50},
}

# ── Latitude-band economizer fractions ────────────────────────────────────────
# Fraction of year where outdoor avg temp < 10°C (50°F) → free cooling possible.
# These are annual averages for the US climate; broadly applicable globally.
# Source: ASHRAE climate data, NOAA normals.
LATITUDE_ECONOMIZER: list[tuple[float, float]] = [
    # (max_lat, econ_fraction)
    (25.0, 0.10),   # Deep South / South Florida / Hawaii
    (30.0, 0.18),   # Houston, New Orleans, Jacksonville
    (33.0, 0.28),   # Dallas, Atlanta, Phoenix
    (36.0, 0.35),   # Nashville, Raleigh, Albuquerque
    (39.0, 0.42),   # DC/Loudoun, Denver, San Francisco
    (41.0, 0.50),   # NYC, Chicago, Salt Lake City
    (44.0, 0.58),   # Minneapolis, Portland OR, Burlington
    (48.0, 0.65),   # Seattle, Billings, Detroit
    (90.0, 0.72),   # Canada / far north
]

# Reduction in WUE per fraction of year with free cooling
# (economizer replaces wet cooling during cold hours → less evaporation)
ECON_WUE_REDUCTION = 0.30   # 30% WUE reduction per 100% economizer fraction

_LITERS_TO_GALLONS = 1 / 3.785411784

SOURCES = [
    "Pacific Institute, 'Water and the Data Center Industry', 2021",
    "Uptime Institute, 'Annual Global Data Center Survey', 2023",
    "DOE, 'Data Center Water Efficiency Best Practices', energy.gov",
    "Open-Meteo, 'Historical Weather API', open-meteo.com",
]


# ── Climate data ───────────────────────────────────────────────────────────────

def _latitude_econ_fraction(lat: float) -> float:
    """Fast fallback: estimate economizer fraction from latitude alone."""
    abs_lat = abs(lat)
    for max_lat, frac in LATITUDE_ECONOMIZER:
        if abs_lat <= max_lat:
            return frac
    return 0.72


# Cache Open-Meteo lookups by rounded coordinates — sites in the same county share
# the same climate, so batch-estimating 200+ sites makes 1 API call, not 200.
_ECON_CACHE: dict[tuple, Optional[float]] = {}


def _fetch_econ_fraction(lat: float, lon: float) -> Optional[float]:
    """
    Fetch real economizer fraction from Open-Meteo using 90 days of past data.
    Returns fraction of days where avg temp < 10°C (free-cooling threshold).
    Returns None if the API is unreachable. Cached by ~10km grid cell.
    """
    if not _HTTPX_AVAILABLE:
        return None
    cache_key = (round(lat, 1), round(lon, 1))
    if cache_key in _ECON_CACHE:
        return _ECON_CACHE[cache_key]
    try:
        resp = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":      lat,
                "longitude":     lon,
                "daily":         "temperature_2m_max,temperature_2m_min",
                "timezone":      "UTC",
                "past_days":     92,
                "forecast_days": 1,
            },
            timeout=10,
            headers={"User-Agent": "DataCenterLens/0.1"},
        )
        resp.raise_for_status()
        data = resp.json()
        daily = data.get("daily", {})
        t_max = daily.get("temperature_2m_max", [])
        t_min = daily.get("temperature_2m_min", [])

        if not t_max:
            return None

        # Count days where avg temp < 10°C
        econ_days = sum(
            1 for mx, mn in zip(t_max, t_min)
            if mx is not None and mn is not None and (mx + mn) / 2 < 10
        )
        # Scale 92-day sample to annual fraction
        # (biased toward the sample period — acceptable for a rough estimate)
        result = round(econ_days / len(t_max), 3)
        _ECON_CACHE[cache_key] = result
        return result

    except Exception:
        _ECON_CACHE[cache_key] = None
        return None


def _get_econ_fraction(lat: Optional[float], lon: Optional[float]) -> tuple[float, str]:
    """
    Get economizer fraction + source label.
    Tries Open-Meteo first, falls back to latitude band.
    """
    if lat is not None and lon is not None:
        frac = _fetch_econ_fraction(lat, lon)
        if frac is not None:
            return frac, "open_meteo_90day"

    if lat is not None:
        return _latitude_econ_fraction(lat), "latitude_band_fallback"

    return 0.35, "global_average_fallback"   # roughly mid-US default


# ── Operator lookup ────────────────────────────────────────────────────────────

def _lookup_operator_wue(operator: Optional[str]) -> Optional[tuple]:
    """Return (central, low, high, year, disclosure_type) if operator is known."""
    if not operator:
        return None
    key = operator.lower().replace(" ", "").replace("-", "")
    for pattern, wue_data in OPERATOR_WUE.items():
        if pattern in key:
            return wue_data
    return None


# ── Core conversion ────────────────────────────────────────────────────────────

def _mw_to_gpd(it_mw: float, wue: float) -> float:
    kwh_per_day = it_mw * 1000 * 24
    return round(kwh_per_day * wue * _LITERS_TO_GALLONS, 0)


# ── Main estimator ─────────────────────────────────────────────────────────────

def estimate_water(
    it_load_mw: float,
    cooling_method: str = "unknown",
    cooling_confidence: float = 0.3,
    operator: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> dict:
    """
    Estimate daily water consumption in gallons/day.

    Args:
        it_load_mw:          IT load in MW (from electricity estimator).
        cooling_method:      'wet', 'dry', 'hybrid', or 'unknown'.
        cooling_confidence:  Confidence in cooling method (0–1).
        operator:            Operator name — used for WUE lookup table.
        latitude:            Site latitude — used for climate adjustment.
        longitude:           Site longitude — used for climate adjustment.

    Returns estimate dict with value, range, confidence, method, assumptions, sources.
    """
    if not it_load_mw or it_load_mw <= 0:
        return {
            "estimate_type": "water_use_gpd", "value": None,
            "uncertainty_low": None, "uncertainty_high": None,
            "unit": "gallons/day", "confidence_score": 0.0, "status": "unknown",
            "method": "IT load required — run electricity estimate first.",
            "assumptions": "Cannot estimate water use without IT load.",
            "sources": SOURCES, "_detail": {"it_load_mw": it_load_mw},
        }

    tier_used       = None
    wue_central     = None
    wue_low         = None
    wue_high        = None
    base_confidence = None
    econ_note       = ""
    econ_source     = ""

    # Per-call copy — never mutate the module-level SOURCES list
    # (previously operator sources accumulated across every site estimated)
    sources = list(SOURCES)

    # ── Tier 1: Operator WUE lookup ───────────────────────────────────────────
    op_data = _lookup_operator_wue(operator)
    if op_data:
        wue_central, wue_low, wue_high, year, disc_type = op_data
        base_confidence = 0.78 if "published" in disc_type else 0.65
        tier_used = "operator_wue_lookup"
        sources.append(f"Operator sustainability report ({operator}, {year})")

    # ── Tier 2: Climate adjustment ────────────────────────────────────────────
    econ_fraction, econ_source = _get_econ_fraction(latitude, longitude)

    # Apply economizer reduction to WUE (only meaningful for wet/unknown/operator)
    econ_reduction = econ_fraction * ECON_WUE_REDUCTION

    if wue_central is None:
        # No operator data — use cooling-method profile
        if cooling_method not in COOLING_WUE:
            cooling_method = "unknown"
        profile = COOLING_WUE[cooling_method]
        wue_central = profile["central"]
        wue_low     = profile["low"]
        wue_high    = profile["high"]
        base_confidence = cooling_confidence * 0.85
        tier_used = "climate_adjusted_cooling_model"

    # Only apply economizer to wet/unknown methods (dry cooling unaffected)
    if cooling_method not in ("dry",):
        wue_central = round(wue_central * (1 - econ_reduction), 3)
        wue_low     = round(wue_low     * (1 - econ_reduction * 0.5), 3)   # conservative
        wue_high    = round(wue_high    * (1 - econ_reduction * 0.3), 3)   # conservative

    econ_note = (
        f"Climate adjustment: {econ_fraction:.0%} of year below 10°C (economizer threshold) "
        f"based on {econ_source.replace('_', ' ')}. "
        f"WUE reduced by {econ_reduction:.0%} × {ECON_WUE_REDUCTION:.0%} = "
        f"{econ_reduction * ECON_WUE_REDUCTION:.1%} from baseline."
    ) if cooling_method not in ("dry",) else ""

    # Confidence boost from climate data
    climate_bonus = 0.08 if econ_source == "open_meteo_90day" else 0.03
    confidence = round(min(0.88, base_confidence + climate_bonus), 2)

    # ── Compute final values ──────────────────────────────────────────────────
    gpd = _mw_to_gpd(it_load_mw, wue_central)
    gpd_low  = _mw_to_gpd(it_load_mw, wue_low)
    gpd_high = _mw_to_gpd(it_load_mw, wue_high)

    kwh_per_day = it_load_mw * 1000 * 24

    tier_labels = {
        "operator_wue_lookup":        "Tier 1 — operator WUE from sustainability report",
        "climate_adjusted_cooling_model": "Tier 2 — climate-adjusted WUE model",
    }

    method = (
        f"{tier_labels.get(tier_used, 'Tier 3 — generic model')}. "
        f"IT load {it_load_mw} MW × 24h = {kwh_per_day:,.0f} kWh/day. "
        f"WUE = {wue_central} L/kWh (after climate adjustment). "
        f"Water = {kwh_per_day:,.0f} × {wue_central} ÷ 3.785 = {gpd:,.0f} gallons/day."
    )

    assumptions_parts = []
    if op_data:
        assumptions_parts.append(
            f"Operator WUE: {op_data[0]} L/kWh (range {op_data[1]}–{op_data[2]}) "
            f"from {operator} sustainability report ({op_data[3]}, {op_data[4].replace('_',' ')})."
        )
    else:
        assumptions_parts.append(
            f"Cooling method: {cooling_method} (WUE range {wue_low}–{wue_high} L/kWh before adjustment)."
        )
    if econ_note:
        assumptions_parts.append(econ_note)
    assumptions_parts.append(
        "Does not include water for sanitation, landscaping, or construction. "
        "WUE is a capacity/design figure — actual utilization may be lower."
    )

    return {
        "estimate_type":    "water_use_gpd",
        "value":            gpd,
        "uncertainty_low":  gpd_low,
        "uncertainty_high": gpd_high,
        "unit":             "gallons/day",
        "confidence_score": confidence,
        "status":           "estimated",
        "method":           method,
        "assumptions":      " ".join(assumptions_parts),
        "sources":          sources,
        "_detail": {
            "it_load_mw":        it_load_mw,
            "cooling_method":    cooling_method,
            "wue_central":       wue_central,
            "econ_fraction":     econ_fraction,
            "econ_source":       econ_source,
            "tier_used":         tier_used,
            "kwh_per_day":       kwh_per_day,
            "gallons_per_year":  round(gpd * 365, 0),
            "acre_feet_per_year": round(gpd * 365 / 325851, 1),
        },
    }
