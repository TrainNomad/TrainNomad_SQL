# API de routage TrainNomad (Go). Image finale ~10 Mo : le binaire + network.bin.gz.
FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/api .

FROM scratch
COPY --from=build /out/api /api
COPY network.bin.gz /network.bin.gz
# Render (offre gratuite) : 0,1 CPU -> un seul thread d'exécution Go évite la contention.
ENV NETWORK_PATH=/network.bin.gz GOMAXPROCS=1
EXPOSE 8000
ENTRYPOINT ["/api"]
