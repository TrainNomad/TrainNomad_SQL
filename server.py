import gzip
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="SNCF Multi-Transport API",
    description="API optimisée GTFS SQLite avec gestion des correspondances multiples et filtrage de pertinence.",
    version="2.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gtfs_indexed.db")
GZ_PATH = os.path.join(BASE_DIR, "gtfs_indexed.db.gz")


def ensure_db_decompressed():
    """Vérifie la présence de la BDD et décompresse la version .gz au besoin."""
    if not os.path.exists(DB_PATH):
        if not os.path.exists(GZ_PATH):
            raise FileNotFoundError(
                f"Ni 'gtfs_indexed.db' ni 'gtfs_indexed.db.gz' n'ont été trouvés dans {BASE_DIR}."
            )
        print("📦 Décompression de gtfs_indexed.db.gz...")
        with gzip.open(GZ_PATH, "rb") as f_in:
            with open(DB_PATH, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        print("✅ Base décompressée avec succès !")


def get_db_connection():
    ensure_db_decompressed()
    conn = sqlite3.connect("file:" + DB_PATH + "?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


@app.on_event("startup")
def startup_event():
    ensure_db_decompressed()


@app.get("/health")
def health_check():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        return {
            "status": "ok",
            "db_path": DB_PATH,
            "db_exists": os.path.exists(DB_PATH),
            "tables": tables,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Erreur vérification BDD: {str(e)}"
        )


# -------------------------------------------------------------------
# 2. AUTOCOMPLÉTION DES GARES ET VILLES
# -------------------------------------------------------------------
@app.get("/stations")
def get_stations(q: str = Query(None, description="Recherche partielle de gare ou ville")): #[cite: 11]
    """Retourne la liste des métropoles et gares correspondant au terme tapé"""
    if not q or not q.strip(): #[cite: 11]
        return {"results": []} #[cite: 11]

    conn = get_db_connection() #[cite: 11]
    cursor = conn.cursor() #[cite: 11]
    search_pattern = q.strip().upper() + "%" #[cite: 11]

    try:
        # Recherche par métropole (parent station)[cite: 11]
        query_cities = """
            SELECT DISTINCT origin_parent_name AS name, origin_parent_id AS id 
            FROM trips 
            WHERE UPPER(origin_parent_name) LIKE ?
            ORDER BY name ASC
            LIMIT 5
        """ #[cite: 11]
        cursor.execute(query_cities, (search_pattern,)) #[cite: 11]
        cities = [
            {
                "type": "city", #[cite: 11]
                "label": row["name"], #[cite: 11]
                "id": row["id"], #[cite: 11]
                "country": "France", # Alimente le sous-titre de la ville en JS
                "search_val": f"{row['name']} (toutes les gares)", #[cite: 11]
            }
            for row in cursor.fetchall() #[cite: 11]
        ]

        # Recherche par gare spécifique[cite: 11]
        query_stations = """
            SELECT DISTINCT origin_name AS name, origin_parent_name AS parent, origin_id AS id 
            FROM trips 
            WHERE UPPER(origin_name) LIKE ?
            ORDER BY name ASC
            LIMIT 10
        """ #[cite: 11]
        cursor.execute(query_stations, (search_pattern,)) #[cite: 11]
        stations = [
            {
                "type": "station", #[cite: 11]
                "label": row["name"], #[cite: 11]
                "city": row["parent"],  # Remplacé 'parent' par 'city' pour matcher le frontend
                "id": row["id"], #[cite: 11]
                "search_val": row["name"], #[cite: 11]
            }
            for row in cursor.fetchall() #[cite: 11]
        ]

        conn.close() #[cite: 11]
        
        # Concaténation : Villes en premier (Niveau 1), gares associées en dessous (Niveau 2)
        return {"results": cities + stations} #[cite: 11]

    except Exception as e:
        conn.close() #[cite: 11]
        raise HTTPException(status_code=500, detail=f"Erreur autocomplétion: {str(e)}") #[cite: 11]
    
@app.get("/explorer")
def explore_destinations_stream(
    from_station: Optional[str] = Query(None, alias="from"),
    origin: Optional[str] = Query(None),
    date: str = Query(...),
):
    start_label = (from_station or origin or "").strip()
    if not start_label:
        raise HTTPException(status_code=400, detail="Paramètre 'from' requis")

    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
    SELECT 
        s2.stop_name AS dest_name,
        s2.stop_lat AS dest_lat, 
        s2.stop_lon AS dest_lon,
        st1.departure_time AS dep_str,
        st2.arrival_time AS arr_str,
        st1.dep_min, 
        st2.dep_min AS arr_min,
        t.trip_headsign AS train_no,
        COALESCE(NULLIF(r.train_type, 'TRAIN'), 'Train SNCF') AS train_type
    FROM stop_times st1
    JOIN stop_times st2 ON st1.trip_id = st2.trip_id AND st1.stop_sequence < st2.stop_sequence
    JOIN trips t ON st1.trip_id = t.trip_id
    JOIN routes r ON t.route_id = r.route_id
    JOIN calendar_dates cd ON t.service_id = cd.service_id
    JOIN stops s1 ON st1.stop_id = s1.stop_id
    JOIN stops s2 ON st2.stop_id = s2.stop_id
    WHERE (UPPER(s1.stop_name) LIKE UPPER(?) OR UPPER(s1.parent_name) LIKE UPPER(?))
      AND cd.date = ?
      AND cd.exception_type = 1
    ORDER BY st1.dep_min ASC
    LIMIT 200
    """
    
    cursor.execute(query, (f"%{start_label}%", f"%{start_label}%", date.strip()))
    rows = cursor.fetchall()
    conn.close()

    results = []
    seen_destinations = set()

    for row in rows:
        dest_name = row["dest_name"]
        if dest_name in seen_destinations:
            continue
        
        duration_min = row["arr_min"] - row["dep_min"]
        if duration_min < 0:
            duration_min += 24 * 60

        seen_destinations.add(dest_name)
        results.append({
            "dest_name": dest_name,
            "dest_lat": row["dest_lat"],
            "dest_lon": row["dest_lon"],
            "duration": duration_min,
            "train1_dep": row["dep_str"],
            "train2_arr": row["arr_str"],
            "transfers": 0,
            "train1_no": row["train_no"],
            "train1_type": row["train_type"]
        })

    return results


def parse_time_to_min(time_str: str) -> int:
    """Convertit une chaîne HH:MM:SS en minutes depuis minuit."""
    parts = list(map(int, time_str.split(":")))
    return parts[0] * 60 + parts[1]


@app.get("/search")
def search_all(
    origin: str = Query(..., description="Nom de la gare ou ville de départ"),
    destination: str = Query(..., description="Nom de la gare ou ville d'arrivée"),
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
    departure_time: Optional[str] = Query(
        "00:00:00", description="Heure minimale de départ (HH:MM:SS)"
    ),
    limit: int = Query(10, description="Nombre de trajets à retourner"),
):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        date_clean = date.strip()
        orig_label = origin.strip()
        dest_label = destination.strip()
        start_min = parse_time_to_min(departure_time)

        # ==========================================
        # 1. Trajets Directs (0 correspondance)
        # ==========================================
        query_direct = """
        SELECT DISTINCT
            s1.stop_name AS orig, s2.stop_name AS dest,
            s1.stop_lat AS orig_lat, s1.stop_lon AS orig_lon,
            s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon,
            st1.departure_time AS train1_dep, st2.arrival_time AS train1_arr,
            t.trip_headsign AS train1_no,
            COALESCE(NULLIF(r.train_type, 'TRAIN'), 'Train SNCF') AS train1_type,
            st1.dep_min AS dep_min, st2.dep_min AS arr_min
        FROM stop_times st1
        JOIN stop_times st2 ON st1.trip_id = st2.trip_id AND st1.stop_sequence < st2.stop_sequence
        JOIN trips t ON st1.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        JOIN calendar_dates cd ON t.service_id = cd.service_id
        JOIN stops s1 ON st1.stop_id = s1.stop_id
        JOIN stops s2 ON st2.stop_id = s2.stop_id
        WHERE (UPPER(s1.stop_name) = UPPER(?) OR UPPER(s1.parent_name) = UPPER(?))
          AND (UPPER(s2.stop_name) = UPPER(?) OR UPPER(s2.parent_name) = UPPER(?))
          AND cd.date = ?
          AND cd.exception_type = 1
          AND st1.dep_min >= ?
        ORDER BY st1.dep_min ASC
        LIMIT ?
        """
        cursor.execute(
            query_direct,
            (orig_label, orig_label, dest_label, dest_label, date_clean, start_min, limit * 2),
        )
        direct_rows = cursor.fetchall()

        direct_results = []
        direct_train_numbers = set()

        for d in direct_rows:
            dur = d["arr_min"] - d["dep_min"]
            if dur < 0:
                dur += 24 * 60

            direct_results.append({
                "is_direct": True,
                "transfers_count": 0,
                "orig": d["orig"],
                "dest": d["dest"],
                "orig_lat": d["orig_lat"],
                "orig_lon": d["orig_lon"],
                "dest_lat": d["dest_lat"],
                "dest_lon": d["dest_lon"],
                "date": date_clean,
                "train1_no": d["train1_no"],
                "train1_type": d["train1_type"],
                "train1_dep": d["train1_dep"],
                "train1_arr": d["train1_arr"],
                "transfer_station_arr": None,
                "transfer_station_dep": None,
                "train2_no": None,
                "train2_type": None,
                "train2_dep": None,
                "train2_arr": None,
                "layover_minutes": 0,
                "transfer_station_2_arr": None,
                "transfer_station_2_dep": None,
                "train3_no": None,
                "train3_type": None,
                "train3_dep": None,
                "train3_arr": None,
                "layover_minutes_2": 0,
                "total_duration_min": dur,
                "dep_min": d["dep_min"],
                "arr_min": d["arr_min"],
            })
            if d["train1_no"]:
                direct_train_numbers.add(d["train1_no"])

        # ==========================================
        # 2. Correspondances - 1 Escale (2 trains)
        # ==========================================
        query_conn_1 = """
        WITH train1 AS (
            SELECT 
                t1.trip_headsign AS train1_no,
                COALESCE(NULLIF(r1.train_type, 'TRAIN'), 'Train SNCF') AS train1_type,
                st1_dep.departure_time AS train1_dep, st1_arr.arrival_time AS train1_arr,
                st1_arr.dep_min AS arr_min1, st1_dep.dep_min AS dep_min1,
                s_trans1.stop_name AS transfer_station_arr, 
                s_trans1.parent_name AS transfer_parent,
                s_trans1.stop_lat AS transfer_lat, s_trans1.stop_lon AS transfer_lon,
                s1.stop_name AS orig, s1.stop_lat AS orig_lat, s1.stop_lon AS orig_lon
            FROM stop_times st1_dep
            JOIN stop_times st1_arr ON st1_dep.trip_id = st1_arr.trip_id AND st1_dep.stop_sequence < st1_arr.stop_sequence
            JOIN trips t1 ON st1_dep.trip_id = t1.trip_id
            JOIN routes r1 ON t1.route_id = r1.route_id
            JOIN calendar_dates cd1 ON t1.service_id = cd1.service_id
            JOIN stops s1 ON st1_dep.stop_id = s1.stop_id
            JOIN stops s_trans1 ON st1_arr.stop_id = s_trans1.stop_id
            WHERE (UPPER(s1.stop_name) = UPPER(?) OR UPPER(s1.parent_name) = UPPER(?))
              AND cd1.date = ? 
              AND cd1.exception_type = 1
              AND st1_dep.dep_min >= ?
            LIMIT 300
        ),
        train2 AS (
            SELECT 
                t2.trip_headsign AS train2_no,
                COALESCE(NULLIF(r2.train_type, 'TRAIN'), 'Train SNCF') AS train2_type,
                st2_dep.departure_time AS train2_dep, st2_arr.arrival_time AS train2_arr,
                st2_dep.dep_min AS dep_min2, st2_arr.dep_min AS arr_min2,
                s_trans2.stop_name AS transfer_station_dep,
                s_trans2.parent_name AS transfer_parent,
                s2.stop_name AS dest, s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon
            FROM stop_times st2_dep
            JOIN stop_times st2_arr ON st2_dep.trip_id = st2_arr.trip_id AND st2_dep.stop_sequence < st2_arr.stop_sequence
            JOIN trips t2 ON st2_dep.trip_id = t2.trip_id
            JOIN routes r2 ON t2.route_id = r2.route_id
            JOIN calendar_dates cd2 ON t2.service_id = cd2.service_id
            JOIN stops s_trans2 ON st2_dep.stop_id = s_trans2.stop_id
            JOIN stops s2 ON st2_arr.stop_id = s2.stop_id
            WHERE (UPPER(s2.stop_name) = UPPER(?) OR UPPER(s2.parent_name) = UPPER(?))
              AND cd2.date = ? 
              AND cd2.exception_type = 1
        )
        SELECT 
            t1.orig, t1.orig_lat, t1.orig_lon,
            t1.transfer_station_arr, t1.transfer_lat, t1.transfer_lon,
            t2.transfer_station_dep,
            t2.dest, t2.dest_lat, t2.dest_lon,
            t1.train1_no, t1.train1_type, t1.train1_dep, t1.train1_arr,
            t2.train2_no, t2.train2_type, t2.train2_dep, t2.train2_arr,
            (t2.dep_min2 - t1.arr_min1) AS layover_minutes,
            t1.dep_min1 AS dep_min, t2.arr_min2 AS arr_min
        FROM train1 t1
        JOIN train2 t2 ON t1.transfer_parent = t2.transfer_parent
        WHERE t2.dep_min2 >= (t1.arr_min1 + 12)
          AND t2.dep_min2 <= (t1.arr_min1 + 150)
        ORDER BY t1.dep_min1 ASC
        LIMIT 200
        """
        cursor.execute(
            query_conn_1,
            (orig_label, orig_label, date_clean, start_min, dest_label, dest_label, date_clean),
        )
        conn_1_rows = [dict(row) for row in cursor.fetchall()]

        # ==========================================
        # 3. Correspondances - 2 Escales (3 trains)
        # ==========================================
        query_conn_2 = """
        WITH train1 AS (
            SELECT 
                t1.trip_headsign AS train1_no,
                COALESCE(NULLIF(r1.train_type, 'TRAIN'), 'Train SNCF') AS train1_type,
                st1_dep.departure_time AS train1_dep, st1_arr.arrival_time AS train1_arr,
                st1_arr.dep_min AS arr_min1, st1_dep.dep_min AS dep_min1,
                s_trans1.stop_name AS transfer1_arr, s_trans1.parent_name AS trans1_parent,
                s1.stop_name AS orig, s1.stop_lat AS orig_lat, s1.stop_lon AS orig_lon
            FROM stop_times st1_dep
            JOIN stop_times st1_arr ON st1_dep.trip_id = st1_arr.trip_id AND st1_dep.stop_sequence < st1_arr.stop_sequence
            JOIN trips t1 ON st1_dep.trip_id = t1.trip_id
            JOIN routes r1 ON t1.route_id = r1.route_id
            JOIN calendar_dates cd1 ON t1.service_id = cd1.service_id
            JOIN stops s1 ON st1_dep.stop_id = s1.stop_id
            JOIN stops s_trans1 ON st1_arr.stop_id = s_trans1.stop_id
            WHERE (UPPER(s1.stop_name) = UPPER(?) OR UPPER(s1.parent_name) = UPPER(?))
              AND cd1.date = ? AND cd1.exception_type = 1
              AND st1_dep.dep_min >= ?
            LIMIT 150
        ),
        train2 AS (
            SELECT 
                t2.trip_headsign AS train2_no,
                COALESCE(NULLIF(r2.train_type, 'TRAIN'), 'Train SNCF') AS train2_type,
                st2_dep.departure_time AS train2_dep, st2_arr.arrival_time AS train2_arr,
                st2_dep.dep_min AS dep_min2, st2_arr.dep_min AS arr_min2,
                s_trans2_dep.stop_name AS transfer1_dep, s_trans2_dep.parent_name AS trans1_parent,
                s_trans2_arr.stop_name AS transfer2_arr, s_trans2_arr.parent_name AS trans2_parent
            FROM stop_times st2_dep
            JOIN stop_times st2_arr ON st2_dep.trip_id = st2_arr.trip_id AND st2_dep.stop_sequence < st2_arr.stop_sequence
            JOIN trips t2 ON st2_dep.trip_id = t2.trip_id
            JOIN routes r2 ON t2.route_id = r2.route_id
            JOIN calendar_dates cd2 ON t2.service_id = cd2.service_id
            JOIN stops s_trans2_dep ON st2_dep.stop_id = s_trans2_dep.stop_id
            JOIN stops s_trans2_arr ON st2_arr.stop_id = s_trans2_arr.stop_id
            WHERE cd2.date = ? AND cd2.exception_type = 1
            LIMIT 300
        ),
        train3 AS (
            SELECT 
                t3.trip_headsign AS train3_no,
                COALESCE(NULLIF(r3.train_type, 'TRAIN'), 'Train SNCF') AS train3_type,
                st3_dep.departure_time AS train3_dep, st3_arr.arrival_time AS train3_arr,
                st3_dep.dep_min AS dep_min3, st3_arr.dep_min AS arr_min3,
                s_trans3.stop_name AS transfer2_dep, s_trans3.parent_name AS trans2_parent,
                s2.stop_name AS dest, s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon
            FROM stop_times st3_dep
            JOIN stop_times st3_arr ON st3_dep.trip_id = st3_arr.trip_id AND st3_dep.stop_sequence < st3_arr.stop_sequence
            JOIN trips t3 ON st3_dep.trip_id = t3.trip_id
            JOIN routes r3 ON t3.route_id = r3.route_id
            JOIN calendar_dates cd3 ON t3.service_id = cd3.service_id
            JOIN stops s_trans3 ON st3_dep.stop_id = s_trans3.stop_id
            JOIN stops s2 ON st3_arr.stop_id = s2.stop_id
            WHERE (UPPER(s2.stop_name) = UPPER(?) OR UPPER(s2.parent_name) = UPPER(?))
              AND cd3.date = ? AND cd3.exception_type = 1
            LIMIT 150
        )
        SELECT 
            t1.orig, t1.orig_lat, t1.orig_lon,
            t1.transfer1_arr AS transfer_station_arr, t2.transfer1_dep AS transfer_station_dep,
            t2.transfer2_arr AS transfer_station_2_arr, t3.transfer2_dep AS transfer_station_2_dep,
            t3.dest, t3.dest_lat, t3.dest_lon,
            t1.train1_no, t1.train1_type, t1.train1_dep, t1.train1_arr,
            t2.train2_no, t2.train2_type, t2.train2_dep, t2.train2_arr,
            t3.train3_no, t3.train3_type, t3.train3_dep, t3.train3_arr,
            (t2.dep_min2 - t1.arr_min1) AS layover_minutes,
            (t3.dep_min3 - t2.arr_min2) AS layover_minutes_2,
            t1.dep_min1 AS dep_min, t3.arr_min3 AS arr_min
        FROM train1 t1
        JOIN train2 t2 ON t1.trans1_parent = t2.trans1_parent
        JOIN train3 t3 ON t2.trans2_parent = t3.trans2_parent
        WHERE t2.dep_min2 >= (t1.arr_min1 + 12) AND t2.dep_min2 <= (t1.arr_min1 + 120)
          AND t3.dep_min3 >= (t2.arr_min2 + 12) AND t3.dep_min3 <= (t2.arr_min2 + 120)
        ORDER BY t1.dep_min1 ASC
        LIMIT 100
        """
        cursor.execute(
            query_conn_2,
            (orig_label, orig_label, date_clean, start_min, date_clean, dest_label, dest_label, date_clean),
        )
        conn_2_rows = [dict(row) for row in cursor.fetchall()]

        # ==========================================
        # 4. Traitement, Filtrage & Déduplication
        # ==========================================
        valid_connections = []
        seen_route_signatures = set()

        # Filtrage 1 correspondance
        for c in conn_1_rows:
            if c["train1_no"] in direct_train_numbers:
                continue

            is_same_station = c["transfer_station_arr"] == c["transfer_station_dep"]
            layover = c["layover_minutes"]
            is_valid = (12 <= layover <= 120) if is_same_station else (45 <= layover <= 180)

            if not is_valid:
                continue

            tot_duration = c["arr_min"] - c["dep_min"]
            if tot_duration < 0:
                tot_duration += 24 * 60

            # Signature unique pour éviter d'afficher le même trajet à quelques secondes près
            signature = f"{c['train1_dep']}_{c['train1_no']}_{c['transfer_station_arr']}_{c['train2_no']}"
            if signature in seen_route_signatures:
                continue
            seen_route_signatures.add(signature)

            valid_connections.append({
                "is_direct": False,
                "transfers_count": 1,
                "orig": c["orig"],
                "dest": c["dest"],
                "orig_lat": c["orig_lat"],
                "orig_lon": c["orig_lon"],
                "dest_lat": c["dest_lat"],
                "dest_lon": c["dest_lon"],
                "date": date_clean,
                "train1_no": c["train1_no"],
                "train1_type": c["train1_type"],
                "train1_dep": c["train1_dep"],
                "train1_arr": c["train1_arr"],
                "transfer_station_arr": c["transfer_station_arr"],
                "transfer_station_dep": c["transfer_station_dep"],
                "train2_no": c["train2_no"],
                "train2_type": c["train2_type"],
                "train2_dep": c["train2_dep"],
                "train2_arr": c["train2_arr"],
                "layover_minutes": layover,
                "transfer_station_2_arr": None,
                "transfer_station_2_dep": None,
                "train3_no": None,
                "train3_type": None,
                "train3_dep": None,
                "train3_arr": None,
                "layover_minutes_2": 0,
                "total_duration_min": tot_duration,
                "dep_min": c["dep_min"],
                "arr_min": c["arr_min"],
            })

        # Filtrage 2 correspondances
        for c in conn_2_rows:
            if c["train1_no"] in direct_train_numbers:
                continue

            is_same_1 = c["transfer_station_arr"] == c["transfer_station_dep"]
            is_same_2 = c["transfer_station_2_arr"] == c["transfer_station_2_dep"]

            valid_1 = (12 <= c["layover_minutes"] <= 120) if is_same_1 else (45 <= c["layover_minutes"] <= 180)
            valid_2 = (12 <= c["layover_minutes_2"] <= 120) if is_same_2 else (45 <= c["layover_minutes_2"] <= 180)

            if not (valid_1 and valid_2):
                continue

            tot_duration = c["arr_min"] - c["dep_min"]
            if tot_duration < 0:
                tot_duration += 24 * 60

            signature = f"{c['train1_dep']}_{c['train1_no']}_{c['transfer_station_arr']}_{c['train2_no']}_{c['transfer_station_2_arr']}_{c['train3_no']}"
            if signature in seen_route_signatures:
                continue
            seen_route_signatures.add(signature)

            valid_connections.append({
                "is_direct": False,
                "transfers_count": 2,
                "orig": c["orig"],
                "dest": c["dest"],
                "orig_lat": c["orig_lat"],
                "orig_lon": c["orig_lon"],
                "dest_lat": c["dest_lat"],
                "dest_lon": c["dest_lon"],
                "date": date_clean,
                "train1_no": c["train1_no"],
                "train1_type": c["train1_type"],
                "train1_dep": c["train1_dep"],
                "train1_arr": c["train1_arr"],
                "transfer_station_arr": c["transfer_station_arr"],
                "transfer_station_dep": c["transfer_station_dep"],
                "train2_no": c["train2_no"],
                "train2_type": c["train2_type"],
                "train2_dep": c["train2_dep"],
                "train2_arr": c["train2_arr"],
                "layover_minutes": c["layover_minutes"],
                "transfer_station_2_arr": c["transfer_station_2_arr"],
                "transfer_station_2_dep": c["transfer_station_2_dep"],
                "train3_no": c["train3_no"],
                "train3_type": c["train3_type"],
                "train3_dep": c["train3_dep"],
                "train3_arr": c["train3_arr"],
                "layover_minutes_2": c["layover_minutes_2"],
                "total_duration_min": tot_duration,
                "dep_min": c["dep_min"],
                "arr_min": c["arr_min"],
            })

        # ==========================================
        # 5. Fusion et Tri par Pertinence
        # ==========================================
        combined = direct_results + valid_connections

        # Tri : Heure de départ > Nombre de correspondances > Durée totale
        combined.sort(key=lambda x: (x["dep_min"], x["transfers_count"], x["total_duration_min"]))

        page_results = combined[:limit]

        next_cursor = None
        if len(combined) > limit:
            last_dep = page_results[-1]["train1_dep"]
            h, m, s = map(int, last_dep.split(":"))
            next_time = (
                datetime(2000, 1, 1, h, m, s) + timedelta(seconds=1)
            ).time()
            next_cursor = str(next_time)

        # Nettoyage des clés internes
        for r in page_results:
            r.pop("dep_min", None)
            r.pop("arr_min", None)

        conn.close()
        return {
            "count": len(page_results),
            "next_cursor": next_cursor,
            "results": page_results,
        }

    except Exception as e:
        conn.close()
        raise HTTPException(
            status_code=500, detail=f"Erreur recherche: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)