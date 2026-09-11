package main

import (
	"compress/gzip"
	"encoding/json"
	"fmt"
	"net/http"
	"runtime"
	"strconv"
	"strings"
	"time"
)

type StopRef struct {
	ID      string  `json:"id"`
	Name    string  `json:"name"`
	City    string  `json:"city"`
	Country string  `json:"country"`
	Lat     float32 `json:"lat"`
	Lon     float32 `json:"lon"`
}

type StopTime struct {
	StopRef
	Arrival   string `json:"arrival,omitempty"`
	Departure string `json:"departure,omitempty"`
}

// Leg : "train" ou "transfer" (changement dans la même gare, marche ou traversée de ville).
type Leg struct {
	Type        string  `json:"type"`
	From        StopRef `json:"from"`
	To          StopRef `json:"to"`
	Departure   string  `json:"departure,omitempty"`
	Arrival     string  `json:"arrival,omitempty"`
	DurationMin int32   `json:"duration_min"`

	// train
	Operator     string     `json:"operator,omitempty"`
	OperatorName string     `json:"operator_name,omitempty"`
	TrainType    string     `json:"train_type,omitempty"`
	TrainNumber  string     `json:"train_number,omitempty"`
	Headsign     string     `json:"headsign,omitempty"`
	CheckinMin   int        `json:"checkin_min,omitempty"`
	Stops        []StopTime `json:"stops,omitempty"`

	// transfer
	TransferKind string `json:"transfer_kind,omitempty"` // "same_station" | "walk" | "city"
	MinMin       int32  `json:"min_transfer_min,omitempty"`
	WaitMin      int32  `json:"wait_min,omitempty"`
}

type Journey struct {
	ID          string   `json:"id"`
	Departure   string   `json:"departure"`
	Arrival     string   `json:"arrival"`
	DurationMin int32    `json:"duration_min"`
	Transfers   int      `json:"transfers"`
	From        StopRef  `json:"from"`
	To          StopRef  `json:"to"`
	Operators   []string `json:"operators"`
	TrainTypes  []string `json:"train_types"`
	Legs        []Leg    `json:"legs"`
}

type apiError struct {
	Error   string   `json:"error"`
	Detail  string   `json:"detail,omitempty"`
	Suggest []*Place `json:"suggestions,omitempty"`
}

func fmtTime(t time.Time) string { return t.Format("2006-01-02T15:04:05-07:00") }

type Server struct {
	e       *Engine
	started time.Time
	loadMs  int64
}

func (s *Server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleRoot)
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/stations", s.handleStations)
	mux.HandleFunc("/search", s.handleSearch)
	mux.HandleFunc("/explorer", s.handleExplorer)
	return withMiddleware(mux)
}

func withMiddleware(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "*")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		h.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, r *http.Request, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	if strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
		w.Header().Set("Content-Encoding", "gzip")
		w.Header().Set("Vary", "Accept-Encoding")
		w.WriteHeader(status)
		gz := gzip.NewWriter(w)
		defer gz.Close()
		json.NewEncoder(gz).Encode(v)
		return
	}
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func param(r *http.Request, names ...string) string {
	q := r.URL.Query()
	for _, n := range names {
		if v := strings.TrimSpace(q.Get(n)); v != "" {
			return v
		}
	}
	return ""
}

func intParam(r *http.Request, name string, def, min, max int) int {
	v, err := strconv.Atoi(param(r, name))
	if err != nil {
		return def
	}
	if v < min {
		return min
	}
	if v > max {
		return max
	}
	return v
}

func (s *Server) handleRoot(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		writeJSON(w, r, http.StatusNotFound, apiError{Error: "not_found"})
		return
	}
	writeJSON(w, r, http.StatusOK, map[string]any{
		"name":      "TrainNomad routing API",
		"endpoints": []string{"/health", "/stations?q=", "/search?from=&to=&date=&time=", "/explorer?from=&date="},
	})
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	n := s.e.Net
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	writeJSON(w, r, http.StatusOK, map[string]any{
		"status":     "ok",
		"built_at":   n.Meta.BuiltAt,
		"valid_from": n.FirstDate().Format("2006-01-02"),
		"valid_to":   n.LastDate().Format("2006-01-02"),
		"operators":  n.Meta.Operators,
		"stops":      n.NumStops(),
		"routes":     n.NumRoutes(),
		"trips":      len(n.TripDays),
		"load_ms":    s.loadMs,
		"uptime_s":   int(time.Since(s.started).Seconds()),
		"memory_mb":  map[string]float64{"heap": float64(m.HeapAlloc) / 1e6, "sys": float64(m.Sys) / 1e6},
		"build":      n.Meta.Stats,
	})
}

func (s *Server) handleStations(w http.ResponseWriter, r *http.Request) {
	q := param(r, "q")
	limit := intParam(r, "limit", 10, 1, 50)
	res := s.e.Places.Search(q, limit)
	if res == nil {
		res = []*Place{}
	}
	writeJSON(w, r, http.StatusOK, map[string]any{"results": res})
}

