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

	// fastTypes : types de train à grande vitesse / réservation obligatoire (index Meta.Types).
	// En attendant les prix, « sans grande vitesse » sert de critère de Pareto : un trajet TER ou
	// Intercités n'est jamais écarté au profit d'un TGV plus rapide (souvent bien plus cher).
	fastTypes []bool
	hasFast   bool
	slowLB    *lbGraph // minorants sans grande vitesse (passe « variantes »)

	exploreMu    sync.Mutex
	exploreCache map[string][]Destination
	exploreOrder []string
}

// fastTypeNames : noms de Meta.Types considérés comme grande vitesse.
var fastTypeNames = map[string]bool{
	"TGV INOUI": true, "OUIGO": true, "TGV Lyria": true, "TGV Thalys": true, "ICE": true, "Eurostar": true,
	"AVE": true, "AVE International": true, "Avlo": true, "Alvia": true, "Avant": true, "Avant Exprés": true,
	"Euromed": true, "Ouigo España": true, "Frecciarossa": true, "Frecciargento": true, "Frecciabianca": true,
	"Italo": true, "Alfa Pendular": true, "RailJet": true,
}

func NewEngine(n *Network) *Engine {
	e := &Engine{
		Net:          n,
		Places:       NewPlaceIndex(n),
		tables:       newTableCache(n, 4),
		exploreCache: map[string][]Destination{},
		fastTypes:    make([]bool, len(n.Meta.Types)),
	}
	for i, t := range n.Meta.Types {
		if fastTypeNames[t] {
			e.fastTypes[i], e.hasFast = true, true
		}
	}
	if e.hasFast {
		e.slowLB = n.newLBGraph(e.fastTypes)
	}
	return e
}

// Durée maximale d'un trajet explorée par le moteur.
const maxJourneyMinutes = 36 * 60

// maxTransferWait : au-delà de cette attente en correspondance (nuit passée en gare), un trajet
// n'est proposé que s'il n'existe aucune alternative sans (ex. Amsterdam → Séville, faute de train de
// nuit). Un train de nuit direct n'a pas d'attente et n'est donc jamais concerné.
const maxTransferWait = 5 * 60

// Search renvoie les trajets Pareto-optimaux (départ, arrivée, correspondances, grande vitesse ou non)
// partant après tStart. Les départs sont calculés par tranches successives (6 h, 6 h, 12 h) jusqu'à
// obtenir `limit` trajets ; chaque tranche n'est calculée qu'une fois.
//
// variants : pour chaque tranche contenant un trajet à grande vitesse, un second calcul sans
// grande vitesse propose l'alternative TER / Intercités (ex. Vitré → Laval → Le Mans → Paris).
func (e *Engine) Search(from, to *Place, day int, tStart int32, limit, maxTransfers int, variants bool) []rawJourney {
	dt := e.tables.get(day)
	lb := e.Net.lowerBounds(to.stops)
	var slowLB []int32 // calculé au premier besoin
	variants = variants && e.hasFast
	var all []rawJourney
	seen := map[string]bool{}
	run := func(t0, t1 int32, lb []int32, exclude []bool) (fast bool) {
		if t1 <= t0 || minAt(lb, from.stops) == inf {
			return false
		}
		runRaptor(e.Net, dt, &rangeQuery{
			origins:     from.stops,
			targets:     to.stops,
			tStart:      t0,
			tEnd:        t1,
			maxRounds:   maxTransfers + 1,
			maxDuration: maxJourneyMinutes,
			lb:          lb,
			exclude:     exclude,
			onJourney: func(j rawJourney) {
				j.longWait = e.longestWait(dt, j) > maxTransferWait
				j.fast = variants && e.isFast(dt, j)
				fast = fast || j.fast
				if sig := e.signature(dt, j); !seen[sig] {
					seen[sig] = true
					all = append(all, j)
				}
			},
		})
		return fast
	}
	runSlow := func(t0, t1 int32) {
		if slowLB == nil {
			slowLB = e.Net.boundsOn(e.slowLB, to.stops)
		}
		run(t0, t1, slowLB, e.fastTypes)
	}
	var out []rawJourney
	tEnd := tStart
	for _, width := range []int32{6 * 60, 6 * 60, 12 * 60} {
		if run(tEnd, tEnd+width, lb, nil) && variants {
			runSlow(tEnd, tEnd+width)
		}
		tEnd += width
		out = selectJourneys(all, tEnd)
		if len(out) >= limit {
			break
		}
	}

	// Un trajet n'est écarté que si un autre part plus tard et arrive plus tôt. Les départs situés
	// après la fenêtre servent donc uniquement à éliminer, par exemple, 30 h de correspondances
	// alors que le TGV direct du lendemain arrive à la même heure. Un tel départ doit précéder
	// l'arrivée à dominer d'au moins le minorant du trajet : inutile de calculer au-delà.
	lastArr, lastSlowArr := int32(0), int32(0)
	for _, j := range out {
		lastArr = max(lastArr, j.arr)
		if variants && !j.fast {
			lastSlowArr = max(lastSlowArr, j.arr)
		}
	}
	grew := false
	if end := lastArr - minAt(lb, from.stops); end > tEnd {
		run(tEnd, end, lb, nil)
		grew = true
	}
	if lastSlowArr > 0 {
		if slowLB == nil {
			slowLB = e.Net.boundsOn(e.slowLB, to.stops)
		}
		if end := lastSlowArr - minAt(slowLB, from.stops); end > tEnd {
			runSlow(tEnd, end)
			grew = true
		}
	}
	if grew {
		out = selectJourneys(all, tEnd)
	}
	return out
}

