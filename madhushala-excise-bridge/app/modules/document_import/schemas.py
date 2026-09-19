from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DocumentType = Literal["invoice", "price_list", "stock_list", "unknown"]


NumberLike = int | float | str | None


class ExtractedProduct(BaseModel):
    model_config = ConfigDict(extra="allow")

    itemName: str = ""
    brand: str = ""
    ml: int | str | None = None
    packing: int | str | None = None
    quantity: NumberLike = None
    box: NumberLike = None
    loose: NumberLike = None
    freeQnty: NumberLike = None
    batchNo: str | None = None
    rate: NumberLike = None
    boxRate: NumberLike = None
    looseRate: NumberLike = None
    mrp: NumberLike = None
    amount: NumberLike = None
    itemAmount: NumberLike = None
    discount: NumberLike = None
    cgst: NumberLike = None
    sgst: NumberLike = None
    cess: NumberLike = None
    addCess: NumberLike = None
    igst: NumberLike = None
    t1Amt: NumberLike = None
    t2Amt: NumberLike = None
    t3Amt: NumberLike = None
    t4Amt: NumberLike = None
    etd: NumberLike = None
    t1Rate: NumberLike = None
    t2Rate: NumberLike = None
    t3Rate: NumberLike = None
    t4Rate: NumberLike = None
    cgstInptLdgr: str | None = None
    sgstInptLdgr: str | None = None
    cessInptLdgr: str | None = None
    adCessInptLdgr: str | None = None
    igstInptLdgr: str | None = None
    barcode: str | None = None
    confidence: NumberLike = None


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    documentType: DocumentType = "unknown"
    supplierName: str | None = None
    invoiceNumber: str | None = None
    transportPassNo: str | None = None
    invoiceDate: str | None = None
    items: list[ExtractedProduct] = Field(default_factory=list)


class NormalizedImportItem(BaseModel):
    source: str
    sourceItemId: str
    rawName: str
    normalizedName: str
    brand: str
    ml: int | None = None
    packing: int | None = None
    quantity: float | None = None
    rate: float | None = None
    mrp: float | None = None
    amount: float | None = None
    batchNo: str | None = None
    box: int | None = None
    loose: int | None = None
    freeQnty: int | None = None
    discount: float | None = None
    cgst: float | None = None
    sgst: float | None = None
    cess: float | None = None
    addCess: float | None = None
    igst: float | None = None
    t1Amt: float | None = None
    t2Amt: float | None = None
    t3Amt: float | None = None
    t4Amt: float | None = None
    etd: float | None = None
    barcode: str | None = None
    confidence: float | None = None
    rawData: dict[str, Any]


