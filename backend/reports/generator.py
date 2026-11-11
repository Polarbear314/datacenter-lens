"""
reports/generator.py — Milestone 1.8: policy report generator.

Takes a site dict (with infrastructure, documents, estimates already attached,
as returned by app_lite.site_to_response) and renders a plain-English,
policy-facing HTML report with all 14 sections from the product spec.

Design principles (from the product spec):
  - Never present estimates as facts. Every figure carries its status label
    (confirmed / estimated / inferred / unknown), uncertainty range, and method.
  - Separate what the imagery shows from what we infer from it.
  - When we don't know something, say so explicitly and tell the reader
    which document would answer it.

The output is a self-contained HTML page (print-friendly — use the browser's
Print → Save as PDF for a PDF copy). No external dependencies.
"""

from datetime import date
from html import escape
from typing import Optional

# ── Context constants (documented, policy-facing) ─────────────────────────────

# Average US household: ~10,700 kWh/yr → ~1.22 kW average draw (EIA 2023).
# So 1 MW of continuous load ≈ 820 homes' average electricity consumption.
HOMES_PER_MW = 820

# Average US household water use: ~300 gallons/day (EPA WaterSense).
GPD_PER_HOUSEHOLD = 300

STATUS_LABELS = {
    "confirmed": ("Confirmed", "#166534", "#dcfce7"),
    "estimated": ("Estimated", "#1e40af", "#dbeafe"),
    "inferred":  ("Inferred",  "#854d0e", "#fef9c3"),
    "unknown":   ("Unknown",   "#525252", "#e5e5e5"),
}

CLASS_LABEL_NAMES = {
    "main_building":        "Main data-center building",
    "server_hall":          "Server hall",
    "campus":               "Campus / site parcel",
    "substation":           "Electrical substation",
    "electrical_substation": "Electrical substation",
    "cooling_towers":       "Cooling towers",
    "cooling_equipment":    "Cooling equipment",
    "generators":           "Backup generators",
    "water_storage":        "Water storage",
    "stormwater_pond":      "Stormwater pond",
    "construction_staging": "Construction staging area",
    "transmission":         "Transmission lines",
    "parking":              "Parking / roads",
    "other":                "Other",
}

DETECTION_NAMES = {
    "manual":      "human annotation",
    "osm_import":  "OpenStreetMap community mapping",
    "loudoun_gis": "Loudoun County GIS (official building outlines)",
    "sam_assisted": "AI-assisted segmentation (human-reviewed)",
}


# ── Small helpers ──────────────────────────────────────────────────────────────

def _fmt(v: Optional[float], nd: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:,.{nd}f}"


def _badge(status: str) -> str:
    label, fg, bg = STATUS_LABELS.get(status, STATUS_LABELS["unknown"])
    return (f'<span style="background:{bg};color:{fg};padding:1px 8px;border-radius:10px;'
            f'font-size:11px;font-weight:600;white-space:nowrap;">{label}</span>')


def _conf_bar(score: Optional[float]) -> str:
    s = score or 0
    pct = round(s * 100)
    color = "#16a34a" if s >= 0.7 else ("#ca8a04" if s >= 0.4 else "#dc2626")
    return (f'<span style="display:inline-block;width:60px;height:8px;background:#e5e7eb;'
            f'border-radius:4px;vertical-align:middle;margin-right:6px;">'
            f'<span style="display:block;width:{pct}%;height:8px;background:{color};'
            f'border-radius:4px;"></span></span><span style="font-size:12px;">{pct}%</span>')


def _get_est(estimates: list, etype: str) -> Optional[dict]:
    return next((e for e in estimates if e.get("estimate_type") == etype), None)


def _section(num: int, title: str, body: str) -> str:
    return (f'<section><h2><span class="secnum">{num}</span>{escape(title)}</h2>{body}</section>')


def _p(text: str) -> str:
    return f"<p>{text}</p>"


def _unknown_note(what: str, doc_hint: str) -> str:
    return (f'<p class="unknown-note">{_badge("unknown")} {what} '
            f'<em>To find out: {doc_hint}</em></p>')


# ── Section builders ───────────────────────────────────────────────────────────

