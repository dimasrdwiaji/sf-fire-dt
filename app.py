import streamlit as st
import pandas as pd
import geopandas as gpd
import pydeck as pdk
import json
import os
from shapely.geometry import Point
from scripts.sf_fire_data import fetch_and_update
import datetime as dt

# --- CONFIGURATION ---
DATA_FOLDER = "data"
# File paths
INCIDENT_FILE = os.path.join(DATA_FOLDER, "sf_fire_data.json")
BUILDINGS_FILE = os.path.join(DATA_FOLDER, "sf_buildings.gpkg")
STATIONS_FILE = os.path.join(DATA_FOLDER, "fire_stations.geojson")

FETCH_SCRIPT = "sf_fire_data.py"  # Ensure this matches your script name exactly
SF_COORDINATES = [37.7749, -122.4194]  # Initial view, center of San Francisco
MAPBOX_API_KEY = st.secrets["MAPBOX_ACCESS_KEY"]
os.environ["MAPBOX_API_KEY"] = MAPBOX_API_KEY

st.set_page_config(layout="wide", page_title="SF Fire Digital Twin")


# --- LOAD DATA ---
@st.cache_data
# Fire incidents
def load_incidents():
    """
    Loads and cleans the fire incident data.
    """
    if not os.path.exists(INCIDENT_FILE):
        fetch_and_update()

    try:
        with open(INCIDENT_FILE, "r") as f:
            raw_incidents = json.load(f)
        df_inc = pd.DataFrame(raw_incidents)
    except:
        return gpd.GeoDataFrame()

    if df_inc.empty or "point" not in df_inc.columns:
        return gpd.GeoDataFrame()

    # Clean Coordinates
    def get_pt(x):
        if isinstance(x, dict) and "coordinates" in x:
            return x["coordinates"]
        return [None, None]

    coords = df_inc["point"].apply(get_pt)
    df_inc["lon"] = [c[0] for c in coords]
    df_inc["lat"] = [c[1] for c in coords]
    df_inc = df_inc.dropna(subset=["lon", "lat"])

    # --- Convert to Datetime before using .dt accessor ---
    df_inc["incident_date"] = pd.to_datetime(df_inc["incident_date"])

    gdf_incidents = gpd.GeoDataFrame(
        df_inc,
        geometry=[Point(xy) for xy in zip(df_inc.lon, df_inc.lat)],
        crs="EPSG:4326",
    )

    return gdf_incidents


@st.cache_data
# Buildings and stations
def load_data():
    """
    Loads the buildings & stations once and cache it.
    """
    if not os.path.exists(BUILDINGS_FILE):
        st.error(f"Missing {BUILDINGS_FILE}. Please run scripts/get_static_data.py")
        return None, None

    gdf_buildings = gpd.read_file(BUILDINGS_FILE)
    gdf_stations = gpd.read_file(STATIONS_FILE)

    # Pre-calculate coordinates for Pydeck to save time later
    def get_poly_coords(geom):
        if geom.geom_type == "Polygon":
            return list(geom.exterior.coords)
        elif geom.geom_type == "MultiPolygon":
            return list(max(geom.geoms, key=lambda a: a.area).exterior.coords)
        return []

    gdf_buildings["coordinates"] = gdf_buildings.geometry.apply(get_poly_coords)

    # We do this here so we don't re-calculate it every time the slider moves
    station_matches = gpd.sjoin(
        gdf_buildings, gdf_stations, how="inner", predicate="contains"
    )
    station_indices = station_matches.index.unique()

    # Map indices to station names for tooltips
    station_names_map = station_matches.groupby(station_matches.index)["name"].first()

    return gdf_buildings, gdf_stations, station_indices, station_names_map


# --- Dashboard UI ---
st.title("San Francisco Fire Incident Digital Twin")

with st.spinner("Loading 3D City Model..."):
    gdf_buildings, gdf_stations, station_indices, station_names_map = load_data()
    gdf_incidents = load_incidents()

