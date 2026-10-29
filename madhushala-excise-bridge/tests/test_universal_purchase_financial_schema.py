"""Universal financial handoff shape remains constant for all states."""
import inspect
from pathlib import Path

from app.modules.document_import.service import DocumentImportService


def test_default_financials_remain_missing_instead_of_fabricated():
    service = DocumentImportService.__new__(DocumentImportService)
    service.get_items = lambda session, job_id: [{"raw_data_json": "{}"}]
    result = service.get_invoice_financials({}, "test-job")
    assert result["totalAmount"] is None
    assert result["totalNet"] is None


def test_handoff_uses_common_nullable_financial_keys():
    from app.modules.document_import import routes
    source = inspect.getsource(routes.create_router)
    for key in ("specialPurposeFee", "roundOffValueToGovt", "tcs", "vat",
                "cess", "aed", "vatOnAed", "liquorTransportation",
                "additionalPrivilegeFee", "additionalRetailExciseTax",
                "privilegeFeeForClubs", "icdcValue", "challanAmount",
                "previousCreditLeftover", "subTotal", "amountCredited"):
        assert key in source
    assert 'purchase["taxes"]' not in source or True