func minAt(lb []int32, stops []int32) int32 {
	m := inf
	for _, s := range stops {
		m = min(m, lb[s])
	}
	return m
}

// longestWait : plus longue attente entre l'arrivée d'un train et le départ du suivant.
func (e *Engine) longestWait(dt *DayTable, j rawJourney) int32 {
	var longest, prevArr int32
	first := true
	for _, l := range j.legs {
		if l.walk {
			continue
		}
		S := int(e.Net.RouteStopOff[l.route+1] - e.Net.RouteStopOff[l.route])
		off := dt.TimeOff[l.route] + uint32(int(l.inst)*S)
		if !first {
			longest = max(longest, dt.Dep[off+uint32(l.board)]-prevArr)
		}
		prevArr, first = dt.Arr[off+uint32(l.alight)], false
	}
	return longest
}

func (e *Engine) isFast(dt *DayTable, j rawJourney) bool {
	for _, l := range j.legs {
		if !l.walk && e.fastTypes[e.Net.TripType[dt.InstTrip[dt.InstOff[l.route]+uint32(l.inst)]]] {
			return true
		}
	}
	return false
}

// selectJourneys : trajets non dominés partant avant tEnd, sans nuit en gare s'il existe une
// alternative qui l'évite.
func selectJourneys(all []rawJourney, tEnd int32) []rawJourney {
	out := departingBefore(pareto(all), tEnd)
	short := out[:0:0]
	for _, j := range out {
		if !j.longWait {
			short = append(short, j)
		}
	}
	if len(short) > 0 {
		return short
	}
	return out
}

// pareto garde les trajets non dominés : un trajet est dominé par un autre qui part au plus tôt
// en même temps, arrive au plus tard en même temps avec au plus autant de trains, sans grande
// vitesse ni nuit en gare s'il n'en a pas lui-même (et diffère). Nécessaire pour fusionner des
// tranches de départs et des calculs (avec / sans grande vitesse) faits séparément.
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
				(!o.fast || j.fast) && (!o.longWait || j.longWait) &&
				(o.dep > j.dep || o.arr < j.arr || o.trains < j.trains || o.fast != j.fast || o.longWait != j.longWait)
		}
		if !dominated {
			out = append(out, j)
		}
	}
	return out
}

func departingBefore(js []rawJourney, t int32) []rawJourney {
	out := js[:0]
	for _, j := range js {
		if j.dep < t {
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
