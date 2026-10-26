# Current production deployment

Production deploys **master**, not the repository's default `main` branch or a feature branch.
The active public URL is https://integrations.madhushalasoftware.com/excise-import/.
`excise.connect.snapkey.in` is a legacy URL and currently has no DNS record.

The `Deploy Excise Bridge` workflow runs the complete test suite on pull requests
into master. A push to master, or a manual run with ref `master`, runs tests and
then invokes `scripts/deploy-production.sh` over the configured VPS SSH connection.
Feature-branch regression checks alone do not deploy anything.

From a local clone or a connected ChatGPT terminal with GitHub CLI authentication:

```bash
gh workflow run deploy-excise-bridge.yml --ref master --repo koushikdatascience-netizen/iremimport
gh run list --workflow deploy-excise-bridge.yml --repo koushikdatascience-netizen/iremimport
```

Use the existing SERVER_HOST, SERVER_USER, SERVER_PORT and SERVER_SSH_KEY_B64
repository secrets. Do not copy server `.env` files or private keys into Git.
The same script can be run over the established SSH connection with
`TARGET_COMMIT=<tested full master SHA>` and
`EXPECTED_ASSET_VERSION=<STATIC_ASSET_VERSION from that commit>`.
The script refuses commits outside master history.

The immutable release lives under `~/madhushala-releases/` and
`~/.madhushala-deploy/current/DEPLOYED_GIT_SHA` records the active SHA.
`/health/ready` reports `gitSha`, which must match the release and every API replica.
The image also carries the OCI revision label. Verify public HTML's asset version.

Routine releases back up data, preserve the server environment byte-for-byte,
and reuse the existing Compose project and persistent volumes. They do not run
SQLite migrations, reset PostgreSQL, change database credentials, or delete volumes.
Previous release directories are retained. A database cutover is a separate operation.
If any health, SHA or public frontend verification fails, stop and inspect the logs.
Do not trigger Purchase Save in smoke tests.

The existing Compose identity is `madhushala-automation-platform`; API traffic goes
through api-gateway to api1/api2, with a singleton web/noVNC container. The data
volume is `madhushala-automation-platform_madhushala_automation_data` and PostgreSQL
uses `madhushala-automation-platform_postgres_data`.

## Historical hosting notes

The notes below describe initial setup. Use the deployment workflow above for upgrades.

# Production Hosting Notes

This project is packaged as an independent Docker Compose microservice. It runs the bridge API, the operator UI, and a noVNC browser view for server-side Chromium.

## Server Layout

Recommended target path:

```text
/srv/projects/madhushala-excise-bridge
```

The service stores runtime state in the Docker volume `madhushala-excise-bridge_excise_bridge_data`.

Recommended CRM integration:

```text
SnapKey CRM button -> https://excise.connect.snapkey.in/
Bridge UI button   -> /excise-browser/vnc.html?autoconnect=true&resize=remote
```

Using a dedicated subdomain is preferred because the app serves root-relative static files and API calls.

## First Deploy

```bash
cd /srv/projects
git clone https://github.com/koushikdatascience-netizen/iremimport.git iremimport
cd iremimport
git checkout master
cd madhushala-excise-bridge
cp .env.example .env
nano .env
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
curl -fsS http://127.0.0.1:8091/health
curl -fsS http://127.0.0.1:6080/vnc.html >/dev/null
```

## Nginx

Copy `deploy/nginx/madhushala-excise-bridge.conf` to `/etc/nginx/sites-available/`, update `server_name`, enable it, then reload Nginx.

```bash
sudo cp deploy/nginx/madhushala-excise-bridge.conf /etc/nginx/sites-available/madhushala-excise-bridge
sudo ln -s /etc/nginx/sites-available/madhushala-excise-bridge /etc/nginx/sites-enabled/madhushala-excise-bridge
sudo nginx -t
sudo systemctl reload nginx
```

## CRM Button

Add a CRM button that opens the bridge in a new tab:

```text
https://excise.connect.snapkey.in/
```

The bridge page has a separate **Open Browser View** button for the live Excise browser tab.

## Browser Display

The production container starts:

- `Xvfb` virtual display
- `fluxbox` window manager
- `x11vnc` bound inside the container
- `websockify` + noVNC on `127.0.0.1:6080`
- FastAPI on `127.0.0.1:8091`

Keep `HEADLESS=false` for live operator use. The user enters CAPTCHA through the noVNC browser view.

## Access Guard

Do not expose this app directly to the internet without a guard. Use one of these before real client use:

- CRM session proxy/SSO gate
- Nginx basic auth
- VPN/IP allow-list

The noVNC endpoint has no password because access should be controlled at Nginx/CRM level.

## Upgrade

```bash
cd /srv/projects/madhushala-excise-bridge
cd /srv/projects/iremimport
git pull
cd madhushala-excise-bridge
docker compose -f docker-compose.prod.yml up -d --build
docker image prune -f
```

## Backup

```bash
docker run --rm \
  -v madhushala-excise-bridge_excise_bridge_data:/data:ro \
  -v "$PWD/backups:/backup" \
  alpine tar czf /backup/excise-bridge-data-$(date +%Y%m%d-%H%M%S).tar.gz /data
```


## Horizontal API topology

Production now keeps the interactive browser/noVNC process as a singleton and
runs HTTP traffic through two FastAPI replicas:

```text
host Nginx -> 127.0.0.1:8091 -> api-gateway -> api1:8091
                                             -> api2:8091

host Nginx /excise-browser/* -> 127.0.0.1:6080 -> web (Playwright/noVNC singleton)
```

Redis is shared by both API replicas. PostgreSQL is required for shared durable
state. Routine deployment requires the existing PostgreSQL backend and performs
no database migration.
