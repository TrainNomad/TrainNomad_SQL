package main

import (
	"math"
	"sort"
)

// Range-RAPTOR (Delling et al., "Round-Based Public Transit Routing").
// Le tour k correspond à k trains, donc k-1 correspondances : on obtient directement, pour chaque
// heure de départ, le meilleur trajet à 0, 1, 2 ... correspondances. Les départs sont traités du
// plus tardif au plus tôt en conservant les étiquettes, ce qui donne l'ensemble de Pareto
// (départ le plus tard, arrivée la plus tôt, moins de correspondances) sur toute la fenêtre.

const inf = int32(math.MaxInt32)

type legRef struct {
	route  int32
	inst   int32
	board  uint16
	alight uint16
}

type rawLeg struct {
	walk     bool
	route    int32 // train
	inst     int32
	board    int
	alight   int
	fromStop int32 // correspondance à pied / en ville
	toStop   int32
}

type rawJourney struct {
	dep, arr int32
	trains   int
	legs     []rawLeg
}

type rangeQuery struct {
	origins     []int32
	targets     []int32
	tStart      int32
	tEnd        int32
	maxRounds   int
	maxDuration int32   // minutes ; 0 = illimité
	lb          []int32 // minorant du temps restant jusqu'à la destination (nil = pas d'élagage)
	minDur      *int32  // durée du trajet le plus rapide trouvé (partagée entre tranches), cf. relevanceLimit
	onJourney   func(rawJourney)
	onArrival   func(k int, stop int32, arr, dep int32) // exploration : chaque amélioration d'arrivée
}

type raptorState struct {
	K        int
	q        *rangeQuery
	tau      int32
	durLimit int32     // durée au-delà de laquelle un trajet ne serait pas pertinent
	arr      [][]int32 // [tour][gare] arrivée en train
	ready    [][]int32 // [tour][gare] prêt à monter dans un train (après changement ou marche)
	leg      [][]legRef
	from     [][]int32 // origine de ready : -1 = départ, la gare elle-même = changement sur place, sinon marche

	marked, improved     []int32
	isMarked, isImproved []bool
	routeMin             []int32
	routeList            []int32

	isOrigin   []bool
	isTarget   []bool
	targetBest []int32
	targetStop []int32
	targetIter []bool
}

func newRaptorState(n *Network, q *rangeQuery) *raptorState {
	nStops, K := n.NumStops(), q.maxRounds
	st := &raptorState{
		K:          K,
		q:          q,
		isMarked:   make([]bool, nStops),
		isImproved: make([]bool, nStops),
		isOrigin:   make([]bool, nStops),
		isTarget:   make([]bool, nStops),
		targetBest: make([]int32, K+1),
		targetStop: make([]int32, K+1),
		targetIter: make([]bool, K+1),
		routeMin:   make([]int32, n.NumRoutes()),
	}
	grid := func() [][]int32 {
		g := make([][]int32, K+1)
		for k := range g {
			g[k] = make([]int32, nStops)
			for i := range g[k] {
				g[k][i] = inf
			}
		}
		return g
	}
	st.arr, st.ready, st.from = grid(), grid(), grid()
	st.leg = make([][]legRef, K+1)
	for k := range st.leg {
		st.leg[k] = make([]legRef, nStops)
	}
	for k := range st.targetBest {
		st.targetBest[k] = inf
	}
	for i := range st.routeMin {
		st.routeMin[i] = -1
	}
	for _, s := range q.origins {
		st.isOrigin[s] = true
	}
	for _, s := range q.targets {
		st.isTarget[s] = true
	}
	return st
}

func (st *raptorState) mark(s int32) {
	if !st.isMarked[s] {
		st.isMarked[s] = true
		st.marked = append(st.marked, s)
	}
}

func (st *raptorState) bestArr(k int, p int32) int32 {
	b := inf
	for i := 1; i <= k; i++ {
		if st.arr[i][p] < b {
			b = st.arr[i][p]
		}
	}
	return b
}

func (st *raptorState) bestReady(k int, p int32) int32 {
	b := inf
	for i := 0; i <= k; i++ {
		if st.ready[i][p] < b {
			b = st.ready[i][p]
		}
	}
	return b
}

// transferPenalty : une correspondance en moins ne justifie pas d'arriver plus de 2 h plus tard.
const transferPenalty = 120

// targetBound : heure d'arrivée à battre pour qu'un trajet à k trains (ou plus) soit utile.
// Un trajet à k' > k trains arrivant à t rend inutile tout trajet à k trains arrivant après
// t + transferPenalty × (k' - k).
func (st *raptorState) targetBound(k int) int32 {
	if k > st.K {
		k = st.K
	}
	b := inf
	for i := 1; i <= st.K; i++ {
		tb := st.targetBest[i]
		if tb == inf {
			continue
		}
		if i > k {
			tb += transferPenalty * int32(i-k)
		}
		if tb < b {
			b = tb
		}
	}
	return b
}

// depEvent : un train précis partant d'une gare d'origine à l'instant t.
type depEvent struct {
	t     int32
	stop  int32
	route int32
	inst  int32
	pos   int32
}

