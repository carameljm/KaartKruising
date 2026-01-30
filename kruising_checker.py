import geopandas as gpd
import pandas as pd
import requests
import folium
import os
import argparse
import json
from io import StringIO
from datetime import datetime, timedelta
from shapely.geometry import box
from shapely import wkt

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

PENDING_FILE = "pending_intersections.json"
MATCHES_FILE = "output_maps/matches.json"

def load_json(path, default=None):
    if default is None: default = []
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {path}: {e}")
    return default

def save_json(path, data):
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"Error saving {path}: {e}")

def lookup_municipality(point_geom):
    try:
        x, y = point_geom.x, point_geom.y
        cql = f"INTERSECTS(SHAPE, POINT({x} {y}))"
        params = {
            'service': 'WFS', 'version': '1.1.0', 'request': 'GetFeature',
            'typeName': 'VRBG:Refgem', 'outputFormat': 'application/json',
            'srsName': 'EPSG:31370', 'CQL_FILTER': cql,
            'propertyName': 'NAAM', 'maxFeatures': '1'
        }
        r = requests.get(VRBG_URL, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            features = data.get('features', [])
            if features:
                return features[0]['properties'].get('NAAM', 'Onbekend')
    except Exception as e:
        print(f"Error looking up municipality: {e}")
    return "Onbekend"

def check_inzageloket(projectnummer):
    try:
        url = "https://omgevingsloketinzage.omgeving.vlaanderen.be/proxy-omv-up/rs/v1/inzage/projecten/header"
        params = {"projectnummer": projectnummer}
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://omgevingsloketinzage.omgeving.vlaanderen.be/{projectnummer}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data and 'uuid' in data:
                return data
    except Exception as e:
        print(f"Error checking Inzageloket for {projectnummer}: {e}")
    return None

def load_local_roads(base_dir):
    print("Loading local road data...")
    path_buurt = os.path.join(base_dir, "buurtwegenoostvlaanderen.geojson")
    path_wijzig = os.path.join(base_dir, "wijzigingenoostvlaanderen.geojson")
    gdfs = []
    bbox_tuple = (MINX, MINY, MAXX, MAXY)
    for p in [path_buurt, path_wijzig]:
        if os.path.exists(p):
            print(f"Reading {p}...")
            try:
                gdf = gpd.read_file(p, bbox=bbox_tuple)
                if not gdf.empty:
                    source_name = "buurtwegen" if "buurtwegen" in os.path.basename(p) else "wijzigingen"
                    gdf['bron_bestand'] = source_name
                    if gdf.crs is None:
                        gdf.set_crs("EPSG:4326", inplace=True)
                    gdfs.append(gdf.to_crs("EPSG:31370"))
            except Exception as e:
                print(f"Error reading {p}: {e}")
    if not gdfs:
        raise ValueError("No road data loaded.")
    combined_roads = pd.concat(gdfs, ignore_index=True)
    print(f"Total road segments loaded in region: {len(combined_roads)}")
    return combined_roads

def fetch_recent_permits(days=7):
    print(f"Fetching permits from last {days} days...")
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    recente_dossiers = []
    for laag in LAGEN_OMGEVING:
        cql = f"BBOX(geom, {MINX}, {MINY}, {MAXX}, {MAXY}) AND datum_indiening >= {cutoff_date}T00:00:00Z"
        params = {
            'service': 'WFS', 'version': '1.1.0', 'request': 'GetFeature',
            'typeName': laag, 'outputFormat': 'application/json',
            'srsName': 'EPSG:31370', 'CQL_FILTER': cql
        }
        try:
            print(f"Querying layer {laag}...")
            r = requests.get(WFS_URL, params=params, timeout=30)
            if "Illegal property name: geom" in r.text:
                params['CQL_FILTER'] = params['CQL_FILTER'].replace('geom', 'geometry')
                r = requests.get(WFS_URL, params=params, timeout=30)
            if r.status_code == 200:
                if "numberOfFeatures\":0" in r.text.replace(" ", ""):
                    continue
                temp_gdf = gpd.read_file(StringIO(r.text))
                if not temp_gdf.empty:
                    if temp_gdf.crs is None:
                        temp_gdf.set_crs("EPSG:31370", inplace=True)
                    recente_dossiers.append(temp_gdf)
        except Exception as e:
            print(f"Connection error: {e}")
    if not recente_dossiers:
        return gpd.GeoDataFrame()
    return pd.concat(recente_dossiers, ignore_index=True)

def clean_data_dict(row, exclude_cols):
    d = row.drop(labels=[c for c in row.index if c in exclude_cols], errors='ignore').to_dict()
    new_d = {}
    for k, v in d.items():
        if isinstance(v, (pd.Timestamp, datetime)):
            new_d[k] = v.isoformat()
        elif pd.isna(v):
            new_d[k] = None
        else:
            new_d[k] = v
    return new_d

def generate_map(permit_geom, road_geoms, permit_data, road_data_list, output_path, context_roads_gdf=None):
    # Convert geometries to 4326 for Folium
    permit_geom_4326 = gpd.GeoSeries([permit_geom], crs="EPSG:31370").to_crs("EPSG:4326")[0]
    center_y, center_x = permit_geom_4326.centroid.y, permit_geom_4326.centroid.x
    
    m = folium.Map(location=[center_y, center_x], zoom_start=17, tiles='OpenStreetMap')

    # Add Tile Layers
    folium.WmsTileLayer(
        url="https://geoservices.informatievlaanderen.be/overdrachtdienst/AtlasBuurtwegen/wms",
        layers="AtlasBuurtwegen", name="Atlas der Buurtwegen (1841)", fmt="image/png",
        transparent=True, overlay=True, control=True, attr="© Digitaal Vlaanderen", show=False
    ).add_to(m)

    folium.WmsTileLayer(
        url="https://geo.api.vlaanderen.be/Luchtfoto/wms",
        layers="Luchtfoto", name="Luchtfoto (Vlaanderen)", fmt="image/png",
        transparent=True, overlay=True, control=True, attr="© Digitaal Vlaanderen", show=False
    ).add_to(m)

    def make_html_table(data, title):
        html = f"<h4>{title}</h4><table style='width:100%; border-collapse: collapse; font-family: sans-serif; font-size: 12px;'>"
        for k, v in data.items():
            val_display = f"<a href='{v}' target='_blank'>Klik hier</a>" if isinstance(v, str) and v.startswith("http") else str(v)
            html += f"<tr style='border-bottom: 1px solid #ddd;'><td style='padding: 4px; font-weight: bold;'>{k}</td><td style='padding: 4px;'>{val_display}</td></tr>"
        return html + "</table>"

    # Add Permit Layer
    permit_html = make_html_table(permit_data, "Omgevingsvergunning Data")
    folium.GeoJson(
        data=gpd.GeoSeries([permit_geom_4326]).to_json(),
        name="Omgevingsvergunning",
        style_function=lambda x: {'fillColor': '#ff7800', 'color': '#ff7800', 'fillOpacity': 0.4},
        tooltip="Omgevingsvergunning",
        popup=folium.Popup(permit_html, max_width=400)
    ).add_to(m)

    # --- GEOMETRY FIX START ---
    # Handle MultiLineString iteration error
    if hasattr(road_geoms, 'geom_type') and road_geoms.geom_type == 'MultiLineString':
        road_geoms = list(road_geoms.geoms)
    elif not isinstance(road_geoms, list):
        # If it's a single LineString or other geometry, wrap it in a list
        road_geoms = [road_geoms]

    # Ensure road_data_list is actually a list to match road_geoms
    if not isinstance(road_data_list, list):
        road_data_list = [road_data_list]
    # --- GEOMETRY FIX END ---

    # Add Road Layers
    for idx, (road_geom, road_data) in enumerate(zip(road_geoms, road_data_list)):
        road_geom_4326 = gpd.GeoSeries([road_geom], crs="EPSG:31370").to_crs("EPSG:4326")[0]
        road_html = make_html_table(road_data, f"Buurtweg {idx+1} (Match)")
        road_name = f"Buurtweg {idx+1}: {road_data.get('DETAILPLAN', 'onbekend')} nr {road_data.get('NR', '?')}"
        
        folium.GeoJson(
            data=gpd.GeoSeries([road_geom_4326]).to_json(),
            name=road_name,
            style_function=lambda x, i=idx: {'color': ['blue', 'purple', 'green', 'orange', 'red'][i % 5], 'weight': 5},
            tooltip=road_name,
            popup=folium.Popup(road_html, max_width=400)
        ).add_to(m)
    
    folium.LayerControl().add_to(m)
    m.save(output_path)

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
    
    existing_projectnums = {m['permit_data'].get('projectnummer') for m in validated_matches if 'permit_data' in m}
    pending_projectnums = {p['permit_data'].get('projectnummer') for p in pending_queue if 'permit_data' in p}

    print(f"Loaded {len(validated_matches)} validated matches and {len(pending_queue)} pending.")

    try:
        roads_gdf = load_local_roads(args.roads_dir)
        permits_gdf = fetch_recent_permits(days=args.days)
    except Exception as e:
        print(f"Error: {e}")
        return

    if not permits_gdf.empty:
        permits_gdf = permits_gdf.copy()
        permits_gdf['geometry_buffered'] = permits_gdf.geometry.buffer(-1.0)
        valid_permits = permits_gdf[~permits_gdf.geometry_buffered.is_empty].copy().set_geometry('geometry_buffered')
        
        matches = gpd.sjoin(valid_permits, roads_gdf, how="inner", predicate="intersects")
        new_count = 0
        for idx, match_row in matches.iterrows():
            permit_row = permits_gdf.loc[idx]
            project_num = permit_row.get('projectnummer')
            if not project_num or project_num in existing_projectnums or project_num in pending_projectnums:
                continue
            
            municipality = lookup_municipality(permit_row.geometry.centroid)
            if municipality in ALLOWED_MUNICIPALITIES:
                road_row = roads_gdf.loc[match_row['index_right']]
                pending_queue.append({
                    "municipality": municipality,
                    "permit_data": clean_data_dict(permit_row, ['geometry', 'geometry_buffered']),
                    "road_data": clean_data_dict(road_row, ['geometry']),
                    "permit_geom_wkt": str(permit_row.geometry),
                    "road_geom_wkt": str(road_row.geometry),
                    "discovered_at": datetime.now().isoformat()
                })
                pending_projectnums.add(project_num)
                new_count += 1
        
        if new_count > 0:
            print(f"Added {new_count} new intersections.")
            save_json(PENDING_FILE, pending_queue)

    if not pending_queue:
        return

    print(f"Validating {len(pending_queue)} pending against Inzageloket...")
    still_pending, newly_validated = [], 0
    
    for item in pending_queue:
        project_num = item['permit_data'].get('projectnummer')
        inzage_data = check_inzageloket(project_num)
        
        if inzage_data:
            print(f"VALIDATED: {project_num}")
            item['permit_data']['inzageloket_link'] = f"https://omgevingsloketinzage.omgeving.vlaanderen.be/{project_num}"
            item['permit_data']['inzage_status'] = inzage_data.get('toestand', 'Onbekend')
            
            pg = wkt.loads(item['permit_geom_wkt'])
            rg = wkt.loads(item['road_geom_wkt'])
            file_id = str(item['permit_data'].get('referentie_project') or project_num).replace('/', '-').replace(':', '')
            filename = f"match_{file_id}.html"
            
            generate_map(pg, rg, item['permit_data'], item['road_data'], os.path.join(args.output, filename))
            
            validated_matches.append({
                "match_id": len(validated_matches),
                "municipality": item['municipality'],
                "permit_data": item['permit_data'],
                "road_data": item['road_data'],
                "map_file": filename,
                "validated_at": datetime.now().isoformat()
            })
            newly_validated += 1
        else:
            still_pending.append(item)

    save_json(MATCHES_FILE, validated_matches)
    save_json(PENDING_FILE, still_pending)
    print(f"Done. Newly validated: {newly_validated}")

if __name__ == "__main__":
    main()
