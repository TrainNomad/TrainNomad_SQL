from datetime import datetime, timedelta
import io
import os
import sqlite3
import zipfile
import pandas as pd
import requests

# 1. Configuration des dates (Fenêtre de 90 jours)
TODAY = datetime.now()
DATE_MIN = (TODAY - timedelta(days=1)).strftime("%Y%m%d")
DATE_MAX = (TODAY + timedelta(days=90)).strftime("%Y%m%d")

DB_FILE = "eu_trains.db"


def init_db(conn):
    """Initialise la structure de la base de données SQLite."""
    cursor = conn.cursor()
    cursor.executescript(
        """
        DROP TABLE IF EXISTS stops;
        DROP TABLE IF EXISTS trips;
        DROP TABLE IF EXISTS stop_times;
        DROP TABLE IF EXISTS operators;

        CREATE TABLE operators (
            id TEXT PRIMARY KEY,
            name TEXT
        );

        CREATE TABLE stops (
            stop_id TEXT PRIMARY KEY,
            stop_name TEXT,
            stop_lat REAL,
            stop_lon REAL,
            operator_id TEXT
        );

        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY,
            route_id TEXT,
            service_id TEXT,
            trip_headsign TEXT,
            operator_id TEXT
        );

        CREATE TABLE stop_times (
            trip_id TEXT,
            arrival_time TEXT,
            departure_time TEXT,
            stop_id TEXT,
            stop_sequence INTEGER,
            date TEXT,
            operator_id TEXT
        );
    """
    )
    conn.commit()


def process_gtfs(operator, conn):
    """Télécharge, filtre et insère un flux GTFS dans la BDD."""
    op_id = operator["id"]
    print(f"--- Traitement de l'opérateur : {operator['name']} ({op_id}) ---")

    try:
        response = requests.get(operator["gtfs_url"], timeout=60)
        response.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(response.content))
    except Exception as e:
        print(f"Erreur lors du téléchargement pour {op_id}: {e}")
        return

    # A. Filtrage du calendrier (90 jours)
    active_services = set()
    service_dates_map = {}  # service_id -> list of dates

    if "calendar_dates.txt" in z.namelist():
        df_cal_dates = pd.read_csv(
            z.open("calendar_dates.txt"), dtype=str, usecols=["service_id", "date", "exception_type"]
        )
        df_cal_dates = df_cal_dates[
            (df_cal_dates["date"] >= DATE_MIN)
            & (df_cal_dates["date"] <= DATE_MAX)
            & (df_cal_dates["exception_type"] == "1")
        ]

        for _, row in df_cal_dates.iterrows():
            s_id, d = row["service_id"], row["date"]
            active_services.add(s_id)
            service_dates_map.setdefault(s_id, []).append(d)

    if not active_services:
        print(f"Aucun service actif trouvé pour {op_id} dans la fenêtre de 90 jours.")
        return

    # B. Traitement des TRIPS
    df_trips = pd.read_csv(
        z.open("trips.txt"),
        dtype=str,
        usecols=["trip_id", "route_id", "service_id", "trip_headsign"],
    )
    df_trips = df_trips[df_trips["service_id"].isin(active_services)].copy()
    df_trips["operator_id"] = op_id

    valid_trips = set(df_trips["trip_id"])

    # C. Traitement des STOPS
    df_stops = pd.read_csv(
        z.open("stops.txt"),
        dtype={"stop_id": str, "stop_name": str, "stop_lat": float, "stop_lon": float},
        usecols=["stop_id", "stop_name", "stop_lat", "stop_lon"],
    )
    df_stops["operator_id"] = op_id

    # D. Traitement des STOP_TIMES (Table la plus lourde)
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
    df_st = df_st[df_st["trip_id"].isin(valid_trips)].copy()

    # Association trip_id -> dates via service_id
    trip_to_service = dict(zip(df_trips["trip_id"], df_trips["service_id"]))

    # Expansion des stop_times par date (uniquement les dates valides)
    st_expanded = []
    for row in df_st.itertuples(index=False):
        srv_id = trip_to_service.get(row.trip_id)
        if srv_id in service_dates_map:
            for d in service_dates_map[srv_id]:
                st_expanded.append(
                    (
                        row.trip_id,
                        row.arrival_time,
                        row.departure_time,
                        row.stop_id,
                        row.stop_sequence,
                        d,
                        op_id,
                    )
                )

    # E. Insertion dans SQLite
    cursor = conn.cursor()

    # Insertion Opérateur
    cursor.execute("INSERT OR REPLACE INTO operators VALUES (?, ?)", (op_id, operator["name"]))

    # Insertion Stops
    df_stops.to_sql("stops", conn, if_exists="append", index=False)

    # Insertion Trips
    df_trips.to_sql("trips", conn, if_exists="append", index=False)

    # Insertion Stop_times
    cursor.executemany(
        """
        INSERT INTO stop_times (trip_id, arrival_time, departure_time, stop_id, stop_sequence, date, operator_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        st_expanded,
    )

    conn.commit()
    print(f"Succès pour {op_id} : {len(st_expanded)} lignes d'arrêts insérées.")


def finalize_db(conn):
    """Crée les index de performance et compacte le fichier BDD."""
    print("--- Création des index SQL ---")
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_st_date_stop ON stop_times(date, stop_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_st_trip ON stop_times(trip_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stops_op ON stops(operator_id);")
    conn.commit()

    print("--- Compactage de la base (VACUUM) ---")
    conn.execute("VACUUM;")
    conn.close()


def main():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)

    # Liste des opérateurs (Exemple : à charger depuis operators.json si présent)
    operators = [
        {
            "id": "sncf",
            "name": "SNCF / TGV Inoui / Ouigo",
            "gtfs_url": "https://eu.api.ovh.com/gtfs/sncf.zip",  # Adapter l'URL exacte
        }
    ]

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    for op in operators:
        process_gtfs(op, conn)

    finalize_db(conn)
    print(f"Base de données générée avec succès : {DB_FILE}")


if __name__ == "__main__":
    main()