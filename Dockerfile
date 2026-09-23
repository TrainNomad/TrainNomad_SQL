# API de routage TrainNomad (Go). Image finale ~10 Mo : le binaire + network.bin.gz.
FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/api .

FROM scratch
COPY --from=build /out/api /api
COPY network.bin.gz /network.bin.gz
# Fly.io : utilise le port 8080 (défini dans fly.toml)
ENV NETWORK_PATH=/network.bin.gz GOMAXPROCS=1 PORT=8080
EXPOSE 8080
ENTRYPOINT ["/api"]
