import os
import io
import sqlite3
import zipfile
import pandas as pd
import requests

GTFS_URL = "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"

def detect_train_type(route_long_name, route_short_name):
    name = f"{route_long_name} {route_short_name}".upper()
    if "OUIGO" in name:
        return "OUIGO"
    if "TER" in name:
        return "TER"
    if "INTERCITÉS" in name or "INTERCITES" in name or "IC" in name:
        return "INTERCITÉS"
    if "TGV" in name or "INOUI" in name:
        return "TGV INOUI"
    return "TRAIN"

def time_to_minutes(t_str):
    if not t_str or ':' not in str(t_str):
        return 0
    parts = str(t_str).strip().split(':')
    return int(parts[0]) * 60 + int(parts[1])

# 1. Chargement des gares
print("1. Chargement des gares...")
df_stations = pd.read_csv('stations.csv', sep=';', low_memory=False)
df_stations['name'] = df_stations['name'].fillna('Gare Inconnue')
df_stations['latitude'] = pd.to_numeric(df_stations['latitude'], errors='coerce').fillna(0.0)
df_stations['longitude'] = pd.to_numeric(df_stations['longitude'], errors='coerce').fillna(0.0)

id_to_name = df_stations.set_index('id')['name'].to_dict()
id_to_parent_id = df_stations.set_index('id')['parent_station_id'].to_dict()

def resolve_parent_id(s_id):
    p_id = id_to_parent_id.get(s_id)
    if pd.isna(p_id) or not p_id:
        return int(s_id)
    return int(p_id)

df_stations['parent_id'] = df_stations['id'].apply(resolve_parent_id)
id_to_parent_id = df_stations.set_index('id')['parent_id'].to_dict()
id_to_parent_name = {s_id: id_to_name.get(p_id, id_to_name.get(s_id)) for s_id, p_id in id_to_parent_id.items()}
id_to_lat = df_stations.set_index('id')['latitude'].to_dict()
id_to_lon = df_stations.set_index('id')['longitude'].to_dict()

# Mapping UIC (SNCF) -> ID local
df_uic = df_stations.dropna(subset=['uic', 'id']).copy()
uic_to_id = {}
for _, row in df_uic.iterrows():
    uic_str = str(int(row['uic'])) if str(row['uic']).replace('.','').isdigit() else str(row['uic']).strip()
    uic_to_id[uic_str] = int(row['id'])

# 2. Téléchargement et extraction du GTFS
print("2. Téléchargement du fichier GTFS SNCF...")
r = requests.get(GTFS_URL, stream=True, timeout=120)
r.raise_for_status()

z = zipfile.ZipFile(io.BytesIO(r.content))

print("3. Parsing des données GTFS...")
routes = pd.read_csv(z.open('routes.txt'), dtype=str)
trips = pd.read_csv(z.open('trips.txt'), dtype=str)
stop_times = pd.read_csv(z.open('stop_times.txt'), dtype=str)
stops = pd.read_csv(z.open('stops.txt'), dtype=str)

# Map stop_id GTFS -> ID station local via le code UIC
stops['clean_uic'] = stops['stop_id'].str.extract(r'(\d+)')
stops['station_id'] = stops['clean_uic'].map(uic_to_id)
stop_id_to_station_id = stops.dropna(subset=['station_id']).set_index('stop_id')['station_id'].to_dict()

# Calendrier de circulation (calendar_dates.txt)
calendar_dates = pd.read_csv(z.open('calendar_dates.txt'), dtype=str)
calendar_dates['date_formatted'] = pd.to_datetime(calendar_dates['date'], format='%Y%m%d').dt.strftime('%Y-%m-%d')
service_to_dates = calendar_dates[calendar_dates['exception_type'] == '1'].groupby('service_id')['date_formatted'].apply(list).to_dict()

# Types de trains
routes['train_type'] = routes.apply(lambda r: detect_train_type(r.get('route_long_name', ''), r.get('route_short_name', '')), axis=1)
route_type_map = routes.set_index('route_id')['train_type'].to_dict()
trips['train_type'] = trips['route_id'].map(route_type_map)

