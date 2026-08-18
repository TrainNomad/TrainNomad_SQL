import io
import os
import sqlite3
import zipfile
import pandas as pd
import requests

GTFS_URL = "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gtfs_indexed.db")

def detect_train_type(row):
    name = f"{row.get('route_long_name', '')} {row.get('route_short_name', '')}".upper()
    if "OUIGO" in name:
        return "OUIGO"
    if "TER" in name:
        return "TER"
    if "INTERCITÉS" in name or "INTERCITES" in name or "IC" in name:
        return "INTERCITÉS"
    if "TGV" in name or "INOUI" in name:
        return "TGV INOUI"
    return "TRAIN"

def init_db(conn):
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA synchronous = OFF;")

    cursor.executescript("""
        DROP TABLE IF EXISTS stops;
        DROP TABLE IF EXISTS routes;
        DROP TABLE IF EXISTS trips;
        DROP TABLE IF EXISTS stop_times;
        DROP TABLE IF EXISTS calendar_dates;

        CREATE TABLE stops (
            stop_id TEXT PRIMARY KEY,
            stop_name TEXT,
            stop_lat REAL,
            stop_lon REAL,
            clean_uic TEXT
        );

        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY,
            train_type TEXT
        );

        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY,
            route_id TEXT,
            service_id TEXT,
            trip_headsign TEXT
        );

        CREATE TABLE stop_times (
            trip_id TEXT,
            arrival_time TEXT,
            departure_time TEXT,
            stop_id TEXT,
            stop_sequence INTEGER,
            dep_min INTEGER
        );

        CREATE TABLE calendar_dates (
            service_id TEXT,
            date TEXT,
            exception_type INTEGER
        );
    """)
    conn.commit()

def create_indexes(conn):
    print("⚡ Création des index SQL...")
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE INDEX idx_stop_times_search ON stop_times(stop_id, dep_min);
        CREATE INDEX idx_stop_times_trip ON stop_times(trip_id, stop_sequence);
        CREATE INDEX idx_calendar_search ON calendar_dates(service_id, date, exception_type);
        CREATE INDEX idx_trips_route ON trips(route_id);
    """)
    conn.commit()

def build_sqlite_gtfs():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    print("1. Téléchargement du fichier GTFS...")
    response = requests.get(GTFS_URL, stream=True, timeout=120)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        # 1. Stops
        print("2. Indexation des gares (stops)...")
        stops = pd.read_csv(z.open('stops.txt'), dtype=str)
        stops['clean_uic'] = stops['stop_id'].str.extract(r'(\d+)')
        stops[['stop_id', 'stop_name', 'stop_lat', 'stop_lon', 'clean_uic']].to_sql('stops', conn, if_exists='append', index=False)

        # 2. Routes (avec détection du type de train)
        print("3. Indexation des lignes (routes)...")
        routes = pd.read_csv(z.open('routes.txt'), dtype=str)
        routes['train_type'] = routes.apply(detect_train_type, axis=1)
        routes[['route_id', 'train_type']].to_sql('routes', conn, if_exists='append', index=False)

        # 3. Trips
        print("4. Indexation des trajets (trips)...")
        trips = pd.read_csv(z.open('trips.txt'), dtype=str)
        trips[['trip_id', 'route_id', 'service_id', 'trip_headsign']].to_sql('trips', conn, if_exists='append', index=False)

        # 4. Calendar Dates
        print("5. Indexation des dates de circulation (calendar_dates)...")
        calendar = pd.read_csv(z.open('calendar_dates.txt'), dtype=str)
        calendar['date'] = pd.to_datetime(calendar['date'], format='%Y%m%d').dt.strftime('%Y-%m-%d')
        calendar[['service_id', 'date', 'exception_type']].to_sql('calendar_dates', conn, if_exists='append', index=False)

        # 5. Stop Times (Traitement par morcellements pour économiser la RAM)
        print("6. Indexation des horaires (stop_times)...")
        chunksize = 100000
        for chunk in pd.read_csv(z.open('stop_times.txt'), dtype=str, chunksize=chunksize):
            # Conversion HH:MM:SS en minutes
            def to_min(t_str):
                if not isinstance(t_str, str) or ':' not in t_str:
                    return 0
                parts = t_str.split(':')
                return int(parts[0]) * 60 + int(parts[1])

            chunk['dep_min'] = chunk['departure_time'].apply(to_min)
            chunk['stop_sequence'] = chunk['stop_sequence'].astype(int)
            
            chunk[['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence', 'dep_min']].to_sql(
                'stop_times', conn, if_exists='append', index=False
            )

    create_indexes(conn)
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.close()
    print(f"✅ Base SQLite générée avec succès : {DB_PATH}")

if __name__ == '__main__':
    build_sqlite_gtfs()