"""Phase 1 excise item models."""
from typing import Optional

from pydantic import BaseModel, Field


class RawExciseItem(BaseModel):
    brand: str = ""
    strengthRaw: str = ""
    measureMl: str = "0"
    packageType: str = ""
    retailerMargin: str = "0"
    roundOffGovt: str = "0"
    specialPurposeFee: str = "0"
    mrpPerUnit: str = "0"
    bottlesPerCase: str = "0"
    mrpPerCase: str = "0"
    supplier: str = ""
    warehouseCasesRaw: str = "0"
    warehouseBottles: str = "0"
    requestedCases: str = "0"
    requestedBottles: str = "0"


class NormalizedExciseItem(BaseModel):
    brand: str
    normalizedBrand: str
    strengthRaw: str
    measureMl: int
    packageType: str
    retailerMargin: str
    roundOffGovt: str
    specialPurposeFee: str
    mrpPerUnit: str
    bottlesPerCase: int
    mrpPerCase: str
    supplier: str
    warehouseCasesRaw: str
    warehouseBottles: int
    requestedCases: int
    requestedBottles: int
    canonicalKey: str
    source: str = Field(default="WB_EXCISE_PREPARE_INDENT")
    capturedAt: str
