from __future__ import annotations

import asyncio

from app.modules.document_import import document_mapping


class FakeCursor:
    def __init__(self, one=None):
        self._one = one

    def fetchone(self):
        return self._one


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.job_status = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT excise_item_code FROM import_items WHERE id=? AND job_id=?"):
            job_item_id, job_id = params
            row = self.rows.get((job_item_id, job_id))
            return FakeCursor(one={"excise_item_code": row["excise_item_code"]} if row else None)

        if normalized.startswith("UPDATE import_items SET mapping_status='MAPPED'"):
            item_code, _updated_at, job_item_id, job_id = params
            row = self.rows[(job_item_id, job_id)]
            row["mapping_status"] = "MAPPED"
            row["mapped_item_code"] = item_code
            return FakeCursor()

        if "COUNT(*) AS total" in normalized and "FROM import_items WHERE job_id=?" in normalized:
            (job_id,) = params
            rows = [row for (row_id, row_job_id), row in self.rows.items() if row_job_id == job_id]
            return FakeCursor(
                one={
                    "total": len(rows),
                    "mapped": sum(1 for row in rows if row.get("mapping_status") == "MAPPED"),
                }
            )

        if normalized.startswith("UPDATE import_jobs SET mapped_count=?"):
            self.job_status = params[1]
            return FakeCursor()

        raise AssertionError(normalized)


class FakeMappingService:
    def __init__(self):
        self.calls = []

    async def save_session_mappings(self, session, selections, job_id=None):
        self.calls.append((session, selections, job_id))
        return {"mappedCount": len(selections), "response": {"ok": True}}


class FakeService:
    def __init__(self):
        self.mapping_service = FakeMappingService()

    def get_job(self, session, job_id):
        return {"id": job_id, "shop_code": session["shop_code"]}


def test_document_mapping_without_excise_code_is_saved_locally(monkeypatch):
    db = FakeDb(
        {
            ("row-1", "job-1"): {
                "excise_item_code": "",
                "mapping_status": "PENDING",
                "mapped_item_code": "",
            }
        }
    )
    monkeypatch.setattr(document_mapping, "conn", lambda: db)
    service = FakeService()
    session = {"shop_code": "SHOP", "session_id": "SESSION"}

    result = asyncio.run(
        document_mapping.save_document_row_mappings(
            service,
            session,
            "job-1",
            [{"jobItemId": "row-1", "exciseItemCode": None, "itemCode": "M1800"}],
        )
    )

    assert result["mappedCount"] == 1
    assert result["globalMappedCount"] == 0
    assert result["localOnlyCount"] == 1
    assert service.mapping_service.calls == []
    assert db.rows[("row-1", "job-1")]["mapping_status"] == "MAPPED"
    assert db.rows[("row-1", "job-1")]["mapped_item_code"] == "M1800"
    assert db.job_status == "COMPLETED"


def test_document_mapping_with_excise_code_still_saves_global_mapping(monkeypatch):
    db = FakeDb(
        {
            ("row-1", "job-1"): {
                "excise_item_code": "1614",
                "mapping_status": "UNMAPPED",
                "mapped_item_code": "",
            }
        }
    )
    monkeypatch.setattr(document_mapping, "conn", lambda: db)
    service = FakeService()
    session = {"shop_code": "SHOP", "session_id": "SESSION"}

    result = asyncio.run(
        document_mapping.save_document_row_mappings(
            service,
            session,
            "job-1",
            [{"jobItemId": "row-1", "exciseItemCode": 1614, "itemCode": "M1614"}],
        )
    )

    assert result["mappedCount"] == 1
    assert result["globalMappedCount"] == 1
    assert result["localOnlyCount"] == 0
    assert len(service.mapping_service.calls) == 1
    _session, selections, job_id = service.mapping_service.calls[0]
    assert selections == [{"exciseItemCode": 1614, "itemCode": "M1614"}]
    assert job_id is None
    assert db.rows[("row-1", "job-1")]["mapped_item_code"] == "M1614"
