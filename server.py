import gzip
import os
import shutil
import sqlite3
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="SNCF Multi-Transport API",
    description="API GTFS SQLite rapide : recherche fiable et déduplication intelligente.",
    version="3.1.0",
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

def ensure_db_and_indexes():
    """Décompresse la DB si besoin et crée les index SQLite pour accélérer les requêtes."""
    if not os.path.exists(DB_PATH):
        if not os.path.exists(GZ_PATH):
            raise FileNotFoundError("Base de données introuvable.")
        print("📦 Décompression de gtfs_indexed.db.gz...")
        with gzip.open(GZ_PATH, 'rb') as f_in:
            with open(DB_PATH, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE INDEX IF NOT EXISTS idx_stops_name ON stops(stop_name);
        CREATE INDEX IF NOT EXISTS idx_st_stop_trip ON stop_times(stop_id, trip_id);
        CREATE INDEX IF NOT EXISTS idx_cd_date_serv ON calendar_dates(date, service_id, exception_type);
    """)
    conn.commit()
    conn.close()

def get_db_connection():
    ensure_db_and_indexes()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA cache_size = -64000;")  # 64 Mo de cache RAM
    return conn

def deduplicate_and_filter_trips(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Conserve la meilleure option par horaire de départ :
    - Priorité absolue aux trajets directs.
    - Sinon, conserve l'option qui arrive le plus tôt.
    """
    best_routes_by_dep: Dict[str, Dict[str, Any]] = {}

    for route in results:
        dep_time = route["train1_dep"]
        arr_time = route["train2_arr"] if route["train2_arr"] else route["train1_arr"]

        if dep_time not in best_routes_by_dep:
            best_routes_by_dep[dep_time] = route
        else:
            existing = best_routes_by_dep[dep_time]
            existing_arr = existing["train2_arr"] if existing["train2_arr"] else existing["train1_arr"]

            if route["is_direct"] and not existing["is_direct"]:
                best_routes_by_dep[dep_time] = route
            elif route["is_direct"] == existing["is_direct"]:
                if arr_time < existing_arr:
                    best_routes_by_dep[dep_time] = route

    final_list = list(best_routes_by_dep.values())
    final_list.sort(key=lambda x: x["train1_dep"])
    return final_list

@app.get("/stations")
def get_stations(q: str = Query(None, description="Recherche partielle du nom de gare")):
    if not q or not q.strip():
        return {"results": []}

    conn = get_db_connection()
    cursor = conn.cursor()
    search_pattern = "%" + q.strip().upper() + "%"

    try:
        query_stations = """
            SELECT DISTINCT stop_name, AVG(stop_lat) as stop_lat, AVG(stop_lon) as stop_lon, clean_uic
            FROM stops 
            WHERE UPPER(stop_name) LIKE ?
            GROUP BY stop_name
            ORDER BY stop_name ASC
            LIMIT 15
        """
        cursor.execute(query_stations, (search_pattern,))
        stations = [
            {
                "type": "station",
                "label": row["stop_name"],
                "uic": row["clean_uic"],
                "lat": row["stop_lat"],
                "lon": row["stop_lon"],
            }
            for row in cursor.fetchall()
        ]
        conn.close()
        return {"results": stations}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur autocomplétion: {str(e)}")

@app.get("/search")
def search_all(
    origin: str = Query(..., description="Nom de la gare de départ"),
    destination: str = Query(..., description="Nom de la gare d'arrivée"),
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        date_clean = date.strip()
        orig_label = origin.strip()
        dest_label = destination.strip()

        # 1. Requête des trajets directs
        query_direct = """
        SELECT 
            s1.stop_name AS orig,
            s2.stop_name AS dest,
            s1.stop_lat AS orig_lat, s1.stop_lon AS orig_lon,
            s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon,
            st1.departure_time AS train1_dep,
            st2.arrival_time AS train1_arr,
            t.trip_headsign AS train1_no,
            t.train_type AS train1_type,
            st1.dep_min
        FROM stop_times st1
        JOIN stop_times st2 ON st1.trip_id = st2.trip_id AND st1.stop_sequence < st2.stop_sequence
        JOIN trips t ON st1.trip_id = t.trip_id
        JOIN calendar_dates cd ON t.service_id = cd.service_id
        JOIN stops s1 ON st1.stop_id = s1.stop_id
        JOIN stops s2 ON st2.stop_id = s2.stop_id
        WHERE UPPER(s1.stop_name) = UPPER(?)
          AND UPPER(s2.stop_name) = UPPER(?)
          AND cd.date = ?
          AND cd.exception_type = 1
        ORDER BY st1.dep_min ASC
        """
        cursor.execute(query_direct, (orig_label, dest_label, date_clean))
        direct_rows = cursor.fetchall()

        direct_results = [
            {
                "is_direct": True,
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
                "is_valid_layover": True,
            }
            for d in direct_rows
        ]

        # 2. Requête des trajets avec correspondance
        query_connections = """
        SELECT 
            s1.stop_name AS orig,
            s1.stop_lat AS orig_lat, s1.stop_lon AS orig_lon,
            s_trans1.stop_name AS transfer_station_arr,
            s_trans1.stop_lat AS transfer_lat, s_trans1.stop_lon AS transfer_lon,
            s_trans2.stop_name AS transfer_station_dep,
            s2.stop_name AS dest,
            s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon,
            t1.trip_headsign AS train1_no,
            t1.train_type AS train1_type,
            st1_dep.departure_time AS train1_dep,
            st1_arr.arrival_time AS train1_arr,
            t2.trip_headsign AS train2_no,
            t2.train_type AS train2_type,
            st2_dep.departure_time AS train2_dep,
            st2_arr.arrival_time AS train2_arr,
            (st2_dep.dep_min - st1_arr.dep_min) AS layover_minutes
        FROM stop_times st1_dep
        JOIN stop_times st1_arr ON st1_dep.trip_id = st1_arr.trip_id AND st1_dep.stop_sequence < st1_arr.stop_sequence
        JOIN trips t1 ON st1_dep.trip_id = t1.trip_id
        JOIN calendar_dates cd1 ON t1.service_id = cd1.service_id

        JOIN stops s_trans1 ON st1_arr.stop_id = s_trans1.stop_id
        JOIN stops s_trans2 ON s_trans1.stop_name = s_trans2.stop_name
        JOIN stop_times st2_dep ON s_trans2.stop_id = st2_dep.stop_id

        JOIN stop_times st2_arr ON st2_dep.trip_id = st2_arr.trip_id AND st2_dep.stop_sequence < st2_arr.stop_sequence
        JOIN trips t2 ON st2_dep.trip_id = t2.trip_id
        JOIN calendar_dates cd2 ON t2.service_id = cd2.service_id

        JOIN stops s1 ON st1_dep.stop_id = s1.stop_id
        JOIN stops s2 ON st2_arr.stop_id = s2.stop_id

        WHERE UPPER(s1.stop_name) = UPPER(?)
          AND UPPER(s2.stop_name) = UPPER(?)
          AND cd1.date = ? AND cd1.exception_type = 1
          AND cd2.date = ? AND cd2.exception_type = 1
          AND st2_dep.dep_min >= (st1_arr.dep_min + 15)
          AND st2_dep.dep_min <= (st1_arr.dep_min + 180)
        ORDER BY st1_dep.dep_min ASC
        """
        cursor.execute(query_connections, (orig_label, dest_label, date_clean, date_clean))
        conn_rows = [dict(row) for row in cursor.fetchall()]

        valid_connections = [
            {**c, "is_direct": False, "date": date_clean, "is_valid_layover": True}
            for c in conn_rows
        ]

        # 3. Fusion et filtrage des doublons
        raw_results = direct_results + valid_connections
        filtered_results = deduplicate_and_filter_trips(raw_results)

        conn.close()
        return {"count": len(filtered_results), "results": filtered_results}

    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur recherche: {str(e)}")

@app.get("/explorer")
def explore_destinations(
    from_station: Optional[str] = Query(None, alias="from", description="Nom de la gare de départ"),
    origin: Optional[str] = Query(None, description="Alternative à 'from'"),
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
):
    start_label = (from_station or origin or "").strip()
    if not start_label:
        raise HTTPException(status_code=400, detail="Paramètre 'from' ou 'origin' requis")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        date_clean = date.strip()
        best_journeys = {}

        query_direct = """
        SELECT 
            s2.stop_name AS to_name,
            s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon,
            st1.departure_time AS dep_str,
            st2.arrival_time AS arr_str,
            st1.dep_min, st2.dep_min AS arr_min,
            t.trip_headsign AS train_no,
            COALESCE(NULLIF(t.train_type, 'TRAIN'), r.train_type) AS train_type
        FROM stop_times st1
        JOIN stop_times st2 ON st1.trip_id = st2.trip_id AND st1.stop_sequence < st2.stop_sequence
        JOIN trips t ON st1.trip_id = t.trip_id
        LEFT JOIN routes r ON t.route_id = r.route_id
        JOIN calendar_dates cd ON t.service_id = cd.service_id
        JOIN stops s1 ON st1.stop_id = s1.stop_id
        JOIN stops s2 ON st2.stop_id = s2.stop_id
        WHERE UPPER(s1.stop_name) = UPPER(?)
          AND cd.date = ?
          AND cd.exception_type = 1
        ORDER BY st1.dep_min ASC
        LIMIT 500
        """
        cursor.execute(query_direct, (start_label, date_clean))
        
        for row in cursor.fetchall():
            dest_name = row["to_name"]
            duration_min = row["arr_min"] - row["dep_min"]
            if duration_min < 0:
                duration_min += 24 * 60

            journey = {
                "dest_name": dest_name,
                "dest_lat": row["dest_lat"],
                "dest_lon": row["dest_lon"],
                "duration": duration_min,
                "dep_str": row["dep_str"],
                "arr_str": row["arr_str"],
                "transfers": 0,
                "legs": [{
                    "from_name": start_label,
                    "to_name": dest_name,
                    "dep_str": row["dep_str"],
                    "arr_str": row["arr_str"],
                    "train_no": row["train_no"],
                    "train_type": row["train_type"],
                    "lat": row["dest_lat"],
                    "lon": row["dest_lon"],
                }],
            }

            if dest_name not in best_journeys or duration_min < best_journeys[dest_name]["duration"]:
                best_journeys[dest_name] = journey

        journeys = list(best_journeys.values())
        journeys.sort(key=lambda x: (x["transfers"], x["duration"]))

        conn.close()
        return {"journeys": journeys, "count": len(journeys)}

    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur explorer: {str(e)}")