#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from typing import Iterable

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


TABLES = [
    "integration_sessions",
    "captures",
    "imports",
    "mappings",
    "mappings_v2",
    "import_jobs",
    "document_product_mappings",
    "document_product_mappings_v2",
    "import_items",
    "purchase_transactions",
    "purchase_events",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy Madhushala Excise Bridge state from SQLite into PostgreSQL."
    )
    parser.add_argument(
        "--sqlite",
        default=os.getenv("DATABASE_PATH", "data/bridge.db"),
        help="Path to existing SQLite bridge.db",
    )
    parser.add_argument(
        "--postgres",
        default=os.getenv("DATABASE_URL", ""),
        help="PostgreSQL URL, e.g. postgresql://user:pass@localhost:5432/madhushala_bridge",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Truncate target tables before copying. Use only during an intentional cutover.",
    )
    return parser.parse_args()


def sqlite_columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()]


def postgres_columns(db: psycopg.Connection, table: str) -> set[str]:
    rows = db.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
        """,
        (table,),
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def copy_table(
    source: sqlite3.Connection,
    target: psycopg.Connection,
    table: str,
) -> int:
    source_cols = sqlite_columns(source, table)
    if not source_cols:
        return 0
    target_cols = postgres_columns(target, table)
    columns = [column for column in source_cols if column in target_cols]
    if not columns:
        return 0

    rows = source.execute(
        f"SELECT {', '.join(columns)} FROM {table}"
    ).fetchall()
    if not rows:
        return 0

    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    values: Iterable[tuple] = (
        tuple(row[column] for column in columns)
        for row in rows
    )
    with target.cursor() as cursor:
        cursor.executemany(statement, values)
    return len(rows)


def reset_serial(target: psycopg.Connection, table: str, column: str = "id") -> None:
    sequence = target.execute(
        "SELECT pg_get_serial_sequence(%s, %s) AS sequence",
        (table, column),
    ).fetchone()
    sequence_name = sequence["sequence"] if sequence else None
    if not sequence_name:
        return
    target.execute(
        sql.SQL(
            "SELECT setval({}, COALESCE((SELECT MAX({}) FROM {}), 1), "
            "EXISTS(SELECT 1 FROM {}))"
        ).format(
            sql.Literal(sequence_name),
            sql.Identifier(column),
            sql.Identifier(table),
            sql.Identifier(table),
        )
    )


def main() -> int:
    args = parse_args()
    if not args.postgres:
        raise SystemExit("--postgres or DATABASE_URL is required")
    if not os.path.exists(args.sqlite):
        raise SystemExit(f"SQLite database not found: {args.sqlite}")

    source = sqlite3.connect(args.sqlite)
    source.row_factory = sqlite3.Row
    target = psycopg.connect(args.postgres, row_factory=dict_row)

    try:
        if args.truncate:
            target.execute(
                """
                TRUNCATE TABLE
                  purchase_events,
                  purchase_transactions,
                  import_items,
                  document_product_mappings_v2,
                  document_product_mappings,
                  import_jobs,
                  mappings_v2,
                  mappings,
                  imports,
                  captures,
                  integration_sessions
                RESTART IDENTITY
                """
            )

        copied: dict[str, int] = {}
        for table in TABLES:
            copied[table] = copy_table(source, target, table)

        reset_serial(target, "captures")
        reset_serial(target, "purchase_events")
        target.commit()

        for table, count in copied.items():
            print(f"{table}: {count} row(s) copied")
        print("Migration completed successfully.")
        return 0
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())
