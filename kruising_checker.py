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

# --- Configuration ---
# BBOX for Maarkedal Region (Lambert 72) - Taken from monitor.js
MINX, MINY, MAXX, MAXY = 77144, 158145, 127271, 200742
# WFS URL for Omgevingsloket
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
    """
    Looks up the municipality (Gemeente) for a given point (EPSG:31370) via VRBG WFS.
    """
    try:
        x, y = point_geom.x, point_geom.y
        # VRBG WFS Filter
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
    """
    Checks if the project is available on the Inzageloket via proxy.
    Returns the project metadata if found, else None.
    """
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
                # The proxy returns a dictionary directly
                return data
    except Exception as e:
        print(f"Error checking Inzageloket for {projectnummer}: {e}")
        
    return None

def load_local_roads(base_dir):
    """
    Loads and combines the local road GeoJSON files.
    """
    print("Loading local road data...")
    path_buurt = os.path.join(base_dir, "buurtwegenoostvlaanderen.geojson")
    path_wijzig = os.path.join(base_dir, "wijzigingenoostvlaanderen.geojson")
    
    gdfs = []
    
    # We filter by bbox immediately to speed up loading if possible, 
    # but gpd.read_file(bbox=) works best with some formats. For GeoJSON it might still parse everything.
    # We use the bbox from config.
    bbox_tuple = (MINX, MINY, MAXX, MAXY)
    
    for p in [path_buurt, path_wijzig]:
        if os.path.exists(p):
            print(f"Reading {p}...")
            try:
                # Read with BBOX filter to optimize
                gdf = gpd.read_file(p, bbox=bbox_tuple)
                if not gdf.empty:
                    # Tag the source
                    source_name = "buurtwegen" if "buurtwegen" in os.path.basename(p) else "wijzigingen"
                    gdf['bron_bestand'] = source_name
                    
                    # Ensure CRS is correct (Source is usually 4326 or 31370, we want 31370 for analysis)
                    if gdf.crs is None:
                        gdf.set_crs("EPSG:4326", inplace=True) # Assuming GeoJSON is 4326 usually
                    
                    gdfs.append(gdf.to_crs("EPSG:31370"))
            except Exception as e:
                print(f"Error reading {p}: {e}")
        else:
            print(f"Warning: File not found: {p}")
            
    if not gdfs:
        raise ValueError("No road data loaded.")
        
    combined_roads = pd.concat(gdfs, ignore_index=True)
    print(f"Total road segments loaded in region: {len(combined_roads)}")
    return combined_roads

def fetch_recent_permits(days=7):
    """
    Fetches permits from the last N days via WFS.
    """
    print(f"Fetching permits from last {days} days...")
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    recente_dossiers = []
    
    for laag in LAGEN_OMGEVING:
        # Construct CQL Filter
        cql = f"BBOX(geom, {MINX}, {MINY}, {MAXX}, {MAXY}) AND datum_indiening >= {cutoff_date}T00:00:00Z"
        
        params = {
            'service': 'WFS', 'version': '1.1.0', 'request': 'GetFeature',
            'typeName': laag, 'outputFormat': 'application/json',
            'srsName': 'EPSG:31370', 'CQL_FILTER': cql
        }
        
        try:
            print(f"Querying layer {laag}...")
            r = requests.get(WFS_URL, params=params, timeout=30)
            
            # Handle possible property name mismatch (geom vs geometry)
            if "Illegal property name: geom" in r.text:
                params['CQL_FILTER'] = params['CQL_FILTER'].replace('geom', 'geometry')
                r = requests.get(WFS_URL, params=params, timeout=30)
                
            if r.status_code == 200:
                try:
                    # Check if empty feature collection
                    if "numberOfFeatures\":0" in r.text.replace(" ", ""):
                        continue
                        
                    temp_gdf = gpd.read_file(StringIO(r.text))
                    if not temp_gdf.empty:
                        # Ensure CRS
                        if temp_gdf.crs is None:
                            temp_gdf.set_crs("EPSG:31370", inplace=True)
                        recente_dossiers.append(temp_gdf)
                except Exception as e:
                    print(f"Error parsing WFS response: {e}")
            else:
                print(f"WFS Request failed: {r.status_code}")
                
        except Exception as e:
            print(f"Connection error: {e}")
            
    if not recente_dossiers:
        print("No matches found in WFS.")
        return gpd.GeoDataFrame()
        
    return pd.concat(recente_dossiers, ignore_index=True)