def _sec_summary(site: dict) -> str:
    op = site.get("operator")
    rows = [
        ("Site name", escape(site.get("name") or "—")),
        ("Operator", escape(op) if op else '<em>Not identified — see Section 12 for how to find the owner</em>'),
        ("Developer", escape(site.get("developer")) if site.get("developer") else "—"),
        ("Address", escape(site.get("address")) if site.get("address") else "—"),
        ("Parcel ID", escape(site.get("parcel_id")) if site.get("parcel_id") else "—"),
        ("Status", escape((site.get("status") or "unknown").replace("_", " "))),
        ("Coordinates", f'{site.get("latitude"):.5f}, {site.get("longitude"):.5f}'
            if site.get("latitude") else "—"),
        ("First recorded", escape(str(site.get("first_seen") or "—"))),
        ("Last updated", escape(str(site.get("last_updated") or "—"))),
    ]
    table = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    body = f"<table class='kv'>{table}</table>"

    srcs = site.get("sources") or []
    if srcs:
        links = "".join(
            f'<li><a href="{escape(s.get("url",""))}" target="_blank">'
            f'{escape(s.get("title") or s.get("url",""))}</a></li>'
            for s in srcs if isinstance(s, dict)
        )
        body += f"<p><strong>Site record sources:</strong></p><ul>{links}</ul>"
    return body


def _sec_imagery(site: dict) -> str:
    infra = site.get("infrastructure") or []
    if not infra:
        return _unknown_note(
            "No infrastructure has been outlined for this site yet.",
            "open the site in the map tool and draw polygons around visible buildings and equipment.")

    rows = []
    for i in infra:
        label = CLASS_LABEL_NAMES.get(i.get("class_label", ""), i.get("class_label", "?"))
        method = DETECTION_NAMES.get(i.get("detection_method", ""), i.get("detection_method", "?"))
        corrected = "yes" if i.get("human_corrected") else "no"
        rows.append(
            f"<tr><td>{escape(label)}</td>"
            f"<td class='num'>{_fmt(i.get('area_sqft'))}</td>"
            f"<td>{escape(method)}</td>"
            f"<td>{_conf_bar(i.get('confidence_score'))}</td>"
            f"<td>{corrected}</td></tr>"
        )
    total_area = sum(i.get("area_sqft") or 0 for i in infra)
    table = ("<table><thead><tr><th>Feature</th><th>Area (sq ft)</th><th>How identified</th>"
             "<th>Confidence</th><th>Human-verified</th></tr></thead>"
             f"<tbody>{''.join(rows)}</tbody></table>")
    note = _p(
        f"{len(infra)} feature(s) outlined, totaling about <strong>{_fmt(total_area)} sq ft</strong> "
        f"({total_area/43560:,.1f} acres). These outlines show what is visible from imagery and "
        "public mapping — they do not reveal what happens inside the buildings. Features identified "
        "by community mapping or automated methods may contain errors; human-verified rows have "
        "been reviewed by a person.")
    return note + table


def _sec_resources(site: dict) -> str:
    ests = site.get("estimates") or []
    if not ests:
        return _unknown_note(
            "No resource estimates have been generated for this site yet.",
            "run resource estimation from the map tool or via POST /sites/{id}/estimate.")

    order = ["electricity_demand_mw", "water_use_gpd", "cooling_method", "land_use_sqft"]
    names = {
        "electricity_demand_mw": "Electricity demand",
        "water_use_gpd":         "Water use",
        "cooling_method":        "Cooling method",
        "land_use_sqft":         "Land use",
    }
    blocks = []
    for etype in order:
        e = _get_est(ests, etype)
        if not e:
            continue
        name = names[etype]
        status = e.get("status", "unknown")
        if etype == "cooling_method":
            value_str = f"<strong>{escape((e.get('unit') or 'unknown').title())}</strong>"
            range_str = ""
        else:
            unit = e.get("unit") or ""
            value_str = f"<strong>{_fmt(e.get('value'), 1 if etype=='electricity_demand_mw' else 0)} {escape(unit)}</strong>"
            lo, hi = e.get("uncertainty_low"), e.get("uncertainty_high")
            range_str = (f"<div class='range'>Range: {_fmt(lo,1)} – {_fmt(hi,1)} {escape(unit)}</div>"
                         if lo is not None and hi is not None else "")
        blocks.append(
            f"<div class='est-card'>"
            f"<div class='est-head'><span class='est-name'>{escape(name)}</span> "
            f"{_badge(status)} <span class='est-conf'>{_conf_bar(e.get('confidence_score'))}</span></div>"
            f"<div class='est-value'>{value_str}</div>{range_str}"
            f"<details><summary>Method &amp; assumptions</summary>"
            f"<p><strong>Method:</strong> {escape(e.get('method') or '—')}</p>"
            f"<p><strong>Assumptions:</strong> {escape(e.get('assumptions') or '—')}</p>"
            f"<p><strong>Sources:</strong> {escape('; '.join(str(s) for s in (e.get('sources') or [])))}</p>"
            f"</details></div>"
        )
    intro = _p(
        "These are <strong>estimates, not measurements</strong>. They are derived from the visible "
        "building footprint, industry benchmarks, and any public documents found for this site. "
        "Actual resource use depends on what is built inside, how full the facility is, and "
        "operational choices the operator does not disclose. Expand each card to see exactly how "
        "the number was produced.")
    return intro + "".join(blocks)


