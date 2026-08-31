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
