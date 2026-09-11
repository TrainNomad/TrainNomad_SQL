package main

import (
	"sort"
	"sync"
)

// DayTable matérialise, pour une date de recherche, toutes les circulations (trajet × jour de service)
// de la veille au surlendemain, avec des horaires en minutes UTC. RAPTOR travaille ensuite sur de
// simples tableaux d'entiers triés.
type DayTable struct {
	Day      int
	InstOff  []uint32 // par route : instances [InstOff[r], InstOff[r+1])
	InstTrip []uint32
	TimeOff  []uint32 // par route : début des horaires ; instance q, arrêt i -> TimeOff[r] + q*S + i
	Arr, Dep []int32
	Sorted   []bool // la route respecte l'ordre FIFO à tous les arrêts (recherche dichotomique possible)
}

const (
	daysBefore = 1
	daysAfter  = 2
)

func buildDayTable(n *Network, day int) *DayTable {
	nr := n.NumRoutes()
	dt := &DayTable{
		Day:     day,
		InstOff: make([]uint32, nr+1),
		TimeOff: make([]uint32, nr+1),
		Sorted:  make([]bool, nr),
	}
	type inst struct {
		trip uint32
		base int32
	}
	var buf []inst
	for r := int32(0); r < int32(nr); r++ {
		S := int(n.RouteStopOff[r+1] - n.RouteStopOff[r])
		t0, t1 := n.RouteTripOff[r], n.RouteTripOff[r+1]
		tz := n.RouteTZ[r]
		buf = buf[:0]
		for d := day - daysBefore; d <= day+daysAfter; d++ {
			if d < 0 || d >= n.Meta.NDays {
				continue
			}
			base := n.dayBase[tz][d]
			for t := t0; t < t1; t++ {
				if n.tripActive(t, d) {
					buf = append(buf, inst{t, base})
				}
			}
		}
		timeOf := func(it inst, i int, arr bool) int32 {
			k := n.RouteTimeOff[r] + (it.trip-t0)*uint32(S) + uint32(i)
			if arr {
				return it.base + int32(n.TimeArr[k])
			}
			return it.base + int32(n.TimeDep[k])
		}
		sort.SliceStable(buf, func(a, b int) bool { return timeOf(buf[a], 0, false) < timeOf(buf[b], 0, false) })

		sorted := true
		for _, it := range buf {
			dt.InstTrip = append(dt.InstTrip, it.trip)
			for i := 0; i < S; i++ {
				dt.Arr = append(dt.Arr, timeOf(it, i, true))
				dt.Dep = append(dt.Dep, timeOf(it, i, false))
			}
		}
		off := dt.TimeOff[r]
		for q := 1; q < len(buf) && sorted; q++ {
			for i := 0; i < S; i++ {
				a, b := off+uint32((q-1)*S+i), off+uint32(q*S+i)
				if dt.Dep[b] < dt.Dep[a] || dt.Arr[b] < dt.Arr[a] {
					sorted = false
					break
				}
			}
		}
		dt.Sorted[r] = sorted
		dt.InstOff[r+1] = uint32(len(dt.InstTrip))
		dt.TimeOff[r+1] = uint32(len(dt.Arr))
	}
	return dt
}

func (dt *DayTable) numInst(r int32) int { return int(dt.InstOff[r+1] - dt.InstOff[r]) }

// earliest renvoie l'instance locale de la route r partant de l'arrêt i au plus tôt à t, ou -1.
func (dt *DayTable) earliest(r int32, S, i int, t int32) int {
	off := dt.TimeOff[r]
	n := dt.numInst(r)
	if dt.Sorted[r] {
		q := sort.Search(n, func(q int) bool { return dt.Dep[off+uint32(q*S+i)] >= t })
		if q == n {
			return -1
		}
		return q
	}
	best, bestT := -1, int32(0)
	for q := 0; q < n; q++ {
		d := dt.Dep[off+uint32(q*S+i)]
		if d >= t && (best < 0 || d < bestT) {
			best, bestT = q, d
		}
	}
	return best
}

// tableCache garde les DayTable des dernières dates interrogées (≈ 5 Mo chacune).
type tableCache struct {
	mu    sync.Mutex
	net   *Network
	max   int
	order []int
	items map[int]*dayEntry
}

type dayEntry struct {
	once sync.Once
	dt   *DayTable
}

func newTableCache(n *Network, max int) *tableCache {
	return &tableCache{net: n, max: max, items: map[int]*dayEntry{}}
}

func (c *tableCache) get(day int) *DayTable {
	c.mu.Lock()
	e, ok := c.items[day]
	if ok {
		for i, d := range c.order {
			if d == day {
				c.order = append(c.order[:i], c.order[i+1:]...)
				break
			}
		}
	} else {
		e = &dayEntry{}
		c.items[day] = e
		if len(c.order) >= c.max {
			delete(c.items, c.order[0])
			c.order = c.order[1:]
		}
	}
	c.order = append(c.order, day)
	c.mu.Unlock()
	e.once.Do(func() { e.dt = buildDayTable(c.net, day) })
	return e.dt
}
