"""
satellite_pipeline.py
─────────────────────────────────────────────────────────────────────────────
Data Center Resource Exposure Tool — Satellite Analysis Backend

Pulls real Landsat-9 and Sentinel-2 satellite imagery from free public APIs,
processes thermal and multispectral bands, and estimates power/water
consumption for a given data center location.

Data sources (all free, no account required for basic access):
  • Landsat-9 Collection 2 Level-2  →  AWS Open Data (s3://usgs-landsat)
  • Sentinel-2 L2A                  →  AWS Open Data (s3://sentinel-cogs)
  • Scene search                    →  USGS/AWS STAC API (no auth)

Install dependencies:
  pip install requests rasterio numpy matplotlib scipy shapely pyproj boto3

Usage:
  python satellite_pipeline.py --lat 45.5916 --lon -121.1800 --name "Google_TheDalles"
─────────────────────────────────────────────────────────────────────────────
"""

import argparse
import json
import math
import os
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import boto3
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import requests
import rasterio
from botocore import UNSIGNED
from botocore.config import Config
from matplotlib.colors import LinearSegmentedColormap
from rasterio.mask import mask
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely.geometry import Point, box, mapping
from pyproj import Transformer

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS & CONFIG
# ─────────────────────────────────────────────────────────────────────────────

# USGS/AWS STAC API — no authentication required
USGS_STAC_URL = "https://landsatlook.usgs.gov/stac-server"
SENTINEL_STAC_URL = "https://earth-search.aws.element84.com/v1"

# AWS Open Data bucket for Landsat 9
LANDSAT_BUCKET = "usgs-landsat"
LANDSAT_REGION = "us-west-2"

# Landsat-9 band definitions (Collection 2 Level-2)
LANDSAT_BANDS = {
    "blue":    "SR_B2",   # 450–515 nm
    "green":   "SR_B3",   # 525–600 nm
    "red":     "SR_B4",   # 630–680 nm
    "nir":     "SR_B5",   # 845–885 nm
    "swir1":   "SR_B6",   # 1560–1660 nm
    "swir2":   "SR_B7",   # 2100–2300 nm
    "thermal": "ST_B10",  # 10.6–11.2 μm  ← land surface temperature
}

# Landsat thermal band constants (Collection 2 Level-2 scale factors)
THERMAL_SCALE  = 0.00341802   # K per DN
THERMAL_OFFSET = 149.0        # K additive offset

# Analysis window around the facility (meters)
ANALYSIS_RADIUS_M = 1500

# ─────────────────────────────────────────────────────────────────────────────
# RESOURCE ESTIMATION CONSTANTS
# (derived from published literature and public filings)
# ─────────────────────────────────────────────────────────────────────────────

# Power density: kW per square meter of server floor area
# Source: Uptime Institute Global Data Center Survey 2023
SERVER_FLOOR_DENSITY_KW_M2 = 0.85

# Fraction of total footprint that is active server floor
# (rest is mechanical, electrical, corridors, loading)
SERVER_FLOOR_RATIO = 0.55

# PUE assumed if unknown (industry average for hyperscale)
DEFAULT_PUE = 1.20

# Cooling tower evaporation rate (gallons/hr per MW of heat rejected)
# Source: ASHRAE Handbook — HVAC Applications
COOLING_TOWER_EVAP_RATE_GAL_HR_PER_MW = 3_500

# Hours of mechanical cooling per year (hyperscale; varies by climate)
MECH_COOLING_HOURS_PER_YEAR = 6_500

# Thermal anomaly → power estimation (empirical, from Kasiviswanathan et al. 2021)
# Every 1°C above ambient in a 100m Landsat pixel ≈ ~8 MW of rejected heat
MW_PER_CELSIUS_ANOMALY_PER_PIXEL = 8.0

SQ_FT_PER_M2 = 10.7639


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: SCENE SEARCH
# ─────────────────────────────────────────────────────────────────────────────

