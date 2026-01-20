import streamlit as st
import pandas as pd
import geopandas as gpd
import pydeck as pdk
import json
import os
import sys
from shapely.geometry import Point
from scripts.sf_fire_data import fetch_and_update

# --- CONFIGURATION ---
DATA_FOLDER = "data"
FILENAME = "sf_fire_data.json"
DATA_PATH = os.path.join(DATA_FOLDER, FILENAME)
FETCH_SCRIPT = "sf_fire_data.py"  # Ensure this matches your script name exactly
SF_COORDINATES = [37.7749, -122.4194]
MAPBOX_API_KEY = st.secrets["MAPBOX_ACCESS_KEY"]
os.environ["MAPBOX_API_KEY"] = MAPBOX_API_KEY

st.set_page_config(layout="wide", page_title="SF Fire Digital Twin")


# --- 1. DATA MANAGEMENT ---
def ensure_data_exists():
    """
    Checks if data exists. If not, imports the script function to fetch it.
    """
    if not os.path.exists(DATA_PATH):
        with st.spinner("Data missing. Fetching latest records from API..."):
            try:
                # Direct function call - much cleaner!
                success = fetch_and_update()

                if success:
                    st.success("Data fetched successfully!")
                else:
                    st.error("Fetch function returned False/None.")
                    st.stop()

            except Exception as e:
                st.error(f"Error during data fetch: {e}")
                st.stop()


# --- 2. DATA LOADER ---
@st.cache_data
def load_and_process_data():
    ensure_data_exists()

    # Load JSON
    try:
        with open(DATA_PATH, "r") as f:
            raw_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st.error("Data file is corrupt or empty. Please delete it and refresh.")
        return gpd.GeoDataFrame()

    if not raw_data:
        return gpd.GeoDataFrame()

    df = pd.DataFrame(raw_data)

    # Clean & Extract Coordinates
    # Socrata 'point' field: {'type': 'Point', 'coordinates': [lon, lat]}
    def extract_coords(point_data):
        if isinstance(point_data, dict) and "coordinates" in point_data:
            return (
                point_data["coordinates"][0],
                point_data["coordinates"][1],
            )  # Lon, Lat
        return None, None

    if "point" in df.columns:
        df["lon"], df["lat"] = zip(*df["point"].apply(extract_coords))
        df = df.dropna(subset=["lon", "lat"])
    else:
        return gpd.GeoDataFrame()

    # Handle Column Name (estimated_property_loss)
    loss_col = "estimated_property_loss"

    # Check if column exists, if not create it with 0s to prevent crash
    if loss_col not in df.columns:
        df[loss_col] = 0

    # Clean numeric data
    df[loss_col] = pd.to_numeric(df[loss_col], errors="coerce").fillna(0)

    # Create Geometry
    geometry = [Point(xy) for xy in zip(df.lon, df.lat)]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

    return gdf


# --- 3. DASHBOARD UI ---
st.title("San Francisco Fire Incident Digital Twin")
st.markdown("Historical incident data, updated daily with fire incident simulation.")

# Load Data
gdf = load_and_process_data()

# Sidebar Stats
st.sidebar.header("Situation Report")

if not gdf.empty:
    st.sidebar.metric("Total Incidents", len(gdf))

    # Calculate Total Loss using the correct column
    total_loss = gdf["estimated_property_loss"].sum()
    st.sidebar.metric("Est. Property Loss Since Last Year", f"${total_loss:,.0f}")

    # Date Filter
    # Ensure date column is datetime
    if "incident_date" in gdf.columns:
        gdf["incident_date"] = pd.to_datetime(gdf["incident_date"])
        min_date = gdf["incident_date"].min().date()
        max_date = gdf["incident_date"].max().date()

        selected_date = st.sidebar.slider(
            "Filter by Date",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )

        # Apply Filter
        mask = (gdf["incident_date"].dt.date >= selected_date[0]) & (
            gdf["incident_date"].dt.date <= selected_date[1]
        )
        filtered_gdf = gdf.loc[mask]
    else:
        filtered_gdf = gdf
else:
    st.sidebar.warning("No data available.")
    filtered_gdf = gdf

# --- 4. MAP VISUALIZATION ---
view_state = pdk.ViewState(
    latitude=SF_COORDINATES[0],
    longitude=SF_COORDINATES[1],
    zoom=12,
    pitch=45,
    bearing=0,
)

if not filtered_gdf.empty:
    map_data = filtered_gdf.copy()

    # 1. Convert Timestamp to String (for Tooltip)
    map_data["incident_date"] = map_data["incident_date"].dt.strftime("%Y-%m-%d")

    # 2. Drop the 'geometry' column (we use lat/lon columns instead)
    #    and drop 'point' if it exists (it's a dictionary, usually fine, but safer to drop)
    cols_to_drop = ["geometry", "point"]
    map_data = map_data.drop(columns=[c for c in cols_to_drop if c in map_data.columns])
else:
    map_data = []

# Fire Incident Layer
fire_layer = pdk.Layer(
    "ScatterplotLayer",
    data=map_data,
    get_position="[lon, lat]",
    get_color="[200, 30, 0, 160]",
    get_radius=100,
    pickable=True,
    auto_highlight=True,
)

st.pydeck_chart(
    pdk.Deck(
        map_style="mapbox://styles/mapbox/streets-v12",
        initial_view_state=view_state,
        layers=[fire_layer],
        tooltip={
            "html": "<b>Date:</b> {incident_date}<br/><b>Cause:</b> {ignition_cause}<br/><b>Loss:</b> ${estimated_property_loss}",
            "style": {"backgroundColor": "steelblue", "color": "white"},
        },
    )
)

# Raw Data Expander
with st.expander("View Raw Data"):
    if not filtered_gdf.empty:
        # Drop geometry for cleaner table display
        st.dataframe(filtered_gdf.drop(columns="geometry").head(10))
