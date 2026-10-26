import copy
import json
from decimal import Decimal
import pytest
from fastapi import HTTPException
from app.modules.document_import.jharkhand_financials import apply_financial_evidence, require_verified_financials, money
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct


def document():
    return ExtractedDocument(sourceState="JHARKHAND", documentProfile="JHARKHAND_STATE_BEVERAGES",
        invoiceNumber="TEST-INVOICE", items=[
            ExtractedProduct(itemName="TEST A", ml=750, box=1, sourcePage=1, sourceRowNumber=1, amount="999.99"),
            ExtractedProduct(itemName="TEST B", ml=375, box=2, sourcePage=2, sourceRowNumber=2, amount="888.88")])


def evidence():
    return [
        {"page":1,"data":{"invoiceNumber":"TEST-INVOICE","transportPassNo":"DHA-2030-2031/42",
            "lastRowNumber":1,"items":[{"sourceRowNumber":1,"sourceAmountText":"1,000.00"}]}},
        {"page":2,"data":{"lastRowNumber":2,"totalCases":"3.00",
            "items":[{"sourceRowNumber":2,"sourceAmountText":"2,000.00"}],
            "invoiceFinancials":{"invoiceAmount":"3,000.00","grandTotal":"3,010.00",
                "vat":"20.00","aed":"5.00","vatOnAed":"1.00","aedIncludingVat":"6.00","liquorTransportation":"10.00"}}},
    ]


def test_printed_evidence_replaces_wrong_taxable_values_and_reconciles():
    result=apply_financial_evidence(document(),evidence())
    assert result.sourceFinancialValidation["status"]=="verified"
    assert [i.invoiceAmount for i in result.items]==["1000.00","2000.00"]
    assert result.transportPassNo=="DHA-2030-2031/42"
    assert result.invoiceFinancials["aed"]=="5.00"
    assert result.invoiceFinancials["aedIncludingVat"]=="6.00"
    assert result.items[1].invoiceFinancials==result.invoiceFinancials
    assert money("12,235.05")==Decimal("12235.05")
    assert money("12,235.O5") is None
    assert money("NaN") is None


@pytest.mark.parametrize("failure",["missing","taxable","duplicate","row_missing","total_missing","conflict","cases","permit"])
def test_unreliable_financials_require_review_and_block_handoff(failure):
    pages=evidence()
    if failure=="missing": pages[1]["data"]["items"][0]["sourceAmountText"]=None
    if failure=="taxable": pages[1]["data"]["items"][0]["sourceAmountText"]="888.88"
    if failure=="duplicate": pages[1]["data"]["items"]*=2
    if failure=="row_missing": pages[1]["data"]["items"]=[]
    if failure=="total_missing": pages[1]["data"]["invoiceFinancials"]={}
    if failure=="conflict": pages[0]["data"]["invoiceFinancials"]={"invoiceAmount":"100.00"}
    if failure=="cases": pages[1]["data"]["totalCases"]="4.00"
    if failure=="permit": pages[0]["data"]["transportPassNo"]=None
    result=apply_financial_evidence(document(),pages)
    assert result.sourceFinancialValidation["status"]=="review_required"
    with pytest.raises(HTTPException) as exc:
        require_verified_financials([{"raw_data_json":json.dumps(result.items[0].model_dump())}])
    assert exc.value.status_code==409


def test_printed_tax_rounding_disagreement_is_preserved_as_warning():
    pages=evidence()
    pages[1]["data"]["invoiceFinancials"]["aedIncludingVat"]="5.95"
    result=apply_financial_evidence(document(),pages)
    assert result.sourceFinancialValidation["status"]=="verified"
    assert result.sourceFinancialValidation["warnings"]
    assert result.invoiceFinancials["aedIncludingVat"]=="5.95"


def test_other_states_and_permits_are_not_gated():
    require_verified_financials([{"raw_data_json":json.dumps({"sourceState":"WEST_BENGAL"})},
                               {"raw_data_json":json.dumps({"sourceState":"ODISHA"})},
                               {"raw_data_json":json.dumps({"sourceState":"JHARKHAND","transportPassNo":"DHA-2030-2031/42"})}])


@pytest.mark.asyncio
async def test_page_by_page_financial_merge_normalization_and_persistence_metadata(monkeypatch,tmp_path):
    import fitz
    from app.modules.document_import.llama_client import LlamaCloudClient
    from app.modules.document_import.normalizer import normalize_extracted_document
    src=fitz.open()
    src.new_page();src.new_page()
    path=tmp_path/"scan.pdf";src.save(path);src.close()
    async def products(self,path,filename,**kwargs):
        number=int(filename.rsplit("-",1)[1].split(".")[0])
        item=document().items[number-1].model_copy(update={"sourceQuantityText":f"{number}.00"})
        return ExtractedDocument(invoiceNumber="TEST-INVOICE" if number==1 else None,
             documentProfile="JHARKHAND_STATE_BEVERAGES",items=[item])
    async def financials(self,path,number): return evidence()[number-1]["data"]
    monkeypatch.setattr(LlamaCloudClient,"extract_products",products)
    monkeypatch.setattr(LlamaCloudClient,"extract_jharkhand_financial_page",financials)
    result=await object.__new__(LlamaCloudClient).extract_scanned_pdf_pages(path,"test.pdf")
    assert result.sourceFinancialValidation["status"]=="verified"
    normalized=normalize_extracted_document(result,"DOCUMENT_PDF")
    assert [i.rawData["invoiceAmount"] for i in normalized]==["1000.00","2000.00"]
    assert all(i.amount is None for i in normalized)
    assert normalized[1].rawData["invoiceFinancials"]["grandTotal"]=="3010.00"
    assert normalized[1].rawData["transportPassNo"]=="DHA-2030-2031/42"


@pytest.mark.asyncio
async def test_financial_instructions_use_documented_config_without_changing_legacy_requests(monkeypatch):
    from app.modules.document_import.llama_client import LlamaCloudClient
    client=object.__new__(LlamaCloudClient)
    calls=[]
    async def request(*args,**kwargs):
        calls.append(kwargs["json"])
        return {"data":{"items":[]}}
    monkeypatch.setattr(client,"_request",request)
    await client.start_extraction("file",prompt="legacy")
    assert "system_prompt" not in calls[-1]["config"]
    await client.start_extraction("file",prompt="financial instructions",data_schema={"type":"object"})
    assert calls[-1]["config"]["system_prompt"]=="financial instructions"
    assert calls[-1]["config"]["high_resolution_mode"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("summary",[True,False])
async def test_table_aed_is_separate_and_product_only_page_cannot_supply_totals(monkeypatch,summary):
    from app.modules.document_import.llama_client import LlamaCloudClient
    client=object.__new__(LlamaCloudClient)
    async def upload(*args): return "file"
    async def start(*args,**kwargs):
        return {"data":{"items":[],"hasPrintedInvoiceSummary":summary,"totalCases":"3.00",
                        "invoiceAmount":"3000.00","tableAedWithoutVat":"5.00","footerAed":"5.95"}}
    monkeypatch.setattr(client,"upload_file",upload)
    monkeypatch.setattr(client,"start_extraction",start)
    result=await client.extract_jharkhand_financial_page(None,1)
    if summary:
        assert result["invoiceFinancials"]["aed"]=="5.00"
        assert result["invoiceFinancials"]["aedIncludingVat"]=="5.95"
    else:
        assert result["invoiceFinancials"]=={} and result["totalCases"] is None
