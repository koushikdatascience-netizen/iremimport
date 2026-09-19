from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import psycopg
from psycopg import sql

from app.postgres_backend import close_postgres_pool, init_postgres


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

SERIAL_TABLES = ("captures", "purchase_events")


def _target_columns(connection: psycopg.Connection, table: str) -> list[str]:
    rows = connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
        ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()
    return [row[0] for row in rows]


def migrate(sqlite_path: str, database_url: str) -> None:
    source_path = Path(sqlite_path)
    if not source_path.exists():
        raise RuntimeError(f"SQLite database not found: {source_path}")
    if not database_url.lower().startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL must point to PostgreSQL")

    # Create the target schema/triggers using the application's canonical DDL.
    init_postgres()

    source = sqlite3.connect(str(source_path))
    source.row_factory = sqlite3.Row
    copied: dict[str, int] = {}

    try:
        with psycopg.connect(database_url) as target:
            for table in TABLES:
                source_columns = [
                    row["name"]
                    for row in source.execute(f'PRAGMA table_info("{table}")').fetchall()
                ]
                if not source_columns:
                    copied[table] = 0
                    continue

                target_columns = set(_target_columns(target, table))
                columns = [name for name in source_columns if name in target_columns]
                if not columns:
                    copied[table] = 0
                    continue

                rows = source.execute(
                    f'SELECT {", ".join(chr(34) + c + chr(34) for c in columns)} FROM "{table}"'
                ).fetchall()
                if rows:
                    statement = sql.SQL(
                        "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING"
                    ).format(
                        sql.Identifier(table),
                        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
                        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
                    )
                    with target.cursor() as cursor:
                        cursor.executemany(
                            statement,
                            [tuple(row[c] for c in columns) for row in rows],
                        )
                copied[table] = len(rows)

            for table in SERIAL_TABLES:
                target.execute(
                    sql.SQL(
                        """
                        SELECT setval(
                          pg_get_serial_sequence({}, 'id'),
                          GREATEST(COALESCE(MAX(id), 0), 1),
                          COALESCE(MAX(id), 0) > 0
                        )
                        FROM {}
                        """
                    ).format(sql.Literal(table), sql.Identifier(table))
                )

            target.commit()

            for table, source_count in copied.items():
                target_count = target.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
                ).fetchone()[0]
                if target_count < source_count:
                    raise RuntimeError(
                        f"Migration verification failed for {table}: "
                        f"source={source_count} target={target_count}"
                    )
                print(
                    f"migration_table={table} source_rows={source_count} "
                    f"target_rows={target_count}"
                )
    finally:
        source.close()
        close_postgres_pool()


if __name__ == "__main__":
    migrate(
        os.getenv("SQLITE_PATH", "/app/data/bridge.db"),
        os.environ["DATABASE_URL"],
    )
