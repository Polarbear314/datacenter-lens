"""
estimators/anchor.py — Update estimates when scraped documents contain explicit figures.

The problem this solves:
  Satellite imagery gives footprint → footprint gives estimate → estimate has wide range.
  But if a scraped document says "72 MW campus" or a LoudounGIS record says "836,766 sq ft",
  that's a much stronger signal than our W/sqft model. We should use it.

How it works:
  1. Scan all documents linked to a site for structured fields (claimed_mw, claimed_sqft,
     claimed_gpd) and free-text patterns (regex for MW/gallon figures in titles).
  2. For each found figure, update the corresponding estimate:
     - Replace the value with the document figure
     - Narrow the uncertainty range (anchored estimates get ±15% instead of ±50%)
     - Raise the confidence score
     - Set status to "confirmed" (if the source is a planning doc or utility filing)
       or "inferred" (if it's a news article)
     - Record the source document that provided the anchor

Source quality weights (determine how much confidence increases):
  planning_doc      → 0.90  (Loudoun GIS, SPEX applications — official county records)
  utility_filing    → 0.85  (FERC/PJM filings — regulatory filings)
  permit            → 0.85  (building permits — official but may reflect planned not actual)
  company_announcement → 0.75  (press releases — companies can overstate planned capacity)
  news              → 0.65  (news articles — secondhand, but often cite filings directly)
  other             → 0.55

IMPORTANT: Anchoring never sets confidence to 1.0. Even confirmed figures can be:
  - Planning-stage numbers (actual buildout may differ)
  - Capacity figures, not actual load (a 100 MW campus may only draw 40 MW today)
  - Outdated (older filings may not reflect expansions)
The UI should always note this distinction.
"""

import re
from typing import Optional

# ── Source quality weights ─────────────────────────────────────────────────────

SOURCE_CONFIDENCE: dict[str, float] = {
    "planning_doc":         0.90,
    "utility_filing":       0.85,
    "permit":               0.85,
    "company_announcement": 0.75,
    "news":                 0.65,
    "environmental_review": 0.80,
    "tax_agreement":        0.80,
    "court_filing":         0.70,
    "other":                0.55,
}

# How much to narrow the uncertainty range for anchored estimates (fraction of value)
ANCHOR_UNCERTAINTY_FRACTION = 0.15   # ±15% of anchored value


# ── Regex patterns for free-text extraction ───────────────────────────────────

MW_PATTERN  = re.compile(
    r'(\d[\d,]*(?:\.\d+)?)\s*(?:MW|megawatt|mega-?watt)', re.IGNORECASE
)
GPD_PATTERN = re.compile(
    r'(\d[\d,]*(?:\.\d+)?)\s*(?:gallon[s]?(?:\s*per\s*day)?|gpd|MGD)', re.IGNORECASE
)
SQFT_PATTERN = re.compile(
    r'(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft\.?|square\s*feet?|sqft)', re.IGNORECASE
)
ACRES_PATTERN = re.compile(
    r'(\d[\d,]*(?:\.\d+)?)\s*(?:acres?)', re.IGNORECASE
)


def _parse_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _extract_from_text(text: str) -> dict:
    """Extract numeric claims from free text. Returns {claimed_mw, claimed_sqft, claimed_gpd}."""
    found: dict = {}

    mw_m = MW_PATTERN.search(text)
    if mw_m:
        v = _parse_float(mw_m.group(1))
        if v and 1 < v < 5000:   # sanity: 1 MW–5 GW
            found["claimed_mw"] = v

    gpd_m = GPD_PATTERN.search(text)
    if gpd_m:
        raw = _parse_float(gpd_m.group(1))
        unit = gpd_m.group(0).upper()
        if raw:
            # Convert MGD (million gallons/day) to gpd
            if "MGD" in unit:
                found["claimed_gpd"] = raw * 1_000_000
            else:
                if raw > 100:   # sanity: >100 gpd
                    found["claimed_gpd"] = raw

    sqft_m = SQFT_PATTERN.search(text)
    if sqft_m:
        v = _parse_float(sqft_m.group(1))
        if v and v > 1000:   # sanity: >1,000 sqft
            found["claimed_sqft"] = v

    return found


def _scan_document(doc: dict) -> dict:
    """
    Scan one document dict (from the DB) for numeric claims.
    Checks extracted_fields first, then title + free text.

    Returns {claimed_mw, claimed_sqft, claimed_gpd, source_quality, doc_title, doc_url}.
    """
    import json

    extracted = doc.get("extracted_fields") or {}
    if isinstance(extracted, str):
        try:
            extracted = json.loads(extracted)
        except Exception:
            extracted = {}

    doc_type = doc.get("document_type", "other")
    quality  = SOURCE_CONFIDENCE.get(doc_type, SOURCE_CONFIDENCE["other"])

    found: dict = {}

    # Structured fields take precedence
    for key in ("claimed_mw", "claimed_sqft", "claimed_gpd", "claimed_acres"):
        v = extracted.get(key)
        if v is not None:
            try:
                found[key] = float(v)
            except (TypeError, ValueError):
                pass

    # Convert claimed_acres → claimed_sqft if sqft not present
    if "claimed_sqft" not in found and "claimed_acres" in found:
        found["claimed_sqft"] = found["claimed_acres"] * 43560

    # Free-text fallback from title
    title = doc.get("title", "")
    text_found = _extract_from_text(title)
    for k, v in text_found.items():
        if k not in found:   # structured fields win
            found[k] = v

    found["source_quality"]  = quality
    found["doc_title"]       = title
    found["doc_url"]         = doc.get("url")
    found["doc_type"]        = doc_type
    found["_skip"]           = extracted.get("_manual_research", False)  # skip reference-only docs

    return found