// resolveQuery lit origine, date et heure communes à /search et /explorer.
func (s *Server) resolveQuery(w http.ResponseWriter, r *http.Request, fromKeys []string) (from *Place, day int, t time.Time, ok bool) {
	n := s.e.Net
	fromV := param(r, fromKeys...)
	from = s.e.Places.Resolve(fromV)
	if from == nil {
		writeJSON(w, r, http.StatusNotFound, apiError{Error: "unknown_origin", Detail: fmt.Sprintf("Gare ou ville inconnue : %q", fromV)})
		return
	}
	loc := n.Locations[n.StopTZ[from.stops[0]]]
	now := time.Now().In(loc)
	dateV := param(r, "date")
	if dateV == "" {
		dateV = now.Format("2006-01-02")
	}
	d, err := time.ParseInLocation("2006-01-02", dateV, loc)
	if err != nil {
		writeJSON(w, r, http.StatusBadRequest, apiError{Error: "bad_date", Detail: "Format attendu : YYYY-MM-DD"})
		return
	}
	hh, mm := 0, 0
	if tv := param(r, "time", "departure_time"); tv != "" {
		parts := strings.Split(tv, ":")
		h, e1 := strconv.Atoi(parts[0])
		var e2 error
		if len(parts) > 1 {
			mm, e2 = strconv.Atoi(parts[1])
		}
		if e1 != nil || e2 != nil || h < 0 || h > 23 || mm < 0 || mm > 59 {
			writeJSON(w, r, http.StatusBadRequest, apiError{Error: "bad_time", Detail: "Format attendu : HH:MM"})
			return
		}
		hh = h
	}
	day = n.DayIndex(d)
	if d.Before(n.FirstDate().AddDate(0, 0, -1)) || d.After(n.LastDate()) {
		writeJSON(w, r, http.StatusBadRequest, apiError{Error: "date_out_of_range", Detail: fmt.Sprintf("Horaires disponibles du %s au %s",
			n.FirstDate().Format("2006-01-02"), n.LastDate().Format("2006-01-02"))})
		return
	}
	t = time.Date(d.Year(), d.Month(), d.Day(), hh, mm, 0, 0, loc)
	return from, day, t, true
}

func (s *Server) minutes(t time.Time) int32 {
	return int32(t.Sub(s.e.Net.BaseDate) / time.Minute)
}

func (s *Server) handleSearch(w http.ResponseWriter, r *http.Request) {
	from, day, t, ok := s.resolveQuery(w, r, []string{"from", "origin"})
	if !ok {
		return
	}
	toV := param(r, "to", "destination")
	to := s.e.Places.Resolve(toV)
	if to == nil {
		writeJSON(w, r, http.StatusNotFound, apiError{Error: "unknown_destination", Detail: fmt.Sprintf("Gare ou ville inconnue : %q", toV)})
		return
	}
	if from.ID == to.ID {
		writeJSON(w, r, http.StatusBadRequest, apiError{Error: "same_place", Detail: "Origine et destination identiques"})
		return
	}
	limit := intParam(r, "limit", 10, 1, 50)
	maxTransfers := intParam(r, "max_transfers", 6, 0, 8)

	start := time.Now()
	raw := s.e.Search(from, to, day, s.minutes(t), limit, maxTransfers)
	elapsed := time.Since(start)

	journeys := make([]Journey, 0, limit)
	dt := s.e.tables.get(day)
	for i, j := range raw {
		if i == limit {
			break
		}
		journeys = append(journeys, s.toJourney(dt, j))
	}

	var next map[string]string
	nextMin := s.minutes(t) + 24*60
	if len(raw) > 0 {
		nextMin = raw[min(limit, len(raw))-1].dep + 1
	}
	if nt := s.e.Net.BaseDate.Add(time.Duration(nextMin) * time.Minute).In(t.Location()); !nt.After(s.e.Net.LastDate().AddDate(0, 0, 1)) {
		next = map[string]string{"date": nt.Format("2006-01-02"), "time": nt.Format("15:04")}
	}

	writeJSON(w, r, http.StatusOK, map[string]any{
		"from":       from,
		"to":         to,
		"date":       t.Format("2006-01-02"),
		"time":       t.Format("15:04"),
		"count":      len(journeys),
		"next":       next,
		"compute_ms": elapsed.Milliseconds(),
		"journeys":   journeys,
	})
}

