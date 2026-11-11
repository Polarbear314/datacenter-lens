-- 001_initial_schema.sql
-- Run automatically by PostGIS Docker container on first startup.
-- If the database already exists, this file is ignored.

-- Enable PostGIS (provides geometry types and spatial functions)
CREATE EXTENSION IF NOT EXISTS postgis;

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── sites ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sites (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    address         TEXT,
    parcel_id       TEXT,
    operator        TEXT,
    developer       TEXT,
    location        GEOMETRY(POINT, 4326),
    footprint       GEOMETRY(POLYGON, 4326),
    status          TEXT DEFAULT 'unknown'
                    CHECK (status IN ('operating','under_construction','proposed','rumored','cancelled','unknown')),
    first_seen_date DATE DEFAULT CURRENT_DATE,
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    notes           TEXT,
    sources         JSONB DEFAULT '[]'::jsonb
);

-- Spatial index on location for fast proximity queries
CREATE INDEX IF NOT EXISTS idx_sites_location  ON sites USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_sites_footprint ON sites USING GIST (footprint);
CREATE INDEX IF NOT EXISTS idx_sites_status    ON sites (status);


-- ── detected_infrastructure ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS detected_infrastructure (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id          UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    class_label      TEXT NOT NULL DEFAULT 'unknown',
    geometry         GEOMETRY(POLYGON, 4326) NOT NULL,
    area_sqft        NUMERIC,
    confidence_score NUMERIC(3,2) DEFAULT 1.0 CHECK (confidence_score BETWEEN 0 AND 1),
    detection_method TEXT DEFAULT 'manual'
                     CHECK (detection_method IN ('manual','sam','yolo','rule_based')),
    imagery_source   TEXT,
    imagery_date     DATE,
    model_version    TEXT,
    human_corrected  BOOLEAN DEFAULT FALSE,
    corrected_by     TEXT,
    correction_date  TIMESTAMPTZ,
    original_label   TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_infra_site_id  ON detected_infrastructure (site_id);
CREATE INDEX IF NOT EXISTS idx_infra_geometry ON detected_infrastructure USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_infra_class    ON detected_infrastructure (class_label);


-- ── public_documents ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public_documents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id             UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    document_type       TEXT,
    title               TEXT NOT NULL,
    url                 TEXT,
    publication_date    DATE,
    issuing_agency      TEXT,
    extracted_fields    JSONB DEFAULT '{}'::jsonb,
    full_text_available BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_docs_site_id ON public_documents (site_id);
CREATE INDEX IF NOT EXISTS idx_docs_type    ON public_documents (document_type);


-- ── resource_estimates ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS resource_estimates (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id          UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    estimate_type    TEXT NOT NULL,
    value            NUMERIC,
    uncertainty_low  NUMERIC,
    uncertainty_high NUMERIC,
    unit             TEXT,
    confidence_score NUMERIC(3,2) DEFAULT 0.5 CHECK (confidence_score BETWEEN 0 AND 1),
    status           TEXT DEFAULT 'estimated'
                     CHECK (status IN ('confirmed','estimated','inferred','unknown')),
    method           TEXT,
    assumptions      TEXT,
    sources          JSONB DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_estimates_site_id ON resource_estimates (site_id);
CREATE INDEX IF NOT EXISTS idx_estimates_type    ON resource_estimates (estimate_type);
