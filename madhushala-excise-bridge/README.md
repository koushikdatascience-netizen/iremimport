# Madhushala Excise Bridge

FastAPI backend plus Chrome extension workflow for WB Excise Prepare Indent capture and Madhushala item mapping.

## Implemented

- Local UI at `http://127.0.0.1:8091`
- Chrome extension auto-capture flow for scalable client-side Excise portal use
- Visible Playwright Chromium browser with persistent profile
- WB Excise login autofill, with manual CAPTCHA and manual login
- Manual Prepare Indent flow observation
- Warehouse Stock row capture after case quantity is typed, from the local Capture Selected button
- Normalized capture JSON in `data/captures/`
- Madhushala API client for:
  - `ExciseItemMasterSave`
  - `unmapped-items`
  - `purchase/dropdown/items`
  - `save-mapping`
- Two-pane mapping workspace:
  - left: unmapped excise items
  - right: best suggested Madhushala match, manual search fallback
  - submit: batch `save-mapping`
- Automatic mapping check after each extension capture
- Local mapping state in `data/mappings/`

## Recommended Production Flow

Use the Chrome extension for real users. This keeps Chrome, CAPTCHA, and Excise portal interaction on the user's own computer instead of opening one server-side Chrome instance per user.

1. User clicks `Open Excise Portal` in the bridge UI.
2. On first use only, the UI asks for the user's BEVCO/WB Excise ID and password and stores them in that Chrome profile.
3. Extension opens the Excise portal and fills the saved ID/password.
4. User enters CAPTCHA and logs in.
5. User goes to Prepare Indent and types case quantities in the rows to import.
6. After input settles, the extension automatically posts only positive case-quantity rows to `POST /extension/capture`.
7. Backend creates/checks Excise item records and returns mapped/unmapped status.
8. If matching is required, the extension automatically opens the mapping workspace in a focused popup.
9. User reviews the suggested matches and saves the correct mappings.

The old server-side Playwright/noVNC flow remains available as a fallback, but it is not the recommended path for 100 concurrent users.

## Setup

```powershell
cd "D:\Madhushala Software\itemimport\madhushala-excise-bridge"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Run

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8091
```

Open:

```text
http://127.0.0.1:8091
```

Configure `MADHUSHALA_TOKEN` on the backend. The client-facing UI intentionally does not display API-token settings.

For the simplest operator flow, create `.env` locally:

```text
MADHUSHALA_TOKEN=your_token_here
```

`EXCISE_USERNAME` and `EXCISE_PASSWORD` remain optional for the legacy server-side Playwright fallback. Extension users enter their separate Excise credentials once in the browser-profile prompt.

## Workflow

1. Configure the Madhushala token in the server `.env`.
2. Click `Open Excise Portal` in the bridge UI.
3. On first use, enter the separate BEVCO/WB Excise credentials. CRM credentials are not reused for the government portal.
4. Manually enter CAPTCHA, log in, and open Prepare Indent.
5. Type case quantities for the rows to import.
6. The extension automatically captures positive case-quantity rows, saves JSON, calls Madhushala import, and checks mapping status.
7. When mapping is required, the mapping workspace opens automatically.
8. Review the best suggestion or search for the correct item, then submit mappings in one batch.

## API

- `GET /health`
- `POST /automation/start`
- `POST /automation/capture-selected`
- `POST /extension/capture`
- `GET /automation/status`
- `GET /captures/latest`
- `GET /captures`
- `POST /automation/stop`
- `GET /madhushala/status`
- `POST /madhushala/token` for explicitly enabled local testing only (`ALLOW_RUNTIME_TOKEN_CONFIG=true`)
- `POST /mapping/prepare-latest-capture` for recovery/manual retry
- `GET /mapping/status`
- `GET /mapping/workspace`
- `GET /madhushala/items?q=...`
- `POST /mapping/submit`

## Matching Rules

The system suggests matches but never auto-submits mappings. Suggestions prioritize:

- exact ML match
- bottles-per-case/packing match
- normalized name similarity
- word overlap

Manual user confirmation is required before `save-mapping`.

## Validation

```powershell
python -m compileall app
pytest -v
```

## Security Notes

- Server binds to `127.0.0.1`.
- Excise password and Madhushala token use password inputs.
- Credentials/tokens are not saved to source or capture JSON.
- Browser profile, captures, mapping state, DB files, logs, and `.env` are ignored by git.

## Production Packaging

The project now includes Option A server packaging:

- `Dockerfile`
- `docker-compose.prod.yml`
- `.env.example`
- `deploy/nginx/madhushala-excise-bridge.conf`
- `deploy/README.md`

For the current server, clone the repo under:

```text
/srv/projects/iremimport
```

Then run the service from `/srv/projects/iremimport/madhushala-excise-bridge`.

The compose file binds the app only to private host ports, so Nginx should be the public entry point:

- Bridge UI/API: `127.0.0.1:8091`
- Browser view/noVNC: `127.0.0.1:6080`

In fallback server mode, the container starts a virtual display and noVNC so the operator can see the server-side Chromium browser, enter CAPTCHA, and work from the SnapKey CRM button flow.

For production scale, deploy the backend privately behind the final subdomain and install the Chrome extension from the `extension/` folder on operator machines.
