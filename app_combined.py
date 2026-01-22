import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import geopandas as gpd
import json
import os
import requests
from shapely.geometry import Point
from scripts.sf_fire_data import fetch_and_update

# --- CONFIGURATION ---
DATA_FOLDER = "data"
INCIDENT_FILE = os.path.join(DATA_FOLDER, "sf_fire_data.json")
BUILDINGS_FILE = os.path.join(DATA_FOLDER, "sf_buildings.gpkg")
STATIONS_FILE = os.path.join(DATA_FOLDER, "fire_stations.geojson")
SF_COORDINATES = [-122.4194, 37.7749]
API_URL = "http://127.0.0.1:8000"

MAPBOX_KEY = st.secrets["MAPBOX_ACCESS_KEY"]

st.set_page_config(layout="wide", page_title="SF Fire Digital Twin")


# --- LOAD DATA ---
@st.cache_data
def load_incidents():
    """Load and clean fire incident data."""
    if not os.path.exists(INCIDENT_FILE):
        fetch_and_update()

    try:
        with open(INCIDENT_FILE, "r") as f:
            raw_incidents = json.load(f)
        df_inc = pd.DataFrame(raw_incidents)
    except Exception:
        return gpd.GeoDataFrame()

    if df_inc.empty or "point" not in df_inc.columns:
        return gpd.GeoDataFrame()

    def get_pt(x):
        if isinstance(x, dict) and "coordinates" in x:
            return x["coordinates"]
        return [None, None]

    coords = df_inc["point"].apply(get_pt)
    df_inc["lon"] = [c[0] for c in coords]
    df_inc["lat"] = [c[1] for c in coords]
    df_inc = df_inc.dropna(subset=["lon", "lat"])
    df_inc["incident_date"] = pd.to_datetime(df_inc["incident_date"])

    gdf_incidents = gpd.GeoDataFrame(
        df_inc,
        geometry=[Point(xy) for xy in zip(df_inc.lon, df_inc.lat)],
        crs="EPSG:4326",
    )
    return gdf_incidents


@st.cache_data
def load_buildings_and_stations():
    """Load buildings and stations data."""
    if not os.path.exists(BUILDINGS_FILE):
        st.error(f"Missing {BUILDINGS_FILE}. Please run scripts/get_static_data.py")
        return None, None

    gdf_buildings = gpd.read_file(BUILDINGS_FILE)
    gdf_stations = gpd.read_file(STATIONS_FILE)

    return gdf_buildings, gdf_stations


@st.cache_data
def prepare_geojson_data(
    _gdf_buildings, _gdf_stations, _gdf_incidents, date_start, date_end
):
    """Prepare GeoJSON for Mapbox GL JS."""
    # Filter incidents by date
    mask = (_gdf_incidents["incident_date"].dt.date >= date_start) & (
        _gdf_incidents["incident_date"].dt.date <= date_end
    )
    filtered_incidents = _gdf_incidents.loc[mask].copy()

    # Spatial joins
    station_matches = gpd.sjoin(
        _gdf_buildings, _gdf_stations, how="inner", predicate="contains"
    )
    station_indices = set(station_matches.index.unique())
    station_names_map = (
        station_matches.groupby(station_matches.index)["name"].first().to_dict()
    )

    burnt_matches = gpd.sjoin(
        _gdf_buildings, filtered_incidents, how="inner", predicate="contains"
    )
    burnt_indices = set(burnt_matches.index.unique())

    # Build incident info map for tooltips
    incident_info = {}
    for idx in burnt_matches.index.unique():
        match_data = burnt_matches.loc[
            [idx] if burnt_matches.index.get_loc(idx).__class__ == int else idx
        ]
        if len(match_data) > 0:
            row = match_data.iloc[0]
            # Handle NaN and mixed type values safely
            loss_val = row.get("estimated_property_loss", 0)
            try:
                loss_val = float(loss_val) if not pd.isna(loss_val) else 0
            except (ValueError, TypeError):
                loss_val = 0
            incident_info[idx] = {
                "address": str(row.get("address", "Unknown")),
                "date": str(row.get("incident_date", ""))[:10],
                "situation": str(row.get("primary_situation", "Unknown")),
                "cause": str(row.get("ignition_cause", "Unknown")),
                "loss": int(loss_val),
            }

    # Build GeoJSON features
    features = []
    for idx, row in _gdf_buildings.iterrows():
        geom = row.geometry
        height = row.get("height", 10)
        if pd.isna(height) or height is None:
            height = 10

        # Determine building type and color
        if idx in station_indices:
            btype = "station"
            color = "#0064FF"
            station_name = station_names_map.get(idx, "Unknown Station")
            props = {
                "height": height,
                "type": btype,
                "color": color,
                "station_name": station_name,
            }
        elif idx in burnt_indices:
            btype = "burnt"
            color = "#800000"
            info = incident_info.get(idx, {})
            props = {
                "height": height,
                "type": btype,
                "color": color,
                "address": info.get("address", ""),
                "date": info.get("date", ""),
                "situation": info.get("situation", ""),
                "cause": info.get("cause", ""),
                "loss": info.get("loss", 0),
            }
        else:
            btype = "normal"
            color = "#E6DDC3"
            props = {"height": height, "type": btype, "color": color}

        # Convert geometry to GeoJSON
        if geom.geom_type == "Polygon":
            coords = [list(geom.exterior.coords)]
        elif geom.geom_type == "MultiPolygon":
            largest = max(geom.geoms, key=lambda a: a.area)
            coords = [list(largest.exterior.coords)]
        else:
            continue

        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {"type": "Polygon", "coordinates": coords},
            }
        )

    geojson = {"type": "FeatureCollection", "features": features}

    # Calculate metrics - ensure numeric conversion
    loss_series = pd.to_numeric(
        filtered_incidents["estimated_property_loss"], errors="coerce"
    ).fillna(0)
    total_loss = loss_series.sum()
    incident_count = len(filtered_incidents)

    # Top causes
    cause_counts = filtered_incidents["ignition_cause"].value_counts().head(5).to_dict()

    return geojson, total_loss, incident_count, cause_counts


