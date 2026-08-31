# Madhushala Excise Bridge

Local FastAPI + Playwright bridge for WB Excise Prepare Indent capture and Madhushala item mapping.

## Implemented

- Local UI at `http://127.0.0.1:8091`
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
- Automatic mapping check after local Capture Selected
- Local mapping state in `data/mappings/`

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

Paste the Madhushala testing token into the UI before the daily Excise work. It is kept only in process memory unless you choose to set `MADHUSHALA_TOKEN` in your local environment.

For the simplest operator flow, create `.env` locally:

```text
MADHUSHALA_TOKEN=your_token_here
EXCISE_USERNAME=your_excise_username
EXCISE_PASSWORD=your_excise_password
```

When these are set, users can leave the local UI credential/token fields blank.

## Workflow

1. Paste Madhushala token once in the local UI, or set it in `.env`.
2. Open Excise portal from the local UI.
3. Manually enter CAPTCHA and log in.
4. Manually use Prepare Indent.
5. Type case quantity for the rows to import in Excise.
6. Do not click Add to Bucket for testing. Click Capture Selected in the local app.
7. The local app captures rows with case quantity, saves JSON, calls Madhushala import, and checks mapping status.
8. If mapping is required, the local app scrolls to the mapping area and shows a yellow Mapping Required message.
9. Select each unmapped item on the left.
10. Use Correct for the best suggestion, or type in Find Item and choose the right match.
11. Submit mappings in one batch.

## API

- `GET /health`
- `POST /automation/start`
- `POST /automation/capture-selected`
- `GET /automation/status`
- `GET /captures/latest`
- `GET /captures`
- `POST /automation/stop`
- `GET /madhushala/status`
- `POST /madhushala/token`
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

For the current server, deploy it as an independent project under:

```text
/srv/projects/madhushala-excise-bridge
```

The compose file binds the app only to private host ports, so Nginx should be the public entry point:

- Bridge UI/API: `127.0.0.1:8091`
- Browser view/noVNC: `127.0.0.1:6080`

In server mode, the container starts a virtual display and noVNC so the operator can see the server-side Chromium browser, enter CAPTCHA, and work from the SnapKey CRM button flow.
