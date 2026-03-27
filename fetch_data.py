"""
fetch_data.py
-------------
Fetches legacy industrial / brownfield site data for Southwestern Pennsylvania
(Mon Valley focus) from verified, working public APIs:

  1. EPA CIMC (Cleanups/MapServer Layer 0) — brownfield + Superfund + RCRA sites
  2. WPRDC property assessments — industrial-classified parcels in Allegheny Co.
  3. Allegheny County GIS OPENDATA/Parcels — parcel geometry
  4. PA DEP / PASDA — abandoned mine reclamation sites (working alternate endpoint)

Run:
  pip install -r requirements.txt
  python fetch_data.py
"""

import json
import logging
import time
from pathlib import Path

import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# SW PA / Mon Valley bounding box (WGS-84)
SWPA_BBOX = {"xmin": -80.519, "ymin": 39.722, "xmax": -79.476, "ymax": 40.680}

# Allegheny + surrounding Mon Valley counties
SWPA_COUNTIES = ("ALLEGHENY", "WASHINGTON", "WESTMORELAND", "FAYETTE", "GREENE")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "SWPA-Industrial-Research/1.0 (academic)"})


# ─────────────────────────────────────────────────────────────────────────────
# Helper: paginated ArcGIS REST query
# ─────────────────────────────────────────────────────────────────────────────

def arcgis_query(base_url: str, layer_id: int = 0, where: str = "1=1",
                 out_fields: str = "*", max_records: int = 5000,
                 bbox: dict | None = None) -> list[dict]:
    endpoint = f"{base_url.rstrip('/')}/{layer_id}/query"
    params: dict = {
        "where": where,
        "outFields": out_fields,
        "f": "geojson",
        "resultRecordCount": 1000,
        "returnGeometry": "true",
    }
    if bbox:
        params["geometry"] = json.dumps({
            "xmin": bbox["xmin"], "ymin": bbox["ymin"],
            "xmax": bbox["xmax"], "ymax": bbox["ymax"],
            "spatialReference": {"wkid": 4326},
        })
        params["geometryType"] = "esriGeometryEnvelope"
        params["spatialRel"] = "esriSpatialRelIntersects"
        params["inSR"] = "4326"

    all_features: list[dict] = []
    offset = 0
    while True:
        params["resultOffset"] = offset
        try:
            r = SESSION.get(endpoint, params=params, timeout=30)
            r.raise_for_status()
            features = r.json().get("features", [])
        except Exception as e:
            log.warning("ArcGIS query failed (%s): %s", endpoint, e)
            break
        all_features.extend(features)
        log.info("  +%d features (total %d)", len(features), len(all_features))
        if len(features) < 1000 or len(all_features) >= max_records:
            break
        offset += 1000
        time.sleep(0.3)
    return all_features


def features_to_gdf(features: list[dict]) -> gpd.GeoDataFrame:
    if not features:
        return gpd.GeoDataFrame()
    fc = {"type": "FeatureCollection", "features": features}
    return gpd.GeoDataFrame.from_features(fc, crs="EPSG:4326")


# ─────────────────────────────────────────────────────────────────────────────
# 1.  EPA CIMC — Cleanups in My Community
#     Verified endpoint: map22.epa.gov/arcgis/rest/services/cimc/Cleanups/MapServer
#     Layer 0 = Sites (Points) with BF_ASSESS_IND, BF_CLEANUP_IND, SF_NPL_CODE, etc.
# ─────────────────────────────────────────────────────────────────────────────

CIMC_BASE = "https://map22.epa.gov/arcgis/rest/services/cimc/Cleanups/MapServer"

CIMC_FIELDS = (
    "REGISTRY_ID,LOCATION_ADDRESS,CITY_NAME,STATE_CODE,COUNTY_NAME,ZIP_CODE,"
    "LATITUDE,LONGITUDE,"
    "BF_PROPERTY_NAME,BF_ASSESS_IND,BF_CLEANUP_IND,BF_ACRES,"
    "SF_SITE_NAME,SF_NPL_CODE,"
    "RCRA_HANDLER_NAME,RCRA_REMEDY_COMPLT_IND,"
    "TRI_FACILITY_NAME,TRI_RELEASE_LBS"
)

# County filter for our five Mon Valley counties
COUNTY_CLAUSE = " OR ".join([f"COUNTY_NAME='{c}'" for c in SWPA_COUNTIES])

def fetch_epa_cimc() -> gpd.GeoDataFrame:
    log.info("=== EPA CIMC: Cleanups/Brownfields ===")
    # Use bounding box only (county names may not match exactly in CIMC data)
    features = arcgis_query(
        CIMC_BASE, layer_id=0,
        where="STATE_CODE='PA'",
        out_fields=CIMC_FIELDS,
        max_records=5000,
        bbox=SWPA_BBOX,
    )
    if not features:
        log.warning("EPA CIMC: no data returned.")
        return gpd.GeoDataFrame()
    gdf = features_to_gdf(features)
    log.info("EPA CIMC: %d sites", len(gdf))
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# 2.  WPRDC — Allegheny County property assessments
#     Verified resource ID: 65855e14-549e-4992-b5be-d629afc676fa
#     Use datastore_search_sql with corrected resource ID
# ─────────────────────────────────────────────────────────────────────────────

WPRDC_BASE = "https://data.wprdc.org/api/3/action/datastore_search"
WPRDC_RESOURCE = "65855e14-549e-4992-b5be-d629afc676fa"
ALCO_PARCELS_BASE = "https://gisdata.alleghenycounty.us/arcgis/rest/services/OPENDATA/Parcels/MapServer"


