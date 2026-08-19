import gzip
import os
import shutil
import sqlite3
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="SNCF Multi-Transport API",
    description="API optimisée GTFS SQLite par noms de gares et détection du type de train.",
    version="2.1.0",
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
    origin: str = Query(..., description="Nom de la gare de départ (ex: 'Paris Gare de Lyon')"),
    destination: str = Query(..., description="Nom de la gare d'arrivée (ex: 'Lyon Part Dieu')"),
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        date_clean = date.strip()
        orig_label = origin.strip()
        dest_label = destination.strip()

        # 1. Trajets directs
        query_direct = """
        SELECT DISTINCT
            s1.stop_name AS orig,
            s2.stop_name AS dest,
            s1.stop_lat AS orig_lat, s1.stop_lon AS orig_lon,
            s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon,
            st1.departure_time AS train1_dep,
            st2.arrival_time AS train1_arr,
            t.trip_headsign AS train1_no,
            COALESCE(NULLIF(t.train_type, 'TRAIN'), r.train_type) AS train1_type,
            st1.dep_min
        FROM stop_times st1
        JOIN stop_times st2 ON st1.trip_id = st2.trip_id AND st1.stop_sequence < st2.stop_sequence
        JOIN trips t ON st1.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        JOIN calendar_dates cd ON t.service_id = cd.service_id
        JOIN stops s1 ON st1.stop_id = s1.stop_id
        JOIN stops s2 ON st2.stop_id = s2.stop_id
        WHERE UPPER(s1.stop_name) = UPPER(?)
          AND UPPER(s2.stop_name) = UPPER(?)
          AND cd.date = ?
          AND cd.exception_type = 1
        ORDER BY st1.dep_min ASC
        LIMIT 30
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

        # 2. Correspondances optimisées
        query_connections = """
        WITH train1 AS (
            SELECT 
                t1.trip_headsign AS train1_no,
                COALESCE(NULLIF(t1.train_type, 'TRAIN'), r1.train_type) AS train1_type,
                st1_dep.departure_time AS train1_dep,
                st1_arr.arrival_time AS train1_arr,
                st1_arr.dep_min AS arr_min1,
                s_trans1.stop_name AS transfer_station,
                s_trans1.stop_lat AS transfer_lat, 
                s_trans1.stop_lon AS transfer_lon,
                s1.stop_name AS orig, s1.stop_lat AS orig_lat, s1.stop_lon AS orig_lon
            FROM stop_times st1_dep
            JOIN stop_times st1_arr ON st1_dep.trip_id = st1_arr.trip_id AND st1_dep.stop_sequence < st1_arr.stop_sequence
            JOIN trips t1 ON st1_dep.trip_id = t1.trip_id
            JOIN routes r1 ON t1.route_id = r1.route_id
            JOIN calendar_dates cd1 ON t1.service_id = cd1.service_id
            JOIN stops s1 ON st1_dep.stop_id = s1.stop_id
            JOIN stops s_trans1 ON st1_arr.stop_id = s_trans1.stop_id
            WHERE UPPER(s1.stop_name) = UPPER(?)
              AND cd1.date = ? 
              AND cd1.exception_type = 1
        ),
        train2 AS (
            SELECT 
                t2.trip_headsign AS train2_no,
                COALESCE(NULLIF(t2.train_type, 'TRAIN'), r2.train_type) AS train2_type,
                st2_dep.departure_time AS train2_dep,
                st2_arr.arrival_time AS train2_arr,
                st2_dep.dep_min AS dep_min2,
                s_trans2.stop_name AS transfer_station,
                s2.stop_name AS dest, s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon
            FROM stop_times st2_dep
            JOIN stop_times st2_arr ON st2_dep.trip_id = st2_arr.trip_id AND st2_dep.stop_sequence < st2_arr.stop_sequence
            JOIN trips t2 ON st2_dep.trip_id = t2.trip_id
            JOIN routes r2 ON t2.route_id = r2.route_id
            JOIN calendar_dates cd2 ON t2.service_id = cd2.service_id
            JOIN stops s_trans2 ON st2_dep.stop_id = s_trans2.stop_id
            JOIN stops s2 ON st2_arr.stop_id = s2.stop_id
            WHERE UPPER(s2.stop_name) = UPPER(?)
              AND cd2.date = ? 
              AND cd2.exception_type = 1
        )
        SELECT 
            t1.orig, t1.orig_lat, t1.orig_lon,
            t1.transfer_station AS transfer_station_arr,
            t1.transfer_lat, t1.transfer_lon,
            t1.transfer_station AS transfer_station_dep,
            t2.dest, t2.dest_lat, t2.dest_lon,
            t1.train1_no, t1.train1_type, t1.train1_dep, t1.train1_arr,
            t2.train2_no, t2.train2_type, t2.train2_dep, t2.train2_arr,
            (t2.dep_min2 - t1.arr_min1) AS layover_minutes
        FROM train1 t1
        JOIN train2 t2 ON t1.transfer_station = t2.transfer_station
        WHERE t2.dep_min2 >= (t1.arr_min1 + 15)
          AND t2.dep_min2 <= (t1.arr_min1 + 120)
        ORDER BY t1.train1_dep ASC, t2.train2_arr ASC
        """
        cursor.execute(query_connections, (orig_label, date_clean, dest_label, date_clean))
        conn_rows = [dict(row) for row in cursor.fetchall()]

        # 3. Filtrage en mémoire : supprimer les correspondances sous-optimales et doublons
        valid_connections = []
        seen_pairs = set()

        for c in conn_rows:
            # Clé unique pour éliminer les combinaisons de trains identiques
            unique_key = (c["train1_no"], c["train2_no"], c["transfer_station_arr"])
            if unique_key in seen_pairs:
                continue

            c["is_direct"] = False
            c["date"] = date_clean
            c["is_valid_layover"] = True
            
            valid_connections.append(c)
            seen_pairs.add(unique_key)

        # Si un trajet direct existe à la même heure, on priorise le trajet direct
        all_results = direct_results + valid_connections
        all_results.sort(key=lambda x: (x["train1_dep"], x["is_direct"] == False))

        conn.close()
        return {"count": len(all_results), "results": all_results[:20]}

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
        JOIN routes r ON t.route_id = r.route_id
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