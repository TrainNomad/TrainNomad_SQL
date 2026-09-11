# API TrainNomad (moteur Go / RAPTOR)

Toutes les routes sont en `GET`, les réponses en JSON (compressées en gzip si le client l'accepte), CORS ouvert.

**Heures** : toujours au format ISO 8601 **en heure locale de la gare concernée, avec son décalage**
(`2026-09-17T09:31:00+01:00` à Londres, `2026-09-17T12:55:00+02:00` à Paris). Pour afficher « 09:31 »,
prendre les caractères 11 à 16. Ne pas convertir dans le fuseau du navigateur.

**Identifiants de lieux** : `city:TL4916` (Paris, toutes gares) ou `station:8727100` (Paris Gare du Nord).
Utiliser de préférence l'`id` renvoyé par `/stations`. Un nom (« Paris », « Lyon Part-Dieu ») fonctionne aussi.

---

## `GET /stations?q=par&limit=10`

Autocomplétion (sans accents, préfixe du nom ou d'un mot). Les villes à plusieurs gares sont proposées en premier.

```json
{ "results": [
  { "type": "city", "id": "city:TL4916", "name": "Paris", "country": "FR", "lat": 48.85, "lon": 2.35, "stations": 9 },
  { "type": "station", "id": "station:8727100", "name": "Paris Gare du Nord", "city": "Paris", "country": "FR", "lat": 48.88, "lon": 2.35 }
]}
```

## `GET /search`

| paramètre | défaut | description |
|---|---|---|
| `from` (alias `origin`) | — | id ou nom du départ |
| `to` (alias `destination`) | — | id ou nom de l'arrivée |
| `date` | aujourd'hui | `YYYY-MM-DD`, date locale au départ |
| `time` (alias `departure_time`) | `00:00` | `HH:MM`, heure locale minimale de départ |
| `limit` | 10 (max 50) | nombre de trajets |
| `max_transfers` | 6 (max 8) | nombre maximal de correspondances |

Réponse : trajets triés par heure de départ. Seuls les trajets « non dominés » sont renvoyés : un trajet
disparaît si un autre part plus tard, arrive plus tôt et a au plus autant de correspondances. Les trajets
beaucoup plus longs que le plus rapide sont aussi écartés.

```json
{
  "from": { "type": "station", "id": "station:7015400", "name": "London St Pancras International", ... },
  "to":   { "type": "city", "id": "city:TL4790", "name": "Marseille", ... },
  "date": "2026-09-17", "time": "09:00",
  "count": 1,
  "next": { "date": "2026-09-17", "time": "09:32" },
  "compute_ms": 2,
  "journeys": [{
    "id": "9014-2609170931_6177-2609171410",
    "departure": "2026-09-17T09:31:00+01:00",
    "arrival": "2026-09-17T17:14:00+02:00",
    "duration_min": 403,
    "transfers": 1,
    "from": { "id": "station:7015400", "name": "London St Pancras International", "city": "London", "country": "GB", "lat": 51.53, "lon": -0.12 },
    "to":   { "id": "station:8775100", "name": "Marseille St-Charles", "city": "Marseille", "country": "FR", "lat": 43.30, "lon": 5.38 },
    "operators": ["EUROSTAR", "SNCF"],
    "train_types": ["Eurostar", "TGV INOUI"],
    "legs": [
      { "type": "train",
        "from": { "id": "station:7015400", "name": "London St Pancras International", ... },
        "to":   { "id": "station:8727100", "name": "Paris Gare du Nord", ... },
        "departure": "2026-09-17T09:31:00+01:00", "arrival": "2026-09-17T12:55:00+02:00", "duration_min": 144,
        "operator": "EUROSTAR", "operator_name": "Eurostar", "train_type": "Eurostar", "train_number": "9014",
        "headsign": "Paris Gare du Nord", "checkin_min": 45,
        "stops": [ { "id": "...", "name": "...", "lat": 0, "lon": 0, "departure": "..." }, { "...": "...", "arrival": "..." } ] },
      { "type": "transfer",
        "from": { "name": "Paris Gare du Nord", ... }, "to": { "name": "Paris Gare de Lyon", ... },
        "transfer_kind": "city", "duration_min": 46, "min_transfer_min": 46, "wait_min": 75 },
      { "type": "train", "...": "TGV INOUI 6177 Paris Gare de Lyon 14:10 -> Marseille St-Charles 17:14" }
    ]
  }]
}
```

- `legs` alterne `train` et `transfer` (autant de `train` que `transfers + 1`).
- `transfer_kind` : `same_station` (changement de quai), `walk` (gares proches, à pied),
  `city` (traversée de ville en transport urbain, ex. Paris Nord → Gare de Lyon).
  `wait_min` = temps total entre l'arrivée et le départ suivant.
- `checkin_min` (> 0 seulement pour l'Eurostar vers/depuis Londres) : fermeture de l'enregistrement avant le départ.
- `stops` : tous les arrêts du train entre la montée et la descente, avec coordonnées (tracé sur la carte).
- Pagination : rappeler `/search` avec `date=next.date&time=next.time`.

## `GET /explorer?from=Paris&date=2026-09-17&time=06:00&max_transfers=2&limit=500`

Toutes les destinations atteignables dans la journée (départs entre `time` et minuit), avec le trajet le plus court.
`max_transfers` vaut 2 par défaut (maximum 6). Réponse triée par durée :

```json
{
  "from": { "type": "city", "id": "city:TL4916", "name": "Paris", ... },
  "date": "2026-09-17", "time": "06:00", "max_transfers": 2, "count": 2941, "compute_ms": 39,
  "destinations": [{
    "place": { "type": "city", "id": "city:TL4718", "name": "Lyon", "country": "FR", "lat": 45.76, "lon": 4.83, "stations": 7 },
    "duration_min": 116, "transfers": 0,
    "departure": "2026-09-17T09:00:00+02:00", "arrival": "2026-09-17T10:56:00+02:00",
    "direct": { "duration_min": 116, "departure": "...", "arrival": "..." }
  }]
}
```

`direct` vaut `null` s'il n'existe pas de train direct. Pour afficher les détails d'une destination,
appeler `/search?from=<from.id>&to=<place.id>`.

## `GET /health`

État du service : période couverte par les horaires (`valid_from` / `valid_to`), date de compilation,
mémoire utilisée, statistiques de compilation.

## Erreurs

Code HTTP 4xx avec `{ "error": "<code>", "detail": "<message en français>" }`. Codes possibles :
`unknown_origin`, `unknown_destination`, `same_place`, `bad_date`, `bad_time`, `date_out_of_range`.
