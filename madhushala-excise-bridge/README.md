# Madhushala Excise Bridge

Production foundation for Madhushala CRM Excise portal integration using Playwright.

## Overview

This service provides a local automation bridge between Madhushala CRM and the West Bengal Excise portal. It enables users to:

1. Manually log into the Excise portal
2. Select products while preparing an indent
3. Capture selected items automatically
4. Sync items to Madhushala CRM
5. Handle unmapped items with mapping UI

## Setup

```powershell
cd d:\Madhushala Software\itemimport\madhushala-excise-bridge
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

## Run

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8091 --reload
```

Then open:

```
http://127.0.0.1:8091
```

## Test

```powershell
pytest -v
```

## User Flow

1. **Start Session** - Click "Start Automation" to launch the Playwright browser
2. **Manual Login** - Log into the Excise portal manually
3. **Prepare Indent** - Navigate to Prepare Indent page
4. **Select Products** - Check products and set quantities
5. **Add to Bucket** - Click "Add to Bucket" to capture selections
6. **Import** - Items are automatically synced to Madhushala
7. **Mapping** - If unmapped, a popup appears for manual item code entry

## API Endpoints

- `GET /health` - Health check
- `POST /automation/start` - Start automation session
- `POST /automation/open-excise` - Open Excise portal
- `GET /automation/status` - Get automation status
- `GET /automation/selected-items` - Get selected items
- `GET /automation/pending-mappings` - Get mapping-required items
- `POST /automation/map` - Save item mapping
- `POST /automation/stop` - Stop automation session
- `WS /ws/events` - WebSocket for real-time events

## Project Structure

```
madhushala-excise-bridge/
├── app/
│   ├── main.py                 # FastAPI application
│   ├── config.py               # Configuration settings
│   ├── browser_manager.py      # Playwright browser management
│   ├── db/
│   │   ├── database.py         # Database connection
│   │   └── models.py           # SQLAlchemy models
│   ├── integrations/
│   │   └── madhushala/
│   │       └── client.py       # Madhushala API client
│   ├── services/               # Business logic services
│   └── static/
│       ├── index.html          # Demo page
│       ├── app.js              # Frontend JavaScript
│       └── style.css           # Styles
├── data/                       # SQLite database
├── tests/                      # Test files
├── .env.example                # Environment template
├── requirements.txt
└── README.md
```

## Security

- No credentials are captured or stored
- User performs manual login in browser
- JWT tokens are kept in memory only
- No CAPTCHA bypass attempts
- No aggressive scraping

## Known Assumptions

- Madhushala API endpoints: ExciseItemMasterSave, unmapped-items, save-mapping
- No Get Item Master or Item Search endpoints available yet
- Mapping UI accepts manual itemCode entry for now
- Will connect to real CRM item search later

## License

Internal use only for Madhushala CRM integration.</arg_value></tool_call><task_progress>
- [x] Create main application file
- [x] Implement Playwright automation for manual consent
- [x] Create static HTML demo page
- [x] Implement Madhushala API client
- [x] Create configuration file
- [x] Create database models
- [x] Create database connection module
- [x] Initialize git repository
- [x] Create requirements.txt
- [x] Create .env.example
- [ ] Create README.md
- [ ] Create test files
- [ ] Run syntax checks
- [ ] Run pytest
- [ ] Verify server starts
- [ ] Verify /health endpoint
- [ ] Verify demo page loads
- [ ] Verify Playwright launches
- [ ] Create IMPLEMENTATION_STATUS.md
</task_progress>
</write_to_file></tool_call>