# --- MAIN UI ---
st.title("San Francisco Fire Digital Twin")

with st.spinner("Loading data..."):
    gdf_buildings, gdf_stations = load_buildings_and_stations()
    gdf_incidents = load_incidents()

if gdf_buildings is None:
    st.stop()

# Date range
min_date = gdf_incidents["incident_date"].min().date()
max_date = gdf_incidents["incident_date"].max().date()

if "slider_range" not in st.session_state:
    st.session_state.slider_range = (min_date, max_date)

# Get current slider values
date_range = st.session_state.slider_range

# Prepare data
geojson_data, total_loss, incident_count, cause_counts = prepare_geojson_data(
    gdf_buildings, gdf_stations, gdf_incidents, date_range[0], date_range[1]
)

# Escape JSON for JS
geojson_str = json.dumps(geojson_data).replace("'", "\\'").replace("</", "<\\/")

# --- MAPBOX GL JS MAP ---
map_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no">
    <link href="https://api.mapbox.com/mapbox-gl-js/v2.15.0/mapbox-gl.css" rel="stylesheet">
    <script src="https://api.mapbox.com/mapbox-gl-js/v2.15.0/mapbox-gl.js"></script>
    <link rel="stylesheet" href="https://api.mapbox.com/mapbox-gl-js/plugins/mapbox-gl-draw/v1.4.0/mapbox-gl-draw.css" type="text/css">
    <script src="https://api.mapbox.com/mapbox-gl-js/plugins/mapbox-gl-draw/v1.4.0/mapbox-gl-draw.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, sans-serif; }}
        #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
        .mapboxgl-popup-content {{
            background: rgba(20, 20, 24, 0.95);
            backdrop-filter: blur(12px);
            color: #fff;
            padding: 16px 20px;
            border-radius: 8px;
            font-size: 13px;
            line-height: 1.5;
            border: 1px solid rgba(255,255,255,0.1);
            box-shadow: 0 8px 32px rgba(0,0,0,0.4);
            max-width: 280px;
        }}
        .mapboxgl-popup-close-button {{
            color: #888;
            font-size: 18px;
            padding: 4px 8px;
        }}
        .mapboxgl-popup-close-button:hover {{ color: #fff; }}
        .popup-title {{
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 1px solid rgba(255,255,255,0.15);
        }}
        .popup-title.station {{ color: #4A9EFF; }}
        .popup-title.incident {{ color: #FF6B6B; }}
        .popup-row {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 6px;
        }}
        .popup-label {{ color: #888; font-size: 12px; }}
        .popup-value {{ color: #fff; font-weight: 500; text-align: right; max-width: 160px; }}
        .legend {{
            position: absolute;
            bottom: 24px;
            left: 24px;
            background: rgba(20, 20, 24, 0.9);
            backdrop-filter: blur(12px);
            padding: 16px 20px;
            border-radius: 8px;
            font-size: 12px;
            color: #fff;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .legend-title {{
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 12px;
            color: #888;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            margin-bottom: 8px;
        }}
        .legend-item:last-child {{ margin-bottom: 0; }}
        .legend-color {{
            width: 14px;
            height: 14px;
            border-radius: 3px;
            margin-right: 10px;
        }}
        .draw-hint {{
            position: absolute;
            top: 16px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(20, 20, 24, 0.9);
            backdrop-filter: blur(12px);
            padding: 10px 20px;
            border-radius: 6px;
            font-size: 12px;
            color: #aaa;
            border: 1px solid rgba(255,255,255,0.1);
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="legend">
        <div class="legend-title">Building Types</div>
        <div class="legend-item">
            <div class="legend-color" style="background: #0064FF;"></div>
            <span>Fire Station</span>
        </div>
        <div class="legend-item">
            <div class="legend-color" style="background: #800000;"></div>
            <span>Fire Incident</span>
        </div>
        <div class="legend-item">
            <div class="legend-color" style="background: #E6DDC3;"></div>
            <span>Building</span>
        </div>
    </div>
    <div class="draw-hint">Click the point tool to simulate an incident location</div>

    <script>
        mapboxgl.accessToken = '{MAPBOX_KEY}';

        const buildingsData = {geojson_str};

        const map = new mapboxgl.Map({{
            container: 'map',
            style: 'mapbox://styles/mapbox/dark-v11',
            center: [{SF_COORDINATES[0]}, {SF_COORDINATES[1]}],
            zoom: 13,
            pitch: 55,
            bearing: -17,
            antialias: true
        }});

        // Draw plugin for point selection
        const draw = new MapboxDraw({{
            displayControlsDefault: false,
            controls: {{ point: true, trash: true }},
            defaultMode: 'simple_select'
        }});
        map.addControl(draw, 'top-right');
        map.addControl(new mapboxgl.NavigationControl(), 'top-right');

        map.on('load', () => {{
            // Add buildings source
            map.addSource('buildings-data', {{
                'type': 'geojson',
                'data': buildingsData
            }});

            // Normal buildings layer
            map.addLayer({{
                'id': 'buildings-normal',
                'type': 'fill-extrusion',
                'source': 'buildings-data',
                'filter': ['==', ['get', 'type'], 'normal'],
                'paint': {{
                    'fill-extrusion-color': '#E6DDC3',
                    'fill-extrusion-height': ['get', 'height'],
                    'fill-extrusion-base': 0,
                    'fill-extrusion-opacity': 0.6
                }}
            }});

            // Burnt buildings layer
            map.addLayer({{
                'id': 'buildings-burnt',
                'type': 'fill-extrusion',
                'source': 'buildings-data',
                'filter': ['==', ['get', 'type'], 'burnt'],
                'paint': {{
                    'fill-extrusion-color': '#800000',
                    'fill-extrusion-height': ['get', 'height'],
                    'fill-extrusion-base': 0,
                    'fill-extrusion-opacity': 0.95
                }}
            }});

            // Station buildings layer
            map.addLayer({{
                'id': 'buildings-station',
                'type': 'fill-extrusion',
                'source': 'buildings-data',
                'filter': ['==', ['get', 'type'], 'station'],
                'paint': {{
                    'fill-extrusion-color': '#0064FF',
                    'fill-extrusion-height': ['get', 'height'],
                    'fill-extrusion-base': 0,
                    'fill-extrusion-opacity': 0.95
                }}
            }});

            // Popup on click - Stations
            map.on('click', 'buildings-station', (e) => {{
                const props = e.features[0].properties;
                const html = `
                    <div class="popup-title station">Fire Station</div>
                    <div class="popup-row">
                        <span class="popup-label">Name</span>
                        <span class="popup-value">${{props.station_name}}</span>
                    </div>
                `;
                new mapboxgl.Popup()
                    .setLngLat(e.lngLat)
                    .setHTML(html)
                    .addTo(map);
            }});

            // Popup on click - Burnt buildings
            map.on('click', 'buildings-burnt', (e) => {{
                const props = e.features[0].properties;
                const loss = props.loss ? '$' + Number(props.loss).toLocaleString() : 'N/A';
                const html = `
                    <div class="popup-title incident">Fire Incident</div>
                    <div class="popup-row">
                        <span class="popup-label">Address</span>
                        <span class="popup-value">${{props.address || 'Unknown'}}</span>
                    </div>
                    <div class="popup-row">
                        <span class="popup-label">Date</span>
                        <span class="popup-value">${{props.date || 'Unknown'}}</span>
                    </div>
                    <div class="popup-row">
                        <span class="popup-label">Situation</span>
                        <span class="popup-value">${{props.situation || 'Unknown'}}</span>
                    </div>
                    <div class="popup-row">
                        <span class="popup-label">Cause</span>
                        <span class="popup-value">${{props.cause || 'Unknown'}}</span>
                    </div>
                    <div class="popup-row">
                        <span class="popup-label">Est. Loss</span>
                        <span class="popup-value">${{loss}}</span>
                    </div>
                `;
                new mapboxgl.Popup()
                    .setLngLat(e.lngLat)
                    .setHTML(html)
                    .addTo(map);
            }});

            // Cursor changes
            map.on('mouseenter', 'buildings-station', () => {{ map.getCanvas().style.cursor = 'pointer'; }});
            map.on('mouseleave', 'buildings-station', () => {{ map.getCanvas().style.cursor = ''; }});
            map.on('mouseenter', 'buildings-burnt', () => {{ map.getCanvas().style.cursor = 'pointer'; }});
            map.on('mouseleave', 'buildings-burnt', () => {{ map.getCanvas().style.cursor = ''; }});
        }});

        // Draw event - send to API
        map.on('draw.create', (e) => {{
            const data = draw.getAll();
            if (data.features.length > 0) {{
                const coords = data.features[data.features.length - 1].geometry.coordinates;
                const lon = coords[0];
                const lat = coords[1];

                fetch('{API_URL}/calculate-response', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ lat: lat, lon: lon }})
                }})
                .then(response => response.json())
                .then(data => console.log("Response calculated:", data))
                .catch(error => console.error('API Error:', error));

                // Keep only the latest point
                if (data.features.length > 1) {{
                    const oldIds = data.features.slice(0, -1).map(f => f.id);
                    oldIds.forEach(id => draw.delete(id));
                }}
            }}
        }});
    </script>
</body>
</html>
"""

# Render map
components.html(map_html, height=580)

# --- BOTTOM PANEL: Date Slider + Live Logistics ---
st.markdown("---")

col_slider, col_metrics, col_logistics = st.columns([1, 1, 2])

with col_slider:
    st.markdown("##### Filter by Date")
    selected_date = st.slider(
        "Incident Date Range",
        min_value=min_date,
        max_value=max_date,
        value=(min_date, max_date),
        key="slider_range",
        label_visibility="collapsed",
    )

with col_metrics:
    st.markdown("##### Incident Summary")
    st.metric(label="Total Incidents", value=f"{incident_count:,}")
    st.metric(label="Est. Property Loss", value=f"${total_loss:,.0f}")

with col_logistics:
    st.markdown("##### Simulation Response")

    @st.fragment(run_every=2)
    def show_logistics():
        try:
            resp = requests.get(f"{API_URL}/widget/latest", timeout=2)
            data = resp.json()

            if data and "station_name" in data:
                st.metric(label="Dispatched", value=data["station_name"])
                time_val = data.get("travel_time_min", 0)
                time_display = data.get("travel_time_fmt", "N/A")
                delta_color = "inverse" if time_val > 4 else "normal"
                st.metric(
                    label="Travel Time",
                    value=time_display,
                    delta="Target: <4 min",
                    delta_color=delta_color,
                )
                st.metric(
                    label="Distance (km)",
                    value=f"{data.get('distance_km', 0)} km",
                )
            else:
                st.caption("Standby - Select point on map")
        except Exception:
            st.caption("API Offline")

    show_logistics()

# --- EXPANDABLE SECTIONS ---
st.markdown("---")

col_chart, col_data = st.columns(2)

with col_chart:
    with st.expander("Top Ignition Causes", expanded=True):
        if cause_counts:
            df_causes = pd.DataFrame(
                {
                    "Cause": list(cause_counts.keys()),
                    "Count": list(cause_counts.values()),
                }
            )
            st.bar_chart(df_causes.set_index("Cause"), color="#800000")
        else:
            st.info("No incidents in selected range.")

with col_data:
    with st.expander("Raw Incident Data", expanded=False):
        # Get filtered incidents for display
        mask = (gdf_incidents["incident_date"].dt.date >= date_range[0]) & (
            gdf_incidents["incident_date"].dt.date <= date_range[1]
        )
        filtered = gdf_incidents.loc[mask].copy()
        if not filtered.empty:
            display_df = filtered[
                [
                    "incident_date",
                    "address",
                    "primary_situation",
                    "ignition_cause",
                    "estimated_property_loss",
                ]
            ].head(15)
            display_df.columns = ["Date", "Address", "Situation", "Cause", "Est. Loss"]
            st.dataframe(display_df, use_container_width=True)
        else:
            st.info("No incidents in selected range.")
