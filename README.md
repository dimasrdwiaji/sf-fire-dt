# San Francisco Fire Incident Digital Twin

**Author:** Dimas Rizqi Dwiaji (s3485544)

## Project Overview

This project is a 3D digital twin application for visualizing and analyzing fire incidents in San Francisco. It combines daily data integration, geospatial analysis, and interactive visualization to simulate emergency response and historical incident analysis.

### Objectives

- **3D Urban Visualization**: Differentiate between historically burnt buildings, fire stations, and general buildings in a 3D urban environment
- **Real-time Data Integration**: Query and display historical fire incident data from the San Francisco data portal, updated daily
- **Emergency Response Simulation**: Simulate emergency fire incident dispatch travel times using network analysis

## Tools and Technologies

### Software Stack

- **Streamlit**: User interface and application framework
- **Sodapy**: API client for querying fire incident data from San Francisco Open Data Portal
- **FastAPI**: Backend API for communication between front-end and back-end services
- **Mapbox GL JS**: Interactive 3D map rendering and functionalities
- **OSMnx**: OpenStreetMap data extraction for buildings and road network query and analysis
- **Geopandas**: Geospatial data processing and handling

### Data Sources

- **Fire Incident Data**: [San Francisco Open Data Portal](https://data.sfgov.org/Public-Safety/Fire-Incidents/wr8u-xric/about_data) - updated daily
- **Road Network**: OpenStreetMap (OSM). Drivable network only.
- **Building Polygons**: OpenStreetMap (OSM) - San Francisco area

## System Workflows

### 1. Historical Fire Incident Data Pipeline

The system connects to the Socrata API ([data.sfgov.org](https://data.sfgov.org/Public-Safety/Fire-Incidents/wr8u-xric/about_data)) to fetch the fire incidents dataset with the following parameters:

- **Filter**: Building fires only (excludes other emergency medical dispatches)
- **Time Range**: Last 365 days
- **Storage**: Data saved to JSON file
- **Update Frequency**: Refreshed every time the digital twin is opened
- **Maintenance**: Automatically removes records older than 365 days

### 2. Urban Infrastructure Data Acquisition

The workflow fetches and processes the following spatial data:

- **Road Network**: Drivable road network for San Francisco using OSMnx
- **Fire Stations**: Locations tagged as `amenity:fire_station` in OSM
- **Buildings**: All features tagged as `building:True`, filtering out structures smaller than 20 square meters (removes minor sheds or guard posts)

### 3. Building Height Estimation and Classification

#### Height Assignment

The system uses a hierarchical approach to determine building heights:

1. **Actual OSM Height Data**: Uses real height values if available in OpenStreetMap
2. **Level-based Estimation**: Calculates height from `building:levels` tag (3.5m per floor)
3. **Type-based Randomization**: Assigns randomized heights based on building type:
   - Offices: 20–100m
   - Houses: 6–12m
   - Other types: Varies accordingly

#### Color Classification (Spatial Join)

Buildings are assigned colors based on spatial analysis:

- **Blue (Fire Station)**: Assigned via spatial join where a building polygon contains a Fire Station coordinate
- **Red (Incident Site)**: Assigned if a building polygon contains the coordinate of a historical fire incident
- **Peach (Normal Building)**: Default color for all other buildings

### 4. User Interface Features

The Streamlit interface provides:

- **3D Map Integration**: Mapbox-powered interactive map with zoom, rotate, and point-drawing capabilities
- **Interactive Tooltips**: Click on burnt buildings or fire stations to view details
- **Date Filter**: Streamlit date slider to filter historical fire incidents by time range
- **Dynamic Metrics**: Real-time updates of total incidents and estimated property loss based on filtered dates
- **Fire Cause Analysis**: Chart visualization of fire causes
- **Raw Data Viewer**: Access to underlying dataset

### 5. Fire Dispatch Simulation

The emergency response simulation workflow:

1. User draws a point on the map to simulate a fire incident
2. Coordinate is sent via FastAPI to the backend
3. System finds the nearest road network node to the incident point
4. System identifies the nearest fire station
5. Calculates the shortest path and estimated travel time

## Technical Challenges

### Original Simulation Design

The project initially aimed for a more complex real-time simulation system:

- **Trigger**: User clicks a building and confirms "Simulate Fire?" prompt via Mapbox popup
- **Severity Progression**: Building receives a severity value that increases over time until threshold is reached, changing texture to fully burnt
- **Dispatch Delay**: Random interval simulates realistic time between fire occurrence and 911 call
- **Response**: Nearest fire station dispatched using existing routing network
- **Extinguishment**: On arrival (including travel time), severity slowly decreases to zero
- **Multi-fire Management**: System tracks multiple active fires simultaneously to analyze fire department resource strain

### The Streamlit Rerun Problem

A significant technical limitation was encountered:

- **State Management Issue**: In Streamlit, any state change (like fire severity updates) triggers a complete script rerun
- **Performance Impact**: Full reruns take 1-2 minutes due to massive building and network datasets loading
- **Data Persistence**: Reruns reset the UI to default state, losing all updates (clicked buildings, fire timestamps, etc.)

This limitation prevented implementation of the original real-time simulation design and necessitated a simplified approach focused on static analysis and single-event simulation.

## Installation

```bash
# Clone the repository
git clone [https://github.com/dimasrdwiaji/sf-fire-dt]

# Install dependencies
pip install streamlit sodapy fastapi uvicorn osmnx geopandas mapbox-gl

# Run the application
streamlit run app.py
```

## Usage

1. Launch the application using Streamlit
2. Wait for initial data loading (building and road network data)
3. Use the date slider to filter historical incidents
4. Click on buildings to view incident details
5. Draw a point on the map to simulate emergency dispatch routing

## Future Improvements

- Implement session state management to gienable real-time fire simulation
- Optimize data loading to reduce rerun times
- Add caching mechanisms for building and network datasets
- Implement multi-fire tracking and resource allocation analysis
- Deploy to web

---
