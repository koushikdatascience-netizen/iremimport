from __future__ import annotations

import pytest

from app.modules.document_import.purchase_contract import (
    ItemMasterCalculateClient,
    build_item_master_calculation_request,
)
from app.modules.document_import.purchase_required import resolve_required_purchase_header


def _master() -> dict:
    return {
        "itemCode": "100003",
        "itemName": "100 PIPER DELUX 180ML",
        "packing": 48,
        "purchaseRate": 10.42,
        "purchaseRateCase": 500,
        "mrp": 280,
        "purchaseDiscountAmount": 2.5,
        "cgst": 1.1,
        "sgst": 1.2,
        "cess": 1.3,
        "addCess": 1.4,
        "igst": 1.5,
        "t1Amt": 11,
        "t2Amt": 12,
        "t3Amt": 13,
        "t4Amt": 14,
        "etd": 15,
        "t1Rate": 2.1,
        "t2Rate": 2.2,
        "t3Rate": 2.3,
        "t4Rate": 2.4,
    }


def test_calculate_request_uses_item_master_and_extracted_bottles_as_loose():
    request = build_item_master_calculation_request(
        {
            "shopCode": "hedu_test2",
            "companyCode": "2",
            "schemeCode": "",
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
        },
        [{"itemCode": "100003", "qnty": 6, "freeQnty": 0}],
        {"100003": _master()},
    )

    item = request["items"][0]
    assert item["itemCode"] == "100003"
    assert item["box"] == 0
    assert item["loose"] == 6
    assert item["packing"] == 48
    assert item["boxRate"] == 500.0
    assert item["looseRate"] == 10.42
    assert item["mrp"] == 280.0
    assert item["discount"] == 0.0
    assert item["cgst"] == 1.1
    assert item["sgst"] == 1.2
    assert item["cess"] == 1.3
    assert item["addCess"] == 1.4
    assert item["igst"] == 1.5
    assert item["t1Amt"] == 11.0
    assert item["t2Amt"] == 12.0
    assert item["t3Amt"] == 13.0
    assert item["t4Amt"] == 14.0
    assert item["etd"] == 15.0
    assert item["t1Rate"] == 2.1
    assert item["t2Rate"] == 2.2
    assert item["t3Rate"] == 2.3
    assert item["t4Rate"] == 2.4


def test_calculate_request_preserves_reviewed_cases_and_loose_bottles():
    request = build_item_master_calculation_request(
        {
            "shopCode": "hedu_test2",
            "companyCode": "2",
            "schemeCode": "",
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
        },
        [
            {
                "itemCode": "100003",
                "box": 7,
                "loose": 47,
                "qnty": 383,
                "freeQnty": 0,
            }
        ],
        {"100003": _master()},
    )

    item = request["items"][0]
    assert item["box"] == 7
    assert item["loose"] == 47
    assert item["packing"] == 48
    assert item["boxRate"] == 500.0
    assert item["looseRate"] == 10.42
    assert item["discount"] == 0.0


@pytest.mark.asyncio
async def test_client_mutates_debug_request_to_exact_request_sent_and_normalizes_tax_total():
    captured = {}

    class Inner:
        async def calculate_purchase(self, payload):
            captured["payload"] = dict(payload)
            return {
                "items": [
                    {
                        "itemCode": "100003",
                        "quantity": 6,
                        "amount": 62.52,
                        "t1Amount": 6,
                    }
                ],
                "grossAmount": 80,
                "totalVAT": 6,
                "totalTCS": 0,
                "totalTP": 7,
                "totalOther": 8,
                "totalETD": 9,
                "netAmount": 80,
            }

    request = {
        "shopCode": "hedu_test2",
        "companyCode": "2",
        "schemeCode": "",
        "salesTaxRate": 0,
        "salesTaxIncludingFree": False,
        "items": [{"itemCode": "100003", "loose": 6, "boxRate": 0, "mrp": 0}],
    }
    client = ItemMasterCalculateClient(
        Inner(),
        [{"itemCode": "100003", "qnty": 6, "freeQnty": 0}],
        {"100003": _master()},
    )

    response = await client.calculate_purchase(request)

    assert request == captured["payload"]
    assert request["items"][0]["loose"] == 6
    assert request["items"][0]["box"] == 0
    assert request["items"][0]["looseRate"] == 10.42
    assert request["items"][0]["boxRate"] == 500.0
    assert request["items"][0]["mrp"] == 280.0
    assert request["items"][0]["packing"] == 48

    # Calculate omitted rate/MRP in this synthetic response, so the facade keeps
    # Item Master values available to the final Purchase merge.
    assert response["items"][0]["looseRate"] == 10.42
    assert response["items"][0]["mrp"] == 280.0
    assert response["taxAmount"] == 30.0


@pytest.mark.asyncio
async def test_qr_header_uses_transport_pass_document_date_not_browser_today():
    class Service:
        def get_job(self, _session, _job_id):
            return {
                "source_type": "QR_HTML",
                "invoice_date": "2026-06-16 13:39:17",
                "supplier_name": "HARPREET SINGH",
                "source_filename": "",
            }

    header = await resolve_required_purchase_header(
        Service(),
        {"shop_code": "hedu_test2"},
        "job-1",
        {
            "docDate": "2026-09-17",
            "tpPassNo": "WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674",
            "supplierCode": "T00005",
            "storeCode": "S00001",
            "purchaseAccCode": "P00002",
            "userCode": "A00001",
        },
    )

    assert header["docDate"] == "2026-06-16"


