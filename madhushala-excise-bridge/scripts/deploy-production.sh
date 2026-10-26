#!/usr/bin/env bash
set -euo pipefail
: "${TARGET_COMMIT:?Set the tested master commit SHA}"
: "${EXPECTED_ASSET_VERSION:?Set the release asset version}"
[[ "$TARGET_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 1

REPO=/srv/projects/iremimport
SERVICE_DIR=madhushala-excise-bridge
RELEASES="$HOME/madhushala-releases"
DEPLOY_STATE="$HOME/.madhushala-deploy"
CONFIG_ENV="$DEPLOY_STATE/.env"
BACKUPS=/srv/backups/madhushala
LIVE_VOLUME=madhushala-automation-platform_madhushala_automation_data
LIVE_CONTAINER=madhushala_automation_web

echo "== Preflight =="
test -d "$REPO/.git"
docker volume inspect "$LIVE_VOLUME" >/dev/null
test -d "$BACKUPS"
mkdir -p "$RELEASES" "$DEPLOY_STATE"
exec 9>"$DEPLOY_STATE/deploy.lock"
flock -n 9 || { echo "ERROR: another deployment is running"; exit 1; }

echo "== Fetch exact GitHub commit =="
git -C "$REPO" fetch origin master
git -C "$REPO" cat-file -e "$TARGET_COMMIT^{commit}"
git -C "$REPO" merge-base --is-ancestor "$TARGET_COMMIT" origin/master

DEPLOY_SHA="$TARGET_COMMIT"
SHORT_SHA="$(printf '%s' "$DEPLOY_SHA" | cut -c1-8)"
STAMP="$(date +%Y%m%d-%H%M%S)"
RELEASE_ROOT="$RELEASES/madhushala-$STAMP-$SHORT_SHA"
APP_DIR="$RELEASE_ROOT/$SERVICE_DIR"

echo "Deploying $DEPLOY_SHA"

echo "== Back up persistent data =="
docker run --rm \
  -v "$LIVE_VOLUME:/data:ro" \
  -v "$BACKUPS:/backup" \
  alpine sh -c "tar czf /backup/madhushala-data-$STAMP-$SHORT_SHA.tar.gz -C /data ."

if docker inspect madhushala_postgres >/dev/null 2>&1; then
  echo "== Back up PostgreSQL =="
  docker exec madhushala_postgres \
    pg_dump -U madhushala_bridge -d madhushala_bridge \
    | gzip \
    | sudo -n tee "$BACKUPS/madhushala-postgres-$STAMP-$SHORT_SHA.sql.gz" >/dev/null
fi

echo "== Create immutable release =="
mkdir -p "$RELEASE_ROOT"
git -C "$REPO" archive "$DEPLOY_SHA" "$SERVICE_DIR" | tar -x -C "$RELEASE_ROOT"

echo "== Restore server-only environment =="
if [ -f "$CONFIG_ENV" ]; then
  cp "$CONFIG_ENV" "$APP_DIR/.env"
else
  CURRENT_DIR=""
  if docker inspect "$LIVE_CONTAINER" >/dev/null 2>&1; then
    CURRENT_DIR="$(docker inspect "$LIVE_CONTAINER" --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' 2>/dev/null || true)"
  fi
  if [ -n "$CURRENT_DIR" ] && [ -f "$CURRENT_DIR/.env" ]; then
    cp "$CURRENT_DIR/.env" "$APP_DIR/.env"
    cp "$CURRENT_DIR/.env" "$CONFIG_ENV"
  else
    echo "ERROR: production .env not found"
    exit 1
  fi
fi
chmod 600 "$APP_DIR/.env" "$CONFIG_ENV" || true

# Routine releases never migrate databases or rewrite production configuration.
CURRENT_DATABASE_URL="$(grep -E '^DATABASE_URL=' "$APP_DIR/.env" | tail -n 1 | cut -d= -f2- || true)"
if ! printf '%s' "$CURRENT_DATABASE_URL" | grep -Eq '^postgres(ql)?://'; then
  echo "ERROR: PostgreSQL is required; database migration is a separate operation"
  exit 1
fi
if grep -q '@postgres:5432/' <<<"$CURRENT_DATABASE_URL"; then
  docker inspect madhushala_postgres --format '{{.State.Health.Status}}' | grep -qx healthy
fi
unset CURRENT_DATABASE_URL
echo "== Validate Compose uses the live production identity =="
cd "$APP_DIR"
echo "Expected frontend asset version: $EXPECTED_ASSET_VERSION"

dc() {
  docker compose -f docker-compose.prod.yml -f docker-compose.observability.yml "$@"
}
umask 077
COMPOSE_CONFIG="$(mktemp)"
dc config >"$COMPOSE_CONFIG"
grep -q 'madhushala-automation-platform_madhushala_automation_data' "$COMPOSE_CONFIG"
grep -q 'container_name: madhushala_automation_web' "$COMPOSE_CONFIG"

rm -f "$COMPOSE_CONFIG"

echo "== Build image =="
dc build --build-arg APP_GIT_SHA="$DEPLOY_SHA" web

echo "== Start shared Redis cache =="
dc up -d redis

echo "== Wait for Redis =="
REDIS_READY=0
for attempt in $(seq 1 30); do
  # Never let docker compose exec inherit the SSH heredoc stdin; it can
  # consume the remaining deployment script and make SSH exit 0 early.
  if dc exec -T redis redis-cli ping </dev/null 2>/dev/null | grep -q PONG; then
    REDIS_READY=1
    break
  fi
  sleep 1
done
if [ "$REDIS_READY" -ne 1 ]; then
  echo "ERROR: Redis did not become ready"
  dc logs --tail=100 redis
  exit 1
fi

echo "== Start singleton browser and first API replica =="
dc up -d --force-recreate web api1

wait_service_healthy() {
  service="$1"
  for attempt in $(seq 1 40); do
    cid="$(dc ps -q "$service")"
    if [ -n "$cid" ]; then
      status="$(docker inspect "$cid" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || true)"
      if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
        echo "$service is $status"
        return 0
      fi
      if [ "$status" = "unhealthy" ] || [ "$status" = "exited" ] || [ "$status" = "dead" ]; then
        echo "ERROR: $service became $status"
        dc logs --tail=200 "$service" || true
        return 1
      fi
    fi
    sleep 2
  done
  echo "ERROR: $service did not become healthy"
  dc logs --tail=200 "$service" || true
  return 1
}

wait_service_healthy api1

echo "== Start second API replica after schema initialization =="
dc up -d --force-recreate api2
wait_service_healthy api2

echo "== Start API gateway after both replicas are healthy =="
dc up -d --force-recreate api-gateway
wait_service_healthy api-gateway

echo "== Verify host port ownership =="
WEB_CID="$(dc ps -q web)"
GATEWAY_CID="$(dc ps -q api-gateway)"
if [ -z "$WEB_CID" ] || [ -z "$GATEWAY_CID" ]; then
  echo "ERROR: web or api-gateway container is missing after recreation"
  dc ps -a
  exit 1
fi

WEB_BINDINGS="$(docker inspect "$WEB_CID" --format '{{json .HostConfig.PortBindings}}')"
GATEWAY_BINDINGS="$(docker inspect "$GATEWAY_CID" --format '{{json .HostConfig.PortBindings}}')"
echo "web bindings: $WEB_BINDINGS"
echo "api-gateway bindings: $GATEWAY_BINDINGS"

if grep -q '"8091/tcp"' <<<"$WEB_BINDINGS"; then
  echo "ERROR: legacy web container still publishes host port 8091"
  exit 1
fi
if ! grep -q '"8091/tcp"' <<<"$GATEWAY_BINDINGS"; then
  echo "ERROR: api-gateway does not own host port 8091"
  exit 1
fi

OWNER_8091="$(docker ps --format '{{.Names}} {{.Ports}}' | grep '127.0.0.1:8091->8091/tcp' || true)"
echo "host 8091 owner: $OWNER_8091"
if ! grep -q 'api-gateway' <<<"$OWNER_8091"; then
  echo "ERROR: host port 8091 is not owned by api-gateway"
  docker ps --format '{{.ID}} {{.Image}} {{.Names}} {{.Ports}}'
  exit 1
fi

echo "== Verify every API replica serves this release =="
for svc in api1 api2; do
  cid="$(dc ps -q "$svc")"
  if [ -z "$cid" ]; then
    echo "ERROR: $svc container is missing"
    exit 1
  fi
  echo "$svc container=$cid image=$(docker inspect "$cid" --format '{{.Image}}')"
  REPLICA_HTML="$(dc exec -T "$svc" curl -fsS "http://127.0.0.1:8091/document-import?replica=$svc-$SHORT_SHA" </dev/null)"
  echo "$svc scripts:"
  printf '%s' "$REPLICA_HTML" | grep -oE '<script[^>]+src="[^"]+"' || true
  if ! grep -q "mapping-row-identity.js?v=$EXPECTED_ASSET_VERSION" <<<"$REPLICA_HTML" \
    || ! grep -q 'continueExtractedDocumentToMapping' <<<"$REPLICA_HTML"; then
    echo "ERROR: $svc is serving an old/incorrect frontend"
    exit 1
  fi
done

echo "== Health check =="
HEALTHY=0
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8091/health/ready >/tmp/madhushala-health.json 2>/dev/null; then
    HEALTHY=1
    break
  fi
  sleep 3
done

if [ "$HEALTHY" -ne 1 ]; then
  echo "ERROR: health check failed"
  dc ps
  dc logs --tail=200 api-gateway api1 api2 web
  exit 1
fi

python3 -c 'import json,sys; assert json.load(open("/tmp/madhushala-health.json"))["gitSha"] == sys.argv[1]' "$DEPLOY_SHA"
cat /tmp/madhushala-health.json
echo

echo "== Verify freshly deployed frontend at the API gateway =="
LOCAL_GATEWAY_HTML="$(curl -fsS -H 'Cache-Control: no-cache, no-store' \
  "http://127.0.0.1:8091/document-import?deploy=$SHORT_SHA")"
if ! grep -q "mapping-row-identity.js?v=$EXPECTED_ASSET_VERSION" <<<"$LOCAL_GATEWAY_HTML" \
  || ! grep -q 'continueExtractedDocumentToMapping' <<<"$LOCAL_GATEWAY_HTML"; then
  echo "ERROR: 127.0.0.1:8091 is not serving deployment $DEPLOY_SHA"
  printf '%s' "$LOCAL_GATEWAY_HTML" | grep -oE '<script[^>]+src="[^"]+"' || true
  exit 1
fi

echo "== Verify local Nginx/SNI route reaches the same release =="
LOCAL_NGINX_HTML="$(curl -kfsSL \
  --resolve integrations.madhushalasoftware.com:443:127.0.0.1 \
  -H 'Cache-Control: no-cache, no-store' \
  "https://integrations.madhushalasoftware.com/excise-import/document-import?deploy=$SHORT_SHA")"
if ! grep -q "mapping-row-identity.js?v=$EXPECTED_ASSET_VERSION" <<<"$LOCAL_NGINX_HTML" \
  || ! grep -q 'continueExtractedDocumentToMapping' <<<"$LOCAL_NGINX_HTML"; then
  echo "ERROR: local Nginx route for integrations.madhushalasoftware.com is not serving the release gateway"
  echo "Gateway scripts:"
  printf '%s' "$LOCAL_GATEWAY_HTML" | grep -oE '<script[^>]+src="[^"]+"' || true
  echo "Local Nginx scripts:"
  printf '%s' "$LOCAL_NGINX_HTML" | grep -oE '<script[^>]+src="[^"]+"' || true
  exit 1
fi

echo "== Origin identity and public-interface diagnostics =="
echo "Hostname: $(hostname)"
echo "IPv4 addresses:"
ip -4 addr show || true
echo "443 listeners:"
sudo -n ss -ltnp 2>/dev/null | grep -E '(:443[[:space:]]|:443$)' || true

IMDS_TOKEN="$(curl -fsS -m 2 -X PUT \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' \
  http://169.254.169.254/latest/api/token 2>/dev/null || true)"
if [ -n "$IMDS_TOKEN" ]; then
  EC2_PUBLIC_IPV4="$(curl -fsS -m 2 \
    -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
    http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  EC2_INSTANCE_ID="$(curl -fsS -m 2 \
    -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
    http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || true)"
  echo "EC2 instance id: $EC2_INSTANCE_ID"
  echo "EC2 public IPv4: $EC2_PUBLIC_IPV4"
else
  echo "EC2 metadata unavailable."
fi

echo "Enabled integrations.madhushalasoftware.com server blocks:"
sudo -n nginx -T 2>/dev/null \
  | awk '
      /server[[:space:]]*\{/ {block=$0; capture=1; found=0; next}
      capture {block=block ORS $0}
      /server_name[[:space:]].*integrations\.madhushalasoftware\.com/ {found=1}
      capture && /^}/ {
        if (found) print block ORS "---"
        capture=0; block=""; found=0
      }
    ' || true

echo "== Start Prometheus and Grafana =="
dc up -d prometheus grafana

echo "== Verify Prometheus =="
PROM_READY=0
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:9090/-/ready >/dev/null 2>&1; then
    PROM_READY=1
    break
  fi
  sleep 2
done
if [ "$PROM_READY" -ne 1 ]; then
  echo "ERROR: Prometheus did not become ready"
  dc logs --tail=150 prometheus
  exit 1
fi

echo "== Verify Grafana =="
GRAFANA_READY=0
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:3300/api/health >/tmp/grafana-health.json 2>/dev/null; then
    GRAFANA_READY=1
    break
  fi
  sleep 2
done
if [ "$GRAFANA_READY" -ne 1 ]; then
  echo "ERROR: Grafana did not become ready"
  dc logs --tail=150 grafana
  exit 1
fi

cat /tmp/grafana-health.json
echo
dc ps

echo "== Record current release =="
ln -sfn "$RELEASE_ROOT" "$DEPLOY_STATE/current"
printf '%s\n' "$DEPLOY_SHA" > "$RELEASE_ROOT/DEPLOYED_GIT_SHA"

# Retain previous releases for explicit rollback; no automatic directory deletion.
echo "Deployment complete: $DEPLOY_SHA"
