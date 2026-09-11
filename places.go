package main

import (
	"sort"
	"strings"
	"unicode"
)

// Place est une origine / destination : une ville (toutes ses gares) ou une gare précise.
type Place struct {
	Type    string  `json:"type"` // "city" | "station"
	ID      string  `json:"id"`
	Name    string  `json:"name"`
	City    string  `json:"city,omitempty"`
	Country string  `json:"country"`
	Lat     float32 `json:"lat"`
	Lon     float32 `json:"lon"`
	// nombre de gares (villes) ; utile au front pour afficher « Paris (toutes les gares) »
	Stations int `json:"stations,omitempty"`

	stops  []int32
	weight float32
}

type placeEntry struct {
	norm   string
	tokens []string
	place  *Place
}

type PlaceIndex struct {
	byID    map[string]*Place
	entries []placeEntry
}

var foldMap = map[rune]string{
	'à': "a", 'á': "a", 'â': "a", 'ã': "a", 'ä': "a", 'å': "a", 'ç': "c", 'è': "e", 'é': "e", 'ê': "e",
	'ë': "e", 'ì': "i", 'í': "i", 'î': "i", 'ï': "i", 'ñ': "n", 'ò': "o", 'ó': "o", 'ô': "o", 'õ': "o",
	'ö': "o", 'ø': "o", 'ù': "u", 'ú': "u", 'û': "u", 'ü': "u", 'ý': "y", 'ÿ': "y", 'œ': "oe", 'æ': "ae",
	'ß': "ss", 'ł': "l", 'š': "s", 'ž': "z", 'č': "c", 'ř': "r", 'ě': "e", 'ő': "o", 'ű': "u",
}

// normalize : minuscules, sans accents, ponctuation -> espace ("Genève-Cornavin" -> "geneve cornavin").
func normalize(s string) string {
	var b strings.Builder
	space := true
	for _, r := range strings.ToLower(s) {
		if f, ok := foldMap[r]; ok {
			b.WriteString(f)
			space = false
		} else if r < 128 && (unicode.IsLetter(r) || unicode.IsDigit(r)) {
			b.WriteRune(r)
			space = false
		} else if unicode.IsLetter(r) {
			b.WriteRune(r)
			space = false
		} else if !space {
			b.WriteByte(' ')
			space = true
		}
	}
	return strings.TrimSpace(b.String())
}

func NewPlaceIndex(n *Network) *PlaceIndex {
	ix := &PlaceIndex{byID: map[string]*Place{}}
	add := func(p *Place) {
		ix.byID[p.ID] = p
		norm := normalize(p.Name)
		ix.entries = append(ix.entries, placeEntry{norm, strings.Fields(norm), p})
	}
	for c := range n.CityID {
		stops := n.CityStops[c]
		if len(stops) < 2 {
			continue // ville à gare unique : seule la gare est proposée
		}
		add(&Place{
			Type: "city", ID: "city:" + n.CityID[c], Name: n.CityName[c], Country: n.CityCountry[c],
			Lat: n.CityLat[c], Lon: n.CityLon[c], Stations: len(stops), stops: stops, weight: n.CityWeight[c],
		})
	}
	for s := range n.StopID {
		c := n.StopCity[s]
		add(&Place{
			Type: "station", ID: "station:" + n.StopID[s], Name: n.StopName[s], City: n.CityName[c],
			Country: n.StopCountry[s], Lat: n.StopLat[s], Lon: n.StopLon[s],
			stops: []int32{int32(s)}, weight: n.StopWeight[s],
		})
	}
	return ix
}

// Search : autocomplétion (préfixe du nom complet ou d'un mot), villes en tête à score égal.
func (ix *PlaceIndex) Search(q string, limit int) []*Place {
	nq := normalize(q)
	if nq == "" {
		return nil
	}
	qTokens := strings.Fields(nq)
	type hit struct {
		p     *Place
		score int
	}
	var hits []hit
	for _, e := range ix.entries {
		score := 0
		switch {
		case e.norm == nq:
			score = 4
		case strings.HasPrefix(e.norm, nq):
			score = 3
		default:
			// chaque mot de la requête doit préfixer un mot du nom
			ok := true
			for _, qt := range qTokens {
				found := false
				for _, t := range e.tokens {
					if strings.HasPrefix(t, qt) {
						found = true
						break
					}
				}
				if !found {
					ok = false
					break
				}
			}
			if ok {
				score = 2
			}
		}
		if score > 0 {
			if e.place.Type == "city" {
				score++
			}
			hits = append(hits, hit{e.place, score})
		}
	}
	sort.SliceStable(hits, func(a, b int) bool {
		if hits[a].score != hits[b].score {
			return hits[a].score > hits[b].score
		}
		return hits[a].p.weight > hits[b].p.weight
	})
	out := make([]*Place, 0, limit)
	for _, h := range hits {
		if len(out) == limit {
			break
		}
		out = append(out, h.p)
	}
	return out
}

// Resolve accepte un identifiant renvoyé par /stations ("city:…", "station:…") ou un nom.
func (ix *PlaceIndex) Resolve(v string) *Place {
	v = strings.TrimSpace(v)
	if v == "" {
		return nil
	}
	if p, ok := ix.byID[v]; ok {
		return p
	}
	if res := ix.Search(v, 1); len(res) > 0 {
		return res[0]
	}
	return nil
}
