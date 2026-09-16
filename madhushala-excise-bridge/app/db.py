import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS integration_sessions (
  session_id TEXT PRIMARY KEY,
  session_token TEXT NOT NULL UNIQUE,
  shop_code TEXT NOT NULL,
  company_code TEXT NOT NULL,
  bill_type TEXT NOT NULL,
  madhushala_token TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'created'
);
CREATE TABLE IF NOT EXISTS captures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  shop_code TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  capture_signature TEXT,
  response_json TEXT,
  payload_json TEXT NOT NULL,
  UNIQUE(session_id, batch_id)
);
CREATE TABLE IF NOT EXISTS imports (
  shop_code TEXT NOT NULL,
  canonical_key TEXT NOT NULL,
  excise_item_code TEXT,
  item_name TEXT NOT NULL,
  captured_item_json TEXT NOT NULL,
  last_seen_batch_id TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(shop_code, canonical_key)
);
CREATE TABLE IF NOT EXISTS mappings (
  shop_code TEXT NOT NULL,
  excise_item_code TEXT NOT NULL,
  madhushala_item_code TEXT NOT NULL,
  mapped_at TEXT NOT NULL,
  PRIMARY KEY(shop_code, excise_item_code)
);
CREATE TABLE IF NOT EXISTS import_jobs (
  id TEXT PRIMARY KEY,
  shop_code TEXT NOT NULL,
  session_id TEXT NOT NULL,
  user_id TEXT,
  source_type TEXT NOT NULL,
  status TEXT NOT NULL,
  source_filename TEXT,
  document_type TEXT,
  supplier_name TEXT,
  invoice_number TEXT,
  invoice_date TEXT,
  extracted_count INTEGER NOT NULL DEFAULT 0,
  mapped_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE TABLE IF NOT EXISTS import_items (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  source_item_id TEXT,
  raw_name TEXT,
  normalized_name TEXT,
  brand TEXT,
  ml INTEGER,
  packing INTEGER,
  quantity REAL,
  rate REAL,
  mrp REAL,
  amount REAL,
  barcode TEXT,
  confidence REAL,
  mapping_status TEXT NOT NULL,
  mapped_item_code TEXT,
  excise_item_code TEXT,
  raw_data_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_transactions (
  job_id TEXT PRIMARY KEY,
  shop_code TEXT NOT NULL,
  company_code TEXT NOT NULL,
  supplier_code TEXT,
  doc_no TEXT,
  payload_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  request_json TEXT NOT NULL,
  response_json TEXT,
  error TEXT,
  madhushala_trn_no TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_import_jobs_shop_code ON import_jobs(shop_code);
CREATE INDEX IF NOT EXISTS idx_import_jobs_session_id ON import_jobs(session_id);
CREATE INDEX IF NOT EXISTS idx_import_jobs_status ON import_jobs(status);
CREATE INDEX IF NOT EXISTS idx_import_items_job_id ON import_items(job_id);
CREATE INDEX IF NOT EXISTS idx_import_items_mapping_status ON import_items(mapping_status);
CREATE INDEX IF NOT EXISTS idx_purchase_transactions_status ON purchase_transactions(status);
CREATE INDEX IF NOT EXISTS idx_purchase_transactions_bill ON purchase_transactions(shop_code, company_code, supplier_code, doc_no);
"""

@contextmanager
def conn():
    os.makedirs(os.path.dirname(settings.DATABASE_PATH) or ".", exist_ok=True)
    db = sqlite3.connect(settings.DATABASE_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()

def init_db():
    with conn() as db:
        db.executescript(SCHEMA)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(captures)").fetchall()}
        if "capture_signature" not in columns:
            db.execute("ALTER TABLE captures ADD COLUMN capture_signature TEXT")
        if "response_json" not in columns:
            db.execute("ALTER TABLE captures ADD COLUMN response_json TEXT")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def json_dump(value):
    return json.dumps(value, ensure_ascii=False)

def json_load(value):
    return json.loads(value)