// departures liste les départs depuis les gares d'origine dans la fenêtre, du plus tard au plus tôt.
func departures(n *Network, dt *DayTable, q *rangeQuery) []depEvent {
	var ev []depEvent
	for _, s := range q.origins {
		for e := n.StopRouteOff[s]; e < n.StopRouteOff[s+1]; e++ {
			r := n.StopRoutes[e]
			i := int(n.StopRoutePos[e])
			S := int(n.RouteStopOff[r+1] - n.RouteStopOff[r])
			if i == S-1 || n.routeFlags(r)[i]&noPickup != 0 {
				continue
			}
			off := dt.TimeOff[r]
			for qi := 0; qi < dt.numInst(r); qi++ {
				d := dt.Dep[off+uint32(qi*S+i)]
				if d >= q.tStart && d < q.tEnd {
					ev = append(ev, depEvent{d, s, r, int32(qi), int32(i)})
				}
			}
		}
	}
	sort.Slice(ev, func(a, b int) bool { return ev[a].t > ev[b].t })
	return ev
}

func runRaptor(n *Network, dt *DayTable, q *rangeQuery) {
	K := q.maxRounds
	st := newRaptorState(n, q)
	events := departures(n, dt, q)
	for gi := 0; gi < len(events); {
		tau := events[gi].t
		st.tau = tau
		st.durLimit = inf
		if q.minDur != nil && *q.minDur != inf {
			st.durLimit = relevanceLimit(*q.minDur)
		}
		for k := range st.targetIter {
			st.targetIter[k] = false
		}

		// tour 1 : uniquement les trains qui partent à tau. Le trajet part donc exactement à tau,
		// les départs plus tardifs de la fenêtre ayant déjà été traités.
		for ; gi < len(events) && events[gi].t == tau; gi++ {
			ev := events[gi]
			st.ready[0][ev.stop] = tau
			st.from[0][ev.stop] = -1
			st.scanTrip(n, dt, ev)
		}
		st.relaxTransfers(n, 1)

		for k := 2; k <= K && len(st.marked) > 0; k++ {
			// routes passant par une gare marquée, à partir de la première gare marquée
			for _, p := range st.marked {
				st.isMarked[p] = false
				for e := n.StopRouteOff[p]; e < n.StopRouteOff[p+1]; e++ {
					r, pos := n.StopRoutes[e], int32(n.StopRoutePos[e])
					if st.routeMin[r] < 0 {
						st.routeList = append(st.routeList, r)
						st.routeMin[r] = pos
					} else if pos < st.routeMin[r] {
						st.routeMin[r] = pos
					}
				}
			}
			st.marked = st.marked[:0]

			for _, r := range st.routeList {
				st.scanRoute(n, dt, r, int(st.routeMin[r]), k)
				st.routeMin[r] = -1
			}
			st.routeList = st.routeList[:0]
			st.relaxTransfers(n, k)
		}
		for _, p := range st.marked {
			st.isMarked[p] = false
		}
		st.marked = st.marked[:0]

		if q.onJourney != nil {
			for k := 1; k <= K; k++ {
				if st.targetIter[k] {
					if j, ok := st.reconstruct(n, dt, k, st.targetStop[k], tau); ok {
						if q.minDur != nil && j.arr-j.dep < *q.minDur {
							*q.minDur = j.arr - j.dep
						}
						q.onJourney(j)
					}
				}
			}
		}
	}
}

// relaxTransfers : correspondances depuis les gares atteintes au tour k (changement sur place,
// marche, traversée de ville).
func (st *raptorState) relaxTransfers(n *Network, k int) {
	for _, p := range st.improved {
		st.isImproved[p] = false
		if k == st.K {
			continue
		}
		a := st.arr[k][p]
		st.relax(k, p, a+int32(n.StopChange[p]), p)
		for e := n.FpOff[p]; e < n.FpOff[p+1]; e++ {
			st.relax(k, n.FpTo[e], a+int32(n.FpMin[e]), p)
		}
	}
	st.improved = st.improved[:0]
}

func (st *raptorState) relax(k int, p int32, v int32, from int32) {
	// revenir dans une gare de départ ne sert à rien : il suffisait d'en partir plus tard
	if st.isOrigin[p] || v >= st.bestReady(k, p) || v >= st.targetBound(k+1) {
		return
	}
	if st.q.maxDuration > 0 && v-st.tau > st.q.maxDuration {
		return
	}
	if st.q.lb != nil && (st.q.lb[p] == inf || v+st.q.lb[p] >= st.targetBound(k+1) || v+st.q.lb[p]-st.tau > st.durLimit) {
		return
	}
	st.ready[k][p] = v
	st.from[k][p] = from
	st.mark(p)
}

