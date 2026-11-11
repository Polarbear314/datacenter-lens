# DataCenter Lens

**Estimating the local power and water footprint of data centers from public evidence.**

Data centers are being built at enormous scale, but operators rarely disclose how much electricity and water a specific site will use. County officials weighing a permit, journalists, and residents are often working from press releases. DataCenter Lens assembles what *can* be observed (building footprints, parcel records, utility filings, environmental permits, SEC filings and news) and turns it into transparent estimates with stated uncertainty.

It currently covers **232 data center sites in Loudoun County, Virginia** ("Data Center Alley"), with 928 estimates and a plain-English report for every site.

Built independently by Henry Risch (Bowdoin College), Summer 2026.

---

## Design principles

- **Never present an estimate as a fact.** Every figure carries its method, assumptions, a low/high range, a confidence score, and a label: *confirmed*, *estimated*, *inferred* or *unknown*.
- **Always show sources**, and keep confirmed information visibly separate from inference.
- **A human can correct any automated output.** Corrections keep the original label so they can become training data later.
- **When we don't know something, say so**, and name the document that would answer it.

## What it does

| Component | What it does |
|---|---|
| Satellite map | ESRI imagery with color-coded outlines for buildings, substations, cooling, generators and water storage |
| Footprint import | Pulls building outlines from OpenStreetMap; explicit data-center tags get high confidence, guesses are flagged for review |
| Human review | Click any outline to reclassify or delete it, or move a site pin |
| Public-records scrapers | Seven sources: Google News, Bing News, FERC, Loudoun County GIS, EPA ECHO, SEC EDGAR, county property records |
| Electricity estimator | Footprint × power density (W/sq ft) ÷ PUE, with benchmarks by facility type (hyperscale, colocation, enterprise) |
| Water estimator | Operator-published WUE where known, otherwise climate-adjusted WUE using Open-Meteo temperature data |
| Anchoring | When a filing or article states a real figure (MW, sq ft, gallons/day), it replaces the model estimate and narrows the range, weighted by how trustworthy the source is |
| Report generator | A 14-section, policy-facing HTML report per site: resource use, grid and water implications, fiscal impact, open questions |
| Satellite exposure (`satellite-exposure/`) | Companion prototype that pulls free Landsat-9 thermal and Sentinel-2 imagery to measure a site's heat signature as an independent physical signal |

**Stack:** Python, FastAPI, SQLite, Leaflet.js, OpenStreetMap / Overpass, ArcGIS REST, Open-Meteo

## Run it

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app_lite:app --reload
```

Then open **http://localhost:8000/app** for the map, or **http://localhost:8000/docs** for the API. The included `dclens_dev.db` is a snapshot of the Loudoun County data, so the map, estimates and reports work without re-scraping.

Useful endpoints:

- `GET /stats`: coverage summary
- `POST /sites/{id}/estimate`, `POST /estimate-all`: run the estimators
- `GET /sites/{id}/report`: the HTML site report (print → Save as PDF)

To refresh public records without the server running: `python scripts/scrape_direct.py --named-only --skip-scraped`

*(`docker-compose.yml` and `backend/main.py` are an earlier PostGIS version of the app, kept for future scaling. The lite SQLite version is the one that runs.)*

## What broke, and how I fixed it

- **One site showed 48 buildings.** My first OpenStreetMap import used a 600 m radius and treated every large building as a data center, so CyrusOne Broadlands picked up the whole neighboring office park. I tightened the radius, raised the minimum size, and only imported buildings with explicit evidence (a data-center tag, a known operator, a substation). I also found that two nearby sites were importing the same buildings, so each building is now assigned to the nearest site only.
- **Fenced campuses inflated the estimates.** Parcels tagged as industrial land were being counted as building floor area. I reclassified them as *campus* and apply a building-coverage discount (about 40% of a campus is building), and fixed the importer so it doesn't happen again.
- **Scraped numbers can't be trusted blindly.** Anchoring to document figures is powerful but risky, so I built sanity caps into it. In testing they caught a news article's regional "70,000 MW" figure that had matched a single site, and rejected it instead of overwriting the estimate.
- **The FERC scraper searched the wrong party.** I first searched by data-center operator, but FERC interconnection filings come from the *utility* (Dominion Energy / PJM). I rewrote it to search the utility side.
- **The county server was unreachable** from many networks, so I switched to Loudoun County's published ArcGIS dataset of 271 official data-center building outlines, which also gives real square footage.
- **Smaller fixes:** polygon area is now computed server-side with latitude-aware math (218 rows were missing it); climate lookups are cached by grid cell, so estimating 232 sites makes one API call instead of 232; a shared list was leaking sources between sites in the water model.

## Known limits

- **Estimates are capacity-style.** The county-wide total is about 8 GW, while actual Loudoun load is closer to 4 GW. The model assumes full build-out, not current draw, and each estimate says so in its assumptions.
- About 200 of the 232 sites have not been scraped for documents yet, so most estimates are footprint-based rather than anchored to filings.
- About 20 OpenStreetMap sites have no known operator; the parcel-records scraper is the planned fix.
- Area uses a flat-earth calculation, accurate to about 1% at data-center scale.

## Next

- Finish the document scrape and re-run estimates
- Server-side PDF export, automatic EJScreen demographics, real assessed values in the fiscal section
- Auto-suggest outlines from satellite imagery with an image-segmentation model (Segment Anything), with human review
- Scale beyond Loudoun County with county-specific property-record adapters
