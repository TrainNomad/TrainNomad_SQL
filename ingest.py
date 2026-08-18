from datetime import datetime, timedelta
import io
import json
import os
import sqlite3
import zipfile
import pandas as pd
import requests

# Fenêtre glissante de 90 jours
TODAY = datetime.now()
DATE_MIN = (TODAY - timedelta(days=1)).strftime("%Y%m%d")
DATE_MAX = (TODAY + timedelta(days=90)).strftime("%Y%m%d")

DB_FILE = "eu_trains.db"


def time_to_minutes(time_str):
    """Convertit HH:MM:SS en minutes depuis minuit."""
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def init_db(conn):
    cursor = conn.cursor()
    cursor.executescript(
        """
        DROP TABLE IF EXISTS trips;
        DROP TABLE IF EXISTS operators;

        CREATE TABLE operators (
            id TEXT PRIMARY KEY,
            name TEXT
        );

        CREATE TABLE trips (
            operator_id TEXT,
            date TEXT,
            origin_name TEXT,
            origin_parent_name TEXT,
            destination_name TEXT,
            destination_parent_name TEXT,
            origin_lat REAL,
            origin_lon REAL,
            dest_lat REAL,
            dest_lon REAL,
            departure_time TEXT,
            arrival_time TEXT,
            dep_min INTEGER,
            train_no TEXT,
            train_type TEXT
        );
    """
    )
    conn.commit()


def process_gtfs(operator, conn):
    op_id = operator["id"]
    print(f"--- Traitement de : {operator['name']} ({op_id}) ---")

    try:
        response = requests.get(operator["gtfs_url"], timeout=90)
        response.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(response.content))
    except Exception as e:
        print(f"Erreur de téléchargement pour {op_id}: {e}")
        return

    # A. Services actifs
    service_dates_map = {}
    if "calendar_dates.txt" in z.namelist():
        df_cal = pd.read_csv(
            z.open("calendar_dates.txt"),
            dtype=str,
            usecols=["service_id", "date", "exception_type"],
        )
        df_cal = df_cal[
            (df_cal["date"] >= DATE_MIN)
            & (df_cal["date"] <= DATE_MAX)
            & (df_cal["exception_type"] == "1")
        ]
        for _, row in df_cal.iterrows():
            service_dates_map.setdefault(row["service_id"], []).append(row["date"])

    if not service_dates_map:
        print(f"Aucun service actif pour {op_id}.")
        return

    # B. Trips & Routes
    df_trips = pd.read_csv(
        z.open("trips.txt"),
        dtype=str,
        usecols=["trip_id", "route_id", "service_id", "trip_headsign"],
    )
    df_trips = df_trips[df_trips["service_id"].isin(service_dates_map.keys())].copy()

    # C. Stops
    df_stops = pd.read_csv(
        z.open("stops.txt"),
        dtype={"stop_id": str, "stop_name": str, "stop_lat": float, "stop_lon": float},
        usecols=["stop_id", "stop_name", "stop_lat", "stop_lon"],
    )
    stops_dict = df_stops.set_index("stop_id").to_dict(orient="index")

    # D. Stop Times
    df_st = pd.read_csv(
        z.open("stop_times.txt"),
        dtype={
            "trip_id": str,
            "arrival_time": str,
            "departure_time": str,
            "stop_id": str,
            "stop_sequence": int,
        },
        usecols=["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
    )
    df_st = df_st[df_st["trip_id"].isin(set(df_trips["trip_id"]))].sort_values(
        ["trip_id", "stop_sequence"]
    )

    # E. Agrégation Origine -> Destination par trajet
    trips_grouped = df_st.groupby("trip_id")
    rows_to_insert = []

    trip_info = df_trips.set_index("trip_id").to_dict(orient="index")

    for trip_id, group in trips_grouped:
        if len(group) < 2:
            continue

        first_stop = group.iloc[0]
        last_stop = group.iloc[-1]

        orig_data = stops_dict.get(first_stop["stop_id"], {})
        dest_data = stops_dict.get(last_stop["stop_id"], {})

        if not orig_data or not dest_data:
            continue

        meta = trip_info.get(trip_id, {})
        srv_id = meta.get("service_id")
        headsign = meta.get("trip_headsign", "")

        dates = service_dates_map.get(srv_id, [])
        dep_time = first_stop["departure_time"]
        arr_time = last_stop["arrival_time"]
        dep_min = time_to_minutes(dep_time)

        for d in dates:
            # Conversion YYYYMMDD -> YYYY-MM-DD
            formatted_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            rows_to_insert.append(
                (
                    op_id,
                    formatted_date,
                    orig_data.get("stop_name"),
                    orig_data.get("stop_name"),
                    dest_data.get("stop_name"),
                    dest_data.get("stop_name"),
                    orig_data.get("stop_lat"),
                    orig_data.get("stop_lon"),
                    dest_data.get("stop_lat"),
                    dest_data.get("stop_lon"),
                    dep_time,
                    arr_time,
                    dep_min,
                    headsign,
                    "Train",
                )
            )

    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO operators VALUES (?, ?)", (op_id, operator["name"]))
    cursor.executemany(
        """
        INSERT INTO trips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        rows_to_insert,
    )
    conn.commit()
    print(f"Succès pour {op_id} : {len(rows_to_insert)} trajets insérés.")


def finalize_db(conn):
    print("--- Création des index et compactage ---")
    cursor = conn.cursor()
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_trips_search ON trips(date, origin_name, destination_name);"
    )
    conn.commit()
    conn.execute("VACUUM;")
    conn.close()


def main():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

    if not os.path.exists("operators.json"):
        print("Erreur : operators.json introuvable.")
        return

    with open("operators.json", "r", encoding="utf-8") as f:
        operators = json.load(f)

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    for op in operators:
        process_gtfs(op, conn)

    finalize_db(conn)


if __name__ == "__main__":
    main()