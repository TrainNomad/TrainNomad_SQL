import os
import io
import zipfile
import sqlite3
import pandas as pd
import requests
from operators import load_operators

def time_to_minutes(t_str):
    if not t_str or ':' not in str(t_str):
        return 0
    parts = str(t_str).strip().split(':')
    return int(parts[0]) * 60 + int(parts[1])

def process_operator(operator: dict, conn: sqlite3.Connection):
    op_id = operator.get("id")
    gtfs_url = operator.get("gtfs_url")

    if not gtfs_url:
        print(f"⚠️ Ignoré ({op_id}) : Pas d'URL GTFS fournie.")
        return

    print(f"\n--- Traitement de l'opérateur : {op_id} ---")

    try:
        response = requests.get(gtfs_url, stream=True, timeout=120)
        response.raise_for_status()
    except Exception as e:
        print(f"❌ Échec du téléchargement pour {op_id}: {e}")
        return

    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            stops = pd.read_csv(z.open('stops.txt'), dtype=str)
            trips = pd.read_csv(z.open('trips.txt'), dtype=str)
            stop_times = pd.read_csv(z.open('stop_times.txt'), dtype=str)
            calendar_dates = (
                pd.read_csv(z.open('calendar_dates.txt'), dtype=str)
                if 'calendar_dates.txt' in z.namelist()
                else None
            )

        stops['stop_lat'] = pd.to_numeric(stops['stop_lat'], errors='coerce').fillna(0.0)
        stops['stop_lon'] = pd.to_numeric(stops['stop_lon'], errors='coerce').fillna(0.0)
        stops_map = stops.set_index('stop_id').to_dict(orient='index')

        stop_times['stop_sequence'] = pd.to_numeric(stop_times['stop_sequence'])
        stop_times = stop_times.sort_values(['trip_id', 'stop_sequence'])

        grouped = stop_times.groupby('trip_id')
        first_stops = grouped.first().reset_index()
        last_stops = grouped.last().reset_index()

        merged = pd.merge(first_stops, last_stops, on='trip_id', suffixes=('_orig', '_dest'))
        merged = pd.merge(merged, trips[['trip_id', 'service_id', 'trip_headsign']], on='trip_id', how='left')

        if calendar_dates is not None:
            merged = pd.merge(merged, calendar_dates[['service_id', 'date', 'exception_type']], on='service_id', how='inner')
            merged = merged[merged['exception_type'] == '1']
        else:
            merged['date'] = '2026-01-01'

        records = []
        for _, row in merged.iterrows():
            orig_id = str(row['stop_id_orig'])
            dest_id = str(row['stop_id_dest'])
            orig_info = stops_map.get(orig_id, {})
            dest_info = stops_map.get(dest_id, {})

            dep_time = str(row['departure_time_orig']).strip()[:5]
            arr_time = str(row['arrival_time_dest']).strip()[:5]

            d_raw = str(row['date']).strip()
            formatted_date = f"{d_raw[:4]}-{d_raw[4:6]}-{d_raw[6:8]}" if len(d_raw) == 8 else d_raw

            records.append({
                'operator_id': op_id,
                'date': formatted_date,
                'origin_id': orig_id,
                'origin_parent_id': orig_info.get('parent_station', orig_id),
                'origin_name': orig_info.get('stop_name', 'Gare Inconnue'),
                'origin_parent_name': orig_info.get('stop_name', 'Gare Inconnue'),
                'origin_lat': orig_info.get('stop_lat', 0.0),
                'origin_lon': orig_info.get('stop_lon', 0.0),

                'destination_id': dest_id,
                'destination_parent_id': dest_info.get('parent_station', dest_id),
                'destination_name': dest_info.get('stop_name', 'Gare Inconnue'),
                'destination_parent_name': dest_info.get('stop_name', 'Gare Inconnue'),
                'dest_lat': dest_info.get('stop_lat', 0.0),
                'dest_lon': dest_info.get('stop_lon', 0.0),

                'departure_time': dep_time,
                'arrival_time': arr_time,
                'dep_min': time_to_minutes(dep_time),
                'arr_min': time_to_minutes(arr_time),
                'train_no': row.get('trip_headsign', row['trip_id']),
                'train_type': op_id
            })

        df_trips = pd.DataFrame(records)

        cursor = conn.cursor()
        cursor.execute("DELETE FROM trips WHERE operator_id = ?", (op_id,))
        df_trips.to_sql('trips', conn, if_exists='append', index=False)
        conn.commit()
        print(f"✅ {len(df_trips)} trajets insérés pour {op_id}.")

    except Exception as e:
        print(f"❌ Erreur lors du traitement de {op_id}: {e}")

def main():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(BASE_DIR, 'eu_trains.db')

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('PRAGMA journal_mode = WAL;')
    cursor.execute('PRAGMA synchronous = NORMAL;')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS trips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id TEXT NOT NULL,
        date TEXT NOT NULL,
        origin_id TEXT NOT NULL,
        origin_parent_id TEXT NOT NULL,
        origin_name TEXT NOT NULL,
        origin_parent_name TEXT NOT NULL,
        origin_lat REAL,
        origin_lon REAL,
        destination_id TEXT NOT NULL,
        destination_parent_id TEXT NOT NULL,
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

    operators = load_operators()
    for op in operators:
        process_operator(op, conn)

    print("\n5. Création des index composites...")
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_direct ON trips(date, origin_name, destination_name, dep_min);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_transfer ON trips(date, origin_name, dep_min, arr_min);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_operator ON trips(operator_id);')

    conn.commit()
    conn.close()
    print("✅ Ingestion globale terminée !")

if __name__ == "__main__":
    main()