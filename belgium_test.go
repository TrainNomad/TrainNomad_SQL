package main

import (
	"strings"
	"testing"
)

// Réseau SNCB (Belgique) : trajets intérieurs et transfrontaliers, trains SNCB typés et numérotés.
func TestBelgiumSNCB(t *testing.T) {
	e := engineForTest(t)
	hasSNCB := false
	for _, o := range e.Net.Meta.Operators {
		hasSNCB = hasSNCB || o.ID == "SNCB"
	}
	if !hasSNCB {
		t.Skip("network.bin compilé sans SNCB")
	}
	s := &Server{e: e}
	date := testDate(e)
	cases := []struct {
		from, to string
		needSNCB bool
	}{
		{"station:8814001", "station:8821006", true}, // Bruxelles-Midi -> Anvers-Central
		{"station:8892007", "station:8841004", true}, // Gand-Saint-Pierre -> Liège-Guillemins
		{"station:8863008", "station:8891702", true}, // Namur -> Ostende
		{"station:8872009", "station:8891009", true}, // Charleroi-Central -> Bruges
		{"Paris", "station:8891009", true},           // Paris -> Bruges (TGV/Eurostar + SNCB)
		{"London", "station:8841004", false},         // Londres -> Liège
	}
	for _, c := range cases {
		f, to := e.Places.Resolve(c.from), e.Places.Resolve(c.to)
		if f == nil || to == nil {
			t.Fatalf("lieu inconnu %s / %s", c.from, c.to)
		}
		day := e.Net.DayIndex(e.Net.FirstDate().AddDate(0, 0, 7))
		raw := searchAt(t, e, c.from, c.to, date, 7, 0, 5)
		if len(raw) == 0 {
			t.Errorf("%s -> %s : aucun trajet", f.Name, to.Name)
			continue
		}
		dt := e.tables.get(day)
		sncb := false
		for _, rj := range raw {
			j := s.toJourney(dt, rj)
			var desc []string
			for _, l := range j.Legs {
				if l.Type != "train" {
					continue
				}
				desc = append(desc, l.TrainType+" "+l.TrainNumber+" "+l.Departure+"→"+l.Arrival)
				if l.Operator == "SNCB" {
					sncb = true
					if l.TrainNumber == "" || !strings.HasPrefix(l.TrainType, "SNCB ") {
						t.Errorf("train SNCB mal libellé : %q %q", l.TrainType, l.TrainNumber)
					}
				}
			}
			t.Logf("%s -> %s : %s", f.Name, to.Name, strings.Join(desc, " | "))
		}
		if c.needSNCB && !sncb {
			t.Errorf("%s -> %s : aucun train SNCB", f.Name, to.Name)
		}
	}
}
