import geopandas as gpd
from shapely.geometry import Polygon, LineString, Point
import kruising_checker
import os

def run_test():
    print("Creating dummy data...")
    
    # 1. Create Dummy Permits (Polygons)
    # A square polygon
    p1 = Polygon([(4.35, 50.84), (4.36, 50.84), (4.36, 50.85), (4.35, 50.85), (4.35, 50.84)])
    # Another one far away
    p2 = Polygon([(3.0, 51.0), (3.1, 51.0), (3.1, 51.1), (3.0, 51.1), (3.0, 51.0)])
    
    permits_gdf = gpd.GeoDataFrame(
        {'projectnummer': ['P001_Match', 'P002_NoMatch'], 'status': ['Open', 'Closed']},
        geometry=[p1, p2],
        crs="EPSG:4326"
    )
    
    # 2. Create Dummy Roads (Lines)
    # A line crossing p1
    r1 = LineString([(4.34, 50.845), (4.37, 50.845)])
    # A line far away
    r2 = LineString([(5.0, 50.0), (5.1, 50.0)])
    
    roads_gdf = gpd.GeoDataFrame(
        {'road_id': ['R001', 'R002'], 'road_name': ['Main St', 'Side St']},
        geometry=[r1, r2],
        crs="EPSG:4326"
    )
    
    print("Permits:\n", permits_gdf)
    print("Roads:\n", roads_gdf)
    
    # 3. Run the Checker
    output_dir = "output_maps"
    kruising_checker.check_intersections_and_generate_maps(permits_gdf, roads_gdf, output_dir=output_dir)
    
    # 4. Verify Output
    expected_file = os.path.join(output_dir, "match_P001_Match.html")
    if os.path.exists(expected_file):
        print(f"SUCCESS: Map generated at {expected_file}")
    else:
        print(f"FAILURE: Expected map {expected_file} not found.")

if __name__ == "__main__":
    run_test()
