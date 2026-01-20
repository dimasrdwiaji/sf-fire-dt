import os
import json
import pandas as pd
from sodapy import Socrata
from datetime import datetime, timedelta

# --- CONFIGURATION ---
DATA_FOLDER = "data"
FILENAME = "sf_fire_data.json"
FILE_PATH = os.path.join(DATA_FOLDER, FILENAME)

# San Francisco Fire Incidents Dataset ID (from DataSF)
DATASET_ID = "wr8u-xric"
DOMAIN = "data.sfgov.org"

# Columns to keep for the Digital Twin simulation
# Fetch: ID, Date, Situation (for 111 check), Location (Point), Ignition cause, and Loss
SELECT_COLS = "incident_number, incident_date, primary_situation, point, ignition_cause, estimated_property_loss, number_of_alarms, station_area"


def ensure_folder_exists():
    """Creates the data directory if it doesn't exist."""
    if not os.path.exists(DATA_FOLDER):
        os.makedirs(DATA_FOLDER)
        print(f"[INFO] Created folder: {DATA_FOLDER}")


def load_existing_data():
    """Loads existing JSON data if file exists; returns empty list otherwise."""
    if os.path.exists(FILE_PATH):
        try:
            with open(FILE_PATH, "r") as f:
                data = json.load(f)
                print(f"[INFO] Found existing data: {len(data)} records.")
                return data
        except json.JSONDecodeError:
            print("[WARN] Data file corrupted. Starting fresh.")
            return []
    return []


def get_start_date(existing_data):
    """
    Determines the start date for the API query.
    - If no data: returns date 365 days ago.
    - If data exists: returns the most recent date in the file.
    """
    now = datetime.now()
    one_year_ago = now - timedelta(days=365)

    if not existing_data:
        print("[INFO] No existing data. Fetching last 365 days.")
        return one_year_ago.strftime("%Y-%m-%dT%H:%M:%S")

    # Find the latest date in the current dataset
    try:
        dates = [
            d.get("incident_date") for d in existing_data if d.get("incident_date")
        ]
        last_date_str = max(dates)  # Get latest date

        # We add 1 second to avoid duplicates, though Socrata handles overlap well
        last_date = datetime.strptime(last_date_str, "%Y-%m-%dT%H:%M:%S")
        print(f"[INFO] Latest record from {last_date}. Fetching updates...")
        return last_date.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        print("[WARN] Date format error in existing file. Defaulting to 1 year ago.")
        return one_year_ago.strftime("%Y-%m-%dT%H:%M:%S")


def clean_old_records(data):
    """Removes records older than 365 days to keep the dataset lightweight."""
    cutoff_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S")

    original_count = len(data)
    # Keep only records where date >= cutoff
    filtered_data = [d for d in data if d.get("incident_date") >= cutoff_date]

    removed = original_count - len(filtered_data)
    if removed > 0:
        print(f"[INFO] Pruned {removed} records older than 365 days.")

    return filtered_data


def fetch_and_update():
    ensure_folder_exists()
    existing_data = load_existing_data()

    # 1. Determine Query Start Date
    start_date = get_start_date(existing_data)

    # 2. Setup Socrata Client (No token needed for public access, but recommended for high volume)
    client = Socrata(DOMAIN, None)

    # 3. Construct SoQL Query
    # Look for Building Fires (111) occurring AFTER our start_date
    where_query = f"incident_date > '{start_date}' AND primary_situation LIKE '111%'"

    try:
        results = client.get(
            DATASET_ID,
            select=SELECT_COLS,
            where=where_query,
            order="incident_date ASC",  # Oldest to newest (helps with appending)
            limit=2000,  # Batch size limit
        )

        if not results:
            print("[INFO] No new records found.")
        else:
            print(f"[INFO] Fetched {len(results)} new records.")

            # 4. Merge Data
            existing_data.extend(results)

        print("Data fetched")
        return True

    except Exception as e:
        print(f"[ERROR] API Query failed: {e}")
        return

    # 5. Clean Old Data (Rolling Window)
    final_data = clean_old_records(existing_data)

    # 6. Save to Disk
    with open(FILE_PATH, "w") as f:
        json.dump(final_data, f, indent=4)
        print(f"[SUCCESS] Saved {len(final_data)} total records to {FILE_PATH}")


if __name__ == "__main__":
    fetch_and_update()