# Association des arrêts consécutifs par trajet
stop_times['station_id'] = stop_times['stop_id'].map(stop_id_to_station_id)
stop_times = stop_times.dropna(subset=['station_id']).sort_values(['trip_id', 'stop_sequence'])

stop_times['next_station_id'] = stop_times.groupby('trip_id')['station_id'].shift(-1)
stop_times['next_arrival_time'] = stop_times.groupby('trip_id')['arrival_time'].shift(-1)

legs = stop_times.dropna(subset=['next_station_id']).copy()
legs = legs.merge(trips[['trip_id', 'service_id', 'trip_headsign', 'train_type']], on='trip_id', how='inner')

records = []
print("4. Génération des segments de trajet par date...")
for _, row in legs.iterrows():
    orig_id = int(row['station_id'])
    dest_id = int(row['next_station_id'])
    dates = service_to_dates.get(row['service_id'], [])
    
    dep_time = str(row['departure_time'])[:5]
    arr_time = str(row['next_arrival_time'])[:5]
    
    dep_m = time_to_minutes(dep_time)
    arr_m = time_to_minutes(arr_time)

    for d in dates:
        records.append({
            'date': d,
            'origin_id': orig_id,
            'origin_parent_id': id_to_parent_id.get(orig_id, orig_id),
            'origin_name': id_to_name.get(orig_id, 'Inconnue'),
            'origin_parent_name': id_to_parent_name.get(orig_id, 'Inconnue'),
            'origin_lat': id_to_lat.get(orig_id, 0.0),
            'origin_lon': id_to_lon.get(orig_id, 0.0),
            
            'destination_id': dest_id,
            'destination_parent_id': id_to_parent_id.get(dest_id, dest_id),
            'destination_name': id_to_name.get(dest_id, 'Inconnue'),
            'destination_parent_name': id_to_parent_name.get(dest_id, 'Inconnue'),
            'dest_lat': id_to_lat.get(dest_id, 0.0),
            'dest_lon': id_to_lon.get(dest_id, 0.0),

            'departure_time': dep_time,
            'arrival_time': arr_time,
            'dep_min': dep_m,
            'arr_min': arr_m,
            'train_no': row.get('trip_headsign', ''),
            'train_type': row['train_type']
        })

df_trips = pd.DataFrame(records)

# 5. Stockage SQLite
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(BASE_DIR, 'sncf_compact.db')

print(f"5. Écriture dans SQLite ({db_path})...")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute('PRAGMA journal_mode = WAL;')
cursor.execute('PRAGMA synchronous = NORMAL;')

cursor.execute('DROP TABLE IF EXISTS trips;')
cursor.execute('''
CREATE TABLE trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    origin_id INTEGER NOT NULL,
    origin_parent_id INTEGER NOT NULL,
    origin_name TEXT NOT NULL,
    origin_parent_name TEXT NOT NULL,
    origin_lat REAL,
    origin_lon REAL,
    destination_id INTEGER NOT NULL,
    destination_parent_id INTEGER NOT NULL,
    destination_name TEXT NOT NULL,
    destination_parent_name TEXT NOT NULL,
    dest_lat REAL,
    dest_lon REAL,
    departure_time TEXT NOT NULL,
    arrival_time TEXT NOT NULL,
    dep_min INTEGER NOT NULL,
    arr_min INTEGER NOT NULL,
    train_no TEXT,
    train_type TEXT NOT NULL
);
''')

df_trips.to_sql('trips', conn, if_exists='append', index=False)

print("6. Indexation...")
cursor.execute('CREATE INDEX idx_search_direct ON trips(date, origin_parent_id, destination_parent_id, dep_min);')
cursor.execute('CREATE INDEX idx_search_transfer ON trips(date, origin_parent_id, dep_min, arr_min);')

conn.commit()
conn.close()
print("✅ Traitement GTFS terminé !")