def _sec_grid(site: dict) -> str:
    e = _get_est(site.get("estimates") or [], "electricity_demand_mw")
    if not e or not e.get("value"):
        return _unknown_note(
            "Electricity demand has not been estimated for this site.",
            "request the utility interconnection application or substation upgrade filings "
            "from the local utility (Dominion Energy in Loudoun County).")
    mw = e["value"]
    homes = int(mw * HOMES_PER_MW)
    body = _p(
        f"The estimated demand of <strong>{_fmt(mw,1)} MW</strong> is roughly equivalent to the "
        f"average electricity consumption of <strong>about {homes:,} homes</strong> "
        f"(assuming ~1.22 kW average draw per US household, EIA 2023). "
        f"Data centers draw power continuously, day and night, unlike residential load.")
    body += _p(
        "Loudoun County sits in Dominion Energy territory within the PJM grid region — the largest "
        "data-center load concentration in the world. New large loads here can require new "
        "substations and transmission lines, whose costs may be shared across all ratepayers "
        "depending on how the utility allocates them. Key questions: Is a new substation or "
        "transmission line needed? Who pays for it? Does the interconnection agreement include "
        "ratepayer protections?")
    body += _p(
        "<em>Note: this figure is closer to design capacity than to actual measured draw. A campus "
        "permitted for 100 MW may draw far less in its first years.</em>")
    return body


def _sec_water(site: dict) -> str:
    ests = site.get("estimates") or []
    w = _get_est(ests, "water_use_gpd")
    c = _get_est(ests, "cooling_method")
    if not w or not w.get("value"):
        return _unknown_note(
            "Water use has not been estimated for this site (usually because electricity demand "
            "could not be estimated first).",
            "request the water service agreement or will-serve letter from the water utility "
            "(Loudoun Water), and any NPDES/discharge permits from the state DEQ.")
    gpd = w["value"]
    households = int(gpd / GPD_PER_HOUSEHOLD)
    cooling = (c.get("unit") if c else None) or "unknown"
    body = _p(
        f"Estimated water use is <strong>{_fmt(gpd)} gallons/day</strong> — comparable to the "
        f"daily water use of about <strong>{households:,} households</strong> "
        f"(at ~{GPD_PER_HOUSEHOLD} gal/day per household, EPA). "
        f"Cooling method: <strong>{escape(cooling)}</strong> "
        f"({escape((c or {}).get('status','unknown'))}).")
    body += _p(
        "Water use depends heavily on cooling technology: evaporative (wet) cooling consumes "
        "1.5–2.5 liters per kWh of computing, while air-cooled (dry) systems use almost none but "
        "consume more electricity. Many operators do not disclose site-level water use. The water "
        "utility's service agreement is the most reliable public record of expected demand.")
    return body


def _sec_fiscal(site: dict) -> str:
    docs = site.get("documents") or []
    tax_docs = [d for d in docs if d.get("document_type") in ("tax_agreement", "property_record", "planning_doc")]
    body = ""
    if tax_docs:
        items = "".join(
            f'<li><a href="{escape(d.get("url") or "#")}" target="_blank">{escape(d.get("title",""))}</a>'
            f' <span class="doc-type">({escape(d.get("document_type",""))})</span></li>'
            for d in tax_docs[:10])
        body += _p("Public records found that bear on fiscal impact:") + f"<ul>{items}</ul>"
    else:
        body += _unknown_note(
            "No assessment, tax-agreement, or planning records have been collected for this site yet.",
            "request the parcel's assessment record from the county assessor and any tax-incentive "
            "or siting agreements from the county board.")
    body += _p(
        "Virginia data centers typically generate substantial local revenue through business tangible "
        "personal property tax on computer equipment — in Loudoun County this is the largest single "
        "commercial revenue source. But statewide sales-tax exemptions on equipment purchases and any "
        "local incentive agreements offset some public benefit. The key documents are the equipment "
        "assessment schedule and any Memorandum of Understanding between the developer and the county.")
    return body


