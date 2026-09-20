from __future__ import annotations

from pathlib import Path

from app import main


def test_mapping_page_loads_row_identity_fix_after_inline_app_script():
    html = main.index_html()
    marker = '<script src="./static/mapping-row-identity.js?v=20260920-purchase-bill-v5"></script>'
    assert marker in html
    assert html.index(marker) > html.rfind("</script>", 0, html.index(marker))


def test_document_mapping_script_keys_rows_by_job_item_id():
    script = Path("app/static/mapping-row-identity.js").read_text(encoding="utf-8")
    assert "jobItemId" in script
    assert "return `job:${jobItemId}`" in script
    assert 'data-row-key="${escapeHtml(rowKey)}"' in script
    assert "selectedMappings.set(rowKey" in script
    assert "jobItemId: documentMapping ? row.jobItemId || null : null" in script
    assert "exciseItemCode: hasValidExciseCode ? parsedExciseCode : null" in script
    assert "/mapping/save`" in script


def test_document_workspace_uses_local_job_row_mapping_state(monkeypatch):
    class FakeCursor:
        def __init__(self, one=None, many=None):
            self._one = one
            self._many = many or []

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._many

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            if "SELECT id FROM import_jobs" in sql:
                return FakeCursor(one={"id": "job-1"})
            if "SELECT id, mapped_item_code, mapping_status FROM import_items" in sql:
                return FakeCursor(
                    many=[
                        {"id": "row-1", "mapped_item_code": "M001", "mapping_status": "MAPPED"},
                        {"id": "row-2", "mapped_item_code": "", "mapping_status": "UNMAPPED"},
                    ]
                )
            raise AssertionError(sql)

    monkeypatch.setattr(main, "conn", lambda: FakeDb())
    workspace = {
        "madhushalaItems": [
            {"itemCode": "M001", "itemName": "Correct item"},
            {"itemCode": "GLOBAL", "itemName": "Wrong inherited item"},
        ],
        "unmappedItems": [
            {
                "jobItemId": "row-1",
                "exciseItemCode": "101",
                "selectedItemCode": "GLOBAL",
                "selectedItem": {"itemCode": "GLOBAL"},
            },
            {
                "jobItemId": "row-2",
                "exciseItemCode": "101",
                "selectedItemCode": "GLOBAL",
                "selectedItem": {"itemCode": "GLOBAL"},
            },
        ],
    }
    session = {"shop_code": "SHOP", "session_id": "SESSION"}

    result = main._apply_document_row_mapping_state(workspace, session, "job-1")

    assert result["unmappedItems"][0]["selectedItemCode"] == "M001"
    assert result["unmappedItems"][0]["selectedItem"]["itemCode"] == "M001"
    assert result["unmappedItems"][1]["selectedItemCode"] is None
    assert result["unmappedItems"][1]["selectedItem"] is None
    assert result["unmappedItems"][1]["mappingStatus"] == "UNMAPPED"
