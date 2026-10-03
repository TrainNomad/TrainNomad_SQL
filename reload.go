package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"runtime"
	"runtime/debug"
	"sync"
	"sync/atomic"
	"time"
)

// Rechargement à chaud du réseau : l'API télécharge network.bin.gz depuis une Release GitHub
// (NETWORK_URL), le compile en mémoire et remplace le moteur sans redémarrer ni redéployer.
// Les requêtes en cours terminent sur l'ancien moteur (chaque requête prend un instantané).

const maxNetworkBytes = 64 << 20 // garde-fou contre un téléchargement aberrant

// netState : un réseau chargé et son moteur, immuable une fois publié.
type netState struct {
	e        *Engine
	loadMs   int64
	source   string // "file:<chemin>" ou "url"
	sha256   string
	loadedAt time.Time
}

type App struct {
	cur     atomic.Pointer[netState]
	started time.Time

	url    string        // vide = pas de rechargement (local)
	every  time.Duration // vérification périodique (0 = aucune : seulement au démarrage et sur POST /reload)
	token  string        // RELOAD_TOKEN : protège POST /reload (vide = route désactivée)
	client *http.Client

	reloadMu sync.Mutex // un seul rechargement à la fois
	etag     string     // dernier ETag reçu (requête conditionnelle)

	statusMu  sync.Mutex
	lastCheck time.Time
	lastError string
}

// buildState compile un réseau et préchauffe l'horaire du jour avant publication.
func buildState(data []byte, source string) (*netState, error) {
	start := time.Now()
	net, err := LoadNetworkBytes(data)
	if err != nil {
		return nil, err
	}
	if net.NumStops() == 0 || len(net.TripDays) == 0 {
		return nil, errors.New("réseau vide")
	}
	e := NewEngine(net)
	e.tables.get(net.DayIndex(time.Now()))
	sum := sha256.Sum256(data)
	return &netState{
		e:        e,
		loadMs:   time.Since(start).Milliseconds(),
		source:   source,
		sha256:   hex.EncodeToString(sum[:]),
		loadedAt: time.Now(),
	}, nil
}

func (a *App) publish(st *netState) {
	a.cur.Store(st)
	runtime.GC()
	debug.FreeOSMemory() // rend la mémoire de l'ancien réseau (512 Mo sur Render)
	n := st.e.Net
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	log.Printf("réseau Europe (%s) chargé en %d ms : %d gares, %d routes, %d trajets, horaires du %s au %s, compilé le %s, heap %.1f Mo",
		st.source, st.loadMs, n.NumStops(), n.NumRoutes(), len(n.TripDays),
		n.FirstDate().Format("2006-01-02"), n.LastDate().Format("2006-01-02"), n.Meta.BuiltAt, float64(m.HeapAlloc)/1e6)
}

// refresh télécharge le réseau distant et le publie s'il a changé. Renvoie true si le moteur a été remplacé.
func (a *App) refresh(ctx context.Context) (bool, error) {
	a.reloadMu.Lock()
	defer a.reloadMu.Unlock()

	changed, err := a.fetch(ctx)
	a.statusMu.Lock()
	a.lastCheck = time.Now()
	if err != nil {
		a.lastError = err.Error()
	} else {
		a.lastError = ""
	}
	a.statusMu.Unlock()
	return changed, err
}

func (a *App) fetch(ctx context.Context) (bool, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, a.url, nil)
	if err != nil {
		return false, err
	}
	if a.etag != "" {
		req.Header.Set("If-None-Match", a.etag)
	}
	resp, err := a.client.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotModified {
		return false, nil
	}
	if resp.StatusCode != http.StatusOK {
		return false, fmt.Errorf("téléchargement de %s : HTTP %d", a.url, resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxNetworkBytes+1))
	if err != nil {
		return false, err
	}
	if len(data) > maxNetworkBytes {
		return false, errors.New("réseau distant trop volumineux")
	}

	sum := sha256.Sum256(data)
	if cur := a.cur.Load(); cur != nil && cur.sha256 == hex.EncodeToString(sum[:]) {
		a.etag = resp.Header.Get("ETag")
		return false, nil
	}
	st, err := buildState(data, "url")
	if err != nil {
		return false, fmt.Errorf("réseau distant invalide : %w", err) // on garde le réseau actuel
	}
	if cur := a.cur.Load(); cur != nil && st.e.Net.Meta.BuiltAt < cur.e.Net.Meta.BuiltAt {
		return false, fmt.Errorf("réseau distant plus ancien (%s) que le réseau chargé (%s)", st.e.Net.Meta.BuiltAt, cur.e.Net.Meta.BuiltAt)
	}
	a.etag = resp.Header.Get("ETag")
	a.publish(st)
	return true, nil
}

// watch vérifie périodiquement la Release (facultatif, NETWORK_REFRESH). Une instance Render gratuite
// endormie retélécharge de toute façon le réseau à son réveil.
func (a *App) watch() {
	for {
		time.Sleep(a.every)
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		if _, err := a.refresh(ctx); err != nil {
			log.Printf("rechargement du réseau : %v", err)
		}
		cancel()
	}
}

// handleReload : POST /reload (Authorization: Bearer <RELOAD_TOKEN>), appelé par la GitHub Action
// juste après la publication pour ne pas attendre la prochaine vérification.
func (a *App) handleReload(w http.ResponseWriter, r *http.Request) {
	if a.token == "" || a.url == "" {
		writeJSON(w, r, http.StatusNotFound, apiError{Error: "not_found"})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, r, http.StatusMethodNotAllowed, apiError{Error: "method_not_allowed"})
		return
	}
	if r.Header.Get("Authorization") != "Bearer "+a.token {
		writeJSON(w, r, http.StatusUnauthorized, apiError{Error: "unauthorized"})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Minute)
	defer cancel()
	changed, err := a.refresh(ctx)
	if err != nil {
		writeJSON(w, r, http.StatusBadGateway, apiError{Error: err.Error()})
		return
	}
	writeJSON(w, r, http.StatusOK, map[string]any{"reloaded": changed, "built_at": a.cur.Load().e.Net.Meta.BuiltAt})
}

func (a *App) status() map[string]any {
	st := a.cur.Load()
	a.statusMu.Lock()
	defer a.statusMu.Unlock()
	out := map[string]any{
		"source":    st.source,
		"sha256":    st.sha256,
		"loaded_at": fmtTime(st.loadedAt),
	}
	if a.url != "" {
		out["url"] = a.url
		out["reload_endpoint"] = a.token != ""
		if a.every > 0 {
			out["refresh_every"] = a.every.String()
		}
		if !a.lastCheck.IsZero() {
			out["last_check"] = fmtTime(a.lastCheck)
		}
		if a.lastError != "" {
			out["last_error"] = a.lastError
		}
	}
	return out
}
