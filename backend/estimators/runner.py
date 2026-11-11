"""
runner.py — Orchestrate all estimators for a site.

Takes a site dict (from the database) and its infrastructure list,
runs each estimator in dependency order, and returns a list of estimate dicts
ready to be written to the `estimates` table.
"""

import json
import math
from typing import Optional

from .cooling import infer_cooling_method
from .electricity import estimate_electricity
from .water import estimate_water


# ── Infrastructure class label sets ───────────────────────────────────────────

MAIN_BUILDING_LABELS = {
    "main_building", "server_hall", "data_center_building",
    "warehouse", "primary_building",
}

# "campus" polygons enclose the whole site, not just the building footprint.
# Parking, roads, setbacks, and green space typically consume ~55–65% of a campus.
# We apply a 40% discount to convert gross campus area → estimated building floor area.
# Source: LBNL site coverage studies; typical data center campus ISR ~35–50%.
CAMPUS_BUILDING_FRACTION = 0.40


def _compute_area_sqft(infra_list: list[dict]) -> float:
    """
    Sum effective building area from annotated polygons.

    Priority:
      1. main_building (and synonyms) — use area directly.
      2. campus — apply CAMPUS_BUILDING_FRACTION discount.
         Only used as fallback when no main_building polygons exist.

    Falls back to geometry-based calculation if area_sqft is missing.
    """
    # Pass 1: sum explicit main-building polygons
    building_total = 0.0
    for item in infra_list:
        label = item.get("class_label", "").lower()
        if label not in MAIN_BUILDING_LABELS:
            continue
        area = item.get("area_sqft")
        if area and area > 0:
            building_total += area
            continue
        geom = item.get("geometry")
        if geom and geom.get("type") == "Polygon":
            building_total += _polygon_sqft(geom["coordinates"][0])

    if building_total > 0:
        return round(building_total, 1)

    # Pass 2: fall back to campus polygon(s) with discount
    campus_total = 0.0
    for item in infra_list:
        if item.get("class_label", "").lower() != "campus":
            continue
        area = item.get("area_sqft")
        if area and area > 0:
            campus_total += area
            continue
        geom = item.get("geometry")
        if geom and geom.get("type") == "Polygon":
            campus_total += _polygon_sqft(geom["coordinates"][0])

    return round(campus_total * CAMPUS_BUILDING_FRACTION, 1)


def _polygon_sqft(coords: list) -> float:
    """
    Latitude-aware Shoelace area in square feet.
    Replaces the old fixed 364,000 × 282,000 ft/degree constants, which were
    only correct near 39°N (Loudoun). This works at any latitude.
    """
    if not coords or len(coords) < 3:
        return 0.0
    lat0 = sum(c[1] for c in coords) / len(coords)
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    lon0 = coords[0][0]
    pts = [((c[0] - lon0) * m_per_deg_lon, (c[1] - lat0) * m_per_deg_lat) for c in coords]
    n = len(pts)
    area_m2 = abs(sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
        for i in range(n)
    )) / 2.0
    return area_m2 * 10.7639


def _cooling_estimate_dict(cooling_result: dict) -> dict:
    """Convert cooling inference to an estimate-table row."""
    METHOD_VALUES = {"wet": 3, "hybrid": 2, "dry": 1, "unknown": 0}
    return {
        "estimate_type":    "cooling_method",
        "value":            METHOD_VALUES.get(cooling_result["method"], 0),
        "uncertainty_low":  None,
        "uncertainty_high": None,
        "unit":             cooling_result["method"],   # store the label in 'unit'
        "confidence_score": cooling_result["confidence"],
        "status":           cooling_result["status"],
        "method":           f"Inferred from annotated infrastructure class labels.",
        "assumptions":      cooling_result["explanation"],
        "sources":          json.dumps(cooling_result["sources"]),
    }