def anchor_estimates(
    estimates: list[dict],
    documents: list[dict],
) -> list[dict]:
    """
    Update estimates using figures found in linked documents.

    Args:
        estimates:  List of estimate dicts (from the DB). Modified in-place.
        documents:  List of document dicts (from the DB).

    Returns:
        Updated estimates list. Each anchored estimate has:
          - anchored: True
          - anchor_source: doc title
          - anchor_url: doc URL
          - Previous values in _pre_anchor dict
    """
    if not documents or not estimates:
        return estimates

    # Sanity bounds — reject values that are clearly regional/aggregate, not site-specific.
    # The largest single data center campus in the world is ~500 MW.
    # Any MW figure above this is a grid/regional demand number, not a site capacity.
    MW_MAX_SITE   = 500.0    # MW — above this, claim is regional, not site-specific
    MW_MIN_SITE   = 0.5      # MW — below this, too small to be a data center
    SQFT_MAX_SITE = 20_000_000  # 20M sqft — ~460 acres, largest plausible campus
    SQFT_MIN_SITE = 5_000       # 5k sqft minimum

    # Collect all claims from all documents (pick the highest-quality source per claim type)
    best: dict[str, dict] = {}   # claim_type → best doc scan result

    for doc in documents:
        scan = _scan_document(doc)
        if scan.get("_skip"):
            continue

        # Sanity-check MW claims before accepting them
        if "claimed_mw" in scan:
            mw = scan["claimed_mw"]
            if not (MW_MIN_SITE <= mw <= MW_MAX_SITE):
                # Strip the bad claim but keep the document for other fields
                scan.pop("claimed_mw")

        if "claimed_sqft" in scan:
            sqft = scan["claimed_sqft"]
            if not (SQFT_MIN_SITE <= sqft <= SQFT_MAX_SITE):
                scan.pop("claimed_sqft")

        quality = scan["source_quality"]
        for claim in ("claimed_mw", "claimed_sqft", "claimed_gpd"):
            if claim in scan:
                existing = best.get(claim, {})
                if quality > existing.get("source_quality", 0):
                    best[claim] = scan

    if not best:
        return estimates

    # Map estimate_type → claim_type
    ESTIMATE_TO_CLAIM = {
        "electricity_demand_mw": "claimed_mw",
        "water_use_gpd":         "claimed_gpd",
        "land_use_sqft":         "claimed_sqft",
    }

    updated = []
    for est in estimates:
        etype = est.get("estimate_type")
        claim_key = ESTIMATE_TO_CLAIM.get(etype)

        if claim_key and claim_key in best:
            scan     = best[claim_key]
            new_val  = scan[claim_key]
            quality  = scan["source_quality"]
            doc_type = scan["doc_type"]

            # Status: planning_doc/utility_filing/permit → confirmed; news → inferred
            if doc_type in ("planning_doc", "utility_filing", "permit", "environmental_review", "tax_agreement"):
                new_status = "confirmed"
            else:
                new_status = "inferred"

            low  = round(new_val * (1 - ANCHOR_UNCERTAINTY_FRACTION), 2)
            high = round(new_val * (1 + ANCHOR_UNCERTAINTY_FRACTION), 2)

            updated_est = dict(est)
            updated_est["_pre_anchor"] = {
                "value":            est.get("value"),
                "uncertainty_low":  est.get("uncertainty_low"),
                "uncertainty_high": est.get("uncertainty_high"),
                "confidence_score": est.get("confidence_score"),
                "status":           est.get("status"),
            }
            updated_est["value"]            = new_val
            updated_est["uncertainty_low"]  = low
            updated_est["uncertainty_high"] = high
            updated_est["confidence_score"] = round(min(0.93, quality), 2)
            updated_est["status"]           = new_status
            updated_est["anchored"]         = True
            updated_est["anchor_source"]    = scan["doc_title"]
            updated_est["anchor_url"]       = scan["doc_url"]
            updated_est["method"] = (
                f"ANCHORED from document: \"{scan['doc_title']}\". "
                + (updated_est.get("method") or "")
            )
            updated_est["assumptions"] = (
                f"Value anchored to {new_val:,.1f} {est.get('unit','')} from "
                f"\"{scan['doc_title']}\" (source type: {doc_type}, quality: {quality:.0%}). "
                f"Uncertainty narrowed to ±{ANCHOR_UNCERTAINTY_FRACTION:.0%} of anchored value. "
                f"Note: this may be a planned/capacity figure, not actual current draw. "
                + (updated_est.get("assumptions") or "")
            )
            updated.append(updated_est)
        else:
            updated.append(est)

    return updated
