package main

import (
	"os"
	"testing"
	"time"
)

var testEngine *Engine

func engineForTest(tb testing.TB) *Engine {
	if testEngine != nil {
		return testEngine
	}
	path := findNetwork()
	if _, err := os.Stat(path); err != nil {
		tb.Skip("network.bin absent")
	}
	n, err := LoadNetwork(path)
	if err != nil {
		tb.Fatal(err)
	}
	testEngine = NewEngine(n)
	return testEngine
}

func searchAt(tb testing.TB, e *Engine, from, to string, date string, hh, mm, limit int) []rawJourney {
	f, t := e.Places.Resolve(from), e.Places.Resolve(to)
	if f == nil || t == nil {
		tb.Fatalf("lieu inconnu %s / %s", from, to)
	}
	loc := e.Net.Locations[e.Net.StopTZ[f.stops[0]]]
	d, _ := time.ParseInLocation("2006-01-02", date, loc)
	start := time.Date(d.Year(), d.Month(), d.Day(), hh, mm, 0, 0, loc)
	return e.Search(f, t, e.Net.DayIndex(d), int32(start.Sub(e.Net.BaseDate)/time.Minute), limit, 6)
}

func testDate(e *Engine) string { return e.Net.FirstDate().AddDate(0, 0, 7).Format("2006-01-02") }

// Chaque trajet renvoyé doit être cohérent : horaires croissants, correspondances respectées.
func TestSearchConsistency(t *testing.T) {
	e := engineForTest(t)
	date := testDate(e)
	pairs := [][2]string{
		{"London", "Paris"}, {"London", "Barcelona"}, {"Paris", "Madrid"}, {"Lille", "Marseille"},
		{"Amsterdam", "Sevilla"}, {"Strasbourg", "Bordeaux"}, {"Brest", "Nice"},
	}
	for _, p := range pairs {
		js := searchAt(t, e, p[0], p[1], date, 6, 0, 10)
		if len(js) == 0 {
			t.Errorf("%s -> %s : aucun trajet", p[0], p[1])
		}
		for _, j := range js {
			if j.arr <= j.dep || j.trains < 1 || j.trains > 7 {
				t.Errorf("%s -> %s : trajet incohérent %+v", p[0], p[1], j)
			}
		}
	}
}

func BenchmarkSearch(b *testing.B) {
	e := engineForTest(b)
	date := testDate(e)
	searchAt(b, e, "Paris", "Madrid", date, 6, 0, 10) // construit l'horaire du jour
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		searchAt(b, e, "Paris", "Madrid", date, 6, 0, 10)
		searchAt(b, e, "London", "Barcelona", date, 6, 0, 10)
	}
}

func BenchmarkExplore(b *testing.B) {
	e := engineForTest(b)
	f := e.Places.Resolve("Paris")
	day := e.Net.DayIndex(e.Net.FirstDate().AddDate(0, 0, 7))
	dt := e.tables.get(day)
	t0 := dt.Dep[0] // valeur arbitraire dans la fenêtre
	t0 = int32(day)*1440 - 120
	for i := 0; i < b.N; i++ {
		e.exploreCache = map[string][]Destination{}
		e.exploreOrder = nil
		e.Explore(f, day, t0, t0+24*60, 2)
	}
}

func BenchmarkDayTable(b *testing.B) {
	e := engineForTest(b)
	for i := 0; i < b.N; i++ {
		buildDayTable(e.Net, 10)
	}
}