def test_calculate_allows_only_purchase_rate_to_be_non_zero():
    master = _master()
    master["purchaseRateCase"] = 0

    request = build_item_master_calculation_request(
        {
            "shopCode": "hedu_test2",
            "companyCode": "2",
            "schemeCode": "",
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
        },
        [{"itemCode": "100003", "qnty": 18, "freeQnty": 0}],
        {"100003": master},
    )

    item = request["items"][0]
    assert item["box"] == 0
    assert item["loose"] == 18
    assert item["boxRate"] == 0
    assert item["looseRate"] == 10.42


def test_calculate_uses_case_rate_as_loose_rate_when_purchase_rate_is_zero():
    master = _master()
    master["purchaseRate"] = 0

    request = build_item_master_calculation_request(
        {
            "shopCode": "hedu_test2",
            "companyCode": "2",
            "schemeCode": "",
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
        },
        [{"itemCode": "100003", "qnty": 18, "freeQnty": 0}],
        {"100003": master},
    )

    item = request["items"][0]
    assert item["box"] == 0
    assert item["loose"] == 18
    assert item["boxRate"] == 500.0
    assert item["looseRate"] == 500.0


def test_calculate_does_not_fall_back_to_generic_rate_aliases():
    master = _master()
    master["purchaseRate"] = 0
    master["purchaseRateCase"] = 0
    master["rate"] = 99.0
    master["boxRate"] = 999.0

    request = build_item_master_calculation_request(
        {
            "shopCode": "hedu_test2",
            "companyCode": "2",
            "schemeCode": "",
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
        },
        [{"itemCode": "100003", "qnty": 18, "freeQnty": 0}],
        {"100003": master},
    )

    item = request["items"][0]
    assert item["boxRate"] == 0
    assert item["looseRate"] == 0


def test_real_item_master_sample_builds_real_calculate_shape():
    master = {
        "itemCode": "100010",
        "itemName": "100 PIPER 750 N",
        "packing": 12,
        "purchaseRate": 0,
        "salesRate": 1880,
        "vat": 100,
        "tcs": 120,
        "tp": 0,
        "others": 0,
        "etd": 0,
        "purchaseRateCase": 200,
        "t1Rate": 0,
        "t2Rate": 0,
        "t3Rate": 2,
        "t4Rate": 0,
    }

    request = build_item_master_calculation_request(
        {
            "shopCode": "WBTEST",
            "companyCode": "3",
            "schemeCode": "",
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
        },
        [{"itemCode": "100010", "qnty": 18, "freeQnty": 0}],
        {"100010": master},
    )

    item = request["items"][0]
    assert item["itemCode"] == "100010"
    assert item["packing"] == 12
    assert item["box"] == 0
    assert item["loose"] == 18
    assert item["boxRate"] == 200.0
    assert item["looseRate"] == 200.0
    assert item["mrp"] == 1880.0
    assert item["t1Amt"] == 100.0
    assert item["t2Amt"] == 120.0
    assert item["t3Amt"] == 0.0
    assert item["t4Amt"] == 0.0
    assert item["t3Rate"] == 2.0


def test_real_item_master_sample_uses_sales_rate_and_tax_master_fields():
    master = {
        "itemCode": "100010",
        "packing": 12,
        "purchaseRate": 0,
        "purchaseRateCase": 200,
        "salesRate": 1880,
        "vat": 100,
        "tcs": 120,
        "tp": 0,
        "others": 0,
        "etd": 0,
        "t1Rate": 0,
        "t2Rate": 0,
        "t3Rate": 2,
        "t4Rate": 0,
    }

    request = build_item_master_calculation_request(
        {
            "shopCode": "WBTEST",
            "companyCode": "3",
            "schemeCode": "",
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
        },
        [{"itemCode": "100010", "qnty": 18, "freeQnty": 0}],
        {"100010": master},
    )

    item = request["items"][0]
    assert item == {
        "itemCode": "100010",
        "box": 0,
        "loose": 18,
        "free": 0,
        "boxRate": 200.0,
        "looseRate": 200.0,
        "mrp": 1880.0,
        "discount": 0.0,
        "cgst": 0.0,
        "sgst": 0.0,
        "cess": 0.0,
        "addCess": 0.0,
        "igst": 0.0,
        "t1Amt": 100.0,
        "t2Amt": 120.0,
        "t3Amt": 0.0,
        "t4Amt": 0.0,
        "etd": 0.0,
        "packing": 12,
        "t1Rate": 0.0,
        "t2Rate": 0.0,
        "t3Rate": 2.0,
        "t4Rate": 0.0,
    }


@pytest.mark.asyncio
async def test_pdf_header_uses_extracted_document_number_and_date_not_browser_defaults():
    class Service:
        def get_job(self, _session, _job_id):
            return {
                "source_type": "DOCUMENT_PDF",
                "invoice_number": "003_COM_DHN_25-26/2026-2027/6267/61/6024",
                "invoice_date": "01/09/2026",
                "supplier_name": "JHARKHAND STATE BEVERAGES CORPORATION LIMITED",
                "source_filename": "1.pdf",
            }

    header = await resolve_required_purchase_header(
        Service(),
        {"shop_code": "hedu_test2"},
        "job-pdf-1",
        {
            "docNo": "BROWSER-DEFAULT",
            "docDate": "2026-09-18",
            "tpPassNo": "DOC",
            "supplierCode": "T00005",
            "storeCode": "S00001",
            "purchaseAccCode": "P00002",
            "userCode": "A00001",
        },
    )

    assert header["docNo"] == "003_COM_DHN_25-26/2026-2027/6267/61/6024"
    assert header["docDate"] == "2026-09-01"
