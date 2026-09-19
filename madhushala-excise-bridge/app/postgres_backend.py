from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings


logger = logging.getLogger("madhushala-excise-bridge.db")

_pool: ConnectionPool | None = None
_pool_guard = threading.Lock()


def _pool_instance() -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    if not settings.DATABASE_URL.lower().startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL is not a PostgreSQL URL")
    with _pool_guard:
        if _pool is None:
            _pool = ConnectionPool(
                conninfo=settings.DATABASE_URL,
                min_size=max(1, settings.DB_POOL_MIN_SIZE),
                max_size=max(settings.DB_POOL_MIN_SIZE, settings.DB_POOL_MAX_SIZE),
                timeout=max(1.0, settings.DB_POOL_TIMEOUT_SECONDS),
                kwargs={"row_factory": dict_row, "autocommit": False},
                open=True,
                name="madhushala-bridge",
            )
    return _pool


def _translate_sql(sql: str) -> str:
    # The existing application uses SQLite's qmark parameter style. Psycopg uses
    # %s, so keep application queries unchanged while the backend is migrated.
    escaped = sql.replace("%", "%%")
    return escaped.replace("?", "%s")


class PostgresConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, params: Any = None):
        query = _translate_sql(sql)
        if params is None:
            return self._connection.execute(query)
        return self._connection.execute(query, tuple(params))

    def executemany(self, sql: str, params_seq: Any):
        return self._connection.executemany(
            _translate_sql(sql),
            [tuple(params) for params in params_seq],
        )

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()


@contextmanager
def postgres_conn() -> Iterator[PostgresConnection]:
    pool = _pool_instance()
    with pool.connection() as connection:
        wrapper = PostgresConnection(connection)
        try:
            yield wrapper
            connection.commit()
        except Exception:
            connection.rollback()
            raise