// arrive enregistre une arrivée en train au tour k si elle améliore quelque chose.
func (st *raptorState) arrive(k int, p int32, a int32, l legRef) {
	if st.isOrigin[p] || a >= st.bestArr(k, p) || a >= st.targetBound(k) {
		return
	}
	if st.q.maxDuration > 0 && a-st.tau > st.q.maxDuration {
		return
	}
	// élagage : même au mieux, on n'arriverait pas avant le meilleur trajet connu
	if st.q.lb != nil && (st.q.lb[p] == inf || a+st.q.lb[p] >= st.targetBound(k) || a+st.q.lb[p]-st.tau > st.durLimit) {
		return
	}
	st.arr[k][p] = a
	st.leg[k][p] = l
	if !st.isImproved[p] {
		st.isImproved[p] = true
		st.improved = append(st.improved, p)
	}
	if st.isTarget[p] {
		st.targetBest[k] = a
		st.targetStop[k] = p
		st.targetIter[k] = true
	}
	if st.q.onArrival != nil {
		st.q.onArrival(k, p, a, st.tau)
	}
}

// scanTrip : tour 1, on suit le seul train du départ ev.
func (st *raptorState) scanTrip(n *Network, dt *DayTable, ev depEvent) {
	stops, flags := n.routeStops(ev.route), n.routeFlags(ev.route)
	S := len(stops)
	off := dt.TimeOff[ev.route] + uint32(int(ev.inst)*S)
	for i := int(ev.pos) + 1; i < S; i++ {
		if flags[i]&noDropoff == 0 {
			st.arrive(1, stops[i], dt.Arr[off+uint32(i)], legRef{ev.route, ev.inst, uint16(ev.pos), uint16(i)})
		}
	}
}

// scanRoute : tours >= 2, on monte dans le premier train possible à chaque gare marquée.
func (st *raptorState) scanRoute(n *Network, dt *DayTable, r int32, i0, k int) {
	if dt.numInst(r) == 0 {
		return
	}
	stops, flags := n.routeStops(r), n.routeFlags(r)
	S := len(stops)
	off := dt.TimeOff[r]
	checkin := int32(n.RouteCheckin[r]) // enregistrement (Eurostar) lors d'une correspondance
	cur, board := -1, 0
	for i := i0; i < S; i++ {
		p := stops[i]
		if cur >= 0 && flags[i]&noDropoff == 0 {
			st.arrive(k, p, dt.Arr[off+uint32(cur*S+i)], legRef{r, int32(cur), uint16(board), uint16(i)})
		}
		if flags[i]&noPickup != 0 || i == S-1 {
			continue
		}
		rd := st.ready[k-1][p]
		if rd == inf {
			continue
		}
		need := rd + checkin
		if cur >= 0 && need > dt.Dep[off+uint32(cur*S+i)] {
			continue
		}
		if qn := dt.earliest(r, S, i, need); qn >= 0 {
			if cur < 0 || dt.Dep[off+uint32(qn*S+i)] < dt.Dep[off+uint32(cur*S+i)] {
				cur, board = qn, i
			}
		}
	}
}

// reconstruct remonte les étiquettes depuis la gare d'arrivée et vérifie la cohérence des horaires.
func (st *raptorState) reconstruct(n *Network, dt *DayTable, k int, p int32, tau int32) (rawJourney, bool) {
	j := rawJourney{arr: st.arr[k][p], trains: k}
	var legs []rawLeg
	for ; k > 0; k-- {
		l := st.leg[k][p]
		legs = append(legs, rawLeg{route: l.route, inst: l.inst, board: int(l.board), alight: int(l.alight)})
		bs := n.routeStops(l.route)[l.board]
		fr := st.from[k-1][bs]
		if k == 1 {
			if fr != -1 {
				return j, false
			}
			break
		}
		if fr < 0 {
			return j, false
		}
		if fr != bs {
			legs = append(legs, rawLeg{walk: true, fromStop: fr, toStop: bs})
		}
		p = fr
	}
	for a, b := 0, len(legs)-1; a < b; a, b = a+1, b-1 {
		legs[a], legs[b] = legs[b], legs[a]
	}
	j.legs = legs

	// validation : chaque train est pris après l'arrivée précédente + changement / marche + enregistrement
	var prevArr int32 = -1
	var prevStop int32 = -1
	var walkMin int32
	for i, l := range legs {
		if l.walk {
			walkMin = footpathMinutes(n, l.fromStop, l.toStop)
			if walkMin < 0 {
				return j, false
			}
			continue
		}
		S := int(n.RouteStopOff[l.route+1] - n.RouteStopOff[l.route])
		off := dt.TimeOff[l.route]
		dep := dt.Dep[off+uint32(int(l.inst)*S+l.board)]
		arr := dt.Arr[off+uint32(int(l.inst)*S+l.alight)]
		if i == 0 {
			j.dep = dep
			if dep < tau {
				return j, false
			}
		} else {
			need := prevArr + int32(n.RouteCheckin[l.route])
			if walkMin > 0 {
				need += walkMin
			} else {
				need += int32(n.StopChange[prevStop])
			}
			if dep < need {
				return j, false
			}
		}
		prevArr, prevStop, walkMin = arr, n.routeStops(l.route)[l.alight], 0
	}
	return j, prevArr == j.arr
}

func footpathMinutes(n *Network, a, b int32) int32 {
	for e := n.FpOff[a]; e < n.FpOff[a+1]; e++ {
		if n.FpTo[e] == b {
			return int32(n.FpMin[e])
		}
	}
	return -1
}