func (s *Server) handleExplorer(w http.ResponseWriter, r *http.Request) {
	from, day, t, ok := s.resolveQuery(w, r, []string{"from", "origin"})
	if !ok {
		return
	}
	maxTransfers := intParam(r, "max_transfers", 2, 0, 6)
	endOfDay := time.Date(t.Year(), t.Month(), t.Day()+1, 0, 0, 0, 0, t.Location())
	tEnd := s.minutes(endOfDay)
	if tEnd-s.minutes(t) < 60 {
		tEnd = s.minutes(t) + 60
	}
	start := time.Now()
	dests := s.e.Explore(from, day, s.minutes(t), tEnd, maxTransfers)
	if limit := intParam(r, "limit", 0, 0, 5000); limit > 0 && len(dests) > limit {
		dests = dests[:limit]
	}
	writeJSON(w, r, http.StatusOK, map[string]any{
		"from":          from,
		"date":          t.Format("2006-01-02"),
		"time":          t.Format("15:04"),
		"max_transfers": maxTransfers,
		"count":         len(dests),
		"compute_ms":    time.Since(start).Milliseconds(),
		"destinations":  dests,
	})
}

func (s *Server) stopRef(p int32) StopRef {
	n := s.e.Net
	return StopRef{
		ID: "station:" + n.StopID[p], Name: n.StopName[p], City: n.CityName[n.StopCity[p]],
		Country: n.StopCountry[p], Lat: n.StopLat[p], Lon: n.StopLon[p],
	}
}

func (s *Server) toJourney(dt *DayTable, j rawJourney) Journey {
	n := s.e.Net
	out := Journey{DurationMin: j.arr - j.dep, Transfers: j.trains - 1, Operators: []string{}, TrainTypes: []string{}}
	seenOp, seenType := map[string]bool{}, map[string]bool{}
	var ids []string

	var prevArr int32
	var prevStop int32 = -1
	var pendingWalk *rawLeg
	for li := range j.legs {
		l := j.legs[li]
		if l.walk {
			pendingWalk = &j.legs[li]
			continue
		}
		stops := n.routeStops(l.route)
		S := len(stops)
		off := dt.TimeOff[l.route] + uint32(int(l.inst)*S)
		trip := dt.InstTrip[dt.InstOff[l.route]+uint32(l.inst)]
		dep, arr := dt.Dep[off+uint32(l.board)], dt.Arr[off+uint32(l.alight)]
		boardStop, alightStop := stops[l.board], stops[l.alight]

		if prevStop >= 0 {
			tr := Leg{Type: "transfer", From: s.stopRef(prevStop), To: s.stopRef(boardStop), WaitMin: dep - prevArr}
			if pendingWalk != nil {
				m := footpathMinutes(n, pendingWalk.fromStop, pendingWalk.toStop)
				tr.DurationMin, tr.MinMin = m, m
				tr.TransferKind = "walk"
				if n.StopCity[prevStop] == n.StopCity[boardStop] && m > 30 {
					tr.TransferKind = "city"
				}
			} else {
				tr.TransferKind = "same_station"
				tr.MinMin = int32(n.StopChange[prevStop])
				tr.DurationMin = tr.MinMin
			}
			out.Legs = append(out.Legs, tr)
		}

		op := n.Meta.Operators[n.TripOp[trip]]
		ttype := n.Meta.Types[n.TripType[trip]]
		leg := Leg{
			Type: "train", From: s.stopRef(boardStop), To: s.stopRef(alightStop),
			Departure: fmtTime(n.TimeAt(dep, boardStop)), Arrival: fmtTime(n.TimeAt(arr, alightStop)),
			DurationMin: arr - dep, Operator: op.ID, OperatorName: op.Name, TrainType: ttype,
			TrainNumber: n.TripNumber[trip], Headsign: n.StopName[stops[S-1]],
			CheckinMin: int(n.RouteCheckin[l.route]),
		}
		for i := l.board; i <= l.alight; i++ {
			st := StopTime{StopRef: s.stopRef(stops[i])}
			if i > l.board {
				st.Arrival = fmtTime(n.TimeAt(dt.Arr[off+uint32(i)], stops[i]))
			}
			if i < l.alight {
				st.Departure = fmtTime(n.TimeAt(dt.Dep[off+uint32(i)], stops[i]))
			}
			leg.Stops = append(leg.Stops, st)
		}
		out.Legs = append(out.Legs, leg)
		if !seenOp[op.ID] {
			seenOp[op.ID] = true
			out.Operators = append(out.Operators, op.ID)
		}
		if !seenType[ttype] {
			seenType[ttype] = true
			out.TrainTypes = append(out.TrainTypes, ttype)
		}
		ids = append(ids, fmt.Sprintf("%s-%s", n.TripNumber[trip], n.TimeAt(dep, boardStop).Format("0601021504")))
		if prevStop < 0 {
			out.From = s.stopRef(boardStop)
			out.Departure = leg.Departure
		}
		out.To = s.stopRef(alightStop)
		out.Arrival = leg.Arrival
		prevArr, prevStop, pendingWalk = arr, alightStop, nil
	}
	out.ID = strings.Join(ids, "_")
	return out
}