def clean_data_dict(row, exclude_cols):
    """
    Converts a Series/Row to a serializable dictionary, excluding specific columns.
    """
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
    """
    Generates a Folium map for a permit with one or more road intersections.
    road_geoms: list of road geometries
    road_data_list: list of road data dicts
    """
    # Convert geometries to 4326 for Folium
    permit_geom_4326 = gpd.GeoSeries([permit_geom], crs="EPSG:31370").to_crs("EPSG:4326")[0]
    
    # Calculate center
    center_y = permit_geom_4326.centroid.y
    center_x = permit_geom_4326.centroid.x
    
    m = folium.Map(location=[center_y, center_x], zoom_start=17, tiles='OpenStreetMap')
    
    # --- Road layers removed temporarily due to file loading issues ---
    # The full datasets will be added in a future update

    # --- Historical Layer ---
    folium.WmsTileLayer(
        url="https://geoservices.informatievlaanderen.be/overdrachtdienst/AtlasBuurtwegen/wms",
        layers="AtlasBuurtwegen",
        name="Atlas der Buurtwegen (1841)",
        fmt="image/png",
        transparent=True,
        overlay=True,
        control=True,
        attr="© Digitaal Vlaanderen",
        show=False # Hidden by default, user can toggle
    ).add_to(m)

    # --- Aerial Layer ---
    folium.WmsTileLayer(
        url="https://geo.api.vlaanderen.be/Luchtfoto/wms",
        layers="Luchtfoto",
        name="Luchtfoto (Vlaanderen)",
        fmt="image/png",
        transparent=True,
        overlay=True,
        control=True,
        attr="© Digitaal Vlaanderen",
        show=False
    ).add_to(m)

    # Create HTML tables for popups
    def make_html_table(data, title):
        html = f"<h4>{title}</h4><table style='width:100%; border-collapse: collapse; font-family: sans-serif; font-size: 12px;'>"
        for k, v in data.items():
            val_display = str(v)
            if isinstance(v, str) and v.startswith("http"):
                val_display = f"<a href='{v}' target='_blank' style='color: #00d2ff; font-weight: bold; text-decoration: underline;'>Klik hier</a>"
            html += f"<tr style='border-bottom: 1px solid #ddd;'><td style='padding: 4px; font-weight: bold;'>{k}</td><td style='padding: 4px;'>{val_display}</td></tr>"
        html += "</table>"
        return html

    permit_html = make_html_table(permit_data, "Omgevingsvergunning Data")
    
    # Add Permit (Layer)
    folium.GeoJson(
        data=gpd.GeoSeries([permit_geom_4326]).to_json(),
        name="Omgevingsvergunning",
        style_function=lambda x: {'fillColor': '#ff7800', 'color': '#ff7800', 'fillOpacity': 0.4},
        tooltip="Omgevingsvergunning",
        popup=folium.Popup(permit_html, max_width=400)
    ).add_to(m)
    
    # Add Road Layers (one for each intersecting road)
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

