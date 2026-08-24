# Phase 1 Status

## IMPLEMENTED

- Simple local FastAPI UI at `http://127.0.0.1:8091`
- `POST /automation/start` opens visible Chromium with persistent profile at `data/browser_profile/`
- WB Excise login URL is opened from the automation flow
- Username and password are autofilled with recorded selectors
- CAPTCHA field is focused and left for manual entry
- Browser remains alive after `/automation/start` returns
- Prepare Indent detection checks the Warehouse Stock grid
- Capture bridge is installed with `context.add_init_script(...)` for every navigation
- Local Capture Selected action snapshots rows with typed case quantity without clicking Add to Bucket
- Only rows with `input[id$="_Qty"]` greater than zero are snapshotted
- Captured rows are normalized into structured JSON
- Canonical key uses normalized brand, measure ML, and normalized package type
- Money values remain strings
- Captures are saved to `data/captures/YYYYMMDD_HHMMSS_<batchid>.json`
- Latest capture is retained in memory for UI/API status
- Browser profile, captures, DB files, logs, and `.env` are gitignored

## AUTOMATED TESTED

- `python -m compileall app` passes
- `pytest -v` passes: 27 tests
- Tests cover brand normalization, apostrophe normalization, ML conversion, string money preservation, canonical key generation, 750 ML vs 50 ML key difference, MRP changes preserving key identity, missing supplier, case-quantity rows only, quantity extraction, selected-row snapshot shape, malformed row skipping, and password absence from response/log messages

## READY FOR REAL PORTAL TEST

- Server starts with:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8091
```

- Verified endpoints:
  - `/` returns 200
  - `/health` returns ready idle status
  - `/automation/status` returns idle status with no sensitive values
  - `/captures/latest` returns 404 when no capture exists
- Verified visible Playwright Chromium launches and closes successfully with a persistent context.

The real WB Excise portal flow was not interactively tested with real credentials or CAPTCHA in this validation pass.

## NOT YET IMPLEMENTED

- Madhushala API calls
- Excise item master save
- Unmapped items workflow
- Save mapping
- Mapping popup
- OAuth
- CRM integration
- Item master matching
- Automatic post-login government portal navigation
- CAPTCHA automation
- EXE packaging
- Nightly or full catalogue scraping

## KNOWN ASSUMPTIONS

- Recorded WB Excise selectors remain valid:
  - `#txt_username`
  - `#txt_password`
  - `#CodeNumberTextBox`
  - `#ImageButton1`
  - Warehouse Stock grid selectors listed in the Phase 1 brief
- The user manually completes CAPTCHA, login, Prepare Indent navigation, product selection, and case quantity entry.
- The local Capture Selected button snapshots rows with typed case quantity without submitting the Excise Add to Bucket action.
