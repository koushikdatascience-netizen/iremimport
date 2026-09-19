# PostgreSQL cutover runbook

The bridge now supports PostgreSQL through `DATABASE_URL` while keeping SQLite as
an explicit fallback until production is cut over. This allows a safe migration
without changing QR/PDF/mapping/purchase contracts.

## Production target

Use PostgreSQL 16 with a private Docker network. Do not expose port 5432 publicly.

Recommended initial bridge pool settings for the current single FastAPI container:

```env
POSTGRES_DB=madhushala_bridge
POSTGRES_USER=madhushala_bridge
POSTGRES_PASSWORD=<strong-random-secret>
DATABASE_URL=postgresql://madhushala_bridge:<url-encoded-password>@postgres:5432/madhushala_bridge
DB_POOL_MIN_SIZE=2
DB_POOL_MAX_SIZE=20
DB_POOL_TIMEOUT_SECONDS=10
```

Do not increase pool size until load testing proves the database needs it.

## Safe cutover

1. Back up the existing persistent volume and SQLite database.
2. Start PostgreSQL with the overlay:
   ```bash
   docker compose -f docker-compose.prod.yml -f docker-compose.postgres.yml up -d postgres
   ```
3. Initialize the PostgreSQL schema using the same application image:
   ```bash
   docker compose -f docker-compose.prod.yml -f docker-compose.postgres.yml run --rm \
     -e DATABASE_URL="$DATABASE_URL" \
     web python -c "from app.db import init_db; init_db()"
   ```
4. Stop bridge writes for the short migration window:
   ```bash
   docker stop madhushala_automation_web
   ```
5. Copy the SQLite state into PostgreSQL:
   ```bash
   docker compose -f docker-compose.prod.yml -f docker-compose.postgres.yml run --rm \
     -e DATABASE_URL="$DATABASE_URL" \
     web python scripts/migrate_sqlite_to_postgres.py \
       --sqlite /app/data/bridge.db \
       --postgres "$DATABASE_URL"
   ```
6. Add `DATABASE_URL` and pool settings to the production `.env`.
7. Start/redeploy the bridge.
8. Verify:
   - `/health` is ready.
   - Existing mappings are reused.
   - A fresh PDF and QR import can map and save.
   - Purchase transaction/event history is present.
   - No SQLite write-lock errors appear in logs.

## Rollback

Do not delete `bridge.db` during cutover.

To roll back, remove/comment `DATABASE_URL` from the production environment and
restart the previous/current application image. The bridge will return to the
existing SQLite file.

Once PostgreSQL has run successfully through the agreed observation period,
archive the SQLite file as a migration backup instead of using it as live state.
