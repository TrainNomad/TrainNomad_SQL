import gzip
import os
import shutil
import sqlite3
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="SNCF Multi-Transport API",
    description="API ultra-rapide optimisée sur base relationnelle GTFS SQLite.",
    version="2.0.0",
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
    """Décompresse gtfs_indexed.db.gz si la base .db n'existe pas encore."""
    if not os.path.exists(DB_PATH):
        if not os.path.exists(GZ_PATH):
            raise FileNotFoundError("Ni 'gtfs_indexed.db' ni 'gtfs_indexed.db.gz' n'ont été trouvés.")
        print("📦 Décompression de gtfs_indexed.db.gz...")
        with gzip.open(GZ_PATH, 'rb') as f_in:
            with open(DB_PATH, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        print("✅ Base décompressée avec succès !")

def get_db_connection():
    ensure_db_decompressed()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA cache_size=-64000;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    return conn

@app.get("/")
def read_root():
    return {"status": "ok", "message": "API Trains France opérationnelle (Base relationnelle)"}

@app.get("/health")
def health_check():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trips;")
        count = cursor.fetchone()[0]
        conn.close()
        return {
            "status": "healthy",
            "database": DB_PATH,
            "trips_count": count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Base de données indisponible: {str(e)}")

@app.get("/stations")
def get_stations(q: str = Query(None, description="Recherche partielle de gare")):
    if not q or not q.strip():
        return {"results": []}

    conn = get_db_connection()
    cursor = conn.cursor()
    search_pattern = "%" + q.strip().upper() + "%"

    try:
        query_stations = """
            SELECT stop_id, stop_name, stop_lat, stop_lon, clean_uic
            FROM stops 
            WHERE UPPER(stop_name) LIKE ?
            ORDER BY stop_name ASC
            LIMIT 15
        """
        cursor.execute(query_stations, (search_pattern,))
        stations = [
            {
                "type": "station",
                "label": row["stop_name"],
                "id": row["stop_id"],
                "uic": row["clean_uic"],
                "lat": row["stop_lat"],
                "lon": row["stop_lon"],
                "search_val": row["stop_name"],
            }
            for row in cursor.fetchall()
        ]

        conn.close()
        return {"results": stations}

    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur autocomplétion: {str(e)}")

def cleanup_connections(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique = {}
    for r in results:
        key = f"{r['train1_no']}|{r['transfer_station_arr']}|{r['transfer_station_dep']}|{r['train2_no']}"
        if key not in unique:
            unique[key] = r

    par_trains = {}
    for r in unique.values():
        train_key = f"{r['train1_no']}_{r['train2_no']}"
        if train_key not in par_trains:
            par_trains[train_key] = []
        par_trains[train_key].append(r)

    final = [alts[0] for alts in par_trains.values()]
    final.sort(key=lambda x: (x["train1_dep"], x["layover_minutes"]))
    return final

@app.get("/search")
def search_all(
    origin: str = Query(..., description="Stop ID de départ (ex: StopPoint:OCETrain...)"),
    destination: str = Query(..., description="Stop ID d'arrivée"),
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        date_clean = date.strip()

        query_direct = """
        SELECT 
            s1.stop_name AS orig,
            s2.stop_name AS dest,
            s1.stop_lat AS orig_lat, s1.stop_lon AS orig_lon,
            s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon,
            st1.departure_time AS train1_dep,
            st2.arrival_time AS train1_arr,
            t.trip_headsign AS train1_no,
            r.train_type AS train1_type,
            st1.dep_min
        FROM stop_times st1
        JOIN stop_times st2 ON st1.trip_id = st2.trip_id AND st1.stop_sequence < st2.stop_sequence
        JOIN trips t ON st1.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        JOIN calendar_dates cd ON t.service_id = cd.service_id
        JOIN stops s1 ON st1.stop_id = s1.stop_id
        JOIN stops s2 ON st2.stop_id = s2.stop_id
        WHERE st1.stop_id = ?
          AND st2.stop_id = ?
          AND cd.date = ?
          AND cd.exception_type = 1
        ORDER BY st1.dep_min ASC
        LIMIT 50
        """
        cursor.execute(query_direct, (origin, destination, date_clean))
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

        query_connections = """
        SELECT 
            s1.stop_name AS orig,
            s1.stop_lat AS orig_lat, s1.stop_lon AS orig_lon,
            st1_arr.stop_id AS transfer_stop_id,
            s_trans1.stop_name AS transfer_station_arr,
            s_trans1.stop_lat AS transfer_lat, s_trans1.stop_lon AS transfer_lon,
            s_trans2.stop_name AS transfer_station_dep,
            s2.stop_name AS dest,
            s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon,
            t1.trip_headsign AS train1_no,
            r1.train_type AS train1_type,
            st1_dep.departure_time AS train1_dep,
            st1_arr.arrival_time AS train1_arr,
            t2.trip_headsign AS train2_no,
            r2.train_type AS train2_type,
            st2_dep.departure_time AS train2_dep,
            st2_arr.arrival_time AS train2_arr,
            (st2_dep.dep_min - st1_arr.dep_min) AS layover_minutes
        FROM stop_times st1_dep
        JOIN stop_times st1_arr ON st1_dep.trip_id = st1_arr.trip_id AND st1_dep.stop_sequence < st1_arr.stop_sequence
        JOIN trips t1 ON st1_dep.trip_id = t1.trip_id
        JOIN routes r1 ON t1.route_id = r1.route_id
        JOIN calendar_dates cd1 ON t1.service_id = cd1.service_id

        JOIN stop_times st2_dep ON st1_arr.stop_id = st2_dep.stop_id
        JOIN stop_times st2_arr ON st2_dep.trip_id = st2_arr.trip_id AND st2_dep.stop_sequence < st2_arr.stop_sequence
        JOIN trips t2 ON st2_dep.trip_id = t2.trip_id
        JOIN routes r2 ON t2.route_id = r2.route_id
        JOIN calendar_dates cd2 ON t2.service_id = cd2.service_id

        JOIN stops s1 ON st1_dep.stop_id = s1.stop_id
        JOIN stops s2 ON st2_arr.stop_id = s2.stop_id
        JOIN stops s_trans1 ON st1_arr.stop_id = s_trans1.stop_id
        JOIN stops s_trans2 ON st2_dep.stop_id = s_trans2.stop_id

        WHERE st1_dep.stop_id = ?
          AND st2_arr.stop_id = ?
          AND cd1.date = ? AND cd1.exception_type = 1
          AND cd2.date = ? AND cd2.exception_type = 1
          AND st2_dep.dep_min >= (st1_arr.dep_min + 15)
          AND st2_dep.dep_min <= (st1_arr.dep_min + 180)
        ORDER BY st1_dep.dep_min ASC
        LIMIT 100
        """
        cursor.execute(query_connections, (origin, destination, date_clean, date_clean))
        conn_rows = [dict(row) for row in cursor.fetchall()]

        valid_connections = []
        for c in conn_rows:
            c["is_direct"] = False
            c["date"] = date_clean
            layover = c["layover_minutes"]
            c["is_valid_layover"] = (15 <= layover <= 180)
            if c["is_valid_layover"]:
                valid_connections.append(c)

        cleaned_connections = cleanup_connections(valid_connections)
        all_results = direct_results + cleaned_connections
        all_results.sort(key=lambda x: x["train1_dep"])

        conn.close()
        return {"count": len(all_results), "results": all_results[:30]}

    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur recherche: {str(e)}")

@app.get("/explorer")
def explore_destinations(
    from_station: Optional[str] = Query(None, alias="from", description="Stop ID de départ"),
    origin: Optional[str] = Query(None, description="Alternative à 'from'"),
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
):
    start_stop = from_station or origin
    if not start_stop:
        raise HTTPException(status_code=400, detail="Paramètre 'from' ou 'origin' requis")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        date_clean = date.strip()
        best_journeys = {}

        query_direct = """
        SELECT 
            s2.stop_id AS to_id,
            s2.stop_name AS to_name,
            s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon,
            st1.departure_time AS dep_str,
            st2.arrival_time AS arr_str,
            st1.dep_min, st2.dep_min AS arr_min,
            t.trip_headsign AS train_no,
            r.train_type
        FROM stop_times st1
        JOIN stop_times st2 ON st1.trip_id = st2.trip_id AND st1.stop_sequence < st2.stop_sequence
        JOIN trips t ON st1.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        JOIN calendar_dates cd ON t.service_id = cd.service_id
        JOIN stops s2 ON st2.stop_id = s2.stop_id
        WHERE st1.stop_id = ?
          AND cd.date = ?
          AND cd.exception_type = 1
        ORDER BY st1.dep_min ASC
        LIMIT 500
        """
        cursor.execute(query_direct, (start_stop, date_clean))
        
        for row in cursor.fetchall():
            dest_id = row["to_id"]
            duration_min = row["arr_min"] - row["dep_min"]
            if duration_min < 0:
                duration_min += 24 * 60

            journey = {
                "dest_lat": row["dest_lat"],
                "dest_lon": row["dest_lon"],
                "duration": duration_min,
                "dep_str": row["dep_str"],
                "arr_str": row["arr_str"],
                "transfers": 0,
                "legs": [{
                    "from_name": start_stop,
                    "to_name": row["to_name"],
                    "to_id": dest_id,
                    "dep_str": row["dep_str"],
                    "arr_str": row["arr_str"],
                    "train_no": row["train_no"],
                    "train_type": row["train_type"],
                    "lat": row["dest_lat"],
                    "lon": row["dest_lon"],
                }],
            }

            if dest_id not in best_journeys or duration_min < best_journeys[dest_id]["duration"]:
                best_journeys[dest_id] = journey

        journeys = list(best_journeys.values())
        journeys.sort(key=lambda x: (x["transfers"], x["duration"]))

        conn.close()
        return {"journeys": journeys, "count": len(journeys)}

    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur explorer: {str(e)}")