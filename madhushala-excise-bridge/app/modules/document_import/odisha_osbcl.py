"""Deterministic Odisha OSBCL Delivery Challan Cum Invoice extraction.

Only the known selectable-text, nine-column OSBCL layout is accepted. The
extractor fails closed on missing rows, arithmetic, or financial totals.
"""
from __future__ import annotations
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct

PROFILE = "ODISHA_OSBCL_INVOICE"
HEADERS = ("slno", "productcategory", "itemname", "packsize", "ratecase",
           "qtyincases", "qtyinlooseunits", "mgervalueinrs", "amountinrs")


def recognized(text: str) -> bool:
    lower = text.casefold()
    return "odisha state beverages corporation" in lower and "delivery challan cum invoice" in lower


def _key(text) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())


def _money(text: Any) -> Decimal:
    raw = str(text or "").strip().replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", raw):
        raise ValueError(f"Invalid Odisha invoice money value: {text!r}")
    return Decimal(raw)


def _count(text: Any) -> int:
    raw = str(text or "").strip()
    if not re.fullmatch(r"\d+", raw):
        raise ValueError(f"Invalid Odisha quantity: {text!r}")
    return int(raw)


def _capture(text: str, expr: str) -> str:
    match = re.search(expr, text, flags=re.I)
    if not match:
        raise ValueError(f"Missing Odisha invoice header: {expr}")
    return match.group(1).strip()


def _osbcl_rows(rows: list[list[str]]) -> list[list[str]] | None:
    """Return canonical nine-column rows for either printed OSBCL grid layout.

    The final page of some invoices repeats the grid with the serial and
    category merged, plus blank spacer cells. Keep this narrowly structural:
    all labels and spacer positions must match before any values are remapped.
    """
    if not rows:
        return None
    if len(rows[0]) == 9 and tuple(_key(x) for x in rows[0]) == HEADERS:
        return rows[1:]
    if len(rows[0]) != 10:
        return None
    header = [_key(x) for x in rows[0]]
    if not (header[0].startswith("slproductno") and header[0].endswith("category")
            and header[2] == "itemname" and header[3] == "packsize"
            and header[4] == "ratecase" and header[5] == "qtyincases"
            and header[7] == "qtyinlooseunits" and header[8] == "mgervalueinrs"
            and header[9] == "amountinrs" and not header[1] and not header[6]):
        return None
    normalized: list[list[str]] = []
    for row in rows[1:]:
        if len(row) != 10:
            raise ValueError("Odisha continuation table has unexpected column layout")
        if _key(row[5]) in {"subtotal", "vat", "tcs", "total"}:
            continue
        if _key(row[4]) == "subtotal":
            if row[2] or row[6]:
                raise ValueError("Odisha continuation subtotal has unexpected column layout")
            normalized.append(["", "", "", "", "Sub Total", row[5], row[7], row[8], row[9]])
            continue
        match = re.fullmatch(r"\s*(\d{1,3})\s*(.+?)\s*", row[0])
        if not match:
            # Printed LPL/MGER metadata appears below the product grid.
            if not row[0].strip() or _key(row[0]).startswith(("totallpl", "monthlymger", "mgerachieved", "mgervalue")):
                continue
            raise ValueError("Odisha continuation table contains an unrecognized row")
        if row[2] or row[6]:
            raise ValueError("Odisha continuation product has unexpected column layout")
        normalized.append([match.group(1), match.group(2), row[1], row[3], row[4],
                           row[5], row[7], row[8], row[9]])
    return normalized


def _financials(text: str) -> dict[str, Decimal]:
    # Total summary is printed after the product grid. Match the LABEL then
    # the immediately following amount, not a product-row field.
    def value(label: str):
        matches = re.findall(rf"(?m)^\s*{label}\s*\n\s*([\d,]+\.\d{{2}})\s*$", text, re.I)
        if not matches or len(set(matches)) != 1:
            raise ValueError(f"Missing or conflicting Odisha total: {label}")
        return _money(matches[0])
    return {"subtotal": value("Sub Total"), "vat": value("VAT"),
            "tcs": value("TCS"), "total": value("Total")}