def fetch_wprdc_industrial() -> gpd.GeoDataFrame:
    """
    WPRDC property assessments have no lat/lon fields.
    Strategy:
      1. Fetch all CLASSDESC=INDUSTRIAL records (~3,544) via paginated datastore_search
         using the verified `filters` parameter.
      2. Batch-query Allegheny County parcel polygons by PIN (= PARID) to get geometry.
      3. Join and return a GeoDataFrame with polygon geometry + assessment attributes.
    """
    log.info("=== WPRDC: Allegheny County industrial properties ===")

    # Step 1: fetch all industrial assessment records
    records = _wprdc_fetch_industrial_records()
    if not records:
        log.warning("WPRDC: no records.")
        return gpd.GeoDataFrame()

    df = pd.DataFrame(records)
    log.info("  %d industrial assessment records fetched", len(df))

    # Step 2: batch-query parcel polygons for those PARIDs
    parids = df["PARID"].dropna().unique().tolist()
    parcel_gdf = _fetch_parcels_by_pins(parids)
    if len(parcel_gdf) == 0:
        log.warning("WPRDC: no parcel geometries matched.")
        return gpd.GeoDataFrame()

    # Step 3: join assessment data to parcel geometry
    merged = parcel_gdf.merge(df, left_on="PIN", right_on="PARID", how="inner")
    log.info("WPRDC industrial properties with geometry: %d", len(merged))
    return merged


def _wprdc_fetch_industrial_records() -> list[dict]:
    """Paginate WPRDC with CLASSDESC=INDUSTRIAL filter (confirmed working)."""
    records = []
    offset = 0
    limit = 1000
    while True:
        try:
            r = SESSION.get(WPRDC_BASE, params={
                "resource_id": WPRDC_RESOURCE,
                "limit": limit,
                "offset": offset,
                "filters": json.dumps({"CLASSDESC": "INDUSTRIAL"}),
            }, timeout=30)
            r.raise_for_status()
            batch = r.json().get("result", {}).get("records", [])
        except Exception as e:
            log.warning("WPRDC fetch error at offset %d: %s", offset, e)
            break
        records.extend(batch)
        log.info("  +%d WPRDC records (total %d)", len(batch), len(records))
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.2)
    return records


def _fetch_parcels_by_pins(parids: list[str], batch_size: int = 30) -> gpd.GeoDataFrame:
    """
    Query Allegheny County parcel service in batches by PIN using POST
    (GET URLs become too long with large IN clauses).
    """
    endpoint = f"{ALCO_PARCELS_BASE}/0/query"
    all_features = []
    total = len(parids)
    for i in range(0, total, batch_size):
        batch = parids[i : i + batch_size]
        pins_sql = ",".join([f"'{p}'" for p in batch])
        try:
            # Use POST to avoid URL length limits
            r = SESSION.post(endpoint, data={
                "where": f"PIN IN ({pins_sql})",
                "outFields": "PIN,MUNICODE,CALCACREAGE",
                "f": "geojson",
                "returnGeometry": "true",
            }, timeout=30)
            r.raise_for_status()
            feats = r.json().get("features", [])
            all_features.extend(feats)
        except Exception as e:
            log.warning("  parcel batch %d failed: %s", i, e)
        if i % 300 == 0:
            log.info("  parcel progress: %d/%d PARIDs → %d geometries", i, total, len(all_features))
        time.sleep(0.1)

    log.info("  parcel fetch complete: %d geometries for %d PARIDs", len(all_features), total)
    if not all_features:
        return gpd.GeoDataFrame()
    fc = {"type": "FeatureCollection", "features": all_features}
    return gpd.GeoDataFrame.from_features(fc, crs="EPSG:4326")


# ─────────────────────────────────────────────────────────────────────────────
# 4.  PA DEP / PASDA — Abandoned mine reclamation sites
#     Verified: mapservices.pasda.psu.edu/server/rest/services/pasda/DEP/MapServer
# ─────────────────────────────────────────────────────────────────────────────

PASDA_DEP_BASE = "https://mapservices.pasda.psu.edu/server/rest/services/pasda/DEP/MapServer"

def fetch_abandoned_mines() -> gpd.GeoDataFrame:
    log.info("=== PA DEP / PASDA: Abandoned mine reclamation sites ===")
    features = arcgis_query(
        PASDA_DEP_BASE, layer_id=0,
        where="1=1",
        max_records=5000,
        bbox=SWPA_BBOX,
    )
    if not features:
        log.warning("PASDA mines: no data.")
        return gpd.GeoDataFrame()
    gdf = features_to_gdf(features)
    log.info("PASDA mines: %d", len(gdf))
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# Save
# ─────────────────────────────────────────────────────────────────────────────

def save(gdf: gpd.GeoDataFrame, name: str):
    if gdf is None or len(gdf) == 0:
        log.warning("Skipping %s (empty)", name)
        return
    path = DATA_DIR / f"{name}.geojson"
    gdf.to_file(path, driver="GeoJSON")
    log.info("Saved %s → %d rows", name, len(gdf))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("Fetching SW Pennsylvania industrial / brownfield data…")

    save(fetch_epa_cimc(),            "epa_cimc_sites")
    save(fetch_wprdc_industrial(),    "wprdc_industrial_properties")
    save(fetch_abandoned_mines(),     "pasda_abandoned_mines")

    log.info("Done. Files in %s/", DATA_DIR)


if __name__ == "__main__":
    main()
