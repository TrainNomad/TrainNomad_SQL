import gzip
import io
import json
import os
import shutil
import sqlite3
import zipfile
import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OPERATORS_FILE = os.path.join(BASE_DIR, "operators.json")
DB_PATH = os.path.join(BASE_DIR, "gtfs_indexed.db")
GZ_PATH = os.path.join(BASE_DIR, "gtfs_indexed.db.gz")

def load_operators():
    """Charge la liste des opérateurs et leurs configurations."""
    if not os.path.exists(OPERATORS_FILE):
        raise FileNotFoundError(f"Fichier introuvable: {OPERATORS_FILE}")
    with open(OPERATORS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def detect_transport_type(stop_id_str: str, available_types: list) -> str:
    """Analyse la chaîne stop_id pour identifier le type de transport parmi les types de l'opérateur."""
    val = str(stop_id_str).upper()
    
    # Vérification ciblée basée sur la chaîne stop_id
    if "CAR TER" in val:
        return "Car TER"
    if "CAR À RÉSERVATION" in val or "CAR A RESERVATION" in val:
        return "Car à réservation"
    if "EUROSTAR" in val:
        return "Eurostar"
    if "ICE" in val:
        return "ICE"
    if "INTERCITÉS" in val or "INTERCITES" in val:
        return "INTERCITES"
    if "LYRIA" in val:
        return "Lyria"
    if "OUIGO" in val:
        return "OUIGO"
    if "TGV INOUI" in val or "INOUI" in val or "TGV" in val:
        return "TGV INOUI"
    if "TRAMTRAIN" in val or "TRAM TRAIN" in val:
        return "TramTrain"
    if "TER" in val or "TRAIN TER" in val:
        return "Train TER"
        
    return available_types[0] if available_types else "Train TER"

def init_db(conn):
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode = OFF;")
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
            trip_headsign TEXT,
            train_type TEXT,
            operator_id TEXT
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
        CREATE INDEX idx_stops_name ON stops(stop_name);
        CREATE INDEX idx_stop_times_search ON stop_times(stop_id, dep_min);
        CREATE INDEX idx_stop_times_trip ON stop_times(trip_id, stop_sequence);
        CREATE INDEX idx_calendar_search ON calendar_dates(service_id, date, exception_type);
        CREATE INDEX idx_trips_route ON trips(route_id);
    """)
    conn.commit()

def process_operator(op: dict, conn: sqlite3.Connection):
    op_id = op.get("id", "UNKNOWN")
    gtfs_url = op.get("gtfs_url")
    transport_types = op.get("transport_types", ["Train TER"])

    if not gtfs_url:
        print(f"⚠️ Aucun URL fourni pour l'opérateur {op_id}, ignoré.")
        return

    print(f"\n🚀 Traitement de l'opérateur [{op_id}] -> {gtfs_url}")
    response = requests.get(gtfs_url, stream=True, timeout=180)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        # 1. Stops (Gares)
        if 'stops.txt' in z.namelist():
            print(f"  └ Indexation des gares (stops)...")
            stops = pd.read_csv(z.open('stops.txt'), usecols=['stop_id', 'stop_name', 'stop_lat', 'stop_lon'], dtype=str)
            # Ajout d'un préfixe sur l'ID pour éviter les collisions entre opérateurs
            stops['stop_id'] = op_id + "_" + stops['stop_id']
            stops['clean_uic'] = stops['stop_id'].str.extract(r'(\d+)')
            stops['stop_lat'] = pd.to_numeric(stops['stop_lat'], errors='coerce')
            stops['stop_lon'] = pd.to_numeric(stops['stop_lon'], errors='coerce')
            
            # Utilisation de INSERT OR REPLACE pour fusionner les gares communes
            stops[['stop_id', 'stop_name', 'stop_lat', 'stop_lon', 'clean_uic']].to_sql(
                'stops', conn, if_exists='append', index=False
            )

        # 2. Routes (Lignes)
        if 'routes.txt' in z.namelist():
            print(f"  └ Indexation des lignes (routes)...")
            routes = pd.read_csv(z.open('routes.txt'), usecols=['route_id'], dtype=str)
            routes['route_id'] = op_id + "_" + routes['route_id']
            routes['train_type'] = transport_types[0] if transport_types else "TRAIN"
            routes[['route_id', 'train_type']].to_sql('routes', conn, if_exists='append', index=False)

        # 3. Stop Times (Horaires)
        if 'stop_times.txt' in z.namelist():
            print(f"  └ Indexation des horaires (stop_times)...")
            chunksize = 100000
            trip_type_map = {}
            use_cols = ['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence']

            for chunk in pd.read_csv(z.open('stop_times.txt'), usecols=use_cols, dtype=str, chunksize=chunksize):
                def to_min(t_str):
                    if not isinstance(t_str, str) or ':' not in t_str:
                        return 0
                    parts = t_str.split(':')
                    return int(parts[0]) * 60 + int(parts[1])

                chunk['dep_min'] = chunk['departure_time'].apply(to_min)
                chunk['stop_sequence'] = chunk['stop_sequence'].astype(int)
                
                # Prefixing IDs
                chunk['trip_id'] = op_id + "_" + chunk['trip_id']
                raw_stop_ids = chunk['stop_id'].copy()
                chunk['stop_id'] = op_id + "_" + chunk['stop_id']

                # Détection du type de transport à partir du stop_id brut
                for idx, row in chunk.iterrows():
                    t_id = row['trip_id']
                    if t_id not in trip_type_map:
                        trip_type_map[t_id] = detect_transport_type(raw_stop_ids[idx], transport_types)

                chunk[['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence', 'dep_min']].to_sql(
                    'stop_times', conn, if_exists='append', index=False
                )

        # 4. Trips (Trajets et Numéro de train / trip_headsign)
        if 'trips.txt' in z.namelist():
            print(f"  └ Indexation des trajets (trips)...")
            trips = pd.read_csv(z.open('trips.txt'), usecols=['trip_id', 'route_id', 'service_id', 'trip_headsign'], dtype=str)
            trips['trip_id'] = op_id + "_" + trips['trip_id']
            trips['route_id'] = op_id + "_" + trips['route_id']
            trips['service_id'] = op_id + "_" + trips['service_id']
            trips['operator_id'] = op_id
            trips['train_type'] = trips['trip_id'].map(trip_type_map).fillna(transport_types[0] if transport_types else "TRAIN")

            trips[['trip_id', 'route_id', 'service_id', 'trip_headsign', 'train_type', 'operator_id']].to_sql(
                'trips', conn, if_exists='append', index=False
            )

        # 5. Calendar Dates
        if 'calendar_dates.txt' in z.namelist():
            print(f"  └ Indexation du calendrier (calendar_dates)...")
            calendar = pd.read_csv(z.open('calendar_dates.txt'), usecols=['service_id', 'date', 'exception_type'], dtype=str)
            calendar['service_id'] = op_id + "_" + calendar['service_id']
            calendar['date'] = pd.to_datetime(calendar['date'], format='%Y%m%d').dt.strftime('%Y-%m-%d')
            calendar['exception_type'] = calendar['exception_type'].astype(int)

            calendar[['service_id', 'date', 'exception_type']].to_sql(
                'calendar_dates', conn, if_exists='append', index=False
            )

def build_sqlite_gtfs():
    operators = load_operators()
    print(f"📋 {len(operators)} opérateur(s) chargé(s) depuis operators.json.")

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    if os.path.exists(GZ_PATH):
        os.remove(GZ_PATH)

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    for op in operators:
        try:
            process_operator(op, conn)
        except Exception as e:
            print(f"❌ Erreur lors du traitement de l'opérateur {op.get('id')}: {e}")

    create_indexes(conn)

    print("\n🧹 Nettoyage et compactage SQL (VACUUM)...")
    conn.execute("VACUUM;")
    conn.execute("ANALYZE;")
    conn.close()

    print("📦 Compression finale en .gz...")
    with open(DB_PATH, 'rb') as f_in:
        with gzip.open(GZ_PATH, 'wb', compresslevel=9) as f_out:
            shutil.copyfileobj(f_in, f_out)

    os.remove(DB_PATH)
    print("✅ Ingestion multi-opérateurs terminée avec succès !")

if __name__ == '__main__':
    build_sqlite_gtfs()