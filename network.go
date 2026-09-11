package main

import (
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"strings"
	"time"
)

// Bits de route.flags (même convention que build_network.py).
const (
	noPickup  = 1
	noDropoff = 2
)

type Operator struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

type Meta struct {
	Version   int            `json:"version"`
	BuiltAt   string         `json:"built_at"`
	BaseDate  string         `json:"base_date"`
	NDays     int            `json:"ndays"`
	Words     int            `json:"words"`
	Timezones []string       `json:"timezones"`
	Types     []string       `json:"types"`
	Operators []Operator     `json:"operators"`
	Stats     map[string]int `json:"stats"`
}

// Network est le réseau compilé par build_network.py, entièrement en RAM (quelques Mo).
// Les horaires sont en minutes locales du fuseau de l'agence, relatives au jour de service ;
// dayBase les convertit en minutes UTC depuis BaseDate.
type Network struct {
	Meta      Meta
	BaseDate  time.Time
	Locations []*time.Location

	StopID, StopName, StopCountry []string
	StopLat, StopLon              []float32
	StopTZ                        []uint8
	StopCity                      []int32
	StopChange                    []uint16
	StopWeight                    []float32

	CityID, CityName, CityCountry []string
	CityLat, CityLon              []float32
	CityStops                     [][]int32
	CityWeight                    []float32

	FpOff []uint32
	FpTo  []int32
	FpMin []uint16

	RouteStopOff []uint32
	RouteStops   []int32
	RouteFlags   []uint8
	RouteTripOff []uint32
	RouteTimeOff []uint32
	RouteTZ      []uint8
	RouteCheckin []uint16

	TimeArr, TimeDep []uint16

	TripDays   []uint32
	TripType   []uint16
	TripOp     []uint8
	TripNumber []string
	DayBits    []uint64

	// Dérivés au chargement
	StopRouteOff []uint32 // CSR gare -> (route, position dans la route)
	StopRoutes   []int32
	StopRoutePos []uint16
	dayBase      [][]int32 // [fuseau][jour] minutes UTC (depuis BaseDate) de "midi - 12 h" local

	// Graphe inverse des temps de parcours minimaux (gare -> gares précédentes), pour les bornes
	// inférieures "temps restant jusqu'à la destination" utilisées comme élagage.
	lbOff  []uint32
	lbFrom []int32
	lbMin  []int32
}

func (n *Network) NumStops() int  { return len(n.StopID) }
func (n *Network) NumRoutes() int { return len(n.RouteTZ) }

func (n *Network) routeStops(r int32) []int32 {
	return n.RouteStops[n.RouteStopOff[r]:n.RouteStopOff[r+1]]
}

func (n *Network) routeFlags(r int32) []uint8 {
	return n.RouteFlags[n.RouteStopOff[r]:n.RouteStopOff[r+1]]
}

func (n *Network) tripActive(trip uint32, day int) bool {
	if day < 0 || day >= n.Meta.NDays {
		return false
	}
	w := n.DayBits[int(n.TripDays[trip])*n.Meta.Words+day/64]
	return w&(1<<(uint(day)%64)) != 0
}

// TimeAt convertit des minutes UTC (depuis BaseDate) en heure locale d'une gare.
func (n *Network) TimeAt(minutes int32, stop int32) time.Time {
	return n.BaseDate.Add(time.Duration(minutes) * time.Minute).In(n.Locations[n.StopTZ[stop]])
}

// ---------------------------------------------------------------------------
// Chargement
// ---------------------------------------------------------------------------

type section struct {
	dtype byte
	count uint64
	data  []byte
}

func LoadNetwork(path string) (*Network, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var r io.Reader = f
	if strings.HasSuffix(path, ".gz") {
		gz, err := gzip.NewReader(f)
		if err != nil {
			return nil, err
		}
		defer gz.Close()
		r = gz
	}
	raw, err := io.ReadAll(r)
	if err != nil {
		return nil, err
	}
	return parseNetwork(raw)
}

