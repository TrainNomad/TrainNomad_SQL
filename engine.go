package main

import (
	"fmt"
	"sort"
	"strings"
	"sync"
)

type Engine struct {
	Net    *Network
	Places *PlaceIndex
	tables *tableCache

	exploreMu    sync.Mutex
	exploreCache map[string][]Destination
	exploreOrder []string
}

func NewEngine(n *Network) *Engine {
	return &Engine{
		Net:          n,
		Places:       NewPlaceIndex(n),
		tables:       newTableCache(n, 4),
		exploreCache: map[string][]Destination{},
	}
}

// Durée maximale d'un trajet explorée par le moteur.
const maxJourneyMinutes = 36 * 60

// Search renvoie les trajets Pareto-optimaux (départ, arrivée, correspondances) partant après tStart.
// Les départs sont calculés par tranches successives (6 h, 6 h, 12 h) jusqu'à obtenir `limit`
// trajets pertinents ; chaque tranche n'est calculée qu'une fois.
func (e *Engine) Search(from, to *Place, day int, tStart int32, limit, maxTransfers int) []rawJourney {
	dt := e.tables.get(day)
	lb := e.Net.lowerBounds(to.stops)
	var all, out []rawJourney
	seen := map[string]bool{}
	minDur := inf
	sliceStart := tStart
	for _, width := range []int32{6 * 60, 6 * 60, 12 * 60} {
		runRaptor(e.Net, dt, &rangeQuery{
			origins:     from.stops,
			targets:     to.stops,
			tStart:      sliceStart,
			tEnd:        sliceStart + width,
			maxRounds:   maxTransfers + 1,
			maxDuration: maxJourneyMinutes,
			lb:          lb,
			minDur:      &minDur,
			onJourney: func(j rawJourney) {
				if sig := e.signature(dt, j); !seen[sig] {
					seen[sig] = true
					all = append(all, j)
				}
			},
		})
		sliceStart += width
		out = relevant(pareto(all))
		if len(out) >= limit {
			break
		}
	}
	return out
}

// pareto garde les trajets non dominés : un trajet est dominé par un autre qui part au plus tôt
// en même temps, arrive au plus tard en même temps avec au plus autant de trains (et diffère).
// Nécessaire pour fusionner des tranches de départs calculées séparément.
func pareto(js []rawJourney) []rawJourney {
	sort.SliceStable(js, func(a, b int) bool {
		if js[a].dep != js[b].dep {
			return js[a].dep < js[b].dep
		}
		return js[a].arr < js[b].arr
	})
	out := make([]rawJourney, 0, len(js))
	for _, j := range js {
		dominated := false
		for k := 0; k < len(js) && !dominated; k++ {
			o := js[k]
			dominated = o.dep >= j.dep && o.arr <= j.arr && o.trains <= j.trains &&
				(o.dep > j.dep || o.arr < j.arr || o.trains < j.trains)
		}
		if !dominated {
			out = append(out, j)
		}
	}
	return out
}

// relevanceLimit : durée maximale d'un trajet pertinent quand le plus rapide dure minDur.
func relevanceLimit(minDur int32) int32 { return max(minDur*3/2, minDur+180) }

// relevant écarte les trajets beaucoup plus longs que le plus rapide de la fenêtre
// (ex. nuit passée en gare alors qu'un départ le lendemain matin arrive presque aussi tôt).
func relevant(js []rawJourney) []rawJourney {
	if len(js) == 0 {
		return js
	}
	minDur := js[0].arr - js[0].dep
	for _, j := range js {
		minDur = min(minDur, j.arr-j.dep)
	}
	limit := relevanceLimit(minDur)
	out := js[:0]
	for _, j := range js {
		if j.arr-j.dep <= limit {
			out = append(out, j)
		}
	}
	return out
}

func (e *Engine) signature(dt *DayTable, j rawJourney) string {
	var b strings.Builder
	for _, l := range j.legs {
		if l.walk {
			continue
		}
		trip := dt.InstTrip[dt.InstOff[l.route]+uint32(l.inst)]
		fmt.Fprintf(&b, "%s@%d/", e.Net.TripNumber[trip], dt.Dep[dt.TimeOff[l.route]+uint32(int(l.inst)*int(e.Net.RouteStopOff[l.route+1]-e.Net.RouteStopOff[l.route])+l.board)])
	}
	return b.String()
}