if gdf_buildings is None:
    st.stop()

# Prepare date range for slider
min_date = gdf_incidents["incident_date"].min().date()
max_date = gdf_incidents["incident_date"].max().date()

# Initialize slider values (default to full range). Slider will be put later, below map
if "slider_range" not in st.session_state:
    st.session_state.slider_range = (min_date, max_date)


# --- DATA PROCESSING (Based on slider date) ---
# Filter incidents
date_range = st.session_state.get("slider_range", (min_date, max_date))
mask = (gdf_incidents["incident_date"].dt.date >= date_range[0]) & (
    gdf_incidents["incident_date"].dt.date <= date_range[1]
)
filtered_incidents = gdf_incidents.loc[mask].copy()

# Add string date for tooltip display
filtered_incidents["date_str"] = filtered_incidents["incident_date"].dt.strftime(
    "%Y-%m-%d"
)
# Clean estimated loss
filtered_incidents["estimated_property_loss"] = (
    filtered_incidents["estimated_property_loss"].fillna(0).astype(int)
)

# Identify burnt buildings and fire station. Normal buildings, burnt buildings, and fire stations has different colors
# Identification will be based on spatial join. Building polygon containing the point of fire station and incident will have their indices taken, so they can be marked later.
# Fire station
station_matches = gpd.sjoin(
    gdf_buildings, gdf_stations, how="inner", predicate="contains"
)
station_indices = station_matches.index.unique()

# Burnt buildings
burnt_matches = gpd.sjoin(
    gdf_buildings, filtered_incidents, how="inner", predicate="contains"
)
burnt_indices = burnt_matches.index.unique()

# Visualization
# Fire station: blue, burnt buildings: maroon, normal buildings: grey
# Apply default color
gdf_buildings["fill_color"] = pd.Series(
    [[230, 221, 195]] * len(gdf_buildings), index=gdf_buildings.index
)

# Apply burnt building color
if not burnt_indices.empty:
    gdf_buildings.loc[burnt_indices, "fill_color"] = pd.Series(
        [[128, 0, 0]] * len(burnt_indices), index=burnt_indices
    )

# Apply fire station color
if not station_indices.empty:
    gdf_buildings.loc[station_indices, "fill_color"] = pd.Series(
        [[0, 100, 255]] * len(station_indices), index=station_indices
    )

    # Get station name for the tooltip
    station_names = station_matches.groupby(station_matches.index)["name"].first()
    gdf_buildings.loc[station_indices, "station_name"] = station_names

# Process for pydeck
# Layer 1 Data: Stations (Interactive)
viz_stations_bldgs = gdf_buildings.loc[station_indices].copy()
viz_stations_bldgs = pd.DataFrame(
    viz_stations_bldgs[["coordinates", "fill_color", "height", "station_name"]]
)

# Layer 2 Data: Burnt Buildings (Interactive). Take relevant columns
viz_burnt_bldgs = pd.DataFrame(
    burnt_matches[
        [
            "coordinates",
            "height",
            # Tooltip fields (from incidents)
            "address",
            "date_str",
            "primary_situation",
            "ignition_cause",
            "estimated_property_loss",
        ]
    ]
)
# Set color
viz_burnt_bldgs["fill_color"] = [[128, 0, 0]] * len(viz_burnt_bldgs)

# Layer 3 Data: General
# Exclude station indices to prevent Z-fighting (drawing two buildings on top of each other)
# Combine indices of burnt and fire station buildings
exclude_indices = station_indices.union(burnt_indices)
other_indices = gdf_buildings.index.difference(exclude_indices)
viz_general_bldgs = gdf_buildings.loc[other_indices].copy()
viz_general_bldgs = pd.DataFrame(
    viz_general_bldgs[["coordinates", "fill_color", "height"]]
)


# --- PYDECK MAP ---
view_state = pdk.ViewState(
    latitude=SF_COORDINATES[0],
    longitude=SF_COORDINATES[1],
    zoom=12,
    pitch=50,
    bearing=0,
)