func parseNetwork(raw []byte) (*Network, error) {
	if len(raw) < 16 || !bytes.Equal(raw[:8], []byte("TNNET001")) {
		return nil, fmt.Errorf("format network.bin inconnu")
	}
	le := binary.LittleEndian
	nsec := int(le.Uint32(raw[8:12]))
	pos := 16
	secs := make(map[string]section, nsec)
	for i := 0; i < nsec; i++ {
		if pos+48 > len(raw) {
			return nil, fmt.Errorf("network.bin tronqué")
		}
		name := string(bytes.TrimRight(raw[pos:pos+24], "\x00"))
		dtype := raw[pos+24]
		count := le.Uint64(raw[pos+32 : pos+40])
		size := int(le.Uint64(raw[pos+40 : pos+48]))
		pos += 48
		if pos+size > len(raw) {
			return nil, fmt.Errorf("section %s tronquée", name)
		}
		secs[name] = section{dtype, count, raw[pos : pos+size]}
		pos += size + (8-size%8)%8
	}

	var perr error
	get := func(name string, dtype byte) []byte {
		s, ok := secs[name]
		if !ok {
			if perr == nil {
				perr = fmt.Errorf("section manquante : %s", name)
			}
			return nil
		}
		if s.dtype != dtype && perr == nil {
			perr = fmt.Errorf("section %s : type %d attendu, %d trouvé", name, dtype, s.dtype)
		}
		return s.data
	}
	u8 := func(name string) []uint8 { return append([]uint8(nil), get(name, 1)...) }
	u16 := func(name string) []uint16 {
		b := get(name, 2)
		out := make([]uint16, len(b)/2)
		for i := range out {
			out[i] = le.Uint16(b[2*i:])
		}
		return out
	}
	u32 := func(name string) []uint32 {
		b := get(name, 3)
		out := make([]uint32, len(b)/4)
		for i := range out {
			out[i] = le.Uint32(b[4*i:])
		}
		return out
	}
	i32 := func(name string) []int32 {
		b := get(name, 4)
		out := make([]int32, len(b)/4)
		for i := range out {
			out[i] = int32(le.Uint32(b[4*i:]))
		}
		return out
	}
	u64 := func(name string) []uint64 {
		b := get(name, 5)
		out := make([]uint64, len(b)/8)
		for i := range out {
			out[i] = le.Uint64(b[8*i:])
		}
		return out
	}
	f32 := func(name string) []float32 {
		b := get(name, 6)
		out := make([]float32, len(b)/4)
		for i := range out {
			out[i] = math.Float32frombits(le.Uint32(b[4*i:]))
		}
		return out
	}
	strs := func(name string) []string {
		off := u32(name + ".off")
		dat := get(name+".dat", 1)
		if len(off) == 0 {
			return nil
		}
		out := make([]string, len(off)-1)
		for i := range out {
			out[i] = string(dat[off[i]:off[i+1]])
		}
		return out
	}

	n := &Network{}
	if err := json.Unmarshal(get("meta", 1), &n.Meta); err != nil {
		return nil, fmt.Errorf("meta : %w", err)
	}
	n.StopID, n.StopName, n.StopCountry = strs("stop.id"), strs("stop.name"), strs("stop.country")
	n.StopLat, n.StopLon = f32("stop.lat"), f32("stop.lon")
	n.StopTZ, n.StopCity = u8("stop.tz"), i32("stop.city")
	n.StopChange, n.StopWeight = u16("stop.change"), f32("stop.weight")
	n.CityID, n.CityName, n.CityCountry = strs("city.id"), strs("city.name"), strs("city.country")
	n.CityLat, n.CityLon = f32("city.lat"), f32("city.lon")
	n.FpOff, n.FpTo, n.FpMin = u32("fp.off"), i32("fp.to"), u16("fp.min")
	n.RouteStopOff, n.RouteStops, n.RouteFlags = u32("route.stopoff"), i32("route.stops"), u8("route.flags")
	n.RouteTripOff, n.RouteTimeOff = u32("route.tripoff"), u32("route.timeoff")
	n.RouteTZ, n.RouteCheckin = u8("route.tz"), u16("route.checkin")
	n.TimeArr, n.TimeDep = u16("time.arr"), u16("time.dep")
	n.TripDays, n.TripType, n.TripOp = u32("trip.days"), u16("trip.type"), u8("trip.op")
	n.TripNumber = strs("trip.number")
	n.DayBits = u64("days.bits")
	if perr != nil {
		return nil, perr
	}
	if n.Meta.Version != 1 {
		return nil, fmt.Errorf("version de network.bin non supportée : %d", n.Meta.Version)
	}
	if err := n.derive(); err != nil {
		return nil, err
	}
	return n, nil
}

