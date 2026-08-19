import sqlite3
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="GTFS Rail Search API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "gtfs.db"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/health")
def health_check():
    """Vérification de l'état de l'API et de l'accès à la base de données."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Erreur base de données: {str(e)}"
        )


@app.get("/stations")
def get_stations(
    q: Optional[str] = Query(
        None, description="Terme de recherche pour autocomplétion"
    ),
    limit: int = Query(20, description="Nombre maximum de gares à retourner"),
):
    """Autocomplétion des gares pour le Front-End."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if q:
            query = """
            SELECT DISTINCT stop_name, stop_lat, stop_lon 
            FROM stops 
            WHERE UPPER(stop_name) LIKE UPPER(?) 
            ORDER BY stop_name ASC 
            LIMIT ?
            """
            cursor.execute(query, (f"%{q.strip()}%", limit))
        else:
            query = """
            SELECT DISTINCT stop_name, stop_lat, stop_lon 
            FROM stops 
            ORDER BY stop_name ASC 
            LIMIT ?
            """
            cursor.execute(query, (limit,))

        rows = cursor.fetchall()
        conn.close()
        return [
            {"name": r["stop_name"], "lat": r["stop_lat"], "lon": r["stop_lon"]}
            for r in rows
        ]
    except Exception as e:
        conn.close()
        raise HTTPException(
            status_code=500, detail=f"Erreur gares: {str(e)}"
        )