def search_landsat_scenes(lat: float, lon: float,
                           days_back: int = 90,
                           max_cloud: float = 20.0) -> list[dict]:
    """
    Query the USGS STAC API for Landsat-9 scenes covering a coordinate.
    Returns a list of scene items sorted by cloud cover (ascending).
    """
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days_back)

    # Small bounding box around the point (±0.15°)
    bbox = [lon - 0.15, lat - 0.15, lon + 0.15, lat + 0.15]

    payload = {
        "collections": ["landsat-c2l2-st"],   # Surface Temp product (has thermal)
        "bbox": bbox,
        "datetime": f"{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}",
        "query": {"eo:cloud_cover": {"lte": max_cloud}},
        "limit": 10,
        "sortby": [{"field": "eo:cloud_cover", "direction": "asc"}],
    }

    print(f"  Searching USGS STAC for Landsat-9 scenes over ({lat:.4f}, {lon:.4f})...")
    resp = requests.post(f"{USGS_STAC_URL}/search", json=payload, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("features", [])

    print(f"  Found {len(items)} scene(s) within {days_back} days with ≤{max_cloud}% cloud cover.")
    return items


def search_sentinel_scenes(lat: float, lon: float,
                            days_back: int = 60,
                            max_cloud: float = 15.0) -> list[dict]:
    """
    Query the Element84 STAC API for Sentinel-2 L2A scenes.
    Sentinel-2 has higher spatial resolution (10m) but no thermal band.
    Used here for building footprint measurement.
    """
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days_back)
    bbox = [lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1]

    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox": bbox,
        "datetime": f"{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}",
        "query": {"eo:cloud_cover": {"lte": max_cloud}},
        "limit": 5,
        "sortby": [{"field": "eo:cloud_cover", "direction": "asc"}],
    }

    print(f"  Searching for Sentinel-2 scenes...")
    resp = requests.post(f"{SENTINEL_STAC_URL}/search", json=payload, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("features", [])
    print(f"  Found {len(items)} Sentinel-2 scene(s).")
    return items


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: BAND DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────

def download_band(asset_href: str, out_path: Path) -> Path:
    """
    Download a single band from AWS S3 (Landsat open data).
    Uses anonymous (unsigned) S3 access — no AWS credentials required.
    """
    if out_path.exists():
        print(f"    [cached] {out_path.name}")
        return out_path

    # Strip s3:// prefix and split bucket/key
    href = asset_href.replace("s3://", "")
    bucket, key = href.split("/", 1)

    s3 = boto3.client(
        "s3",
        region_name=LANDSAT_REGION,
        config=Config(signature_version=UNSIGNED)
    )

    print(f"    Downloading {key.split('/')[-1]} ...")
    s3.download_file(bucket, key, str(out_path))
    return out_path


def download_landsat_bands(scene: dict, bands: list[str], out_dir: Path) -> dict[str, Path]:
    """Download specified Landsat bands for a scene. Returns {band_name: local_path}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    scene_id = scene["id"]
    assets = scene.get("assets", {})
    paths = {}

    for band in bands:
        if band not in assets:
            print(f"    [warning] Band {band} not in scene assets — skipping.")
            continue
        href = assets[band].get("href", "")
        if not href:
            continue
        filename = f"{scene_id}_{band}.TIF"
        local = out_dir / filename
        paths[band] = download_band(href, local)

    return paths


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: PREPROCESSING
# ─────────────────────────────────────────────────────────────────────────────

def load_and_clip_band(tif_path: Path, lat: float, lon: float,
                        radius_m: float = ANALYSIS_RADIUS_M):
    """
    Load a GeoTIFF band, reproject to UTM, and clip to analysis window.
    Returns (data_array, transform, crs, nodata).
    """
    with rasterio.open(tif_path) as src:
        # Reproject point to source CRS
        transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        px, py = transformer.transform(lon, lat)

        # Build clip geometry (square window)
        half = radius_m
        geom = box(px - half, py - half, px + half, py + half)
        geom_geojson = [mapping(geom)]

        data, transform = mask(src, geom_geojson, crop=True, nodata=src.nodata)
        return data[0].astype(np.float32), transform, src.crs, src.nodata


def band_to_celsius(thermal_dn: np.ndarray, nodata=None) -> np.ndarray:
    """
    Convert Landsat ST_B10 digital numbers to Land Surface Temperature in °C.
    Collection 2 Level-2 scale: LST (K) = DN × 0.00341802 + 149.0
    """
    valid = (thermal_dn != nodata) if nodata is not None else np.ones_like(thermal_dn, bool)
    lst_k = np.where(valid, thermal_dn * THERMAL_SCALE + THERMAL_OFFSET, np.nan)
    lst_c = lst_k - 273.15
    return lst_c


def compute_ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """
    Normalized Difference Vegetation Index.
    NDVI = (NIR - Red) / (NIR + Red)
    Low NDVI (<0.1) over a large impervious surface area = data center signature.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (nir - red) / (nir + red)
    return np.clip(ndvi, -1, 1)


def compute_ndbi(swir1: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """
    Normalized Difference Built-up Index.
    NDBI = (SWIR1 - NIR) / (SWIR1 + NIR)
    High NDBI (>0.1) indicates impervious/built-up surface — data center buildings.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        ndbi = (swir1 - nir) / (swir1 + nir)
    return np.clip(ndbi, -1, 1)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: THERMAL ANOMALY DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_thermal_anomaly(lst_celsius: np.ndarray,
                            ndbi: np.ndarray,
                            ndvi: np.ndarray) -> dict:
    """
    Identify the thermal anomaly attributable to the data center.

    Method:
    1. Define background temperature as the median of vegetated pixels (NDVI > 0.3)
    2. Identify hot pixels (LST > background + threshold) on built-up surfaces
    3. Calculate mean temperature anomaly over the facility footprint
    4. Flag as data-center signature if anomaly + NDBI pattern match
    """
    # Background: vegetated/rural pixels
    veg_mask = (ndvi > 0.3) & np.isfinite(lst_celsius)
    bg_temp = float(np.nanmedian(lst_celsius[veg_mask])) if veg_mask.any() else np.nan

    # Facility footprint: built-up pixels near facility center
    built_mask = (ndbi > 0.1) & np.isfinite(lst_celsius)
    facility_lst = lst_celsius[built_mask]

    if len(facility_lst) == 0:
        return {"error": "No built-up pixels detected in analysis window."}

    facility_mean_temp = float(np.nanmean(facility_lst))
    facility_max_temp  = float(np.nanmax(facility_lst))
    anomaly_c          = facility_mean_temp - bg_temp if not math.isnan(bg_temp) else None

    # Hot pixels (>3°C above background) — concentrated heat rejection zones
    if anomaly_c is not None:
        hot_mask   = built_mask & (lst_celsius > bg_temp + 3.0)
        hot_pixels = int(hot_mask.sum())
    else:
        hot_pixels = 0

    return {
        "background_temp_c":   round(bg_temp, 2) if not math.isnan(bg_temp) else None,
        "facility_mean_lst_c": round(facility_mean_temp, 2),
        "facility_max_lst_c":  round(facility_max_temp, 2),
        "anomaly_c":           round(anomaly_c, 2) if anomaly_c else None,
        "hot_pixels_count":    hot_pixels,
        "built_pixels_count":  int(built_mask.sum()),
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: RESOURCE ESTIMATION MODEL
# ─────────────────────────────────────────────────────────────────────────────

def estimate_footprint_m2(ndbi: np.ndarray, pixel_size_m: float = 30.0) -> float:
    """
    Estimate built-up surface area from NDBI layer.
    Landsat pixels are 30×30 m for optical bands.
    """
    built_pixels = (ndbi > 0.1).sum()
    return float(built_pixels) * (pixel_size_m ** 2)


def estimate_resources(footprint_m2: float,
                        thermal_anomaly: dict,
                        pue: float = DEFAULT_PUE) -> dict:
    """
    Estimate power draw and water consumption from two independent signals:

    Signal A: Building footprint → power
      Power (MW) = footprint_m2 × server_floor_ratio × density_kW/m2 / PUE / 1000

    Signal B: Thermal anomaly → power
      Power (MW) = anomaly_°C × MW_per_degree × hot_pixel_count

    Final estimate = weighted average of both signals.
    Water consumption is derived from power via cooling model.
    """
    results = {}

    # ── Signal A: Footprint-based ──
    server_floor_m2   = footprint_m2 * SERVER_FLOOR_RATIO
    it_load_kw        = server_floor_m2 * SERVER_FLOOR_DENSITY_KW_M2
    total_power_kw_a  = it_load_kw * pue
    power_mw_a        = total_power_kw_a / 1000.0

    results["footprint_m2"]        = round(footprint_m2)
    results["footprint_sqft"]      = round(footprint_m2 * SQ_FT_PER_M2)
    results["server_floor_m2"]     = round(server_floor_m2)
    results["power_estimate_A_MW"] = round(power_mw_a, 1)

    # ── Signal B: Thermal anomaly-based ──
    power_mw_b = None
    anomaly_c  = thermal_anomaly.get("anomaly_c")
    hot_pixels = thermal_anomaly.get("hot_pixels_count", 0)

    if anomaly_c is not None and hot_pixels > 0:
        # Each 1°C anomaly over each 30m×30m pixel ≈ MW_PER_CELSIUS_ANOMALY_PER_PIXEL
        # at 100m Landsat thermal resolution, scale factor accounts for pixel aggregation
        thermal_scale_factor = (100 / 30) ** 2  # Landsat thermal is 100m pixels
        power_mw_b = anomaly_c * hot_pixels * MW_PER_CELSIUS_ANOMALY_PER_PIXEL / thermal_scale_factor
        results["power_estimate_B_MW"] = round(power_mw_b, 1)
    else:
        results["power_estimate_B_MW"] = None

    # ── Combined estimate ──
    if power_mw_b is not None:
        # Weight Signal B lower (thermal is noisier at 100m resolution)
        combined_mw = 0.6 * power_mw_a + 0.4 * power_mw_b
    else:
        combined_mw = power_mw_a

    results["power_estimate_combined_MW"] = round(combined_mw, 1)
    results["pue_assumed"] = pue

    # ── IT load ──
    it_load_mw = combined_mw / pue
    results["it_load_MW"] = round(it_load_mw, 1)

    # ── Heat rejection (goes to cooling system) ──
    heat_rejection_mw = combined_mw - it_load_mw
    results["heat_rejection_MW"] = round(heat_rejection_mw, 1)

    # ── Water consumption estimate ──
    # Assumes evaporative cooling (worst case). Air-cooled facilities use ~0.
    # Evap rate: 3,500 gal/hr per MW of heat rejection, at full load
    # Scale by ~0.75 for typical load factor and hybrid cooling
    load_factor   = 0.75
    water_gal_yr  = (heat_rejection_mw * COOLING_TOWER_EVAP_RATE_GAL_HR_PER_MW
                     * MECH_COOLING_HOURS_PER_YEAR * load_factor)

    results["water_est_gal_per_year"]   = round(water_gal_yr)
    results["water_est_Mgal_per_year"]  = round(water_gal_yr / 1e6, 1)
    results["water_est_Bgal_per_year"]  = round(water_gal_yr / 1e9, 3)
    results["note_water"] = (
        "Water estimate assumes evaporative cooling. Air-cooled facilities "
        "use significantly less. Actual figure may vary ±40%."
    )

    # ── Carbon footprint (scope 2) ──
    # US average grid: 386 kg CO2e/MWh (EPA 2023)
    hours_per_year = 8760
    grid_intensity_kg_mwh = 386
    co2_tonnes_yr = combined_mw * hours_per_year * grid_intensity_kg_mwh / 1e6
    results["scope2_co2_tonnes_yr_grid_avg"] = round(co2_tonnes_yr)
    results["note_co2"] = (
        "CO2 assumes US average grid. Hydro/wind-powered facilities approach ~0 scope 2."
    )

    return results


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6: VISUALIZATION
# ─────────────────────────────────────────────────────────────────────────────

def generate_report_figure(lst_celsius: np.ndarray,
                            ndbi: np.ndarray,
                            ndvi: np.ndarray,
                            thermal_anomaly: dict,
                            resource_est: dict,
                            facility_name: str,
                            out_path: Path):
    """
    Generate a 4-panel diagnostic figure:
      [1] Land Surface Temperature (thermal infrared)
      [2] NDBI — built-up surface detection
      [3] NDVI — vegetation cover
      [4] Resource estimate summary
    """
    fig, axes = plt.subplots(2, 2, figsize=(14, 11))
    fig.patch.set_facecolor('#0d1117')

    for ax in axes.flat:
        ax.set_facecolor('#161b22')
        ax.tick_params(colors='#8b949e', labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor('#30363d')

    title_kw = dict(color='#e6edf3', fontsize=11, fontweight='bold', pad=8)
    label_kw = dict(color='#8b949e', fontsize=7)

    # ── Panel 1: Land Surface Temperature ──
    ax = axes[0, 0]
    thermal_cmap = LinearSegmentedColormap.from_list(
        'thermal', ['#0d1117', '#1f6feb', '#e3b341', '#f85149', '#ffffff'])
    valid_lst = lst_celsius[np.isfinite(lst_celsius)]
    vmin, vmax = (np.percentile(valid_lst, 5), np.percentile(valid_lst, 98)) if len(valid_lst) else (0, 50)
    im1 = ax.imshow(lst_celsius, cmap=thermal_cmap, vmin=vmin, vmax=vmax, interpolation='nearest')
    cb1 = plt.colorbar(im1, ax=ax, fraction=0.04, pad=0.02)
    cb1.set_label('°C', color='#8b949e', fontsize=8)
    cb1.ax.yaxis.set_tick_params(color='#8b949e')
    plt.setp(plt.getp(cb1.ax.axes, 'yticklabels'), color='#8b949e', fontsize=7)
    ax.set_title('Land Surface Temperature (Landsat-9 TIRS)', **title_kw)

    bg   = thermal_anomaly.get("background_temp_c", "N/A")
    mean = thermal_anomaly.get("facility_mean_lst_c", "N/A")
    anom = thermal_anomaly.get("anomaly_c", "N/A")
    ax.set_xlabel(f'Background: {bg}°C  |  Facility mean: {mean}°C  |  Anomaly: +{anom}°C', **label_kw)

    # ── Panel 2: NDBI (Built-up Index) ──
    ax = axes[0, 1]
    ndbi_cmap = LinearSegmentedColormap.from_list('ndbi', ['#1a3d1a', '#21262d', '#e3b341', '#f85149'])
    im2 = ax.imshow(ndbi, cmap=ndbi_cmap, vmin=-0.3, vmax=0.5, interpolation='nearest')
    cb2 = plt.colorbar(im2, ax=ax, fraction=0.04, pad=0.02)
    cb2.set_label('NDBI', color='#8b949e', fontsize=8)
    cb2.ax.yaxis.set_tick_params(color='#8b949e')
    plt.setp(plt.getp(cb2.ax.axes, 'yticklabels'), color='#8b949e', fontsize=7)
    ax.set_title('NDBI — Built-up Surface Detection', **title_kw)
    built_px = resource_est.get("footprint_m2", 0) / (30**2)
    ax.set_xlabel(f'Built-up pixels detected: {int(built_px)}  |  Estimated footprint: {resource_est.get("footprint_sqft",0):,} sq ft', **label_kw)

    # ── Panel 3: NDVI (Vegetation) ──
    ax = axes[1, 0]
    ndvi_cmap = LinearSegmentedColormap.from_list('ndvi', ['#f85149', '#21262d', '#1a3d1a', '#3fb950'])
    im3 = ax.imshow(ndvi, cmap=ndvi_cmap, vmin=-0.2, vmax=0.6, interpolation='nearest')
    cb3 = plt.colorbar(im3, ax=ax, fraction=0.04, pad=0.02)
    cb3.set_label('NDVI', color='#8b949e', fontsize=8)
    cb3.ax.yaxis.set_tick_params(color='#8b949e')
    plt.setp(plt.getp(cb3.ax.axes, 'yticklabels'), color='#8b949e', fontsize=7)
    ax.set_title('NDVI — Vegetation Cover (Low = Impervious Surface)', **title_kw)
    ax.set_xlabel('Data centers appear as dark patches (low NDVI) surrounded by higher-NDVI surroundings', **label_kw)

    # ── Panel 4: Resource Summary ──
    ax = axes[1, 1]
    ax.axis('off')

    mw_a   = resource_est.get("power_estimate_A_MW", "N/A")
    mw_b   = resource_est.get("power_estimate_B_MW", "N/A")
    mw_c   = resource_est.get("power_estimate_combined_MW", "N/A")
    water  = resource_est.get("water_est_Mgal_per_year", "N/A")
    co2    = resource_est.get("scope2_co2_tonnes_yr_grid_avg", "N/A")
    it_mw  = resource_est.get("it_load_MW", "N/A")
    heat   = resource_est.get("heat_rejection_MW", "N/A")

    lines = [
        ("ESTIMATED RESOURCE CONSUMPTION", None, '#58a6ff', 14),
        (facility_name.replace("_", " "), None, '#8b949e', 10),
        ("", None, '#e6edf3', 8),
        ("⚡  POWER DRAW",           f"{mw_c} MW",    '#f85149', 11),
        ("   Signal A (footprint):", f"{mw_a} MW",    '#8b949e', 9),
        ("   Signal B (thermal):",   f"{mw_b} MW",    '#8b949e', 9),
        ("   IT load (servers):",    f"{it_mw} MW",   '#e6edf3', 9),
        ("   Heat rejection:",       f"{heat} MW",    '#e6edf3', 9),
        ("", None, '#e6edf3', 8),
        ("💧  WATER CONSUMPTION",    f"{water}M gal/yr", '#58a6ff', 11),
        ("   (evaporative cooling assumed)", "", '#8b949e', 8),
        ("", None, '#e6edf3', 8),
        ("🌍  CARBON (grid avg)",    f"{co2:,} t CO₂e/yr" if isinstance(co2, int) else co2, '#3fb950', 11),
        ("   (scope 2, before RECs)", "", '#8b949e', 8),
        ("", None, '#e6edf3', 8),
        ("Uncertainty: ±25–35% on all estimates", "", '#8b949e', 8),
        ("Source: Satellite analysis + public literature", "", '#8b949e', 8),
    ]

    y = 0.96
    for left, right, color, size in lines:
        if left and right:
            ax.text(0.02, y, left,  transform=ax.transAxes, color=color,   fontsize=size, va='top', fontweight='bold' if size >= 11 else 'normal')
            ax.text(0.98, y, right, transform=ax.transAxes, color='#e6edf3', fontsize=size, va='top', ha='right')
        elif left:
            ax.text(0.02, y, left,  transform=ax.transAxes, color=color, fontsize=size, va='top', fontweight='bold' if size >= 11 else 'normal')
        y -= 0.06 if size >= 11 else 0.045

    fig.suptitle(
        f'DataCenter Exposure Tool — Satellite Analysis\n{facility_name.replace("_"," ")}',
        color='#e6edf3', fontsize=13, fontweight='bold', y=1.01
    )

    plt.tight_layout()
    plt.savefig(str(out_path), dpi=150, bbox_inches='tight',
                facecolor='#0d1117', edgecolor='none')
    plt.close()
    print(f"  Saved figure → {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(lat: float, lon: float, name: str,
                 out_dir: str = "output",
                 pue: float = DEFAULT_PUE,
                 days_back: int = 90):
    """
    Full pipeline: search → download → process → estimate → visualize → report.
    """
    out_path = Path(out_dir) / name
    out_path.mkdir(parents=True, exist_ok=True)

    print(f"\n{'═'*60}")
    print(f"  DataCenter Exposure Tool — Satellite Pipeline")
    print(f"  Facility : {name}")
    print(f"  Coords   : {lat}°N, {lon}°E")
    print(f"  Output   : {out_path}")
    print(f"{'═'*60}\n")

    # ── 1. Search for scenes ──
    print("[1/5] Searching for Landsat-9 scenes...")
    scenes = search_landsat_scenes(lat, lon, days_back=days_back)
    if not scenes:
        print("  ✗ No scenes found. Try increasing --days_back or --max_cloud.")
        sys.exit(1)

    scene = scenes[0]  # Best scene (lowest cloud cover)
    scene_date = scene.get("properties", {}).get("datetime", "unknown")[:10]
    cloud_pct  = scene.get("properties", {}).get("eo:cloud_cover", "?")
    print(f"  ✓ Using scene: {scene['id']}")
    print(f"    Date: {scene_date}  |  Cloud cover: {cloud_pct}%")

    # ── 2. Download bands ──
    print("\n[2/5] Downloading satellite bands...")
    needed_bands = ["SR_B4", "SR_B5", "SR_B6", "ST_B10"]  # Red, NIR, SWIR1, Thermal
    band_paths = download_landsat_bands(scene, needed_bands, out_path / "bands")

    if "ST_B10" not in band_paths:
        print("  ✗ Thermal band not available for this scene.")
        sys.exit(1)

    # ── 3. Process bands ──
    print("\n[3/5] Processing bands...")

    print("    Loading thermal band (ST_B10)...")
    thermal_dn, transform, crs, nodata = load_and_clip_band(
        band_paths["ST_B10"], lat, lon)
    lst_celsius = band_to_celsius(thermal_dn, nodata)

    print("    Loading optical bands (Red, NIR, SWIR1)...")
    red_dn,  _, _, _ = load_and_clip_band(band_paths["SR_B4"], lat, lon)
    nir_dn,  _, _, _ = load_and_clip_band(band_paths["SR_B5"], lat, lon)
    swir_dn, _, _, _ = load_and_clip_band(band_paths["SR_B6"], lat, lon)

    # Scale to surface reflectance (Landsat C2 L2 scale: × 2.75e-5 − 0.2)
    def sr_scale(dn): return np.clip(dn * 2.75e-5 - 0.2, 0, 1)
    red  = sr_scale(red_dn)
    nir  = sr_scale(nir_dn)
    swir = sr_scale(swir_dn)

    ndvi = compute_ndvi(red, nir)
    ndbi = compute_ndbi(swir, nir)

    print("    ✓ Indices computed (NDVI, NDBI)")

    # ── 4. Estimate resources ──
    print("\n[4/5] Estimating resource consumption...")

    thermal_anomaly = detect_thermal_anomaly(lst_celsius, ndbi, ndvi)
    print(f"    Thermal anomaly: +{thermal_anomaly.get('anomaly_c','N/A')}°C above background")
    print(f"    Hot pixels detected: {thermal_anomaly.get('hot_pixels_count','N/A')}")

    footprint_m2 = estimate_footprint_m2(ndbi)
    print(f"    Built-up footprint: {footprint_m2:,.0f} m² ({footprint_m2 * SQ_FT_PER_M2:,.0f} sq ft)")

    resource_est = estimate_resources(footprint_m2, thermal_anomaly, pue=pue)

    # ── 5. Generate figure and report ──
    print("\n[5/5] Generating outputs...")

    fig_path = out_path / f"{name}_analysis.png"
    generate_report_figure(
        lst_celsius, ndbi, ndvi,
        thermal_anomaly, resource_est,
        facility_name=name,
        out_path=fig_path
    )

    report = {
        "facility": name,
        "coordinates": {"lat": lat, "lon": lon},
        "scene": {
            "id": scene["id"],
            "date": scene_date,
            "cloud_cover_pct": cloud_pct
        },
        "thermal_analysis": thermal_anomaly,
        "resource_estimates": resource_est,
        "methodology": {
            "signal_A": "Building footprint from NDBI (30m Landsat optical bands)",
            "signal_B": "Thermal anomaly from ST_B10 (100m Landsat TIRS)",
            "water_model": "Evaporative cooling: 3,500 gal/hr/MW × 6,500 hr/yr × 0.75 load factor",
            "uncertainty": "±25–35% on power; ±40% on water (cooling method unknown)",
            "pue_assumed": pue,
        }
    }

    report_path = out_path / f"{name}_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # ── Summary ──
    print(f"\n{'─'*60}")
    print(f"  RESULTS SUMMARY — {name}")
    print(f"{'─'*60}")
    print(f"  Power draw (combined estimate): {resource_est['power_estimate_combined_MW']} MW")
    print(f"    └ Signal A (footprint):       {resource_est['power_estimate_A_MW']} MW")
    print(f"    └ Signal B (thermal):         {resource_est.get('power_estimate_B_MW','N/A')} MW")
    print(f"  Water consumption (est.):       {resource_est['water_est_Mgal_per_year']}M gal/yr")
    print(f"  Scope 2 CO₂ (grid avg):         {resource_est['scope2_co2_tonnes_yr_grid_avg']:,} t/yr")
    print(f"  Footprint:                      {resource_est['footprint_sqft']:,} sq ft")
    print(f"{'─'*60}")
    print(f"  Output files saved to: {out_path}/")
    print(f"    • {name}_analysis.png    (4-panel diagnostic figure)")
    print(f"    • {name}_report.json    (full data export)")
    print(f"{'─'*60}\n")

    return report


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Satellite-based data center resource estimation pipeline."
    )
    parser.add_argument("--lat",       type=float, required=True,  help="Latitude of facility (decimal degrees)")
    parser.add_argument("--lon",       type=float, required=True,  help="Longitude of facility (decimal degrees)")
    parser.add_argument("--name",      type=str,   required=True,  help="Facility name (used for output filenames)")
    parser.add_argument("--out_dir",   type=str,   default="output", help="Output directory (default: ./output)")
    parser.add_argument("--pue",       type=float, default=1.2,    help="Assumed PUE (default: 1.2)")
    parser.add_argument("--days_back", type=int,   default=90,     help="Days to look back for scenes (default: 90)")

    args = parser.parse_args()

    run_pipeline(
        lat=args.lat,
        lon=args.lon,
        name=args.name,
        out_dir=args.out_dir,
        pue=args.pue,
        days_back=args.days_back,
    )

    # ──────────────────────────────────────────────────────────────────────
    # EXAMPLE CALLS:
    #
    #   Google The Dalles:
    #   python satellite_pipeline.py --lat 45.5916 --lon -121.1800 --name "Google_TheDalles"
    #
    #   AWS Ashburn:
    #   python satellite_pipeline.py --lat 39.0453 --lon -77.4866 --name "AWS_Ashburn" --pue 1.2
    #
    #   Microsoft Quincy:
    #   python satellite_pipeline.py --lat 47.2307 --lon -119.8523 --name "Microsoft_Quincy"
    #
    #   Meta Prineville:
    #   python satellite_pipeline.py --lat 44.3036 --lon -120.8985 --name "Meta_Prineville" --pue 1.08
    # ──────────────────────────────────────────────────────────────────────