def run_all_estimates(site: dict, infra_list: list[dict], doc_sqft: float = 0) -> list[dict]:
    """
    Run all estimators for a site.

    Args:
        site:       Site dict from the database (id, name, operator, etc.)
        infra_list: List of infrastructure annotation dicts.
        doc_sqft:   Building sqft from scraped documents (LoudounGIS etc.).
                    Used as fallback when no polygons are drawn.

    Returns:
        List of estimate dicts, each matching the `estimates` table schema.
        (id and site_id are NOT set — the caller sets those before DB insert.)
    """
    results = []

    operator = site.get("operator")

    # ── Step 1: Building footprint ────────────────────────────────────────────
    # Prefer manually drawn polygons; fall back to document-sourced sqft
    area_sqft = _compute_area_sqft(infra_list)
    if area_sqft == 0 and doc_sqft > 0:
        area_sqft = doc_sqft

    # ── Step 2: Cooling method inference ──────────────────────────────────────
    cooling = infer_cooling_method(infra_list)
    cooling_row = _cooling_estimate_dict(cooling)
    results.append(cooling_row)

    # ── Step 3: Electricity demand ─────────────────────────────────────────────
    elec = estimate_electricity(
        area_sqft=area_sqft,
        operator=operator,
        infra_list=infra_list,
    )
    elec_row = {
        "estimate_type":    elec["estimate_type"],
        "value":            elec["value"],
        "uncertainty_low":  elec["uncertainty_low"],
        "uncertainty_high": elec["uncertainty_high"],
        "unit":             elec["unit"],
        "confidence_score": elec["confidence_score"],
        "status":           elec["status"],
        "method":           elec["method"],
        "assumptions":      elec["assumptions"],
        "sources":          json.dumps(elec["sources"]),
    }
    results.append(elec_row)

    # ── Step 4: Water use ─────────────────────────────────────────────────────
    it_load_mw = elec.get("_detail", {}).get("it_load_mw", 0)
    water = estimate_water(
        it_load_mw=it_load_mw,
        cooling_method=cooling["method"],
        cooling_confidence=cooling["confidence"],
        operator=operator,
        latitude=site.get("latitude"),
        longitude=site.get("longitude"),
    )
    water_row = {
        "estimate_type":    water["estimate_type"],
        "value":            water["value"],
        "uncertainty_low":  water["uncertainty_low"],
        "uncertainty_high": water["uncertainty_high"],
        "unit":             water["unit"],
        "confidence_score": water["confidence_score"],
        "status":           water["status"],
        "method":           water["method"],
        "assumptions":      water["assumptions"],
        "sources":          json.dumps(water["sources"]),
    }
    results.append(water_row)

    # ── Step 5: Land use ──────────────────────────────────────────────────────
    # If a campus polygon exists, use that as the authoritative site area.
    # Otherwise sum all annotated polygons.
    campus_areas = [
        (i.get("area_sqft") or 0)
        for i in infra_list if i.get("class_label", "").lower() == "campus"
    ]
    campus_area = sum(campus_areas)
    all_area    = sum((i.get("area_sqft") or 0) for i in infra_list)
    land_value  = campus_area if campus_area > 0 else all_area

    if campus_area > 0:
        land_method = (
            f"Campus boundary polygon area: {campus_area:,.0f} sq ft. "
            f"Estimated building floor area: {campus_area * CAMPUS_BUILDING_FRACTION:,.0f} sq ft "
            f"({CAMPUS_BUILDING_FRACTION:.0%} of campus — typical data center site coverage ratio)."
        )
        land_assumptions = (
            f"Campus polygon drawn by annotator covers the full site including parking, roads, "
            f"and setbacks. Building floor area estimated at {CAMPUS_BUILDING_FRACTION:.0%} of "
            f"gross campus area based on LBNL data center site coverage studies."
        )
    else:
        land_method = "Sum of all annotated polygon areas (sq ft)."
        land_assumptions = (
            "Area computed from GeoJSON polygon coordinates using the Shoelace formula "
            "with a flat-earth approximation appropriate for the Loudoun County region (~39°N). "
            "Includes all annotated infrastructure classes, not just buildings."
        )

    land_row = {
        "estimate_type":    "land_use_sqft",
        "value":            round(land_value, 0) if land_value > 0 else None,
        "uncertainty_low":  None,
        "uncertainty_high": None,
        "unit":             "sq ft",
        "confidence_score": 0.85 if land_value > 0 else 0.0,
        "status":           "estimated" if land_value > 0 else "unknown",
        "method":           land_method,
        "assumptions":      land_assumptions,
        "sources":          json.dumps([
            "Manual polygon annotation via DataCenter Lens map tool",
            "LBNL, 'United States Data Center Energy Usage Report', 2024",
        ]),
    }
    results.append(land_row)

    return results
