import os
import osmnx as ox
import geopandas as gpd
import pandas as pd
import random
import warnings

# Suppress warnings about projection
warnings.filterwarnings("ignore")

# ----------------------------
# CONFIGURATION
# ----------------------------
DATA_FOLDER = "data"
PLACE_NAME = "San Francisco, California, USA"


# ----------------------------
# Processing height for buildings (fetched OSM data is 2D)
# ----------------------------
def process_height(row):
    """
    Get building height for 3D visualization.
    Prioritizes real data. If unavailable, then estimates based on floors, then defaults.
    """
    # 1. Use actual height if available
    if "height" in row and pd.notnull(row["height"]):
        try:
            return float(str(row["height"]).replace("m", ""))
        except:
            pass

    # 2. Estimate from levels (approx 3.5m per floor)
    if "building:levels" in row and pd.notnull(row["building:levels"]):
        try:
            return float(row["building:levels"]) * 3.5
        except:
            pass

    # 3. Fallback: Assign random height based on 'building type'
    # High-rises are usually 'commercial' or 'apartments'
    b_type = row.get("building", "yes")
    if b_type in ["office", "commercial", "hotel", "apartments", "high_rise"]:
        return random.uniform(20, 100)
    # If they're not commercial, then randomize the height between 10-20m
    elif b_type in ["warehouse", "industrial"]:
        return random.uniform(10, 20)
    # For the rest, assume they're houses.
    else:
        # Standard residential house
        return random.uniform(6, 12)


# ----------------------------
# Fetch building and road network
# ----------------------------
def fetch_static_data():
    if not os.path.exists(DATA_FOLDER):
        os.makedirs(DATA_FOLDER)

    print(f"Area: {PLACE_NAME}")

    # 1. Road network
    # We download the 'drive' network for the whole city.
    print("Downloading drivable road network...")
    try:
        G = ox.graph_from_place(PLACE_NAME, network_type="drive")
        # Add speeds/times for routing later
        G = ox.add_edge_speeds(G)
        G = ox.add_edge_travel_times(G)

        save_path = os.path.join(DATA_FOLDER, "sf_drive_graph.graphml")
        ox.save_graphml(G, save_path)
        print(f"Saved as graph to {save_path}")
    except Exception as e:
        print(f"[ERROR] Road fetch failed: {e}")

    # 2. Fire stations
    print("Downloading Fire Stations...")
    try:
        tags_stations = {"amenity": "fire_station"}
        stations = ox.features_from_place(PLACE_NAME, tags=tags_stations)

        # Convert Polygons to Points (centroids)
        stations["geometry"] = stations.geometry.apply(
            lambda geom: geom.centroid if geom.geom_type == "Polygon" else geom
        )

        # Keep only relevant columns
        cols_to_keep = ["name", "geometry", "amenity"]
        stations = stations[[c for c in cols_to_keep if c in stations.columns]]

        # Save as standard GeoJSON (small file, so JSON is fine)
        stations.to_file(
            os.path.join(DATA_FOLDER, "fire_stations.geojson"), driver="GeoJSON"
        )
        print(f"Saved {len(stations)} stations.")
    except Exception as e:
        print(f"[ERROR] Station fetch failed: {e}")

    # 3. Buildings
    print("Downloading Buildings...")
    try:
        tags_buildings = {"building": True}
        buildings = ox.features_from_place(PLACE_NAME, tags=tags_buildings)

        # Filter: Only Polygons
        buildings = buildings[buildings.geometry.type == "Polygon"]

        # Optimization: Drop tiny buildings (< 20 sq meters, likely a guard post)
        # We need to project to meters to calculate area, then project back
        print("Filtering tiny structures...")
        proj_buildings = buildings.to_crs(buildings.estimate_utm_crs())
        mask = proj_buildings.area > 20
        buildings = buildings.loc[mask]

        print("Calculating 3D heights...")
        buildings["height"] = buildings.apply(process_height, axis=1)

        # Clean columns to reduce file size
        final_buildings = buildings[["geometry", "height", "building"]]

        # Save as GeoPackage (Faster I/O than GeoJSON for 100k+ rows)
        save_path = os.path.join(DATA_FOLDER, "sf_buildings.gpkg")
        final_buildings.to_file(save_path, driver="GPKG")
        print(f"Saved {len(final_buildings)} buildings to {save_path}")

    except Exception as e:
        print(f"[ERROR] Building fetch failed: {e}")

    print("All data acquired.")


if __name__ == "__main__":
    fetch_static_data()
