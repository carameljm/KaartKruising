import geopandas as gpd
import pandas as pd
import requests
import os
import argparse
import json
import logging
import time
from io import StringIO
from datetime import datetime, timedelta
from shapely.geometry import box, mapping

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Configuration ---
MINX, MINY, MAXX, MAXY = 77144, 158145, 127271, 200742
WFS_URL = "https://www.mercator.vlaanderen.be/raadpleegdienstenmercatorpubliek/wfs"
VRBG_URL = "https://geo.api.vlaanderen.be/VRBG/wfs"
LAGEN_OMGEVING = ["lu:lu_omv_gd_v2", "lu:lu_omv_vk_v2"]

ALLOWED_MUNICIPALITIES = [
    "Anzegem", "Avelgem", "Brakel", "Deinze", "Gavere",
    "Geraardsbergen", "Horebeke", "Kluisbergen", "Kruisem",
    "Lierde", "Maarkedal", "Nazareth-De Pinte", "Oudenaarde",
    "Ronse", "Waregem", "Wortegem-Petegem", "Zottegem", "Zulte", "Zwalm"
]

MATCHES_FILE = "output_maps/matches.json"
PENDING_FILE = "pending_intersections.json"


def load_json(path, default=None):
    if default is None:
        default = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error("Error loading %s: %s", path, e)
    return default


def save_json(path, data):
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        log.error("Error saving %s: %s", path, e)


