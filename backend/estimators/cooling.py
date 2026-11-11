"""
cooling.py — Infer cooling method from annotated infrastructure.

Cooling method drives water consumption significantly:
  - Wet (evaporative/tower): high water use, ~1.5–2.5 L/kWh IT load
  - Dry (air-cooled):        low water use,  ~0.0–0.2 L/kWh IT load
  - Hybrid:                  mixed, ~0.5–1.2 L/kWh IT load
  - Unknown:                 wide uncertainty, assume mid-range
"""

from typing import Optional


# Class labels that indicate wet/tower cooling
WET_COOLING_LABELS = {"cooling_towers", "cooling_tower", "water_storage", "stormwater_pond"}

# Class labels that indicate dry/air cooling (e.g. rooftop CRAC units, chillers)
DRY_COOLING_LABELS = {"cooling_equipment", "chiller", "hvac", "crac_units"}

# Combined "any cooling visible"
ANY_COOLING_LABELS = WET_COOLING_LABELS | DRY_COOLING_LABELS


def infer_cooling_method(infra_list: list[dict]) -> dict:
    """
    Infer cooling method from a list of infrastructure annotations.

    Args:
        infra_list: List of infrastructure dicts with 'class_label' and 'confidence_score'.

    Returns:
        {
            method: "wet" | "dry" | "hybrid" | "unknown",
            confidence: float,           # 0.0–1.0
            status: "inferred" | "unknown",
            explanation: str,
            sources: list[str],
        }
    """
    labels = {i["class_label"].lower(): i.get("confidence_score", 1.0) for i in infra_list}

    has_wet = any(lbl in WET_COOLING_LABELS for lbl in labels)
    has_dry = any(lbl in DRY_COOLING_LABELS for lbl in labels)

    # Confidence: average of the matching annotation confidence scores
    wet_scores = [v for k, v in labels.items() if k in WET_COOLING_LABELS]
    dry_scores = [v for k, v in labels.items() if k in DRY_COOLING_LABELS]

    SOURCES = [
        "Uptime Institute, Data Center Industry Survey 2023",
        "DOE, Data Center Water Usage Best Practices",
    ]

    if has_wet and has_dry:
        conf = round((sum(wet_scores + dry_scores) / len(wet_scores + dry_scores)) * 0.85, 2)
        return {
            "method":      "hybrid",
            "confidence":  conf,
            "status":      "inferred",
            "explanation": (
                "Both cooling towers/water storage (wet cooling) and mechanical "
                "cooling equipment (dry cooling) are visible in the annotated imagery. "
                "A hybrid system is common in large hyperscale facilities that use "
                "free-air economization for part of the year and wet cooling for peak loads."
            ),
            "sources": SOURCES,
        }

    if has_wet:
        conf = round(sum(wet_scores) / len(wet_scores) * 0.90, 2)
        return {
            "method":      "wet",
            "confidence":  conf,
            "status":      "inferred",
            "explanation": (
                "Cooling towers or water-storage ponds visible in the annotated imagery "
                "suggest an evaporative (wet) cooling system. This is the most common "
                "cooling method for large facilities and results in significant water consumption "
                "(roughly 1.5–2.5 liters per kWh of IT load)."
            ),
            "sources": SOURCES,
        }

    if has_dry:
        conf = round(sum(dry_scores) / len(dry_scores) * 0.75, 2)
        return {
            "method":      "dry",
            "confidence":  conf,
            "status":      "inferred",
            "explanation": (
                "Mechanical cooling equipment (e.g. CRAC units, chillers) is visible "
                "but no cooling towers or water storage was annotated. This suggests "
                "an air-cooled or dry cooling system. Water consumption is lower than "
                "wet cooling, primarily humidification (~0.05–0.2 L/kWh)."
            ),
            "sources": SOURCES,
        }

    return {
        "method":      "unknown",
        "confidence":  0.3,
        "status":      "unknown",
        "explanation": (
            "No cooling infrastructure was identified in the annotated imagery. "
            "Cooling method cannot be reliably inferred. The estimate assumes a "
            "typical mix of wet and dry cooling used by regional colocation facilities."
        ),
        "sources": SOURCES,
    }