func (n *Network) derive() error {
	base, err := time.Parse("2006-01-02", n.Meta.BaseDate)
	if err != nil {
		return fmt.Errorf("base_date : %w", err)
	}
	n.BaseDate = base.UTC()

	n.Locations = make([]*time.Location, len(n.Meta.Timezones))
	n.dayBase = make([][]int32, len(n.Meta.Timezones))
	for i, name := range n.Meta.Timezones {
		loc, err := time.LoadLocation(name)
		if err != nil {
			return fmt.Errorf("fuseau %q : %w", name, err)
		}
		n.Locations[i] = loc
		// GTFS : les heures sont relatives à "midi moins 12 h" du jour de service (gère les changements d'heure).
		n.dayBase[i] = make([]int32, n.Meta.NDays)
		for d := 0; d < n.Meta.NDays; d++ {
			day := n.BaseDate.AddDate(0, 0, d)
			noon := time.Date(day.Year(), day.Month(), day.Day(), 12, 0, 0, 0, loc)
			n.dayBase[i][d] = int32(noon.Sub(n.BaseDate)/time.Minute) - 720
		}
	}

	ns := n.NumStops()
	counts := make([]uint32, ns+1)
	for _, s := range n.RouteStops {
		counts[s+1]++
	}
	for i := 1; i <= ns; i++ {
		counts[i] += counts[i-1]
	}
	n.StopRouteOff = append([]uint32(nil), counts...)
	n.StopRoutes = make([]int32, len(n.RouteStops))
	n.StopRoutePos = make([]uint16, len(n.RouteStops))
	fill := append([]uint32(nil), counts[:ns]...)
	for r := int32(0); r < int32(n.NumRoutes()); r++ {
		for pos, s := range n.routeStops(r) {
			k := fill[s]
			n.StopRoutes[k] = r
			n.StopRoutePos[k] = uint16(pos)
			fill[s]++
		}
	}

	n.buildLowerBoundGraph()

	n.CityStops = make([][]int32, len(n.CityID))
	n.CityWeight = make([]float32, len(n.CityID))
	for s := int32(0); s < int32(ns); s++ {
		c := n.StopCity[s]
		n.CityStops[c] = append(n.CityStops[c], s)
		n.CityWeight[c] += n.StopWeight[s]
	}
	return nil
}

// FirstDate / LastDate : plage de dates interrogeables (le moteur regarde la veille et 2 jours après).
func (n *Network) FirstDate() time.Time { return n.BaseDate.AddDate(0, 0, 1) }
func (n *Network) LastDate() time.Time  { return n.BaseDate.AddDate(0, 0, n.Meta.NDays-3) }

// DayIndex renvoie l'index de jour d'une date calendaire (YYYY-MM-DD).
func (n *Network) DayIndex(d time.Time) int {
	y, m, dd := d.Date()
	return int(time.Date(y, m, dd, 0, 0, 0, 0, time.UTC).Sub(n.BaseDate).Hours() / 24)
}