def _sec_benefits(site: dict) -> str:
    docs = site.get("documents") or []
    news = [d for d in docs if d.get("document_type") in ("news", "company_announcement")]
    body = _p(
        "Developers typically cite construction jobs, permanent jobs, tax revenue, and infrastructure "
        "investment. Construction employment is real but temporary (1–3 years); permanent staffing of "
        "a large data center is typically <strong>30–150 jobs</strong>, far fewer than most industrial "
        "uses of similar land area. Claims should be compared against the county's own fiscal analysis, "
        "not the developer's press release.")
    if news:
        items = "".join(
            f'<li><a href="{escape(d.get("url") or "#")}" target="_blank">{escape(d.get("title",""))}</a></li>'
            for d in news[:8])
        body += _p("Coverage and announcements collected for this site:") + f"<ul>{items}</ul>"
    else:
        body += _unknown_note("No news coverage or company announcements collected yet for this site.",
                              "run the document scraper, or search local outlets for the project name.")
    return body


def _sec_risks(site: dict) -> str:
    infra = site.get("infrastructure") or []
    labels = {i.get("class_label", "") for i in infra}
    risks = []
    risks.append(
        "<li><strong>Backup generators:</strong> large facilities keep dozens of diesel generators "
        "for outages. These are tested regularly and emit diesel exhaust; permits are issued by the "
        "state air-quality agency." +
        (" <em>Generator infrastructure is visible in the imagery for this site.</em>" if
         "generators" in labels else " <em>Not yet identified in imagery — check air permits.</em>") + "</li>")
    risks.append(
        "<li><strong>Noise:</strong> cooling fans and generator tests produce continuous low-frequency "
        "noise that has driven complaints in Loudoun neighborhoods near data centers.</li>")
    risks.append(
        "<li><strong>Grid and rate pressure:</strong> concentrated load growth can raise transmission "
        "costs shared by all ratepayers and delay connection of other projects.</li>")
    risks.append(
        "<li><strong>Water competition:</strong> if wet-cooled, the facility competes with residential "
        "and agricultural users, which matters most during drought.</li>")
    risks.append(
        "<li><strong>Land use lock-in:</strong> data centers occupy large parcels with low employment "
        "density, foreclosing other uses for decades.</li>")
    return _p("Risks to weigh against the claimed benefits (site-specific where imagery allows):") + \
        f"<ul>{''.join(risks)}</ul>"


def _sec_equity(site: dict) -> str:
    lat, lon = site.get("latitude"), site.get("longitude")
    ej_link = (f'https://ejscreen.epa.gov/mapper/index.html?wherestr={lat},{lon}'
               if lat and lon else "https://ejscreen.epa.gov/mapper/")
    return _p(
        "Impacts are not evenly distributed: noise, generator emissions, and truck traffic fall on "
        "immediate neighbors, while benefits (tax revenue) are countywide. Reports should check who "
        "lives within roughly one mile — income, race/ethnicity, existing pollution burden — and "
        "whether affected communities were part of the approval process.") + _p(
        f'A demographic and environmental-burden snapshot for this exact location is available from '
        f'<a href="{ej_link}" target="_blank">EPA EJScreen</a> (free, official). This tool does not '
        f'yet pull those numbers automatically — treat this section as a checklist item, not a finding.')


def _sec_tribal(site: dict) -> str:
    return _p(
        "Virginia is home to seven federally recognized tribes and several state-recognized tribes. "
        "For sites in Loudoun County, direct tribal land conflicts are less common than in the West, "
        "but ground-disturbing construction can affect cultural resources; Section 106 review applies "
        "when federal permits (e.g., wetlands) are involved.") + _unknown_note(
        "This tool has not checked tribal consultation records for this site.",
        "ask the county whether any Section 106 or DHR (Department of Historic Resources) review "
        "was triggered, and request the archaeological survey if one exists.")