def lookup_municipality(point_geom):
    try:
        x, y = point_geom.x, point_geom.y
        cql = f"INTERSECTS(SHAPE, POINT({x} {y}))"
        params = {
            "service": "WFS", "version": "1.1.0", "request": "GetFeature",
            "typeName": "VRBG:Refgem", "outputFormat": "application/json",
            "srsName": "EPSG:31370", "CQL_FILTER": cql,
            "propertyName": "NAAM", "maxFeatures": "1"
        }
        r = requests.get(VRBG_URL, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            features = data.get("features", [])
            if features:
                return features[0]["properties"].get("NAAM", "Onbekend")
    except Exception as e:
        log.warning("Error looking up municipality: %s", e)
    return "Onbekend"


def load_local_roads(base_dir):
    log.info("Loading local road data from '%s'...", base_dir)
    path_buurt = os.path.join(base_dir, "buurtwegenoostvlaanderen.geojson")
    path_wijzig = os.path.join(base_dir, "wijzigingenoostvlaanderen.geojson")

    missing_files = []
    for p in [path_buurt, path_wijzig]:
        if not os.path.exists(p):
            missing_files.append(os.path.basename(p))

    if missing_files:
        log.error("Missing road data files: %s", ", ".join(missing_files))
        raise FileNotFoundError(
            f"Road data files not found in '{base_dir}': {', '.join(missing_files)}. "
            f"Download from https://github.com/carameljm/buurtwegenomgevingsdossiers"
        )

    gdfs = []
    bbox_tuple = (MINX, MINY, MAXX, MAXY)
    for p in [path_buurt, path_wijzig]:
        log.info("Reading %s...", p)
        try:
            gdf = gpd.read_file(p, bbox=bbox_tuple)
            if not gdf.empty:
                source_name = "buurtwegen" if "buurtwegen" in os.path.basename(p) else "wijzigingen"
                gdf["bron_bestand"] = source_name
                if gdf.crs is None:
                    gdf.set_crs("EPSG:4326", inplace=True)
                gdfs.append(gdf.to_crs("EPSG:31370"))
        except Exception as e:
            log.error("Error reading %s: %s", p, e)

    if not gdfs:
        raise ValueError("No road data loaded - files may be empty or outside bounding box.")

    combined_roads = pd.concat(gdfs, ignore_index=True)
    log.info("Total road segments loaded in region: %d", len(combined_roads))
    return combined_roads


def fetch_recent_permits(days=7):
    log.info("Fetching permits from last %d days...", days)
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    recente_dossiers = []
    for laag in LAGEN_OMGEVING:
        cql = f"BBOX(geom, {MINX}, {MINY}, {MAXX}, {MAXY}) AND datum_indiening >= {cutoff_date}T00:00:00Z"
        params = {
            "service": "WFS", "version": "1.1.0", "request": "GetFeature",
            "typeName": laag, "outputFormat": "application/json",
            "srsName": "EPSG:31370", "CQL_FILTER": cql
        }
        try:
            log.info("Querying layer %s...", laag)
            r = requests.get(WFS_URL, params=params, timeout=30)
            if "Illegal property name: geom" in r.text:
                params["CQL_FILTER"] = params["CQL_FILTER"].replace("geom", "geometry")
                r = requests.get(WFS_URL, params=params, timeout=30)
            if r.status_code == 200:
                if 'numberOfFeatures":0' in r.text.replace(" ", ""):
                    continue
                temp_gdf = gpd.read_file(StringIO(r.text))
                if not temp_gdf.empty:
                    if temp_gdf.crs is None:
                        temp_gdf.set_crs("EPSG:31370", inplace=True)
                    recente_dossiers.append(temp_gdf)
            else:
                log.warning("WFS returned %d for layer %s", r.status_code, laag)
        except Exception as e:
            log.error("Connection error querying %s: %s", laag, e)
    if not recente_dossiers:
        return gpd.GeoDataFrame()
    return pd.concat(recente_dossiers, ignore_index=True)


def clean_data_dict(row, exclude_cols):
    d = row.drop(labels=[c for c in row.index if c in exclude_cols], errors="ignore").to_dict()
    new_d = {}
    for k, v in d.items():
        if isinstance(v, (pd.Timestamp, datetime)):
            new_d[k] = v.isoformat()
        elif pd.isna(v):
            new_d[k] = None
        else:
            new_d[k] = v
    return new_d


def _group_pending_by_project(pending_queue):
    """Group pending entries by projectnummer, collecting all roads per project."""
    grouped = {}
    for item in pending_queue:
        pn = item["permit_data"].get("projectnummer")
        if pn not in grouped:
            grouped[pn] = {
                "municipality": item["municipality"],
                "permit_data": item["permit_data"],
                "permit_geom": item["permit_geom"],
                "roads": [],
                "discovered_at": item["discovered_at"],
            }
        grouped[pn]["roads"].append({
            "road_data": item["road_data"],
            "road_geom": item["road_geom"],
        })
    return list(grouped.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--roads-dir", default="buurtwegenomgevingsdossiers-main")
    parser.add_argument("--output", "-o", default="output_maps")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    global MATCHES_FILE, PENDING_FILE
    MATCHES_FILE = os.path.join(args.output, "matches.json")
    validated_matches = load_json(MATCHES_FILE, [])
    pending_queue = load_json(PENDING_FILE, [])

    existing_projectnums = {m["permit_data"].get("projectnummer") for m in validated_matches if "permit_data" in m}
    pending_projectnums = {p["permit_data"].get("projectnummer") for p in pending_queue if "permit_data" in p}

    log.info("Loaded %d validated matches and %d pending.", len(validated_matches), len(pending_queue))

    try:
        roads_gdf = load_local_roads(args.roads_dir)
        permits_gdf = fetch_recent_permits(days=args.days)
    except FileNotFoundError as e:
        log.error("%s", e)
        return
    except Exception as e:
        log.error("Error: %s", e)
        return

    if not permits_gdf.empty:
        permits_gdf = permits_gdf.copy()
        permits_gdf["geometry_buffered"] = permits_gdf.geometry.buffer(-1.0)
        valid_permits = permits_gdf[~permits_gdf.geometry_buffered.is_empty].copy().set_geometry("geometry_buffered")

        spatial_matches = gpd.sjoin(valid_permits, roads_gdf, how="inner", predicate="intersects")
        new_count = 0
        for idx, match_row in spatial_matches.iterrows():
            permit_row = permits_gdf.loc[idx]
            project_num = permit_row.get("projectnummer")
            if not project_num or project_num in existing_projectnums or project_num in pending_projectnums:
                continue

            municipality = lookup_municipality(permit_row.geometry.centroid)
            if municipality in ALLOWED_MUNICIPALITIES:
                road_row = roads_gdf.loc[match_row["index_right"]]
                # Store geometry as GeoJSON dict (EPSG:4326) for inline map rendering
                permit_geom_4326 = gpd.GeoSeries([permit_row.geometry], crs="EPSG:31370").to_crs("EPSG:4326")[0]
                road_geom_4326 = gpd.GeoSeries([road_row.geometry], crs="EPSG:31370").to_crs("EPSG:4326")[0]

                pending_queue.append({
                    "municipality": municipality,
                    "permit_data": clean_data_dict(permit_row, ["geometry", "geometry_buffered"]),
                    "road_data": clean_data_dict(road_row, ["geometry"]),
                    "permit_geom": mapping(permit_geom_4326),
                    "road_geom": mapping(road_geom_4326),
                    "discovered_at": datetime.now().isoformat()
                })
                pending_projectnums.add(project_num)
                new_count += 1

        if new_count > 0:
            log.info("Added %d new intersections.", new_count)
            save_json(PENDING_FILE, pending_queue)

    if not pending_queue:
        log.info("No pending items to process.")
        return

    # Group pending by project (1 entry per project, all roads included)
    grouped_pending = _group_pending_by_project(pending_queue)
    log.info("Processing %d unique projects (%d total roads)...",
             len(grouped_pending), len(pending_queue))

    newly_validated = 0
    validated_projectnums = set()

    for group in grouped_pending:
        project_num = group["permit_data"].get("projectnummer")

        if project_num in validated_projectnums or project_num in existing_projectnums:
            continue

        log.info("MATCH: %s (%d roads)", project_num, len(group["roads"]))

        # TODO: Re-enable Inzageloket validation when Anubis bot protection is resolved.
        # The Inzageloket API (omgevingsloketinzage.omgeving.vlaanderen.be) now requires
        # JavaScript-based Proof-of-Work (Anubis v1.25.0), which blocks headless requests.
        # When this is fixed, restore the check_inzageloket() call here to:
        #   1. Confirm the dossier is publicly available on the Inzageloket
        #   2. Fetch the inzage_status (e.g. "Het openbaar onderzoek loopt tot en met DD.MM.YYYY")
        #   3. Only then add to validated_matches
        # For now, all discovered intersections are shown directly without Inzageloket validation.

        group["permit_data"]["inzageloket_link"] = f"https://omgevingsloketinzage.omgeving.vlaanderen.be/{project_num}"
        group["permit_data"]["inzage_status"] = "Nog niet gevalideerd op Inzageloket"

        # Build road geometries list for the inline map
        road_geoms = [r["road_geom"] for r in group["roads"]]
        road_data_list = [r["road_data"] for r in group["roads"]]

        validated_matches.append({
            "match_id": len(validated_matches),
            "municipality": group["municipality"],
            "permit_data": group["permit_data"],
            "road_data": group["roads"][0]["road_data"],
            "road_count": len(group["roads"]),
            "permit_geom": group["permit_geom"],
            "road_geoms": road_geoms,
            "validated_at": datetime.now().isoformat()
        })
        validated_projectnums.add(project_num)
        newly_validated += 1

    save_json(MATCHES_FILE, validated_matches)
    save_json(PENDING_FILE, [])
    log.info("Done. Newly matched: %d", newly_validated)


if __name__ == "__main__":
    main()