# Layer 1: The City (Polygons)
building_layer = pdk.Layer(
    "PolygonLayer",
    data=viz_general_bldgs,
    get_polygon="coordinates",
    get_fill_color="fill_color",
    get_elevation="height",
    extruded=True,
    pickable=False,
    auto_highlight=True,
    opacity=0.7,
)

# Layer 2: Fire Stations (Blue Dots)
station_layer = pdk.Layer(
    "PolygonLayer",
    data=viz_stations_bldgs,
    get_polygon="coordinates",
    get_fill_color="fill_color",
    get_elevation="height",
    extruded=True,
    pickable=True,  # Active tooltip
    auto_highlight=True,
    opacity=1.0,  # Get popped
)
# Layer 3: Fire Incidents
fire_layer = pdk.Layer(
    "PolygonLayer",
    data=viz_burnt_bldgs,
    get_polygon="coordinates",
    get_fill_color="fill_color",
    get_elevation="height",
    extruded=True,
    pickable=True,  # Active tooltip
    auto_highlight=True,
    opacity=1.0,  # Get popped
)

# Clean tooltip columns
incident_tooltip_cols = [
    "date_str",
    "primary_situation",
    "ignition_cause",
    "estimated_property_loss",
    "address",
]

for col in incident_tooltip_cols:
    viz_stations_bldgs[col] = None

filtered_incidents["station_name"] = None

# Prepare tooltip
tooltip = {
    "html": """
            <b>Station:</b> {station_name}<br/>
            <b>Adress:</b> {address}<br/>
            <b>Incident date:</b> {date_str}<br/>
            <b>Situation:</b> {primary_situation}<br/>
            <b>Ignition cause:</b> {ignition_cause}<br/>
            <b>Estimated loss:</b> ${estimated_property_loss}
            """,
    "style": {
        "backgroundColor": "black",
        "color": "white",
        "fontSize": "12px",
    },
}

# Render
st.pydeck_chart(
    pdk.Deck(
        map_style="mapbox://styles/mapbox/streets-v12",
        initial_view_state=view_state,
        layers=[building_layer, station_layer, fire_layer],
        # Show tooltips for stations and burnt buildings
        tooltip=tooltip,  # type: ignore
    )
)

# Incident Date Slider (Below title, controlling the map)
selected_date = st.slider(
    "Filter Incidents by Date",
    min_value=min_date,
    max_value=max_date,
    value=(min_date, max_date),
    key="slider_range",
)

# Divider
st.markdown("---")

# Simple metrics
st.subheader("Fire metrics")
col1, col2 = st.columns([1, 2])  # 1/3 width for metrics, 2/3 for chart

with col1:
    # Metric: Total Loss
    total_loss = filtered_incidents["estimated_property_loss"].sum()
    incident_count = len(filtered_incidents)

    st.metric(label="Total Est. Property Loss", value=f"${total_loss:,.0f}")
    st.metric(label="Total Incidents", value=incident_count)

with col2:
    # Chart: Top Ignition Causes
    if not filtered_incidents.empty:
        st.markdown("**Top Ignition Causes**")
        # Count causes
        cause_counts = filtered_incidents["ignition_cause"].value_counts().head(5)
        # Display as simple bar chart
        st.bar_chart(
            cause_counts,
            x_label="Ignition cause",
            y_label="Count",
            sort="-count",
            height="stretch",
            width="content",
            color="#800000",
        )  # Maroon color to match map
    else:
        st.info("No incidents in selected range.")

# Raw Data Viewer
with st.expander("Fire Incident Raw Data"):
    if not viz_burnt_bldgs.empty:
        st.dataframe(
            viz_burnt_bldgs.rename(
                columns={
                    "date_str": "Incident Date",
                    "primary_situation": "Situation",
                    "ignition_cause": "Ignition Cause",
                    "estimated_property_loss": "Estimated Property Loss ($)",
                }
            ).head(10)
        )
