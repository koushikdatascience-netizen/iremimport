import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.automation.row_parser import normalize_raw_items


FIXTURE = Path(__file__).parent / "fixtures" / "warehouse_stock.html"


FIELD_SUFFIXES = {
    "_glbl_brandvt": "brand",
    "_mlbllegStr": "strengthRaw",
    "_lblmsr": "measureMl",
    "_lblbottle": "packageType",
    "_lblrm": "retailerMargin",
    "_lbl_Round_Off_Govt3": "roundOffGovt",
    "_lbl_Special_Levy3": "specialPurposeFee",
    "_Label55": "mrpPerUnit",
    "_lblnobotpercase": "bottlesPerCase",
    "_lblmrppercase": "mrpPerCase",
    "_lblsupplier": "supplier",
    "_lblclblcase": "warehouseCasesRaw",
    "_lblclosbal": "warehouseBottles",
}


class WarehouseFixtureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current_row = None
        self.current_field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        element_id = attrs.get("id", "")

        if tag == "tr":
            self.current_row = {"checked": False, "fields": {}}
            return

        if self.current_row is None:
            return

        if tag == "input" and element_id.endswith("_chkselect"):
            self.current_row["checked"] = "checked" in attrs
        elif tag == "input" and element_id.endswith("_Qty"):
            self.current_row["fields"]["requestedCases"] = attrs.get("value", "")
        elif tag == "input" and element_id.endswith("_txt_bot"):
            self.current_row["fields"]["requestedBottles"] = attrs.get("value", "")
        else:
            self.current_field = next(
                (field for suffix, field in FIELD_SUFFIXES.items() if element_id.endswith(suffix)),
                None,
            )

    def handle_data(self, data):
        if self.current_row is not None and self.current_field:
            self.current_row["fields"][self.current_field] = (
                self.current_row["fields"].get(self.current_field, "") + data
            ).strip()

    def handle_endtag(self, tag):
        if tag in {"span", "td"}:
            self.current_field = None
        if tag == "tr" and self.current_row is not None:
            self.rows.append(self.current_row)
            self.current_row = None


def has_typed_case_quantity(row: dict) -> bool:
    try:
        return int(row["fields"].get("requestedCases", "0") or "0") > 0
    except ValueError:
        return False


def snapshot_case_typed_rows_from_fixture() -> list[dict]:
    parser = WarehouseFixtureParser()
    parser.feed(FIXTURE.read_text(encoding="utf-8"))
    return [row["fields"] for row in parser.rows if has_typed_case_quantity(row)]


def test_normalize_checked_snapshot_skips_malformed_row():
    raw_items = [
        {"brand": "Absolut Vodka", "measureMl": "750 ML", "packageType": "Glass Bottle", "mrpPerUnit": "2020"},
        {"brand": "", "measureMl": "N/A", "packageType": "Glass Bottle"},
    ]

    items = normalize_raw_items(raw_items, captured_at="2026-08-20T12:00:00+00:00")

    assert len(items) == 1
    assert items[0]["canonicalKey"] == "absolut vodka|750|glass bottle"
    assert items[0]["mrpPerUnit"] == "2020"


def test_selected_row_snapshot_json_case_quantity_rows_only():
    raw_items = snapshot_case_typed_rows_from_fixture()
    assert len(raw_items) == 1
    assert [item.get("brand", "") for item in raw_items] == [
        "Aberfeldy Single Highland Malt Scotch Whisky Aged 12 Years",
    ]

    items = normalize_raw_items(raw_items, captured_at="2026-08-20T12:00:00+00:00")
    assert len(items) == 1
    assert items[0]["requestedCases"] == 2
    assert items[0]["requestedBottles"] == 3
    assert {item["canonicalKey"] for item in items} == {
        "aberfeldy single highland malt scotch whisky aged 12 years|750|glass bottle",
    }


def test_password_never_appears_in_start_response_or_logs(monkeypatch, caplog):
    from app.main import app, browser_manager

    async def fake_start_browser():
        browser_manager.is_running = True
        return True

    async def fake_open_excise_portal(username, password):
        browser_manager.login_page_detected = True
        return True

    monkeypatch.setattr(browser_manager, "start_browser", fake_start_browser)
    monkeypatch.setattr(browser_manager, "open_excise_portal", fake_open_excise_portal)

    secret = "phase1-secret-password"
    client = TestClient(app)
    with caplog.at_level("INFO"):
        response = client.post("/automation/start", json={"username": "demo", "password": secret})

    assert response.status_code == 200
    assert secret not in response.text
    assert secret not in "\n".join(record.getMessage() for record in caplog.records)
