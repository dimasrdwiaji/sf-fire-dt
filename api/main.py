# To run, use: uvicorn api.main:app --reload
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
import geopandas as gpd
from shapely.geometry import Point
import os
import osmnx as ox
import networkx as nx
import time

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FOLDER = os.path.join(BASE_DIR, "data")
STATIONS_FILE = os.path.join(DATA_FOLDER, "fire_stations.geojson")
GRAPH_FILE = os.path.join(DATA_FOLDER, "sf_drive_graph.graphml")

# --- GLOBAL STATE ---
state = {"graph": None, "stations": None, "latest_result": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Loading files in WGS84...")

    # 1. Load Fire Stations
    if os.path.exists(STATIONS_FILE):
        state["stations"] = gpd.read_file(STATIONS_FILE).to_crs(epsg=4326)
        print(f"Stations loaded.")

    # 2. Load Street Network
    if os.path.exists(GRAPH_FILE):
        print(f"Loading street network from {GRAPH_FILE}...")
        G = ox.load_graphml(GRAPH_FILE)

        # --- MANUAL SPEED/TIME CALCULATION ---
        print("Validating edge weights...")
        count = 0
        for u, v, k, data in G.edges(keys=True, data=True):
            count += 1
            length_m = data.get("length", 0)

            # --- FIX: Parse maxspeed string ---
            speed_kph = parse_speed(data.get("maxspeed", None))

            speed_mps = speed_kph / 3.6

            if speed_mps > 0:
                data["travel_time"] = length_m / speed_mps
            else:
                data["travel_time"] = 0

        print(f"✅ Processed {count} edges. Graph ready.")
        state["graph"] = G
    else:
        print(f"Graph file not found at {GRAPH_FILE}")

    yield
    state["graph"] = None


def parse_speed(raw_speed) -> float:
    """
    Parse OSM maxspeed values to km/h.
    Handles: "50 mph", "30", "50 km/h", ["50 mph", "35 mph"], None
    """
    DEFAULT_SPEED_KPH = 40.0  # Default for urban streets

    if raw_speed is None:
        return DEFAULT_SPEED_KPH

    # Handle lists (take first value)
    if isinstance(raw_speed, list):
        raw_speed = raw_speed[0]

    # Already a number
    if isinstance(raw_speed, (int, float)):
        return float(raw_speed)

    # Parse string
    raw_speed = str(raw_speed).strip().lower()

    try:
        # "50 mph" -> convert to km/h
        if "mph" in raw_speed:
            numeric = float(raw_speed.replace("mph", "").strip())
            return numeric * 1.60934

        # "50 km/h" or "50 kph"
        elif "km" in raw_speed or "kph" in raw_speed:
            numeric = float(raw_speed.replace("km/h", "").replace("kph", "").strip())
            return numeric

        # Plain number as string: "50"
        else:
            return float(raw_speed)

    except (ValueError, AttributeError):
        return DEFAULT_SPEED_KPH


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class PointRequest(BaseModel):
    lat: float
    lon: float


@app.post("/calculate-response")
def calculate_response(req: PointRequest):
    G = state["graph"]
    stations = state["stations"]

    if G is None or stations is None:
        raise HTTPException(status_code=503, detail="System not ready")

    # --- 1. FIND NEAREST NODE TO INCIDENT ---
    target_node = ox.distance.nearest_nodes(G, X=req.lon, Y=req.lat)

    # --- 2. FIND NEAREST STATION ---
    req_point = gpd.GeoDataFrame(geometry=[Point(req.lon, req.lat)], crs="EPSG:4326")

    # Project BOTH to meters for accurate nearest calculation
    req_point_proj = req_point.to_crs(epsg=32610)
    stations_proj = stations.to_crs(epsg=32610)

    nearest_result = gpd.sjoin_nearest(
        req_point_proj, stations_proj, distance_col="dist_m"
    ).iloc[0]

    # Get station name and INDEX
    station_name = nearest_result["name"]
    station_idx = nearest_result["index_right"]  # <-- Key fix!

    # --- 3. GET THE ACTUAL STATION GEOMETRY (from original dataframe) ---
    station_geom = stations.loc[station_idx].geometry  # Back in WGS84

    # --- 4. FIND NEAREST NODE TO STATION ---
    station_node = ox.distance.nearest_nodes(G, X=station_geom.x, Y=station_geom.y)

    print(f"DEBUG: Incident node={target_node}, Station node={station_node}")

    # --- 5. CALCULATE ROUTE ---
    try:
        travel_time_seconds = nx.shortest_path_length(
            G, station_node, target_node, weight="travel_time"
        )
        distance_meters = nx.shortest_path_length(
            G, station_node, target_node, weight="length"
        )

        result = {
            "status": "success",
            "incident_loc": [req.lat, req.lon],
            "station_name": station_name,
            "travel_time_min": round(
                travel_time_seconds / 60, 2
            ),  # Numeric for comparisons
            "travel_time_fmt": time.strftime(
                "%M:%S", time.gmtime(travel_time_seconds)
            ),  # Formatted for display
            "distance_km": round(distance_meters / 1000, 2),
        }

        state["latest_result"] = result
        print(f"Calculated: {result}")
        return result

    except nx.NetworkXNoPath:
        raise HTTPException(status_code=400, detail="No driving route found.")
    except Exception as e:
        print(f"Routing Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/widget/latest")
def get_latest_widget_data():
    return state.get("latest_result") or {}