def extract_osbcl(document, page_texts=None) -> tuple[ExtractedDocument, dict[str, Any]]:
    started = time.perf_counter()
    page_texts = page_texts if page_texts is not None else [page.get_text("text") for page in document]
    first_text = page_texts[0]
    if not recognized(first_text):
        raise ValueError("Not an Odisha OSBCL invoice")
    invoice_number = _capture(first_text, r"Invoice\s*No\.?\s*:\s*(INV\([A-Z]+\)\s*/\s*\d{4}-\d{4}\s*/\s*\d+)")
    invoice_date = _capture(first_text, r"Invoice\s*Date\s*:\s*(\d{2}/\d{2}/\d{4})")
    token = _capture(first_text, r"Token\s*No\.?\s*:\s*(OD-[^\n]+)")
    depot = _capture(first_text, r"Depot\s*Name\s*:\s*([^\n]+)")
    retailer = _capture(first_text, r"Retailer\s*Code\s*:\s*(\d+)")

    serials: list[int] = []
    cases_total = loose_total = 0
    amount_total = mger_total = Decimal(0)
    products: list[ExtractedProduct] = []
    reported = None
    table_ms = cell_ms = 0.0
    for page_num, page in enumerate(document, 1):
        # Terms-only pages have no product grid. Reuse the routing text pass
        # instead of running table detection on them.
        page_key = _key(page_texts[page_num - 1])
        if not all(header in page_key for header in ("itemname", "qtyincases", "qtyinlooseunits")):
            continue
        table_started = time.perf_counter()
        tables = page.find_tables().tables
        table_ms += (time.perf_counter() - table_started) * 1000
        from app.modules.document_import.pdf_extractor import _horizontal_page_chars, _horizontal_cell_text
        chars = sorted(_horizontal_page_chars(page), key=lambda char: char[3])
        centres_y = [char[3] for char in chars]
        for table in tables:
            # Table.extract() can reorder punctuation and glyphs in OSBCL's
            # tightly spaced cells. Read each detected cell from one indexed
            # glyph pass, preserving the printed decimal and full item name.
            cell_started = time.perf_counter()
            rows = [[_horizontal_cell_text(cell, chars, centres_y).strip() if cell else ""
                     for cell in row.cells] for row in table.rows]
            cell_ms += (time.perf_counter() - cell_started) * 1000
            canonical_rows = _osbcl_rows(rows)
            if canonical_rows is None:
                continue
            for row in canonical_rows:
                if len(row) != 9:
                    raise ValueError("Odisha table has unexpected column count")
                if _key(row[4]) == "subtotal":
                    if reported is not None:
                        raise ValueError("Duplicate Odisha subtotal row")
                    reported = {
                        "cases": _count(row[5]), "loose": _count(row[6]),
                        "mger": _money(row[7]), "amount": _money(row[8]),
                    }
                    continue
                serial = _count(row[0])
                name = " ".join(str(row[2] or "").split())
                category = " ".join(str(row[1] or "").split())
                mlmatch = re.fullmatch(r"(\d{2,4})\s*ML",str(row[3] or "").strip(),re.I)
                if not name or not category or mlmatch is None:
                    raise ValueError(f"Incomplete Odisha row {serial}")
                rate = _money(row[4])
                cases, loose = _count(row[5]), _count(row[6])
                mger, amount = _money(row[7]), _money(row[8])
                if cases + loose <= 0:
                    raise ValueError(f"Zero Odisha quantity at row {serial}")
                # The printed amount equals case_rate × cases in supplied
                # fixtures; a loose-unit purchase requires a confirmed rate.
                if loose and rate * cases != amount:
                    raise ValueError(f"Odisha row {serial} has loose units without verified unit rate")
                if not loose and rate * cases != amount:
                    raise ValueError(f"Odisha row {serial} printed amount disagrees with rate and cases")
                serials.append(serial)
                cases_total += cases
                loose_total += loose
                mger_total += mger
                amount_total += amount
                products.append(ExtractedProduct(
                    itemName=name,brand=name,ml=int(mlmatch.group(1)),
                    box=cases,loose=loose,confidence=1,
                    sourceState="ODISHA",sourcePage=page_num,sourceSerial=serial,invoiceAmount=str(amount),
                    quantitySemantics="explicit_cases_and_loose_units",
                    rawPdfRow={"column1":serial,"productCategory":category,
                        "invoiceRatePerCase":str(rate),"invoiceAmount":str(amount),
                        "invoiceMger":str(mger),"sourceToken":token},
                ))

    if not products or serials != list(range(1, len(products)+1)):
        raise ValueError("Odisha serials are missing, duplicated or out of order")
    if reported is None:
        raise ValueError("Odisha invoice printed subtotal row not detected")
    if (reported["cases"] != cases_total or reported["loose"] != loose_total
        or reported["mger"] != mger_total or reported["amount"] != amount_total):
        raise ValueError("Odisha product totals do not match printed table subtotal")
    financials = _financials("\n".join(page_texts))
    if amount_total != financials["subtotal"]:
        raise ValueError("Odisha printed invoice subtotal mismatch")
    if financials["subtotal"] + financials["vat"] + financials["tcs"] != financials["total"]:
        raise ValueError("Odisha invoice VAT/TCS/grand total do not reconcile")

    metadata = {
        "engine":"pymupdf-osbcl", "profile":PROFILE, "state":"ODISHA",
        "usable":True,"needsFallback":False,"pageCount":len(document),
        "productCount":len(products),"candidateRowCount":len(products),
        "documentRole":"primary","validated":True,
        "timingsMs": {"findTablesMs": round(table_ms, 2), "readCellsMs": round(cell_ms, 2),
                      "totalMs": round((time.perf_counter() - started) * 1000, 2)},
        "invoiceTotals": {
            "cases":cases_total,"loose":loose_total,
            **{key:str(value) for key,value in financials.items()},
            "mger":str(mger_total), "grandTotal":str(financials["total"]),
            "grossAmount":str(financials["subtotal"]), "netAmount":str(financials["total"]),
            "roundOff":None, "taxes":{"vat":str(financials["vat"]), "tcs":str(financials["tcs"])},
        },
    }
    extracted = ExtractedDocument(
        documentType="invoice",invoiceNumber=re.sub(r"\s+", "", invoice_number),
        invoiceDate=invoice_date,items=products,extractionEngine="pymupdf-osbcl",
        extractionProfile=PROFILE,sourceState="ODISHA",
        transportPassNo=token,sourceDepot=depot,retailerCode=retailer,
        extractionDiagnostics={"validated":True,**metadata},
        invoiceFinancials=metadata["invoiceTotals"],
    )
    return extracted, metadata