def _sec_questions(site: dict) -> str:
    op = site.get("operator")
    qs = []
    if not op:
        qs.append("Who actually owns and will operate this facility? (The parcel owner is often an "
                  "LLC; the county assessor and SCC business registry can unmask it.)")
    qs += [
        "What is the contracted utility capacity (MW) in the interconnection agreement — not the "
        "marketing number?",
        "What cooling technology will be used, and what is the projected daily/annual water demand "
        "in the water service agreement?",
        "How many permanent on-site jobs, at what wages, are committed in writing?",
        "Are there tax incentives, and what is the county's own net fiscal projection?",
        "How many diesel generators are permitted, and what are the testing schedules and emission limits?",
        "What noise limits apply at the property line, and how will they be enforced?",
        "Who pays for substation and transmission upgrades required to serve this load?",
    ]
    return "<ol>" + "".join(f"<li>{escape(q)}</li>" for q in qs) + "</ol>"


def _sec_documents_to_request(site: dict) -> str:
    docs = [
        ("Utility interconnection request / load letter", "Dominion Energy or PJM interconnection queue"),
        ("Water service agreement or will-serve letter", "Loudoun Water"),
        ("Site plan and special exception (SPEX) application", "County planning department"),
        ("Air permit for backup generators", "Virginia DEQ"),
        ("Business tangible personal property assessment", "County Commissioner of Revenue"),
        ("Any incentive MOU or performance agreement", "County Board of Supervisors / EDA"),
        ("Parcel deed and owner LLC registration", "County land records + VA SCC Clerk's Information System"),
        ("Stormwater / land disturbance permit", "County building & development"),
    ]
    rows = "".join(f"<tr><td>{escape(d)}</td><td>{escape(w)}</td></tr>" for d, w in docs)
    return ("<table><thead><tr><th>Document</th><th>Where to request it</th></thead>"
            f"<tbody>{rows}</tbody></table>")


def _sec_developer_questions(site: dict) -> str:
    qs = [
        "Will you commit your jobs and investment claims to a written, enforceable agreement?",
        "What is your projected water use in year 1 and at full build-out, by source?",
        "Will you fund grid upgrades so costs are not passed to residential ratepayers?",
        "What noise mitigation is designed in, and will you accept property-line noise limits as "
        "a condition of approval?",
        "How many diesel generators, and will you commit to cleaner backup alternatives or stricter "
        "testing windows?",
        "Will you publish annual water and electricity consumption for this site?",
    ]
    return "<ol>" + "".join(f"<li>{escape(q)}</li>" for q in qs) + "</ol>"


def _sec_confidence(site: dict) -> str:
    ests = site.get("estimates") or []
    infra = site.get("infrastructure") or []
    docs = site.get("documents") or []

    est_scores = [e.get("confidence_score") or 0 for e in ests if e.get("value") is not None]
    avg_conf = sum(est_scores) / len(est_scores) if est_scores else 0.0

    # Simple, explainable composite
    infra_pts = min(0.3, len(infra) * 0.05)
    doc_pts = min(0.2, len(docs) * 0.02)
    human_pts = 0.1 if any(i.get("human_corrected") for i in infra) else 0.0
    overall = round(min(0.95, avg_conf * 0.5 + infra_pts + doc_pts + human_pts + 0.1), 2)

    parts = [
        f"Average confidence across generated estimates: {avg_conf:.0%}",
        f"Infrastructure outlined: {len(infra)} feature(s) ({'+' if infra else ''}{infra_pts:.0%})",
        f"Public documents collected: {len(docs)} ({'+' if docs else ''}{doc_pts:.0%})",
        f"Human review: {'yes (+10%)' if human_pts else 'not yet (0%)'}",
    ]
    items = "".join(f"<li>{escape(p)}</li>" for p in parts)
    return _p(
        f"<strong>Overall report confidence: {overall:.0%}</strong> "
        f"{_conf_bar(overall)}") + f"<ul>{items}</ul>" + _p(
        "Confidence improves as more public documents are collected, more infrastructure is outlined "
        "and verified by a person, and estimates get anchored to official figures. A low score does "
        "not mean the report is wrong — it means more of it rests on modeled assumptions rather than "
        "confirmed records.")


# ── Main entry point ───────────────────────────────────────────────────────────