// Destination : meilleur trajet vers une ville (ou gare isolée) pour l'exploration sur carte.
type Destination struct {
	Place       *Place         `json:"place"`
	DurationMin int32          `json:"duration_min"`
	Transfers   int            `json:"transfers"`
	Departure   string         `json:"departure"`
	Arrival     string         `json:"arrival"`
	Direct      *DirectSummary `json:"direct"`
}

type DirectSummary struct {
	DurationMin int32  `json:"duration_min"`
	Departure   string `json:"departure"`
	Arrival     string `json:"arrival"`
}

type arrivalBest struct {
	dur, dep, arr int32
	trains        int
}

// Explore calcule en un seul passage RAPTOR (sans destination) le trajet le plus court vers
// toutes les gares atteignables depuis `from` pour les départs de la fenêtre.
func (e *Engine) Explore(from *Place, day int, tStart, tEnd int32, maxTransfers int) []Destination {
	key := fmt.Sprintf("%s|%d|%d|%d|%d", from.ID, day, tStart, tEnd, maxTransfers)
	e.exploreMu.Lock()
	if res, ok := e.exploreCache[key]; ok {
		e.exploreMu.Unlock()
		return res
	}
	e.exploreMu.Unlock()

	n := e.Net
	dt := e.tables.get(day)
	best := make([]arrivalBest, n.NumStops())
	direct := make([]arrivalBest, n.NumStops())
	runRaptor(n, dt, &rangeQuery{
		origins:     from.stops,
		tStart:      tStart,
		tEnd:        tEnd,
		maxRounds:   maxTransfers + 1,
		maxDuration: maxJourneyMinutes,
		onArrival: func(k int, p int32, arr, dep int32) {
			dur := arr - dep
			if b := &best[p]; b.trains == 0 || dur < b.dur {
				*b = arrivalBest{dur, dep, arr, k}
			}
			if k == 1 {
				if b := &direct[p]; b.trains == 0 || dur < b.dur {
					*b = arrivalBest{dur, dep, arr, k}
				}
			}
		},
	})

	isOrigin := map[int32]bool{}
	for _, s := range from.stops {
		isOrigin[n.StopCity[s]] = true
	}
	type cityBest struct {
		best, direct      arrivalBest
		bestStop, dirStop int32
	}
	byCity := map[int32]*cityBest{}
	for s := int32(0); s < int32(n.NumStops()); s++ {
		c := n.StopCity[s]
		if best[s].trains == 0 || isOrigin[c] {
			continue
		}
		cb := byCity[c]
		if cb == nil {
			cb = &cityBest{}
			byCity[c] = cb
		}
		if cb.best.trains == 0 || best[s].dur < cb.best.dur {
			cb.best, cb.bestStop = best[s], s
		}
		if direct[s].trains > 0 && (cb.direct.trains == 0 || direct[s].dur < cb.direct.dur) {
			cb.direct, cb.dirStop = direct[s], s
		}
	}

	origin := from.stops[0]
	out := make([]Destination, 0, len(byCity))
	for c, cb := range byCity {
		place := e.Places.byID["city:"+n.CityID[c]]
		if place == nil {
			place = e.Places.byID["station:"+n.StopID[cb.bestStop]]
		}
		d := Destination{
			Place:       place,
			DurationMin: cb.best.dur,
			Transfers:   cb.best.trains - 1,
			Departure:   fmtTime(n.TimeAt(cb.best.dep, origin)),
			Arrival:     fmtTime(n.TimeAt(cb.best.arr, cb.bestStop)),
		}
		if cb.direct.trains > 0 {
			d.Direct = &DirectSummary{
				DurationMin: cb.direct.dur,
				Departure:   fmtTime(n.TimeAt(cb.direct.dep, origin)),
				Arrival:     fmtTime(n.TimeAt(cb.direct.arr, cb.dirStop)),
			}
		}
		out = append(out, d)
	}
	sort.Slice(out, func(a, b int) bool {
		if out[a].DurationMin != out[b].DurationMin {
			return out[a].DurationMin < out[b].DurationMin
		}
		return out[a].Place.Name < out[b].Place.Name
	})

	e.exploreMu.Lock()
	if len(e.exploreOrder) >= 64 {
		delete(e.exploreCache, e.exploreOrder[0])
		e.exploreOrder = e.exploreOrder[1:]
	}
	e.exploreCache[key] = out
	e.exploreOrder = append(e.exploreOrder, key)
	e.exploreMu.Unlock()
	return out
}
