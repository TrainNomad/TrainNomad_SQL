package main

import "container/heap"

// lbGraph : pour chaque paire d'arrêts consécutifs d'une route, le temps de parcours minimal,
// plus les correspondances à pied. Stocké à l'envers (CSR par gare d'arrivée) pour un Dijkstra
// depuis la destination.
type lbGraph struct {
	off  []uint32
	from []int32
	min  []int32
}

// newLBGraph construit le graphe des minorants en ignorant les trains dont le type est exclu
// (nil = tous les trains).
func (n *Network) newLBGraph(exclude []bool) *lbGraph {
	best := map[[2]int32]int32{}
	add := func(u, v, w int32) {
		k := [2]int32{u, v}
		if old, ok := best[k]; !ok || w < old {
			best[k] = w
		}
	}
	for r := int32(0); r < int32(n.NumRoutes()); r++ {
		stops := n.routeStops(r)
		S := len(stops)
		t0, t1 := n.RouteTripOff[r], n.RouteTripOff[r+1]
		for i := 0; i+1 < S; i++ {
			m := int32(1 << 30)
			for t := t0; t < t1; t++ {
				if exclude != nil && exclude[n.TripType[t]] {
					continue
				}
				k := n.RouteTimeOff[r] + (t-t0)*uint32(S)
				if d := int32(n.TimeArr[k+uint32(i+1)]) - int32(n.TimeDep[k+uint32(i)]); d < m {
					m = d
				}
			}
			if m < 1<<30 {
				add(stops[i], stops[i+1], max(m, 0))
			}
		}
	}
	for u := int32(0); u < int32(n.NumStops()); u++ {
		for e := n.FpOff[u]; e < n.FpOff[u+1]; e++ {
			add(u, n.FpTo[e], int32(n.FpMin[e]))
		}
	}

	ns := n.NumStops()
	g := &lbGraph{off: make([]uint32, ns+1), from: make([]int32, len(best)), min: make([]int32, len(best))}
	for k := range best {
		g.off[k[1]+1]++
	}
	for i := 1; i <= ns; i++ {
		g.off[i] += g.off[i-1]
	}
	fill := append([]uint32(nil), g.off[:ns]...)
	for k, w := range best {
		v := k[1]
		g.from[fill[v]] = k[0]
		g.min[fill[v]] = w
		fill[v]++
	}
	return g
}

func (n *Network) buildLowerBoundGraph() {
	g := n.newLBGraph(nil)
	n.lbOff, n.lbFrom, n.lbMin = g.off, g.from, g.min
}

// lowerBounds renvoie, pour chaque gare, un minorant du temps de trajet jusqu'à l'une des cibles
// (inf si la cible est inatteignable).
func (n *Network) lowerBounds(targets []int32) []int32 {
	return n.boundsOn(&lbGraph{n.lbOff, n.lbFrom, n.lbMin}, targets)
}

func (n *Network) boundsOn(g *lbGraph, targets []int32) []int32 {
	dist := make([]int32, n.NumStops())
	for i := range dist {
		dist[i] = inf
	}
	h := &distHeap{}
	for _, t := range targets {
		dist[t] = 0
		heap.Push(h, distItem{t, 0})
	}
	for h.Len() > 0 {
		it := heap.Pop(h).(distItem)
		if it.d > dist[it.stop] {
			continue
		}
		for e := g.off[it.stop]; e < g.off[it.stop+1]; e++ {
			u := g.from[e]
			if d := it.d + g.min[e]; d < dist[u] {
				dist[u] = d
				heap.Push(h, distItem{u, d})
			}
		}
	}
	return dist
}

type distItem struct {
	stop int32
	d    int32
}

type distHeap []distItem

func (h distHeap) Len() int           { return len(h) }
func (h distHeap) Less(i, j int) bool { return h[i].d < h[j].d }
func (h distHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *distHeap) Push(x any)        { *h = append(*h, x.(distItem)) }
func (h *distHeap) Pop() any {
	old := *h
	x := old[len(old)-1]
	*h = old[:len(old)-1]
	return x
}
