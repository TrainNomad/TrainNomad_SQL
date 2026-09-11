import sqlite3

conn = sqlite3.connect("gtfs_indexed.db")
cursor = conn.cursor()

# 1. Plage de dates disponibles pour la RENFE
cursor.execute("""
    SELECT MIN(date), MAX(date), COUNT(DISTINCT date) 
    FROM calendar_dates 
    WHERE operator_id = 'RENFE' AND exception_type = 1
""")
min_date, max_date, total_days = cursor.fetchone()
print(f"Dates RENFE disponibles : du {min_date} au {max_date} ({total_days} jours au total)")

# 2. Test direct avec les IDs exacts identifies
query = """
SELECT DISTINCT
    s1.stop_name AS depart, 
    st1.departure_time, 
    s2.stop_name AS arrivee, 
    st2.arrival_time,
    t.trip_id,
    cd.date
FROM stop_times st1
JOIN stop_times st2 ON st1.trip_id = st2.trip_id AND st1.stop_sequence < st2.stop_sequence
JOIN trips t ON st1.trip_id = t.trip_id
JOIN calendar_dates cd ON t.service_id = cd.service_id
JOIN stops s1 ON st1.stop_id = s1.stop_id
JOIN stops s2 ON st2.stop_id = s2.stop_id
WHERE st1.stop_id = 'CANONICAL_UIC_7151003' -- Sevilla Santa Justa
  AND st2.stop_id IN ('CANONICAL_UIC_7160000', 'CANONICAL_UIC_7118000') -- Madrid Atocha
  AND cd.exception_type = 1
ORDER BY cd.date ASC, st1.departure_time ASC
LIMIT 10;
"""

cursor.execute(query)
results = cursor.fetchall()

print(f"\n--> {len(results)} premiers trajets trouves dans la base :")
for r in results:
    print(f"Le {r[5]} : {r[0]} ({r[1]}) -> {r[2]} ({r[3]}) [Trip: {r[4]}]")

conn.close()