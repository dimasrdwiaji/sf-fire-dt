import streamlit as st
import streamlit.components.v1 as components
import requests
import pydeck as pdk
import pandas as pd
import time

st.set_page_config(layout="wide", page_title="SF Fire Response Twin")

# --- CONFIG
API_URL = "http://127.0.0.1:8000"
MAPBOX_KEY = st.secrets[
    "MAPBOX_ACCESS_KEY"
]  # Ensure this is in .streamlit/secrets.toml

st.title("🚒 SF Fire Response Simulator")

# --- LAYOUT ---
col_map, col_widget = st.columns([3, 1])

with col_map:
    # --- EMBEDDED JAVASCRIPT MAP ---
    # We inject the JS Fetch logic directly into the HTML
    map_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Mapbox Draw</title>
        <meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no">
        <link href="https://api.mapbox.com/mapbox-gl-js/v2.15.0/mapbox-gl.css" rel="stylesheet">
        <script src="https://api.mapbox.com/mapbox-gl-js/v2.15.0/mapbox-gl.js"></script>
        <link rel="stylesheet" href="https://api.mapbox.com/mapbox-gl-js/plugins/mapbox-gl-draw/v1.4.0/mapbox-gl-draw.css" type="text/css">
        <script src="https://api.mapbox.com/mapbox-gl-js/plugins/mapbox-gl-draw/v1.4.0/mapbox-gl-draw.js"></script>
        <style>
            body {{ margin: 0; padding: 0; }}
            #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
        </style>
    </head>
    <body>
        <div id="map"></div>
        <script>
            mapboxgl.accessToken = '{MAPBOX_KEY}';
            const map = new mapboxgl.Map({{
                container: 'map',
                style: 'mapbox://styles/mapbox/dark-v11',
                center: [-122.4194, 37.7749],
                zoom: 12
            }});

            const draw = new MapboxDraw({{
                displayControlsDefault: false,
                controls: {{ point: true, trash: true }},
                defaultMode: 'draw_point'
            }});
            map.addControl(draw);

            // EVENT LISTENER: When a point is created
            map.on('draw.create', updateArea);
            
            function updateArea(e) {{
                const data = draw.getAll();
                if (data.features.length > 0) {{
                    const coords = data.features[0].geometry.coordinates;
                    const lon = coords[0];
                    const lat = coords[1];

                    // SEND TO FASTAPI
                    fetch('{API_URL}/calculate-response', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ lat: lat, lon: lon }})
                    }})
                    .then(response => response.json())
                    .then(data => console.log("Calculated:", data))
                    .catch(error => console.error('Error:', error));
                    
                    // Clear older points if you only want one
                    if (data.features.length > 1) {{
                        draw.delete(data.features[0].id); 
                    }}
                }}
            }}
        </script>
    </body>
    </html>
    """

    # Render the map in an iframe (height 600px)
    components.html(map_html, height=600)


# --- WIDGET FRAGMENT (POLLS API) ---
with col_widget:
    st.subheader("Live Logistics")

    @st.fragment(run_every=2)
    def show_metrics():
        try:
            # Poll the API
            resp = requests.get(f"{API_URL}/widget/latest")
            data = resp.json()

            if data and "station_name" in data:
                st.success(f"Response Calculated")

                # 1. Nearest Station Name
                st.metric(label="Dispatched Station", value=data["station_name"])

                # 2. Travel Time (with color logic)
                time_val = data["travel_time_min"]  # Numeric value
                time_display = data["travel_time_fmt"]  # Formatted string
                st.metric(
                    label="Est. Travel Time",
                    value=time_display,
                    delta="Target: <4 min",
                    delta_color="inverse" if time_val > 4 else "normal",
                )

                # 3. Distance (NEW)
                dist_val = data["distance_km"]
                st.metric(label="Route Distance", value=f"{dist_val} km")

            else:
                st.info("System Standby")
                st.caption("Select a point on the map to calculate response metrics.")

        except Exception:
            st.error("Engine Offline")

    show_metrics()