def check_intersections_and_generate_maps(permits_gdf, roads_gdf, output_dir="output_maps"):
    """
    Utility function for external usage (e.g. tests or notebook).
    Computes intersections and generates maps for found matches.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # Ensure CRS
    if permits_gdf.crs != "EPSG:31370":
        permits_gdf = permits_gdf.to_crs("EPSG:31370")
    if roads_gdf.crs != "EPSG:31370":
        roads_gdf = roads_gdf.to_crs("EPSG:31370")
        
    # Buffering logic
    permits_copy = permits_gdf.copy()
    permits_copy['orig_geom'] = permits_copy.geometry
    permits_copy['geometry_buffered'] = permits_copy.geometry.buffer(-1.0)
    valid_permits = permits_copy[~permits_copy.geometry_buffered.is_empty].copy()
    valid_permits = valid_permits.set_geometry('geometry_buffered')
    
    matches = gpd.sjoin(valid_permits, roads_gdf, how="inner", predicate="intersects")
    
    matches_data = []
    count = 0
    for idx, match_row in matches.iterrows():
        permit_row = permits_copy.loc[idx]
        road_idx = match_row['index_right']
        road_row = roads_gdf.loc[road_idx]
        
        permit_dict = clean_data_dict(permit_row, ['geometry', 'orig_geom', 'geometry_buffered'])
        road_dict = clean_data_dict(road_row, ['geometry'])
        
        project_num = permit_row.get('projectnummer')
        if not project_num:
            continue
            
        municipality = lookup_municipality(permit_row.geometry.centroid)
        
        # --- Filter Phase 2: Municipality ---
        if municipality not in ALLOWED_MUNICIPALITIES:
            print(f"Skipping {project_num}: Municipality '{municipality}' not in allowed list.")
            continue
        # --- Filter Phase 3: Inzageloket Validation ---
        inzage_data = check_inzageloket(project_num)
        if not inzage_data:
            print(f"Skipping {project_num}: Not found on Inzageloket.")
            continue
            
        # Add inzageloket link
        permit_dict['inzageloket_link'] = f"https://omgevingsloketinzage.omgeving.vlaanderen.be/{project_num}"
        permit_dict['inzage_status'] = inzage_data.get('toestand', 'Onbekend')

        file_id = str(permit_row.get('referentie_project') or project_num or count).replace('/', '-').replace(':', '')
        filename = f"match_{file_id}.html"
        path = os.path.join(output_dir, filename)
        
        matches_data.append({
            "match_id": count,
            "municipality": municipality,
            "permit_data": permit_dict,
            "road_data": road_dict,
            "map_file": filename
        })
        
        generate_map(permit_row.geometry, road_row.geometry, permit_dict, road_dict, path, context_roads_gdf=roads_gdf)
        count += 1
        
    # Write JSON
    with open(os.path.join(output_dir, "matches.json"), 'w', encoding='utf-8') as f:
        json.dump(matches_data, f, indent=2, default=str)
        
    print(f"Generated {count} matches.")
    return matches_data

def main():
    parser = argparse.ArgumentParser(description="Full Pipeline: Check intersections between WFS Permits and Local Roads.")
    parser.add_argument("--roads-dir", default="buurtwegenomgevingsdossiers-main", help="Directory containing road GeoJSONs")
    parser.add_argument("--output", "-o", default="output_maps", help="Directory to save generated maps")
    parser.add_argument("--days", type=int, default=100, help="Number of days to look back for permits (default: 7)")
    
    args = parser.parse_args()
    
    # 1. Load Databases
    global MATCHES_FILE, PENDING_FILE
    MATCHES_FILE = os.path.join(args.output, "matches.json")
    
    validated_matches = load_json(MATCHES_FILE, [])
    pending_queue = load_json(PENDING_FILE, [])
    
    # helper for duplicates
    existing_projectnums = {m['permit_data'].get('projectnummer') for m in validated_matches if 'permit_data' in m}
    pending_projectnums = {p['permit_data'].get('projectnummer') for p in pending_queue if 'permit_data' in p}

    print(f"Loaded {len(validated_matches)} validated matches and {len(pending_queue)} pending intersections.")

    # 2. Stage 1: Discover New Intersections
    try:
        roads_gdf = load_local_roads(args.roads_dir)
        permits_gdf = fetch_recent_permits(days=args.days)
    except Exception as e:
        print(f"Error in Stage 1 Setup: {e}")
        return

    if not permits_gdf.empty:
        print(f"Checking for new intersections in {len(permits_gdf)} permits...")
        permits_gdf = permits_gdf.copy()
        permits_gdf['orig_geom'] = permits_gdf.geometry
        permits_gdf['geometry_buffered'] = permits_gdf.geometry.buffer(-1.0)
        
        valid_permits_mask = ~permits_gdf.geometry_buffered.is_empty
        if valid_permits_mask.any():
            valid_permits = permits_gdf[valid_permits_mask].copy()
            valid_permits = valid_permits.set_geometry('geometry_buffered')
            
            matches = gpd.sjoin(valid_permits, roads_gdf, how="inner", predicate="intersects")
            
            new_discoveries_count = 0
            for idx, match_row in matches.iterrows():
                permit_row = permits_gdf.loc[idx]
                project_num = permit_row.get('projectnummer')
                
                if not project_num or project_num in existing_projectnums or project_num in pending_projectnums:
                    continue
                    
                centroid = permit_row.geometry.centroid
                municipality = lookup_municipality(centroid)
                
                if municipality not in ALLOWED_MUNICIPALITIES:
                    continue
                
                # New discovery!
                permit_dict = clean_data_dict(permit_row, ['geometry', 'orig_geom', 'geometry_buffered'])
                road_idx = match_row['index_right']
                road_row = roads_gdf.loc[road_idx]
                road_dict = clean_data_dict(road_row, ['geometry'])
                
                pending_queue.append({
                    "municipality": municipality,
                    "permit_data": permit_dict,
                    "road_data": road_dict,
                    "permit_geom_wkt": str(permit_row.geometry),
                    "road_geom_wkt": str(road_row.geometry),
                    "discovered_at": datetime.now().isoformat()
                })
                pending_projectnums.add(project_num)
                new_discoveries_count += 1
            
            if new_discoveries_count > 0:
                print(f"Added {new_discoveries_count} new intersections to the pending queue.")
                save_json(PENDING_FILE, pending_queue)

    # 3. Stage 2: Validate Pending Queue (Deferred Validation)
    if not pending_queue:
        print("No pending intersections to validate.")
        return

    print(f"Stage 2: Validating {len(pending_queue)} pending intersections against Inzageloket...")
    
    still_pending = []
    newly_validated_count = 0
    from shapely import wkt
    
    for item in pending_queue:
        project_num = item['permit_data'].get('projectnummer')
        inzage_data = check_inzageloket(project_num)
        
        if inzage_data:
            print(f"VALIDATED: {project_num} is now live on Inzageloket!")
            
            # Enrich and move to matches
            item['permit_data']['inzageloket_link'] = f"https://omgevingsloketinzage.omgeving.vlaanderen.be/{project_num}"
            item['permit_data']['inzage_status'] = inzage_data.get('toestand', 'Onbekend')
            
            # Reconstruct geometries for map generation
            pg = wkt.loads(item['permit_geom_wkt'])
            rg = wkt.loads(item['road_geom_wkt'])
            
            file_id = str(item['permit_data'].get('referentie_project') or project_num).replace('/', '-').replace(':', '')
            filename = f"match_{file_id}.html"
            path = os.path.join(args.output, filename)
            
            # Generate map
            generate_map(pg, rg, item['permit_data'], item['road_data'], path, context_roads_gdf=roads_gdf)
            
            # Add to matches
            validated_matches.append({
                "match_id": len(validated_matches),
                "municipality": item['municipality'],
                "permit_data": item['permit_data'],
                "road_data": item['road_data'],
                "map_file": filename,
                "validated_at": datetime.now().isoformat()
            })
            newly_validated_count += 1
        else:
            still_pending.append(item)

    # 4. Final Save
    save_json(MATCHES_FILE, validated_matches)
    save_json(PENDING_FILE, still_pending)
    
    print(f"\nProcessing Summary:")
    print(f"- Newly validated: {newly_validated_count}")
    print(f"- Remaining pending: {len(still_pending)}")
    print(f"- Total matches in dashboard: {len(validated_matches)}")

if __name__ == "__main__":
    main()
