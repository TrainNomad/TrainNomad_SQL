package main

import (
	"strings"
	"testing"
)

// Réseau grandes lignes allemand (gtfs.de) : trajets intérieurs et internationaux.
func TestGermanyDB(t *testing.T) {
	e := engineForTest(t)
	hasDB := false
	for _, o := range e.Net.Meta.Operators {
		hasDB = hasDB || o.ID == "DB"
	}
	if !hasDB {
		t.Skip("network.bin compilé sans l'Allemagne")
	}
	s := &Server{e: e}
	date := testDate(e)
	day := e.Net.DayIndex(e.Net.FirstDate().AddDate(0, 0, 7))
	cases := []struct {
		from, to string
		needDB   bool
	}{
		{"station:8065969", "station:8020347", true}, // Berlin Hbf -> München Hbf
		{"station:8015458", "station:8001071", true}, // Köln Hbf -> Hamburg Hbf
		{"station:8400058", "station:8065969", true}, // Amsterdam Centraal -> Berlin Hbf
		{"station:8011068", "Wien", false},           // Frankfurt (Main) Hbf -> Vienne
		{"Paris", "station:8065969", false},          // Paris -> Berlin
		{"Bruxelles", "station:8020347", false},      // Bruxelles -> Munich
		{"Hannover", "Peine", false},                 // ligne régionale (réseau compilé avec DB_REGIO)
	}
	for _, c := range cases {
		f, to := e.Places.Resolve(c.from), e.Places.Resolve(c.to)
		if f == nil || to == nil {
			t.Fatalf("lieu inconnu %s / %s", c.from, c.to)
		}
		raw := searchAt(t, e, c.from, c.to, date, 7, 0, 3)
		if len(raw) == 0 {
			t.Errorf("%s -> %s : aucun trajet", f.Name, to.Name)
			continue
		}
		dt := e.tables.get(day)
		db := false
		for _, rj := range raw[:min(3, len(raw))] {
			j := s.toJourney(dt, rj)
			var desc []string
			for _, l := range j.Legs {
				if l.Type != "train" {
					continue
				}
				desc = append(desc, strings.TrimSpace(l.TrainType+" "+l.TrainNumber)+" "+l.From.Name+" "+l.Departure[11:16]+" → "+l.To.Name+" "+l.Arrival[11:16])
				db = db || l.Operator == "DB"
			}
			t.Logf("%s -> %s : %s", f.Name, to.Name, strings.Join(desc, " | "))
		}
		if c.needDB && !db {
			t.Errorf("%s -> %s : aucun train allemand", f.Name, to.Name)
		}
	}
}
