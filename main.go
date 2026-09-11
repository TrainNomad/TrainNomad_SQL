package main

import (
	"log"
	"net/http"
	"os"
	"runtime"
	"runtime/debug"
	"time"
	_ "time/tzdata" // fuseaux embarqués : l'image Docker n'a pas besoin de /usr/share/zoneinfo
)

func findNetwork() string {
	if p := os.Getenv("NETWORK_PATH"); p != "" {
		return p
	}
	for _, p := range []string{"network.bin.gz", "network.bin", "../gtfs/network.bin.gz"} {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return "network.bin.gz"
}

func main() {
	// Render (offre gratuite) : 512 Mo. Le GC devient agressif avant d'approcher la limite.
	debug.SetMemoryLimit(350 << 20)

	path := findNetwork()
	start := time.Now()
	net, err := LoadNetwork(path)
	if err != nil {
		log.Fatalf("chargement de %s : %v", path, err)
	}
	engine := NewEngine(net)
	engine.tables.get(net.DayIndex(time.Now())) // préchauffe l'horaire du jour
	loadMs := time.Since(start).Milliseconds()
	runtime.GC()

	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	log.Printf("réseau %s chargé en %d ms : %d gares, %d routes, %d trajets, horaires du %s au %s, heap %.1f Mo",
		path, loadMs, net.NumStops(), net.NumRoutes(), len(net.TripDays),
		net.FirstDate().Format("2006-01-02"), net.LastDate().Format("2006-01-02"), float64(m.HeapAlloc)/1e6)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8000"
	}
	srv := &Server{e: engine, started: time.Now(), loadMs: loadMs}
	log.Printf("écoute sur :%s", port)
	log.Fatal(http.ListenAndServe(":"+port, srv.routes()))
}
