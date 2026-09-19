# Madhushala observability

This stack scrapes the bridge's existing `/metrics` endpoint with Prometheus and provisions a Grafana dashboard automatically.

## Start

From `madhushala-excise-bridge`:

```bash
export GRAFANA_ADMIN_USER=admin
export GRAFANA_ADMIN_PASSWORD='replace-with-a-strong-password'

docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.observability.yml \
  up -d
```

Grafana binds to `127.0.0.1:3000` and Prometheus to `127.0.0.1:9090` so neither is publicly exposed by default. Access them through SSH port forwarding or an authenticated reverse proxy.

## Dashboard

The provisioned **Madhushala Excise Bridge - Production** dashboard includes:

- Bridge HTTP p50 / p95 / p99 latency
- Madhushala upstream p50 / p95 / p99 latency
- Cache hit ratio
- HTTP error rate (4xx + 5xx)
- Purchase-save p50 / p95 / p99 latency
- Request rate and upstream error rate for operational context

Prometheus retention defaults to 15 days.
