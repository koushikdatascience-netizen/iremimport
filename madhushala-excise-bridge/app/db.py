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

-- Durable product-level mapping for document/QR imports.  Excise item codes are
-- not a reliable identity for repeated QR imports because a mapped Excise item
-- can disappear from the upstream "unmapped-items" list.  Product name + ML is
-- stable in the normalized document rows, so the mapping is remembered once per
-- shop and automatically reused by later jobs.
CREATE TABLE IF NOT EXISTS document_product_mappings (
  shop_code TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  ml INTEGER NOT NULL,
  barcode TEXT,
  madhushala_item_code TEXT NOT NULL,
  mapped_at TEXT NOT NULL,
  PRIMARY KEY(shop_code, normalized_name, ml)
);

CREATE TABLE IF NOT EXISTS purchase_transactions (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE,
  shop_code TEXT NOT NULL,
  company_code TEXT NOT NULL,
  supplier_code TEXT,
  doc_no TEXT,
  payload_hash TEXT,
  status TEXT NOT NULL,
  madhushala_trn_no TEXT,
  response_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  transaction_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  duration_ms INTEGER,
  details_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_import_jobs_shop_code ON import_jobs(shop_code);
CREATE INDEX IF NOT EXISTS idx_import_jobs_session_id ON import_jobs(session_id);
CREATE INDEX IF NOT EXISTS idx_import_jobs_status ON import_jobs(status);
CREATE INDEX IF NOT EXISTS idx_import_items_job_id ON import_items(job_id);
CREATE INDEX IF NOT EXISTS idx_import_items_mapping_status ON import_items(mapping_status);
CREATE INDEX IF NOT EXISTS idx_document_product_mappings_item ON document_product_mappings(shop_code, madhushala_item_code);
CREATE INDEX IF NOT EXISTS idx_purchase_transactions_status ON purchase_transactions(status);
CREATE INDEX IF NOT EXISTS idx_purchase_transactions_doc ON purchase_transactions(shop_code, company_code, supplier_code, doc_no);
CREATE INDEX IF NOT EXISTS idx_purchase_events_job_id ON purchase_events(job_id);

-- When a user confirms/changes a document mapping, remember that decision at
-- product level. INSERT OR REPLACE means an explicit Change Mapping action is
-- automatically the value used by future imports.
CREATE TRIGGER IF NOT EXISTS trg_document_mapping_remember
AFTER UPDATE OF mapped_item_code ON import_items
WHEN TRIM(COALESCE(NEW.mapped_item_code, '')) <> ''
 AND TRIM(COALESCE(NEW.normalized_name, '')) <> ''
 AND COALESCE(NEW.ml, 0) > 0
BEGIN
  INSERT OR REPLACE INTO document_product_mappings(
    shop_code, normalized_name, ml, barcode, madhushala_item_code, mapped_at
  )
  SELECT
    j.shop_code,
    NEW.normalized_name,
    NEW.ml,
    NEW.barcode,
    NEW.mapped_item_code,
    NEW.updated_at
  FROM import_jobs j
  WHERE j.id = NEW.job_id;
END;

-- New QR/PDF jobs inherit a previously confirmed mapping immediately, before
-- the mapping workspace is rendered.
CREATE TRIGGER IF NOT EXISTS trg_document_mapping_reuse_on_insert
AFTER INSERT ON import_items
WHEN TRIM(COALESCE(NEW.normalized_name, '')) <> ''
 AND COALESCE(NEW.ml, 0) > 0
BEGIN
  UPDATE import_items
  SET
    mapped_item_code = COALESCE(
      (
        SELECT dpm.madhushala_item_code
        FROM document_product_mappings dpm
        JOIN import_jobs j ON j.id = NEW.job_id
        WHERE dpm.shop_code = j.shop_code
          AND dpm.normalized_name = NEW.normalized_name
          AND dpm.ml = NEW.ml
        LIMIT 1
      ),
      mapped_item_code
    ),
    mapping_status = CASE
      WHEN EXISTS(
        SELECT 1
        FROM document_product_mappings dpm
        JOIN import_jobs j ON j.id = NEW.job_id
        WHERE dpm.shop_code = j.shop_code
          AND dpm.normalized_name = NEW.normalized_name
          AND dpm.ml = NEW.ml
      ) THEN 'MAPPED'
      ELSE mapping_status
    END
  WHERE id = NEW.id;
END;

-- prepare_document_job also refreshes the Excise item code.  If the upstream
-- unmapped-items API no longer returns an already-mapped product, that refresh
-- used to replace the remembered mapped_item_code with blank.  Restore the
-- durable product mapping instead of asking the user to map the same item again.
CREATE TRIGGER IF NOT EXISTS trg_document_mapping_preserve_on_refresh
AFTER UPDATE OF mapped_item_code ON import_items
WHEN TRIM(COALESCE(NEW.mapped_item_code, '')) = ''
 AND TRIM(COALESCE(NEW.normalized_name, '')) <> ''
 AND COALESCE(NEW.ml, 0) > 0
 AND EXISTS(
   SELECT 1
   FROM document_product_mappings dpm
   JOIN import_jobs j ON j.id = NEW.job_id
   WHERE dpm.shop_code = j.shop_code
     AND dpm.normalized_name = NEW.normalized_name
     AND dpm.ml = NEW.ml
 )
BEGIN
  UPDATE import_items
  SET
    mapped_item_code = (
      SELECT dpm.madhushala_item_code
      FROM document_product_mappings dpm
      JOIN import_jobs j ON j.id = NEW.job_id
      WHERE dpm.shop_code = j.shop_code
        AND dpm.normalized_name = NEW.normalized_name
        AND dpm.ml = NEW.ml
      LIMIT 1
    ),
    mapping_status = 'MAPPED'
  WHERE id = NEW.id;
END;
"""

@contextmanager
def conn():
    os.makedirs(os.path.dirname(settings.DATABASE_PATH) or ".", exist_ok=True)
    db = sqlite3.connect(settings.DATABASE_PATH)
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

        # Backfill mappings already confirmed before this migration.  Rows are
        # ordered oldest -> newest so the user's latest Change Mapping decision
        # wins for a repeated product.
        historical = db.execute(
            """
            SELECT
              j.shop_code,
              ii.normalized_name,
              ii.ml,
              ii.barcode,
              ii.mapped_item_code,
              ii.updated_at
            FROM import_items ii
            JOIN import_jobs j ON j.id = ii.job_id
            WHERE TRIM(COALESCE(ii.mapped_item_code, '')) <> ''
              AND TRIM(COALESCE(ii.normalized_name, '')) <> ''
              AND COALESCE(ii.ml, 0) > 0
            ORDER BY ii.updated_at ASC
            """
        ).fetchall()
        for row in historical:
            db.execute(
                """
                INSERT OR REPLACE INTO document_product_mappings(
                  shop_code, normalized_name, ml, barcode, madhushala_item_code, mapped_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["shop_code"],
                    row["normalized_name"],
                    row["ml"],
                    row["barcode"],
                    row["mapped_item_code"],
                    row["updated_at"],
                ),
            )

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def json_dump(value):
    return json.dumps(value, ensure_ascii=False)

def json_load(value):
    return json.loads(value)
