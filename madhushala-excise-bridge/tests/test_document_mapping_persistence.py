from __future__ import annotations

import sqlite3

from app import db as bridge_db


def _job(connection: sqlite3.Connection, job_id: str, *, shop_code: str = "SHOP") -> None:
    connection.execute(
        """
        INSERT INTO import_jobs(
          id, shop_code, session_id, source_type, status,
          created_at, updated_at
        ) VALUES (?, ?, ?, 'QR_HTML', 'MAPPING_REQUIRED', ?, ?)
        """,
        (job_id, shop_code, f"session-{job_id}", "2026-09-17T00:00:00+00:00", "2026-09-17T00:00:00+00:00"),
    )


def _item(
    connection: sqlite3.Connection,
    item_id: str,
    job_id: str,
    *,
    name: str = "tenjaku blended whisky",
    ml: int = 700,
) -> None:
    connection.execute(
        """
        INSERT INTO import_items(
          id, job_id, source_item_id, raw_name, normalized_name, brand, ml,
          packing, quantity, mapping_status, mapped_item_code, excise_item_code,
          raw_data_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', '', '', '{}', ?, ?)
        """,
        (
            item_id,
            job_id,
            f"source-{item_id}",
            "Tenjaku Blended Whisky 700ML",
            name,
            "Tenjaku Blended Whisky",
            ml,
            12,
            3,
            "2026-09-17T00:00:00+00:00",
            "2026-09-17T00:00:00+00:00",
        ),
    )


def test_confirmed_document_mapping_is_reused_by_next_qr_job():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(bridge_db.SCHEMA)

    _job(connection, "job-1")
    _item(connection, "row-1", "job-1")

    # User maps the item once.
    connection.execute(
        """
        UPDATE import_items
        SET mapped_item_code='100003', mapping_status='MAPPED', updated_at='2026-09-17T01:00:00+00:00'
        WHERE id='row-1'
        """
    )

    remembered = connection.execute(
        "SELECT madhushala_item_code FROM document_product_mappings WHERE shop_code='SHOP' AND normalized_name='tenjaku blended whisky' AND ml=700"
    ).fetchone()
    assert remembered["madhushala_item_code"] == "100003"

    # The same product arrives in a new QR/document job. It must already be mapped.
    _job(connection, "job-2")
    _item(connection, "row-2", "job-2")
    reused = connection.execute(
        "SELECT mapping_status, mapped_item_code FROM import_items WHERE id='row-2'"
    ).fetchone()
    assert reused["mapping_status"] == "MAPPED"
    assert reused["mapped_item_code"] == "100003"

    # prepare_document_job can refresh the Excise code and previously attempted to
    # blank the row when the already-mapped Excise item was absent from unmapped-items.
    # The durable mapping must survive that refresh.
    connection.execute(
        """
        UPDATE import_items
        SET excise_item_code='', mapped_item_code='', mapping_status='REVIEW_REQUIRED', updated_at='2026-09-17T01:10:00+00:00'
        WHERE id='row-2'
        """
    )
    preserved = connection.execute(
        "SELECT mapping_status, mapped_item_code FROM import_items WHERE id='row-2'"
    ).fetchone()
    assert preserved["mapping_status"] == "MAPPED"
    assert preserved["mapped_item_code"] == "100003"

    # Explicit Change Mapping updates the durable choice for future QR jobs.
    connection.execute(
        """
        UPDATE import_items
        SET mapped_item_code='100099', mapping_status='MAPPED', updated_at='2026-09-17T02:00:00+00:00'
        WHERE id='row-2'
        """
    )
    _job(connection, "job-3")
    _item(connection, "row-3", "job-3")
    changed = connection.execute(
        "SELECT mapping_status, mapped_item_code FROM import_items WHERE id='row-3'"
    ).fetchone()
    assert changed["mapping_status"] == "MAPPED"
    assert changed["mapped_item_code"] == "100099"

    connection.close()


def test_mapping_reuse_is_scoped_to_shop():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(bridge_db.SCHEMA)

    _job(connection, "job-a", shop_code="SHOP-A")
    _item(connection, "row-a", "job-a")
    connection.execute(
        "UPDATE import_items SET mapped_item_code='A001', mapping_status='MAPPED' WHERE id='row-a'"
    )

    _job(connection, "job-b", shop_code="SHOP-B")
    _item(connection, "row-b", "job-b")
    other_shop = connection.execute(
        "SELECT mapping_status, mapped_item_code FROM import_items WHERE id='row-b'"
    ).fetchone()
    assert other_shop["mapping_status"] == "PENDING"
    assert not other_shop["mapped_item_code"]

    connection.close()
