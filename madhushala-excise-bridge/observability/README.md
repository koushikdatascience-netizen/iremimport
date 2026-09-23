# Madhushala observability

The production observability stack contains:

- **Prometheus** for metrics and latency/error dashboards.
- **Loki** for searchable structured application and upstream API logs.
- **Grafana Alloy** for collecting Docker logs from the bridge containers and forwarding them to Loki.
- **Grafana** as the UI for both Prometheus metrics and Loki logs.

The application writes JSON logs to stdout. Alloy collects logs only from the `web`, `api1`, and `api2` services in the `madhushala-automation-platform` Compose project.

## Start / update the stack

From `madhushala-excise-bridge`:

```bash
export GRAFANA_ADMIN_USER=admin
export GRAFANA_ADMIN_PASSWORD='replace-with-a-strong-password'

docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.observability.yml \
  up -d
```

Verify:

```bash
docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.observability.yml \
  ps
```

Expected observability services include `prometheus`, `grafana`, `loki`, and `alloy`.

Grafana binds to `127.0.0.1:3300`, Prometheus to `127.0.0.1:9090`, and Loki to `127.0.0.1:3100`. They are not publicly exposed by default. Use SSH port forwarding or an authenticated reverse proxy.

## Metrics dashboard

The provisioned **Madhushala Excise Bridge - Production** dashboard includes:

- Bridge HTTP p50 / p95 / p99 latency
- Madhushala upstream p50 / p95 / p99 latency
- Cache hit ratio
- HTTP error rate
- Purchase-save p50 / p95 / p99 latency
- Request rate
- Upstream error rate

Prometheus retention defaults to 15 days.

## Loki debugging

Grafana automatically provisions the `Loki` datasource.

Open:

`Grafana -> Explore -> Loki`

All bridge logs:

```logql
{service="madhushala-excise-bridge"}
```

Only structured Madhushala API request/response flow:

```logql
{service="madhushala-excise-bridge"} | json | event=~"madhushala_flow_request|madhushala_flow_response|madhushala_flow_error"
```

Find a specific correlation/job flow:

```logql
{service="madhushala-excise-bridge"} |= "purchase-preview-a334329290324704812559a70407d0ea"
```

Find an item code:

```logql
{service="madhushala-excise-bridge"} |= "ABS50"
```

Inspect only Calculate:

```logql
{service="madhushala-excise-bridge"} | json | upstreamPath="/api/purchase/calculate"
```

Inspect Item Master detail calls:

```logql
{service="madhushala-excise-bridge"} | json | upstreamPath=~"/api/items/.*"
```

A Calculate request log contains fields such as:

```json
{
  "event": "madhushala_flow_request",
  "correlationId": "purchase-preview-a334329290324704812559a70407d0ea",
  "httpMethod": "POST",
  "upstreamPath": "/api/purchase/calculate",
  "requestPayload": {
    "shopCode": "hedu_test",
    "companyCode": "2",
    "items": [
      {
        "itemCode": "ABS50",
        "box": 0,
        "loose": 1,
        "boxRate": 3415.2,
        "looseRate": 0
      }
    ]
  }
}
```

The matching response uses the same `correlationId` and contains `responsePayload`, making request-vs-response comparison possible without the browser Network tab.

## Security and log size

Observability logging automatically redacts credential-like fields including Authorization, tokens, passwords, API keys, secrets, and cookies. Business values such as item codes, quantities, rates, amounts and taxes remain visible.

Large arrays are bounded before logging to protect Loki from oversized individual log lines. The log adds a `_truncated` marker and original item count when this occurs.

Loki retention is configured for 7 days. Prometheus retention remains 15 days.

## n8n

n8n is not used as the log store. If desired later, n8n can consume Grafana alert webhooks and reproduce a debugging/notification workflow—for example, send a WhatsApp/Slack alert containing the job ID, item code and Grafana search link when Calculate returns an error. Loki remains the searchable source of truth.