@app.get("/explorer")
def explore_destinations(
    origin: str = Query(..., description="Gare de départ"),
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
    limit: int = Query(15, description="Nombre de destinations directes à explorer"),
):
    """Découverte des destinations directes accessibles depuis une gare donnée."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        date_clean = date.strip()
        orig_label = origin.strip()

        query = """
        SELECT DISTINCT 
            s2.stop_name AS dest,
            s2.stop_lat AS dest_lat,
            s2.stop_lon AS dest_lon,
            COUNT(DISTINCT t.trip_id) AS trip_count
        FROM stop_times st1
        JOIN stop_times st2 ON st1.trip_id = st2.trip_id AND st1.stop_sequence < st2.stop_sequence
        JOIN trips t ON st1.trip_id = t.trip_id
        JOIN calendar_dates cd ON t.service_id = cd.service_id
        JOIN stops s1 ON st1.stop_id = s1.stop_id
        JOIN stops s2 ON st2.stop_id = s2.stop_id
        WHERE UPPER(s1.stop_name) = UPPER(?)
          AND cd.date = ?
          AND cd.exception_type = 1
        GROUP BY s2.stop_name
        ORDER BY trip_count DESC
        LIMIT ?
        """
        cursor.execute(query, (orig_label, date_clean, limit))
        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "destination": r["dest"],
                "lat": r["dest_lat"],
                "lon": r["dest_lon"],
                "trains_count": r["trip_count"],
            }
            for r in rows
        ]
    except Exception as e:
        conn.close()
        raise HTTPException(
            status_code=500, detail=f"Erreur exploration: {str(e)}"
        )


@app.get("/search")
def search_all(
    origin: str = Query(..., description="Nom de la gare de départ"),
    destination: str = Query(..., description="Nom de la gare d'arrivée"),
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
    departure_time: Optional[str] = Query(
        "00:00:00", description="Heure minimale de départ (HH:MM:SS)"
    ),
    limit: int = Query(6, description="Nombre de trajets à retourner"),
):
    """Recherche principale de trajets avec pagination et dédoublonnage."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        date_clean = date.strip()
        orig_label = origin.strip()
        dest_label = destination.strip()

        time_parts = list(map(int, departure_time.split(":")))
        start_min = time_parts[0] * 60 + time_parts[1]

        # 1. Trajets Directs
        query_direct = """
        SELECT DISTINCT
            s1.stop_name AS orig, s2.stop_name AS dest,
            s1.stop_lat AS orig_lat, s1.stop_lon AS orig_lon,
            s2.stop_lat AS dest_lat, s2.stop_lon AS dest_lon,
            st1.departure_time AS train1_dep, st2.arrival_time AS train1_arr,
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
          AND st1.dep_min >= ?
        ORDER BY st1.dep_min ASC
        LIMIT ?
        """
        cursor.execute(
            query_direct,
            (orig_label, dest_label, date_clean, start_min, limit * 2),
        )
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
                "dep_min": d["dep_min"],
            }
            for d in direct_rows
        ]

        direct_train_numbers = {
            d["train1_no"] for d in direct_results if d["train1_no"]
        }

        # 2. Correspondances optimisées (CTE)
        query_connections = """
        WITH train1 AS (
            SELECT 
                t1.trip_headsign AS train1_no,
                COALESCE(NULLIF(t1.train_type, 'TRAIN'), r1.train_type) AS train1_type,
                st1_dep.departure_time AS train1_dep, st1_arr.arrival_time AS train1_arr,
                st1_arr.dep_min AS arr_min1, st1_dep.dep_min AS dep_min1,
                s_trans1.stop_name AS transfer_station, s_trans1.stop_lat AS transfer_lat, s_trans1.stop_lon AS transfer_lon,
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
              AND st1_dep.dep_min >= ?
            ORDER BY st1_dep.dep_min ASC
            LIMIT 40
        ),
        train2 AS (
            SELECT 
                t2.trip_headsign AS train2_no,
                COALESCE(NULLIF(t2.train_type, 'TRAIN'), r2.train_type) AS train2_type,
                st2_dep.departure_time AS train2_dep, st2_arr.arrival_time AS train2_arr,
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
            t1.transfer_station AS transfer_station_arr, t1.transfer_lat, t1.transfer_lon,
            t1.transfer_station AS transfer_station_dep,
            t2.dest, t2.dest_lat, t2.dest_lon,
            t1.train1_no, t1.train1_type, t1.train1_dep, t1.train1_arr,
            t2.train2_no, t2.train2_type, t2.train2_dep, t2.train2_arr,
            (t2.dep_min2 - t1.arr_min1) AS layover_minutes,
            t1.dep_min1 AS dep_min
        FROM train1 t1
        JOIN train2 t2 ON t1.transfer_station = t2.transfer_station
        WHERE t2.dep_min2 >= (t1.arr_min1 + 15)
          AND t2.dep_min2 <= (t1.arr_min1 + 90)
        ORDER BY t1.dep_min1 ASC
        """
        cursor.execute(
            query_connections,
            (orig_label, date_clean, start_min, dest_label, date_clean),
        )
        conn_rows = [dict(row) for row in cursor.fetchall()]

        # 3. Filtrage strict
        valid_connections = []
        seen_first_trains = set()

        for c in conn_rows:
            if c["train1_no"] in direct_train_numbers:
                continue
            if c["train1_no"] in seen_first_trains:
                continue

            c["is_direct"] = False
            c["date"] = date_clean
            c["is_valid_layover"] = True
            valid_connections.append(c)
            seen_first_trains.add(c["train1_no"])

        combined = direct_results + valid_connections
        combined.sort(key=lambda x: x["dep_min"])

        page_results = combined[:limit]

        next_cursor = None
        if len(combined) > limit:
            last_dep = page_results[-1]["train1_dep"]
            h, m, s = map(int, last_dep.split(":"))
            next_time = (
                datetime(2000, 1, 1, h, m, s) + timedelta(seconds=1)
            ).time()
            next_cursor = str(next_time)

        for r in page_results:
            r.pop("dep_min", None)

        conn.close()

        return {
            "count": len(page_results),
            "next_cursor": next_cursor,
            "results": page_results,
        }

    except Exception as e:
        conn.close()
        raise HTTPException(
            status_code=500, detail=f"Erreur lors de la recherche: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)