import gzip
import os
import shutil
import sqlite3
from typing import Optional
from fastapi import FastAPI, HTTPException, Query

# --- 1. Gestion de la base de données décompressée ---
DB_GZ_FILE = "eu_trains.db.gz"
DB_FILE = "eu_trains.db"

if not os.path.exists(DB_FILE) and os.path.exists(DB_GZ_FILE):
    with gzip.open(DB_GZ_FILE, "rb") as f_in:
        with open(DB_FILE, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    print("Base de données décompressée avec succès.")

app = FastAPI(title="Train Nomad API")


def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


# --- 2. Endpoints ---


@app.get("/stations")
def get_stations(q: Optional[str] = Query(None, description="Recherche par nom de gare")):
    conn = get_db_connection()
    cursor = conn.cursor()

    if q:
        cursor.execute(
            "SELECT stop_id, stop_name, stop_lat, stop_lon, operator_id FROM stops WHERE stop_name LIKE ? LIMIT 50",
            (f"%{q}%",),
        )
    else:
        cursor.execute(
            "SELECT stop_id, stop_name, stop_lat, stop_lon, operator_id FROM stops LIMIT 100"
        )

    stations = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return stations


@app.get("/operators")
def get_operators():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, name FROM operators")
    operators = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return operators


@app.get("/search")
def search_trips(origin: str, destination: str, date: Optional[str] = None):
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT 
            st1.trip_id,
            s1.stop_name AS origin,
            st1.departure_time,
            s2.stop_name AS destination,
            st2.arrival_time,
            st1.date,
            st1.operator_id
        FROM stop_times st1
        JOIN stop_times st2 ON st1.trip_id = st2.trip_id AND st1.stop_sequence < st2.stop_sequence
        JOIN stops s1 ON st1.stop_id = s1.stop_id
        JOIN stops s2 ON st2.stop_id = s2.stop_id
        WHERE LOWER(s1.stop_name) LIKE LOWER(?) 
          AND LOWER(s2.stop_name) LIKE LOWER(?)
    """
    params = [f"%{origin}%", f"%{destination}%"]

    if date:
        query += " AND st1.date = ?"
        params.append(date)

    query += " LIMIT 50"

    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results