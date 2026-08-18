import sqlite3
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from operators import load_operators

app = FastAPI(
    title="European Trains Visualizer API",
    description="API multi-opérateurs pour trains européens.",
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "eu_trains.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

@app.get("/")
def read_root():
    return {"status": "ok", "message": "API Trains Européens opérationnelle"}

@app.get("/operators")
def get_operators():
    """Retourne la liste des opérateurs enregistrés."""
    return {"operators": load_operators()}

@app.get("/search")
def search_all(
    origin: str = Query(..., description="Gare ou Ville de départ"),
    destination: str = Query(..., description="Gare ou Ville d'arrivée"),
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
    operator_id: Optional[str] = Query(None, description="Filtre par opérateur (ex: SNCF, RENFE)")
):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        origin_clean = origin.strip().upper()
        dest_clean = destination.strip().upper()
        date_clean = date.strip()

        op_filter = " AND operator_id = ?" if operator_id else ""
        params = [date_clean, origin_clean, origin_clean, dest_clean, dest_clean]
        if operator_id:
            params.append(operator_id)

        query_direct = f"""
        SELECT 
            operator_id, date, 
            origin_name AS orig, destination_name AS dest, 
            origin_lat, origin_lon, dest_lat, dest_lon,
            departure_time AS train1_dep, arrival_time AS train1_arr, 
            train_no AS train1_no, train_type AS train1_type
        FROM trips
        WHERE date = ?
          AND (UPPER(origin_name) = ? OR UPPER(origin_parent_name) = ?)
          AND (UPPER(destination_name) = ? OR UPPER(destination_parent_name) = ?)
          {op_filter}
        ORDER BY dep_min ASC
        LIMIT 50
        """
        cursor.execute(query_direct, params)
        direct_rows = cursor.fetchall()

        direct_results = [
            {
                "is_direct": True,
                "operator_id": d["operator_id"],
                "orig": d["orig"],
                "dest": d["dest"],
                "orig_lat": d["origin_lat"],
                "orig_lon": d["origin_lon"],
                "dest_lat": d["dest_lat"],
                "dest_lon": d["dest_lon"],
                "date": d["date"],
                "train1_no": d["train1_no"],
                "train1_type": d["train1_type"],
                "train1_dep": d["train1_dep"],
                "train1_arr": d["train1_arr"],
            }
            for d in direct_rows
        ]

        conn.close()
        return {"count": len(direct_results), "results": direct_results}

    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur recherche: {str(e)}")