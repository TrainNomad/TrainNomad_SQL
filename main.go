package main

import (
	"context"
	"log"
	"net/http"
	"os"
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

	app := &App{
		started: time.Now(),
		url:     os.Getenv("NETWORK_URL"), // Release GitHub du dépôt TrainNomad_GTFS ; vide = fichier local uniquement
		token:   os.Getenv("RELOAD_TOKEN"),
		client:  &http.Client{Timeout: 2 * time.Minute},
	}
	// Vérification périodique facultative : par défaut, l'API est prévenue par POST /reload (GitHub Action).
	if v := os.Getenv("NETWORK_REFRESH"); v != "" {
		if d, err := time.ParseDuration(v); err == nil && d >= time.Minute {
			app.every = d
		} else {
			log.Printf("NETWORK_REFRESH=%q ignoré (durée Go ≥ 1m attendue, ex. 30m, 2h)", v)
		}
	}

	// 1. Réseau distant (le plus récent) ; 2. à défaut, celui embarqué dans l'image.
	if app.url != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		if _, err := app.refresh(ctx); err != nil {
			log.Printf("réseau distant indisponible au démarrage (%v), repli sur le fichier local", err)
		}
		cancel()
	}
	if app.cur.Load() == nil {
		path := findNetwork()
		data, err := os.ReadFile(path)
		if err != nil {
			log.Fatalf("chargement de %s : %v", path, err)
		}
		st, err := buildState(data, "file:"+path)
		if err != nil {
			log.Fatalf("chargement de %s : %v", path, err)
		}
		app.publish(st)
	}
	if app.url != "" && app.every > 0 {
		go app.watch()
		log.Printf("vérification de %s toutes les %s", app.url, app.every)
	}
	if app.url != "" && app.token == "" {
		log.Printf("RELOAD_TOKEN absent : POST /reload désactivé, le réseau ne sera rechargé qu'au redémarrage")
	}

	port := os.Getenv("PORT")
	if port == "" {
		port = "8000" // Europe : 8000, guides : 8001, TGVmax : 8002 (tous lançables en même temps en local)
	}
	log.Printf("écoute sur :%s", port)
	log.Fatal(http.ListenAndServe(":"+port, app.routes()))
}
