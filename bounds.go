package main

import "container/heap"

// buildLowerBoundGraph : pour chaque paire d'arrêts consécutifs d'une route, le temps de parcours
// minimal (tous trajets confondus), plus les correspondances à pied. Stocké à l'envers (CSR par
// gare d'arrivée) pour un Dijkstra depuis la destination.
func (n *Network) buildLowerBoundGraph() {
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
				k := n.RouteTimeOff[r] + (t-t0)*uint32(S)
				if d := int32(n.TimeArr[k+uint32(i+1)]) - int32(n.TimeDep[k+uint32(i)]); d < m {
					m = d
				}
			}
			add(stops[i], stops[i+1], max(m, 0))
		}
	}
	for u := int32(0); u < int32(n.NumStops()); u++ {
		for e := n.FpOff[u]; e < n.FpOff[u+1]; e++ {
			add(u, n.FpTo[e], int32(n.FpMin[e]))
		}
	}

	ns := n.NumStops()
	n.lbOff = make([]uint32, ns+1)
	for k := range best {
		n.lbOff[k[1]+1]++
	}
	for i := 1; i <= ns; i++ {
		n.lbOff[i] += n.lbOff[i-1]
	}
	fill := append([]uint32(nil), n.lbOff[:ns]...)
	n.lbFrom = make([]int32, len(best))
	n.lbMin = make([]int32, len(best))
	for k, w := range best {
		v := k[1]
		n.lbFrom[fill[v]] = k[0]
		n.lbMin[fill[v]] = w
		fill[v]++
	}
}

// lowerBounds renvoie, pour chaque gare, un minorant du temps de trajet jusqu'à l'une des cibles
// (inf si la cible est inatteignable).
func (n *Network) lowerBounds(targets []int32) []int32 {
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
		for e := n.lbOff[it.stop]; e < n.lbOff[it.stop+1]; e++ {
			u := n.lbFrom[e]
			if d := it.d + n.lbMin[e]; d < dist[u] {
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