def generate_report_html(site: dict) -> str:
    """Render the full 14-section policy report as a self-contained HTML page."""
    name = escape(site.get("name") or "Unnamed site")
    today = date.today().isoformat()

    sections = [
        (1,  "Project summary",                          _sec_summary(site)),
        (2,  "What the imagery shows",                   _sec_imagery(site)),
        (3,  "Estimated resource use",                   _sec_resources(site)),
        (4,  "Local grid implications",                  _sec_grid(site)),
        (5,  "Local water implications",                 _sec_water(site)),
        (6,  "Fiscal impact",                            _sec_fiscal(site)),
        (7,  "Community benefits claimed",               _sec_benefits(site)),
        (8,  "Community risks",                          _sec_risks(site)),
        (9,  "Equity and environmental justice",         _sec_equity(site)),
        (10, "Tribal and community consultation",        _sec_tribal(site)),
        (11, "Key unanswered questions for policymakers", _sec_questions(site)),
        (12, "Recommended documents to request",         _sec_documents_to_request(site)),
        (13, "Questions to ask the developer",           _sec_developer_questions(site)),
        (14, "Confidence score",                         _sec_confidence(site)),
    ]
    body = "".join(_section(n, t, b) for n, t, b in sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Policy Report — {name}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: #1f2937; margin: 0; background: #f3f4f6; line-height: 1.55;
  }}
  .page {{ max-width: 820px; margin: 0 auto; background: #fff; padding: 48px 56px;
          min-height: 100vh; }}
  header {{ border-bottom: 3px solid #1e3a5f; padding-bottom: 16px; margin-bottom: 28px; }}
  header h1 {{ margin: 0 0 4px; font-size: 26px; color: #1e3a5f; }}
  header .sub {{ color: #6b7280; font-size: 14px; }}
  .disclaimer {{
    background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px;
    padding: 12px 16px; font-size: 13px; margin: 18px 0 26px; color: #713f12;
  }}
  section {{ margin-bottom: 30px; }}
  h2 {{ font-size: 17px; color: #1e3a5f; border-bottom: 1px solid #e5e7eb;
       padding-bottom: 6px; margin: 0 0 12px; }}
  .secnum {{ display: inline-block; background: #1e3a5f; color: #fff; border-radius: 6px;
            width: 26px; height: 26px; text-align: center; line-height: 26px;
            font-size: 13px; margin-right: 10px; }}
  p {{ margin: 8px 0; font-size: 14px; }}
  ul, ol {{ font-size: 14px; margin: 8px 0; padding-left: 22px; }}
  li {{ margin-bottom: 6px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 10px 0; }}
  th, td {{ border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left;
           vertical-align: top; }}
  thead th {{ background: #f3f4f6; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  table.kv th {{ width: 160px; background: #f9fafb; font-weight: 600; }}
  .est-card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px;
              margin-bottom: 12px; }}
  .est-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
  .est-name {{ font-weight: 600; font-size: 14px; }}
  .est-conf {{ margin-left: auto; }}
  .est-value {{ font-size: 20px; margin: 4px 0; }}
  .range {{ color: #6b7280; font-size: 13px; }}
  details {{ margin-top: 8px; font-size: 13px; }}
  summary {{ cursor: pointer; color: #1e40af; }}
  details p {{ font-size: 13px; color: #374151; }}
  .unknown-note {{ background: #f9fafb; border-left: 3px solid #9ca3af; padding: 8px 12px;
                  font-size: 13px; }}
  .doc-type {{ color: #6b7280; font-size: 12px; }}
  a {{ color: #1d4ed8; }}
  footer {{ margin-top: 40px; padding-top: 14px; border-top: 1px solid #e5e7eb;
           font-size: 12px; color: #6b7280; }}
  @media print {{
    body {{ background: #fff; }}
    .page {{ padding: 0; max-width: none; }}
    details {{ display: block; }}
    details > summary {{ display: none; }}
    details[open] > *, details > * {{ display: block; }}
  }}
</style>
</head>
<body>
<div class="page">
  <header>
    <h1>{name}</h1>
    <div class="sub">Community Impact Report · DataCenter Lens · generated {today}</div>
  </header>
  <div class="disclaimer">
    <strong>How to read this report.</strong> This is an evidence and estimation tool, not a source
    of exact measurements. Figures are labeled <em>confirmed</em> (from official records),
    <em>estimated</em> (modeled from visible infrastructure and benchmarks), <em>inferred</em>
    (indirect evidence), or <em>unknown</em>. Every estimate shows its method, assumptions, and
    uncertainty range. Use it to know what questions to ask — not as a substitute for the public
    records it recommends requesting.
  </div>
  {body}
  <footer>
    Generated by DataCenter Lens (research preview). Print this page and choose "Save as PDF"
    for a shareable copy. All estimates carry uncertainty; see Section 14 for the overall
    confidence assessment.
  </footer>
</div>
</body>
</html>"""