POSTGRES_DDL = [
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS captures (
      id BIGSERIAL PRIMARY KEY,
      session_id TEXT NOT NULL,
      shop_code TEXT NOT NULL,
      batch_id TEXT NOT NULL,
      captured_at TEXT NOT NULL,
      capture_signature TEXT,
      response_json TEXT,
      payload_json TEXT NOT NULL,
      UNIQUE(session_id, batch_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS imports (
      shop_code TEXT NOT NULL,
      canonical_key TEXT NOT NULL,
      excise_item_code TEXT,
      item_name TEXT NOT NULL,
      captured_item_json TEXT NOT NULL,
      last_seen_batch_id TEXT,
      updated_at TEXT NOT NULL,
      PRIMARY KEY(shop_code, canonical_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mappings (
      shop_code TEXT NOT NULL,
      excise_item_code TEXT NOT NULL,
      madhushala_item_code TEXT NOT NULL,
      mapped_at TEXT NOT NULL,
      PRIMARY KEY(shop_code, excise_item_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mappings_v2 (
      shop_code TEXT NOT NULL,
      company_code TEXT NOT NULL,
      excise_item_code TEXT NOT NULL,
      madhushala_item_code TEXT NOT NULL,
      mapped_at TEXT NOT NULL,
      PRIMARY KEY(shop_code, company_code, excise_item_code)
    )
    """,
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_items (
      id TEXT PRIMARY KEY,
      job_id TEXT NOT NULL,
      source_item_id TEXT,
      raw_name TEXT,
      normalized_name TEXT,
      brand TEXT,
      ml INTEGER,
      packing INTEGER,
      quantity DOUBLE PRECISION,
      box INTEGER,
      loose INTEGER,
      rate DOUBLE PRECISION,
      mrp DOUBLE PRECISION,
      amount DOUBLE PRECISION,
      barcode TEXT,
      confidence DOUBLE PRECISION,
      mapping_status TEXT NOT NULL,
      mapped_item_code TEXT,
      excise_item_code TEXT,
      raw_data_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_product_mappings (
      shop_code TEXT NOT NULL,
      normalized_name TEXT NOT NULL,
      ml INTEGER NOT NULL,
      barcode TEXT,
      madhushala_item_code TEXT NOT NULL,
      mapped_at TEXT NOT NULL,
      PRIMARY KEY(shop_code, normalized_name, ml)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_product_mappings_v2 (
      shop_code TEXT NOT NULL,
      company_code TEXT NOT NULL,
      normalized_name TEXT NOT NULL,
      ml INTEGER NOT NULL,
      barcode TEXT,
      madhushala_item_code TEXT NOT NULL,
      mapped_at TEXT NOT NULL,
      PRIMARY KEY(shop_code, company_code, normalized_name, ml)
    )
    """,
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS purchase_events (
      id BIGSERIAL PRIMARY KEY,
      transaction_id TEXT NOT NULL,
      job_id TEXT NOT NULL,
      stage TEXT NOT NULL,
      status TEXT NOT NULL,
      duration_ms INTEGER,
      details_json TEXT,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_import_jobs_shop_code ON import_jobs(shop_code)",
    "CREATE INDEX IF NOT EXISTS idx_import_jobs_session_id ON import_jobs(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_import_jobs_status ON import_jobs(status)",
    "CREATE INDEX IF NOT EXISTS idx_import_items_job_id ON import_items(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_import_items_mapping_status ON import_items(mapping_status)",
    "CREATE INDEX IF NOT EXISTS idx_document_product_mappings_item ON document_product_mappings(shop_code, madhushala_item_code)",
    "CREATE INDEX IF NOT EXISTS idx_mappings_v2_item ON mappings_v2(shop_code, company_code, madhushala_item_code)",
    "CREATE INDEX IF NOT EXISTS idx_document_product_mappings_v2_item ON document_product_mappings_v2(shop_code, company_code, madhushala_item_code)",
    "CREATE INDEX IF NOT EXISTS idx_purchase_transactions_status ON purchase_transactions(status)",
    "CREATE INDEX IF NOT EXISTS idx_purchase_transactions_doc ON purchase_transactions(shop_code, company_code, supplier_code, doc_no)",
    "CREATE INDEX IF NOT EXISTS idx_purchase_events_job_id ON purchase_events(job_id)",
]


POSTGRES_TRIGGER_DDL = [
    """
    CREATE OR REPLACE FUNCTION remember_document_mapping_v2() RETURNS trigger AS $$
    BEGIN
      IF COALESCE(BTRIM(NEW.mapped_item_code), '') <> ''
         AND COALESCE(BTRIM(NEW.normalized_name), '') <> ''
         AND COALESCE(NEW.ml, 0) > 0 THEN
        INSERT INTO document_product_mappings_v2(
          shop_code, company_code, normalized_name, ml, barcode,
          madhushala_item_code, mapped_at
        )
        SELECT
          j.shop_code, s.company_code, NEW.normalized_name, NEW.ml, NEW.barcode,
          NEW.mapped_item_code, NEW.updated_at
        FROM import_jobs j
        JOIN integration_sessions s ON s.session_id = j.session_id
        WHERE j.id = NEW.job_id
        ON CONFLICT (shop_code, company_code, normalized_name, ml)
        DO UPDATE SET
          barcode = EXCLUDED.barcode,
          madhushala_item_code = EXCLUDED.madhushala_item_code,
          mapped_at = EXCLUDED.mapped_at;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE OR REPLACE FUNCTION reuse_document_mapping_v2() RETURNS trigger AS $$
    DECLARE remembered TEXT;
    BEGIN
      IF COALESCE(BTRIM(NEW.normalized_name), '') <> '' AND COALESCE(NEW.ml, 0) > 0 THEN
        SELECT dpm.madhushala_item_code
          INTO remembered
        FROM document_product_mappings_v2 dpm
        JOIN import_jobs j ON j.id = NEW.job_id
        JOIN integration_sessions s ON s.session_id = j.session_id
        WHERE dpm.shop_code = j.shop_code
          AND dpm.company_code = s.company_code
          AND dpm.normalized_name = NEW.normalized_name
          AND dpm.ml = NEW.ml
        LIMIT 1;

        IF remembered IS NOT NULL AND COALESCE(BTRIM(NEW.mapped_item_code), '') = '' THEN
          NEW.mapped_item_code := remembered;
          NEW.mapping_status := 'MAPPED';
        END IF;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE OR REPLACE FUNCTION preserve_document_mapping_v2() RETURNS trigger AS $$
    DECLARE remembered TEXT;
    BEGIN
      IF COALESCE(BTRIM(NEW.mapped_item_code), '') = ''
         AND COALESCE(BTRIM(NEW.normalized_name), '') <> ''
         AND COALESCE(NEW.ml, 0) > 0 THEN
        SELECT dpm.madhushala_item_code
          INTO remembered
        FROM document_product_mappings_v2 dpm
        JOIN import_jobs j ON j.id = NEW.job_id
        JOIN integration_sessions s ON s.session_id = j.session_id
        WHERE dpm.shop_code = j.shop_code
          AND dpm.company_code = s.company_code
          AND dpm.normalized_name = NEW.normalized_name
          AND dpm.ml = NEW.ml
        LIMIT 1;

        IF remembered IS NOT NULL THEN
          NEW.mapped_item_code := remembered;
          NEW.mapping_status := 'MAPPED';
        END IF;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_document_mapping_remember_v2 ON import_items",
    """
    CREATE TRIGGER trg_document_mapping_remember_v2
    AFTER UPDATE OF mapped_item_code ON import_items
    FOR EACH ROW EXECUTE FUNCTION remember_document_mapping_v2()
    """,
    "DROP TRIGGER IF EXISTS trg_document_mapping_reuse_on_insert_v2 ON import_items",
    """
    CREATE TRIGGER trg_document_mapping_reuse_on_insert_v2
    BEFORE INSERT ON import_items
    FOR EACH ROW EXECUTE FUNCTION reuse_document_mapping_v2()
    """,
    "DROP TRIGGER IF EXISTS trg_document_mapping_preserve_on_refresh_v2 ON import_items",
    """
    CREATE TRIGGER trg_document_mapping_preserve_on_refresh_v2
    BEFORE UPDATE OF mapped_item_code ON import_items
    FOR EACH ROW EXECUTE FUNCTION preserve_document_mapping_v2()
    """,
]


def init_postgres() -> None:
    with postgres_conn() as db:
        for statement in POSTGRES_DDL:
            db.execute(statement)
        for statement in POSTGRES_TRIGGER_DDL:
            db.execute(statement)
    logger.info(
        "database_backend=postgres pool_min=%s pool_max=%s",
        settings.DB_POOL_MIN_SIZE,
        settings.DB_POOL_MAX_SIZE,
    )


def close_postgres_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
